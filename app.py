"""
Worker Hub + Backup Browser — ONE server, ONE bot.

  hub_app.py      Worker Hub: control bot (password + 2FA), PC worker manager, Telegram accounts UI (/app)
  tgweb.py/tgui.py  Telegram accounts engine + web UI (part of Worker Hub)
  storage_app.py  Backup Browser (tgstorage): browse / stream the backup groups, backup API (/hapi)

Both halves run in this one process. The same bot token is used by both:
  • Worker Hub talks to the Bot API (getUpdates) and owns the main menu / login.
  • Backup Browser keeps its Pyrogram (MTProto) bot client for reading/streaming group files.
  • Whoever is logged in to the Hub bot (password + 2FA) is automatically trusted by Backup Browser's bot commands.
A tiny path router sends each web request to the right half (no URL clashes, separate cookies).
"""
import os, sys, signal


def _alias(a, b):
    va, vb = os.environ.get(a, "").strip(), os.environ.get(b, "").strip()
    if va and not vb:
        os.environ[b] = va
    elif vb and not va:
        os.environ[a] = vb


# One set of credentials is enough — either spelling works.
for _a, _b in (("BOT_TOKEN", "TELEGRAM_BOT_TOKEN"), ("MONGO_URI", "MONGODB_URI"),
               ("API_ID", "TG_API_ID"), ("API_HASH", "TG_API_HASH"), ("WEB_URL", "PUBLIC_URL")):
    _alias(_a, _b)

def _mem_limit_mb():
    for f in ("/sys/fs/cgroup/memory.max", "/sys/fs/cgroup/memory/memory.limit_in_bytes"):
        try:
            v = int(open(f).read().strip())
            if 0 < v < 1 << 40:
                return v // 1048576
        except Exception:
            pass
    return 0

# Small server (<= 768 MB, e.g. Koyeb free 512 MB): safer defaults — everything stays overridable by env.
_mem = _mem_limit_mb()
if 0 < _mem <= 768:
    os.environ.setdefault("MAX_ACCOUNTS", "3")        # each Telegram account ~30-50 MB
    os.environ.setdefault("DISK_CACHE_GB", "1")       # free-tier disk is small and ephemeral
os.environ.setdefault("BW_LIMIT_GB", "100000")        # this server has no outbound limit

_missing = [k for k in ("BOT_TOKEN", "API_ID", "API_HASH", "WEB_PASSWORD", "ADMIN_PASSWORD",
                        "COLAB_SECRET", "PANEL_PASSWORD") if not os.environ.get(k, "").strip()]
if _missing:
    print("[config] Missing required environment variables: " + ", ".join(_missing), flush=True)
    sys.exit(1)

storage_app = hub_app = None
_loaded = False


def load():
    """Import both halves and wire them together. Slow on a small server (Mongo connect etc.),
    so the web server is already listening before this runs (see __main__)."""
    global storage_app, hub_app, _loaded
    import storage_app as _s          # noqa: E402  (Backup Browser)
    import hub_app as _h              # noqa: E402  (Worker Hub)
    # One login for the whole bot: Hub's admin list decides who may use Backup Browser's bot commands.
    _s.ADMIN_CHECK = _h.AUTH.is_admin
    _s.ADMIN_IDS = _h.AUTH.admin_ids
    _h.STORAGE = _s
    storage_app, hub_app = _s, _h
    _loaded = True


# ── path router ──────────────────────────────────────────────────────────
_HUB_EXACT = {"/health", "/api/sync", "/app"}
_HUB_PREFIX = ("/app/", "/i/", "/f/")


def _is_hub(path):
    return path in _HUB_EXACT or path.startswith(_HUB_PREFIX)


def application(environ, start_response):
    path = environ.get("PATH_INFO", "")
    if not _loaded:                      # still booting: health checks pass, the rest waits
        ok = path in ("/healthz", "/health")
        start_response("200 OK" if ok else "503 Service Unavailable",
                       [("Content-Type", "text/plain"), ("Retry-After", "5")])
        return [b"ok" if ok else b"starting"]
    target = hub_app.app if _is_hub(path) else storage_app.app
    return target(environ, start_response)


def _on_sigterm(signum, frame):
    if not _loaded:
        os._exit(0)
    try:
        import tgweb
        if tgweb.M:
            try:
                tgweb.M.call(tgweb.M.stop_all(), timeout=5)
            except Exception:
                pass
            tgweb.M._release()
    except Exception as e:
        print(f"[shutdown] hub: {e}", flush=True)
    storage_app._on_sigterm(signum, frame)      # stops Pyrogram, saves index snapshot, frees lock, exits


if __name__ == "__main__":
    import threading, time, traceback
    signal.signal(signal.SIGTERM, _on_sigterm)
    from waitress import create_server
    port = int(os.environ.get("PORT", "8080"))
    # 1) open the port FIRST — the platform's TCP health check passes immediately,
    #    even if Mongo / Telegram are slow on a small 512 MB instance.
    server = create_server(application, host="0.0.0.0", port=port,
                           threads=int(os.environ.get("WEB_THREADS", "32")),   # every worker PC holds one long-poll connection
                           connection_limit=300, channel_timeout=300, cleanup_interval=30,
                           send_bytes=1, ident="web")
    print(f"[web] listening on 0.0.0.0:{port} | RAM limit: {_mem or '?'} MB", flush=True)

    # 2) then load the app and start the heavy parts one by one
    def _boot():
        try:
            load()
            time.sleep(1)
            storage_app.start_background()
            time.sleep(3)
            hub_app.start_background()
            print("[boot] ready — background services started", flush=True)
        except BaseException:
            traceback.print_exc()
            print("[boot] FAILED — exiting so the platform restarts / shows the error", flush=True)
            os._exit(1)
    threading.Thread(target=_boot, daemon=True, name="boot").start()
    server.run()
