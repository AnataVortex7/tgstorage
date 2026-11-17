"""machines.py - "Machines" page of tgstorage: one PC-style file manager for every SSH terminal on your tailnet.

How it works (all stdlib, nothing runs until somebody opens /machines):
  * tailscaled is started LAZILY in userspace mode (SOCKS5 on 127.0.0.1:1055) with the same TAILSCALE_AUTHKEY you
    already use for the hermes terminal, and stopped again after TS_IDLE_MIN idle minutes -> ~0 RAM / CPU when unused.
  * tailnet peers are discovered with `tailscale status --json`; each one is probed for the hermes file API
    (hermesterminal fsd.py, port FS_PORT=8765, tailnet-only). Extra machines: MACHINES="name=host:port, ..."
  * the browser only ever talks to tgstorage (same website login as the rest of the site); tgstorage streams
    files to / from the machines in 256 KB chunks, so RAM stays flat for any file size.

Env: TAILSCALE_AUTHKEY, TS_HOSTNAME (tgstorage), TS_IDLE_MIN (10), TS_STATE_DIR, FS_PORT (8765), MACHINES.
Without TAILSCALE_AUTHKEY it connects directly (use this if the host itself is already on the tailnet).
"""
import os, re, json, time, shutil, socket, struct, threading, subprocess, http.client, ipaddress
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import quote
from flask import Response, request, jsonify, session, stream_with_context

TS_KEY = os.environ.get("TAILSCALE_AUTHKEY", "").strip()
TS_HOST = os.environ.get("TS_HOSTNAME", "tgstorage").strip() or "tgstorage"
TS_IDLE = max(1, float(os.environ.get("TS_IDLE_MIN", "10") or 10)) * 60
FS_PORT = int(os.environ.get("FS_PORT", "8765") or 8765)
SOCK = "/tmp/tailscaled-tgs.sock"
SOCKS = ("127.0.0.1", 1055)
CHUNK = 256 * 1024
MANUAL = {}
for _part in os.environ.get("MACHINES", "").split(","):
    if "=" in _part:
        _n, _a = _part.split("=", 1)
        _h, _, _p = _a.strip().rpartition(":") if ":" in _a else (_a.strip(), "", "")
        MANUAL[_n.strip()] = (_h or _a.strip(), int(_p) if _p.isdigit() else FS_PORT)

_ts = {"proc": None, "last": 0.0, "err": "", "lock": threading.Lock(), "reaper": False}
_disc = {"at": 0.0, "machines": {}, "others": 0, "lock": threading.Lock()}
_known = set()
JOBS = {}
_jobs_lock = threading.Lock()


# ───────────────────────── tailnet (lazy tailscaled) ─────────────────────────
def _state_dir():
    d = os.environ.get("TS_STATE_DIR", "").strip()
    for cand in ([d] if d else []) + ["/data/tailscale", "/tmp/tailscale"]:
        try:
            os.makedirs(cand, exist_ok=True)
            if os.access(cand, os.W_OK):
                return cand
        except OSError:
            pass
    return "/tmp"


def _ts_cli(*args, timeout=15):
    return subprocess.run(["tailscale", f"--socket={SOCK}", *args], capture_output=True, text=True, timeout=timeout)


def _ts_running():
    p = _ts["proc"]
    return p is not None and p.poll() is None


def ts_stop():
    with _ts["lock"]:
        p, _ts["proc"] = _ts["proc"], None
        if p and p.poll() is None:
            p.terminate()
            try:
                p.wait(5)
            except Exception:
                p.kill()
        print("[machines] tailscale stopped (idle)", flush=True)


def _reaper():
    while True:
        time.sleep(30)
        if _ts_running() and time.time() - _ts["last"] > TS_IDLE:
            ts_stop()


def ensure_up():
    """Start tailscaled if needed. Returns '' when ready, else a human readable error."""
    if not TS_KEY:
        return ""                                   # direct mode
    _ts["last"] = time.time()
    with _ts["lock"]:
        if _ts_running():
            return ""
        if not (shutil.which("tailscaled") and shutil.which("tailscale")):
            return "tailscale is not installed in this image (rebuild with the updated Dockerfile)"
        try:
            if os.path.exists(SOCK):
                os.remove(SOCK)
            env = dict(os.environ, GOMEMLIMIT="64MiB", GOGC="50")        # keep the Go runtime small
            _ts["proc"] = subprocess.Popen(
                ["tailscaled", f"--state={_state_dir()}/tailscaled.state", f"--socket={SOCK}", "--tun=userspace-networking",
                 f"--socks5-server={SOCKS[0]}:{SOCKS[1]}"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, env=env)
            for _ in range(50):
                if os.path.exists(SOCK):
                    break
                time.sleep(0.1)
            r = _ts_cli("up", f"--authkey={TS_KEY}", f"--hostname={TS_HOST}", "--accept-dns=false", "--accept-routes=false",
                        "--timeout=25s", timeout=40)
            if r.returncode != 0:
                raise RuntimeError((r.stderr or r.stdout).strip()[:200] or "tailscale up failed")
        except Exception as e:
            p, _ts["proc"] = _ts["proc"], None
            if p and p.poll() is None:
                p.kill()
            return f"tailnet connect failed: {e}"
        if not _ts["reaper"]:
            _ts["reaper"] = True
            threading.Thread(target=_reaper, daemon=True, name="ts_reaper").start()
        print("[machines] tailscale up", flush=True)
    return ""


# ───────────────────────── HTTP to a machine (through SOCKS5) ─────────────────────────
def _recv(s, n):
    b = b""
    while len(b) < n:
        c = s.recv(n - len(b))
        if not c:
            raise OSError("socks: connection closed")
        b += c
    return b


def _open(ip, port, timeout):
    if not TS_KEY:
        return socket.create_connection((ip, port), timeout)
    s = socket.create_connection(SOCKS, timeout)
    try:
        s.sendall(b"\x05\x01\x00")
        if _recv(s, 2) != b"\x05\x00":
            raise OSError("socks: auth refused")
        s.sendall(b"\x05\x01\x00\x01" + socket.inet_aton(ip) + struct.pack(">H", port))
        h = _recv(s, 4)
        if h[1] != 0:
            raise OSError(f"cannot reach {ip}:{port} over tailnet (socks code {h[1]})")
        _recv(s, {1: 4, 4: 16}.get(h[3]) + 2 if h[3] != 3 else _recv(s, 1)[0] + 2)
        return s
    except BaseException:
        s.close()
        raise


class _Conn(http.client.HTTPConnection):
    def connect(self):
        self.sock = _open(self.host, self.port, self.timeout)
        self.sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)


def _req(m, method, url, body=None, headers=None, timeout=30):
    c = _Conn(m["ip"], m["port"], timeout=timeout, blocksize=CHUNK)
    h = {"X-Requested-With": "fsd"}
    h.update(headers or {})
    try:
        c.request(method, url, body=body, headers=h)
        return c, c.getresponse()
    except BaseException:
        c.close()
        raise


def _jcall(m, method, url, body=None, timeout=30):
    c, r = _req(m, method, url, None if body is None else json.dumps(body).encode(),
                {"Content-Type": "application/json"} if body is not None else None, timeout)
    try:
        data = r.read()
        try:
            return r.status, json.loads(data or b"{}")
        except ValueError:
            return 502, {"ok": False, "error": "bad reply from machine"}
    finally:
        c.close()


# ───────────────────────── discovery ─────────────────────────
def _slug(s):
    return re.sub(r"[^a-z0-9._-]+", "-", (s or "").lower()).strip("-") or "machine"


def _probe(entry):
    mid, ip, port, os_name = entry
    m = {"id": mid, "name": mid, "ip": ip, "port": port, "os": os_name, "up": False}
    try:
        st, d = _jcall(m, "GET", "/fs/ping", timeout=3.5)
        if st == 200 and d.get("ok"):
            m.update(up=True, host=d.get("host", mid), root=d.get("root", "/"), free=d.get("free", 0), total=d.get("total", 0))
    except Exception:
        pass
    return m


def discover(force=False):
    with _disc["lock"]:
        if not force and time.time() - _disc["at"] < 15 and _disc["machines"]:
            return None, _disc
        err = ensure_up()
        if err:
            return err, _disc
        cands = []
        if TS_KEY:
            try:
                r = _ts_cli("status", "--json", timeout=10)
                peers = (json.loads(r.stdout or "{}").get("Peer") or {}).values()
            except Exception as e:
                return f"tailscale status failed: {e}", _disc
            seen = set()
            for p in peers:
                if not p.get("Online"):
                    continue
                ip = next((i for i in p.get("TailscaleIPs", []) if ":" not in i), None)
                if not ip:
                    continue
                mid = _slug(p.get("HostName") or p.get("DNSName", "").split(".")[0])
                if mid in seen:
                    mid = f"{mid}-{ip.split('.')[-1]}"
                seen.add(mid)
                cands.append((mid, ip, FS_PORT, p.get("OS", "")))
        for n, (h, port) in MANUAL.items():
            cands.append((_slug(n), h, port, ""))
        with ThreadPoolExecutor(max_workers=min(12, max(1, len(cands)))) as ex:
            res = list(ex.map(_probe, cands))
        out = {}
        for m in res:
            if m["up"]:
                _known.add(m["id"])
                out[m["id"]] = m
            elif m["id"] in _known:                 # was seen before (e.g. waking up) -> keep it greyed out
                out[m["id"]] = m
        _disc.update(at=time.time(), machines=out, others=sum(1 for m in res if not m["up"] and m["id"] not in _known))
        return None, _disc


def get_machine(mid):
    m = _disc["machines"].get(mid)
    if not m:
        discover(force=True)
        m = _disc["machines"].get(mid)
    if m:
        _ts["last"] = time.time()
    return m


# ───────────────────────── cross-machine copy / move (background jobs) ─────────────────────────
class _Cancelled(Exception):
    pass


class _Pipe:
    """file-like wrapper: pulls from the source response, counts bytes, honours cancel."""
    def __init__(self, resp, job):
        self.r, self.job = resp, job

    def read(self, n=-1):
        if self.job["cancel"]:
            raise _Cancelled()
        b = self.r.read(CHUNK if n is None or n < 0 else min(n, CHUNK))
        self.job["bytes"] += len(b)
        return b


def _unique_names(name):
    stem, ext = os.path.splitext(name)
    yield name
    for i in range(1, 6):
        yield f"{stem} (copy{'' if i == 1 else ' ' + str(i)}){ext}"


def _join(d, n):
    return (d.rstrip("/") + "/" + n) if d != "/" else "/" + n


def _copy_file(sm, sp, dm, ddir, name, job):
    for cand in _unique_names(name):
        sc, sr = _req(sm, "GET", f"/fs/read?path={quote(sp)}&dl=1", timeout=60)
        try:
            if sr.status != 200:
                raise OSError((json.loads(sr.read() or b"{}").get("error") or f"read failed ({sr.status})"))
            dc, dr = _req(dm, "PUT", f"/fs/write?path={quote(_join(ddir, cand))}", body=_Pipe(sr, job),
                          headers={"Content-Length": sr.getheader("Content-Length") or "0"}, timeout=120)
            try:
                d = json.loads(dr.read() or b"{}")
            finally:
                dc.close()
            if dr.status == 409:
                continue
            if dr.status != 200:
                raise OSError(d.get("error") or f"write failed ({dr.status})")
            job["files"] += 1
            return
        finally:
            sc.close()
    raise OSError("could not find a free name")


def _copy_item(sm, it, dm, ddir, job):
    if job["cancel"]:
        raise _Cancelled()
    sp, name = it["p"], os.path.basename(it["p"].rstrip("/")) or "root"
    job["cur"] = name
    if not it.get("d"):
        return _copy_file(sm, sp, dm, ddir, name, job)
    target = None
    for cand in _unique_names(name):
        st, d = _jcall(dm, "POST", "/fs/op", {"op": "mkdir", "path": _join(ddir, cand)})
        if st == 200:
            target = _join(ddir, cand)
            break
        if st != 409:
            raise OSError(d.get("error") or "mkdir failed")
    if not target:
        raise OSError("could not create folder")
    st, d = _jcall(sm, "GET", f"/fs/list?path={quote(sp)}")
    if st != 200:
        raise OSError(d.get("error") or "cannot read folder")
    for sub in d.get("items", []):
        _copy_item(sm, {"p": _join(sp, sub["n"]), "d": sub["d"] and not sub.get("l")}, dm, target, job)


def _xfer_worker(job, sm, items, dm, ddir, move):
    try:
        for it in items:
            try:
                _copy_item(sm, it, dm, ddir, job)
                if move:
                    st, d = _jcall(sm, "POST", "/fs/op", {"op": "delete", "paths": [it["p"]]}, timeout=300)
                    if st != 200 or not d.get("ok"):
                        raise OSError("copied, but could not delete source")
            except _Cancelled:
                job["state"] = "cancelled"
                return
            except Exception as e:
                if len(job["errors"]) < 20:
                    job["errors"].append({"path": it["p"], "error": str(e)[:160]})
        job["state"] = "done"
    except Exception as e:
        job["state"], job["errors"] = "error", [{"path": "", "error": str(e)[:160]}]
    finally:
        job["end"] = time.time()


# ───────────────────────── Flask routes ─────────────────────────
INLINE_OK = re.compile(r"^(image/(png|jpe?g|gif|webp|avif|bmp)|video/(mp4|webm|ogg)|audio/[a-z0-9.+-]+|text/plain)$")
SAFE_HDRS = ("Content-Length", "Content-Range", "Accept-Ranges", "Last-Modified")


def register(app, require_auth, require_api):
    def fail(msg, code=502):
        return jsonify({"ok": False, "error": msg}), code

    def machine_or_err(mid):
        err = ensure_up()
        if err:
            return None, fail(err)
        m = get_machine(mid)
        if not m or not m.get("up"):
            return None, fail("machine is offline or has no file agent", 404)
        return m, None

    def guard_write():                     # CSRF: same rule as require_api, also for PUT
        if request.headers.get("X-Requested-With") != "fetch":
            return fail("bad request", 400)

    @app.route("/machines")
    @require_auth
    def machines_page():
        resp = Response(MACHINES_HTML, mimetype="text/html")
        resp.headers["Content-Security-Policy"] = (
            "default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; "
            "connect-src 'self'; img-src 'self' data: blob:; media-src 'self'; base-uri 'none'; "
            "form-action 'none'; frame-ancestors 'self'")
        resp.headers["Cache-Control"] = "no-store"
        return resp

    @app.route("/api/m/list")
    @require_api
    def m_list():
        err, d = discover(force=request.args.get("refresh") == "1")
        if err:
            return jsonify({"ok": False, "error": err, "configured": bool(TS_KEY or MANUAL)}), 502
        ms = sorted(d["machines"].values(), key=lambda x: x["name"])
        keep = ("id", "name", "os", "up", "host", "root", "free", "total")
        return jsonify({"ok": True, "machines": [{k: m.get(k) for k in keep} for m in ms], "others": d["others"],
                        "configured": bool(TS_KEY or MANUAL)})

    @app.route("/api/m/<mid>/ls")
    @require_api
    def m_ls(mid):
        m, e = machine_or_err(mid)
        if e:
            return e
        try:
            st, d = _jcall(m, "GET", "/fs/list?path=" + quote(request.args.get("path", "/")))
        except Exception as ex:
            return fail(f"machine unreachable: {ex}")
        return jsonify(d), st

    @app.route("/api/m/<mid>/op", methods=["POST"])
    @require_api
    def m_op(mid):
        m, e = machine_or_err(mid)
        if e:
            return e
        b = request.get_json(silent=True) or {}
        op = b.get("op")
        if op not in ("mkdir", "delete", "rename", "move", "copy"):
            return fail("bad op", 400)
        body = {"op": op}
        for k in ("path", "name", "dest"):
            if isinstance(b.get(k), str):
                body[k] = b[k]
        if isinstance(b.get("paths"), list):
            body["paths"] = [p for p in b["paths"] if isinstance(p, str)][:2000]
        try:
            st, d = _jcall(m, "POST", "/fs/op", body, timeout=600)
        except Exception as ex:
            return fail(f"machine unreachable: {ex}")
        return jsonify(d), st

    @app.route("/api/m/<mid>/get")
    @require_auth
    def m_get(mid):
        m, e = machine_or_err(mid)
        if e:
            return e
        path = request.args.get("path", "")
        hdr = {"Range": request.headers["Range"]} if request.headers.get("Range") else {}
        try:
            c, r = _req(m, "GET", f"/fs/read?path={quote(path)}&dl=1", headers=hdr, timeout=60)
        except Exception as ex:
            return fail(f"machine unreachable: {ex}")
        if r.status >= 400 or r.status == 416:
            try:
                data = r.read()
            finally:
                c.close()
            return Response(data, r.status, mimetype="application/json")
        ctype = (r.getheader("Content-Type") or "application/octet-stream").split(";")[0].strip().lower()
        name = os.path.basename(path) or "file"
        inline = request.args.get("dl") != "1" and bool(INLINE_OK.match(ctype))
        h = {k: r.getheader(k) for k in SAFE_HDRS if r.getheader(k)}
        h["Content-Disposition"] = ("inline" if inline else "attachment") + "; filename*=UTF-8''" + quote(name)
        h["Content-Security-Policy"] = "sandbox; default-src 'none'; style-src 'unsafe-inline'; media-src 'self'; img-src 'self'"
        h["X-Content-Type-Options"] = "nosniff"
        h["Cache-Control"] = "no-store"

        def gen():
            try:
                while True:
                    b = r.read(CHUNK)
                    if not b:
                        break
                    yield b
            finally:
                c.close()
        return Response(stream_with_context(gen()), r.status, headers=h, mimetype=ctype if inline else "application/octet-stream")

    @app.route("/api/m/<mid>/put", methods=["PUT"])
    def m_put(mid):
        if not session.get("authenticated"):
            return fail("login required", 401)
        bad = guard_write()
        if bad:
            return bad
        m, e = machine_or_err(mid)
        if e:
            return e
        n = request.content_length
        if n is None:
            return fail("Content-Length required", 411)
        q = "/fs/write?path=" + quote(request.args.get("path", "")) + ("&overwrite=1" if request.args.get("overwrite") == "1" else "")
        try:
            c, r = _req(m, "PUT", q, body=request.stream, headers={"Content-Length": str(n)}, timeout=120)
            try:
                d = json.loads(r.read() or b"{}")
            finally:
                c.close()
        except Exception as ex:
            return fail(f"upload failed: {ex}")
        return jsonify(d), r.status

    @app.route("/api/m/xfer", methods=["POST"])
    @require_api
    def m_xfer():
        b = request.get_json(silent=True) or {}
        sm, dm = get_machine(str(b.get("src", ""))), get_machine(str(b.get("dst", "")))
        items = [{"p": str(i.get("p", "")), "d": bool(i.get("d"))} for i in (b.get("items") or []) if isinstance(i, dict) and i.get("p")]
        ddir = str(b.get("dir") or "/")
        if not (sm and dm and sm.get("up") and dm.get("up")) or not items:
            return fail("machine offline or nothing selected", 400)
        with _jobs_lock:
            for k in [k for k, j in JOBS.items() if j.get("end") and time.time() - j["end"] > 600]:
                JOBS.pop(k, None)
            if sum(1 for j in JOBS.values() if j["state"] == "running") >= 2:
                return fail("2 transfers already running - wait for one to finish", 429)
            jid = os.urandom(6).hex()
            JOBS[jid] = {"state": "running", "bytes": 0, "files": 0, "cur": "", "errors": [], "cancel": False,
                         "start": time.time(), "move": bool(b.get("move")), "n": len(items)}
        threading.Thread(target=_xfer_worker, args=(JOBS[jid], sm, items, dm, ddir, bool(b.get("move"))), daemon=True).start()
        return jsonify({"ok": True, "job": jid})

    @app.route("/api/m/job/<jid>", methods=["GET", "POST"])
    @require_api
    def m_job(jid):
        j = JOBS.get(jid)
        if not j:
            return fail("no such job", 404)
        if request.method == "POST":
            j["cancel"] = True
        _ts["last"] = time.time()
        return jsonify({"ok": True, **{k: j[k] for k in ("state", "bytes", "files", "cur", "errors", "move", "n")}})


# ───────────────────────── page ─────────────────────────
MACHINES_HTML = r"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>Machines</title>
<style>
:root{--bg:#f3f3f3;--content:#fff;--text:#1b1b1b;--muted:#6b6b6b;--line:#e1e1e1;--hover:#eaf3fb;--sel:#cce8ff;--selb:#99d1ff;--menu:#fff;--shadow:0 8px 24px rgba(0,0,0,.18);--acc:#0067c0;--bad:#c42b1c;--ok:#107c10}
@media(prefers-color-scheme:dark){:root:not([data-theme=light]){--bg:#202020;--content:#191919;--text:#f1f1f1;--muted:#a0a0a0;--line:#333;--hover:#2a2d31;--sel:#0b3a5e;--selb:#1a6aa8;--menu:#2b2b2b;--shadow:0 8px 24px rgba(0,0,0,.6);--bad:#ff6b5e;--ok:#6ccb5f}}
*{box-sizing:border-box}html,body{height:100%;margin:0}
body{font:14px/1.35 "Segoe UI",system-ui,-apple-system,Roboto,sans-serif;background:var(--bg);color:var(--text);display:flex;flex-direction:column;overflow:hidden}
button,input,select,textarea{font:inherit;color:inherit}
button{background:none;border:1px solid transparent;border-radius:6px;padding:5px 9px;cursor:pointer;white-space:nowrap}
button:hover:not(:disabled){background:var(--hover)}button:disabled{opacity:.4;cursor:default}
.bar{display:flex;align-items:center;gap:2px;padding:6px 8px;flex-wrap:wrap}
.bar .sp{flex:1}.sep{width:1px;height:20px;background:var(--line);margin:0 4px}
#addr{flex:1;min-width:140px;border:1px solid var(--line);background:var(--content);border-radius:6px;padding:5px 9px}
#q{width:150px;border:1px solid var(--line);background:var(--content);border-radius:6px;padding:5px 9px}
.main{flex:1;min-height:0;display:flex}
#side{width:210px;flex:none;overflow:auto;padding:4px 6px;border-right:1px solid var(--line)}
.m{display:block;width:100%;text-align:left;padding:7px 9px;border-radius:6px;margin-bottom:2px}
.m.on{background:var(--sel)}.m small{display:block;color:var(--muted)}
.dot{display:inline-block;width:8px;height:8px;border-radius:50%;background:var(--ok);margin-right:6px}.dot.off{background:var(--muted)}
.dsk{height:4px;background:var(--line);border-radius:2px;margin-top:4px;overflow:hidden}.dsk i{display:block;height:100%;background:var(--acc)}
#pane{flex:1;min-width:0;display:flex;flex-direction:column;background:var(--content);border-radius:8px 0 0 0;position:relative}
#list{flex:1;overflow:auto}
table{width:100%;border-collapse:collapse}
th{position:sticky;top:0;background:var(--content);text-align:left;font-weight:600;color:var(--muted);padding:6px 8px;border-bottom:1px solid var(--line);cursor:pointer;user-select:none}
td{padding:5px 8px;border-bottom:1px solid transparent;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;max-width:0}
td.n{max-width:60vw}td.s,td.t{color:var(--muted)}th.s,td.s{text-align:right;width:90px}th.t,td.t{width:150px}th.c,td.c{width:30px;max-width:30px}
tr.r{cursor:default}tr.r:hover{background:var(--hover)}tr.r.sel{background:var(--sel)}
#status{padding:5px 12px;color:var(--muted);border-top:1px solid var(--line);font-size:12px;display:flex;gap:12px}
#drop{display:none;position:absolute;inset:0;background:rgba(0,103,192,.12);border:2px dashed var(--acc);align-items:center;justify-content:center;font-size:18px;pointer-events:none}
.empty{padding:40px;text-align:center;color:var(--muted)}
#tx{position:fixed;right:12px;bottom:12px;width:300px;max-width:calc(100vw - 24px);display:flex;flex-direction:column;gap:6px;z-index:20}
.job{background:var(--menu);box-shadow:var(--shadow);border-radius:8px;padding:8px 10px;font-size:12px}
.job .bar2{height:4px;background:var(--line);border-radius:2px;margin-top:5px;overflow:hidden}.job .bar2 i{display:block;height:100%;background:var(--acc);width:0}
.toast{position:fixed;left:50%;bottom:24px;transform:translateX(-50%);background:#323232;color:#fff;padding:8px 14px;border-radius:8px;z-index:50;max-width:90vw}
.modal{position:fixed;inset:0;background:rgba(0,0,0,.45);display:flex;align-items:center;justify-content:center;z-index:40;padding:12px}
.box{background:var(--menu);border-radius:10px;box-shadow:var(--shadow);padding:16px;max-width:96vw;max-height:94vh;display:flex;flex-direction:column;gap:10px;min-width:min(420px,96vw)}
.box h3{margin:0;font-size:15px;word-break:break-all}.box .row{display:flex;gap:8px;justify-content:flex-end}
.box input[type=text]{border:1px solid var(--line);background:var(--content);border-radius:6px;padding:7px 9px}
.box textarea{width:min(900px,92vw);height:60vh;font:13px ui-monospace,Consolas,monospace;background:var(--content);border:1px solid var(--line);border-radius:6px;padding:8px;resize:none;white-space:pre}
.box img,.box video{max-width:90vw;max-height:78vh}
button.pri{background:var(--acc);color:#fff}button.pri:hover:not(:disabled){background:var(--acc);filter:brightness(1.1)}button.dng{background:var(--bad);color:#fff}
#ctx{position:fixed;background:var(--menu);box-shadow:var(--shadow);border-radius:8px;padding:4px;z-index:45;min-width:160px}
#ctx button{display:block;width:100%;text-align:left}
.card{margin:40px auto;max-width:520px;padding:20px;color:var(--muted);text-align:center}
@media(max-width:720px){#side{position:absolute;z-index:10;height:100%;background:var(--bg);display:none;width:230px}#side.open{display:block}th.t,td.t{display:none}#q{display:none}}
@media(min-width:721px){#bm{display:none}}
</style></head><body>
<div class="bar">
 <button id="bm" title="Machines">☰</button>
 <button id="bk" title="Back (Alt+←)">←</button><button id="up" title="Up folder">↑</button><button id="rf" title="Refresh (F5)">⟳</button>
 <input id="addr" spellcheck="false" autocomplete="off"><input id="q" placeholder="Filter…" autocomplete="off">
</div>
<div class="bar" style="padding-top:0">
 <button id="nf">＋ Folder</button><button id="upl">⬆ Upload</button><input type="file" id="fi" multiple hidden>
 <span class="sep"></span>
 <button id="cut">✂ Cut</button><button id="cpy">⧉ Copy</button><button id="pst">📋 Paste</button>
 <span class="sep"></span>
 <button id="ren">✎ Rename</button><button id="dl">⬇ Download</button><button id="del">🗑 Delete</button>
 <span class="sp"></span><a id="back" href="/browse" style="color:var(--muted);text-decoration:none;padding:5px 9px">← Storage</a>
</div>
<div class="main"><div id="side"></div>
 <div id="pane"><div id="list"></div><div id="status"><span id="st1"></span><span id="st2"></span><span id="st3" style="margin-left:auto"></span></div><div id="drop">Drop files to upload here</div></div></div>
<div id="tx"></div>
<script>
(()=>{
const $=id=>document.getElementById(id);
const h=(t,p,...k)=>{const e=document.createElement(t);for(const a in p||{}){if(a==='class')e.className=p[a];else if(a.startsWith('on'))e[a]=p[a];else e.setAttribute(a,p[a])}k.flat().forEach(c=>e.append(c==null?'':c));return e};
const api=async(url,body,opt)=>{const o={headers:{'X-Requested-With':'fetch'}};if(body!==undefined){o.method='POST';o.headers['Content-Type']='application/json';o.body=JSON.stringify(body)}
 let r;try{r=await fetch(url,o)}catch(e){throw new Error('network error')}
 if(r.status===401){location.href='/login';throw new Error('login required')}
 let d={};try{d=await r.json()}catch(e){}
 if(!r.ok||d.ok===false){const x=new Error(d.error||('error '+r.status));x.data=d;throw x}return d};
const enc=encodeURIComponent;
const fmt=b=>b<1024?b+' B':b<1048576?(b/1024).toFixed(1)+' KB':b<1073741824?(b/1048576).toFixed(1)+' MB':(b/1073741824).toFixed(2)+' GB';
const when=t=>t?new Date(t*1000).toLocaleString([], {dateStyle:'short',timeStyle:'short'}):'';
const join=(d,n)=>d==='/'?'/'+n:d+'/'+n;
const base=p=>p.replace(/\/+$/,'').split('/').pop()||'/';
let toastT;const toast=m=>{document.querySelectorAll('.toast').forEach(x=>x.remove());const t=h('div',{class:'toast'},m);document.body.append(t);clearTimeout(toastT);toastT=setTimeout(()=>t.remove(),3500)};
const S={ms:[],m:null,path:'/',items:[],sel:new Set(),last:null,sort:'name',desc:false,clip:null,hist:[],filter:'',busy:false,disk:null};
const TXT=/\.(txt|md|json|ya?ml|toml|ini|cfg|conf|log|sh|bash|zsh|py|js|mjs|ts|css|html?|xml|csv|env|service|sql|c|h|cpp|go|rs|java|php|rb|lua|dockerfile|gitignore)$|^[^.]+$/i;
const IMG=/\.(png|jpe?g|gif|webp|avif|bmp)$/i, VID=/\.(mp4|webm|ogv)$/i, AUD=/\.(mp3|wav|ogg|m4a|flac|opus)$/i;

function modal(content,{onclose}={}){const o=h('div',{class:'modal'},h('div',{class:'box'},content));const close=()=>{o.remove();onclose&&onclose()};o.addEventListener('mousedown',e=>{if(e.target===o)close()});document.body.append(o);return {el:o,close}}
function ask(title,def,ok='OK'){return new Promise(res=>{const i=h('input',{type:'text'});i.value=def||'';let m;const done=v=>{res(v);m.close()};
 m=modal([h('h3',{},title),i,h('div',{class:'row'},h('button',{onclick:()=>done(null)},'Cancel'),h('button',{class:'pri',onclick:()=>done(i.value.trim())},ok))],{onclose:()=>res(null)});
 i.onkeydown=e=>{if(e.key==='Enter')done(i.value.trim());if(e.key==='Escape')done(null)};i.focus();const dot=i.value.lastIndexOf('.');i.setSelectionRange(0,dot>0?dot:i.value.length)})}
function confirmBox(title,msg,ok,danger){return new Promise(res=>{let m;const done=v=>{res(v);m.close()};
 m=modal([h('h3',{},title),h('div',{style:'max-width:460px;word-break:break-all'},msg),h('div',{class:'row'},h('button',{onclick:()=>done(false)},'Cancel'),h('button',{class:danger?'dng':'pri',onclick:()=>done(true)},ok))],{onclose:()=>res(false)})})}

// ───── machines ─────
async function loadMachines(force){
 $('list').replaceChildren(h('div',{class:'empty'},'Connecting to your tailnet… (first open can take ~10 s)'));
 let d;try{d=await api('/api/m/list'+(force?'?refresh=1':''))}catch(e){
  $('side').replaceChildren();$('list').replaceChildren(h('div',{class:'card'},h('b',{},'Cannot connect'),h('p',{},e.message),
   h('p',{},'Set TAILSCALE_AUTHKEY (same key as the hermes terminal) on this server, then retry.'),h('button',{class:'pri',onclick:()=>loadMachines(true)},'Retry')));return}
 S.ms=d.machines;S.others=d.others;drawSide();
 if(!S.ms.length){$('list').replaceChildren(h('div',{class:'card'},h('b',{},'No machines with the file agent found'),
   h('p',{},'Deploy the updated hermesterminal (fsd.py) on your SSH terminal box and make sure it is online in Tailscale.'),h('button',{class:'pri',onclick:()=>loadMachines(true)},'Rescan'))); return}
 const want=location.hash.slice(1).split('|');const m=S.ms.find(x=>x.id===want[0]&&x.up)||S.ms.find(x=>x.up);
 if(m)openMachine(m.id,want[1]||'/')}
function drawSide(){const s=$('side');s.replaceChildren(...S.ms.map(m=>{
  const used=m.total?Math.round((1-m.free/m.total)*100):0;
  return h('button',{class:'m'+(S.m&&S.m.id===m.id?' on':''),onclick:()=>{if(m.up){openMachine(m.id,'/');s.classList.remove('open')}else toast('Machine is offline')}},
   h('span',{class:'dot'+(m.up?'':' off')}),m.name,h('small',{},m.up?[m.os,fmt(m.free)+' free'].filter(Boolean).join(' · '):'offline'),
   m.up&&m.total?h('div',{class:'dsk'},h('i',{style:`width:${used}%`})):'')}),
  h('button',{class:'m',onclick:()=>loadMachines(true),style:'color:var(--muted)'},'⟳ Rescan'),
  S.others?h('small',{style:'display:block;padding:4px 9px;color:var(--muted)'},S.others+' other tailnet device(s) without file agent'):'')}
async function openMachine(id,path){S.m=S.ms.find(x=>x.id===id);S.hist=[];drawSide();await go(path||'/',true)}

// ───── listing ─────
async function go(path,fresh){
 if(!S.m||S.busy)return;S.busy=true;
 try{const d=await api(`/api/m/${S.m.id}/ls?path=${enc(path)}`);
  if(!fresh&&S.path&&(S.path!==d.path))S.hist.push(S.path);
  S.path=d.path;S.parent=d.parent;S.items=d.items;S.trunc=d.truncated;S.sel=new Set();S.last=null;$('addr').value=d.path;S.filter='';$('q').value='';
  history.replaceState(null,'','#'+S.m.id+'|'+d.path);document.title=S.m.name+' · '+d.path;render()}
 catch(e){toast(e.message)}finally{S.busy=false}}
const reload=()=>{S.busy=false;return go(S.path,true)};
function sorted(){const k=S.sort,f=S.filter.toLowerCase();let a=S.items.filter(i=>!f||i.n.toLowerCase().includes(f));
 a.sort((x,y)=>{if(x.d!==y.d)return x.d?-1:1;let r=k==='size'?x.s-y.s:k==='date'?x.m-y.m:x.n.localeCompare(y.n,undefined,{numeric:true,sensitivity:'base'});return S.desc?-r:r});return a}
function render(){const a=sorted();const arrow=k=>S.sort===k?(S.desc?' ▼':' ▲'):'';
 const th=(k,l,c)=>h('th',{class:c,onclick:()=>{if(S.sort===k)S.desc=!S.desc;else{S.sort=k;S.desc=false}render()}},l+arrow(k));
 const tb=h('tbody');
 a.forEach(i=>{const cb=h('input',{type:'checkbox'});cb.checked=S.sel.has(i.n);
  cb.onclick=e=>{e.stopPropagation();pick(i.n,e,true)};
  const tr=h('tr',{class:'r'+(S.sel.has(i.n)?' sel':''),'data-n':i.n},h('td',{class:'c'},cb),h('td',{class:'n',title:i.n},(i.d?'📁 ':i.l?'🔗 ':'📄 ')+i.n),h('td',{class:'s'},i.d?'':fmt(i.s)),h('td',{class:'t'},when(i.m)));
  tr.onclick=e=>pick(i.n,e);tr.ondblclick=()=>openItem(i);tr.oncontextmenu=e=>{e.preventDefault();if(!S.sel.has(i.n)){S.sel=new Set([i.n]);S.last=i.n;paintSel()}ctx(e.clientX,e.clientY)};tb.append(tr)});
 const all=h('input',{type:'checkbox'});all.checked=a.length>0&&S.sel.size===a.length;all.onclick=()=>{S.sel=all.checked?new Set(a.map(i=>i.n)):new Set();paintSel()};
 const t=h('table',{},h('thead',{},h('tr',{},h('th',{class:'c'},all),th('name','Name'),th('size','Size','s'),th('date','Modified','t'))),tb);
 $('list').replaceChildren(a.length?t:h('div',{class:'empty'},S.filter?'No match':'This folder is empty'));updateBar()}
function paintSel(){document.querySelectorAll('tr.r').forEach(tr=>{const on=S.sel.has(tr.dataset.n);tr.classList.toggle('sel',on);const c=tr.querySelector('input');if(c)c.checked=on});updateBar()}
function pick(n,e,box){const a=sorted().map(i=>i.n);
 if(e.shiftKey&&S.last){const x=a.indexOf(S.last),y=a.indexOf(n);S.sel=new Set(a.slice(Math.min(x,y),Math.max(x,y)+1))}
 else if(e.ctrlKey||e.metaKey||box){S.sel.has(n)?S.sel.delete(n):S.sel.add(n);S.last=n}
 else{S.sel=new Set([n]);S.last=n}paintSel()}
const selItems=()=>S.items.filter(i=>S.sel.has(i.n));
function updateBar(){const n=S.sel.size;['cut','cpy','dl','del'].forEach(i=>$(i).disabled=!n);$('ren').disabled=n!==1;$('pst').disabled=!S.clip;
 $('dl').disabled=!n||selItems().some(i=>i.d);
 $('st1').textContent=S.items.length+' items'+(S.trunc?' (truncated)':'');$('st2').textContent=n?n+' selected'+(selItems().every(i=>!i.d)?' · '+fmt(selItems().reduce((s,i)=>s+i.s,0)):''):'';
 $('st3').textContent=S.clip?`${S.clip.cut?'✂':'⧉'} ${S.clip.items.length} on clipboard (${S.clip.mname})`:''}

// ───── actions ─────
async function openItem(i){const p=join(S.path,i.n);
 if(i.d)return go(p);
 const url=`/api/m/${S.m.id}/get?path=${enc(p)}`;
 if(IMG.test(i.n))return modal([h('h3',{},i.n),h('img',{src:url})]);
 if(VID.test(i.n))return modal([h('h3',{},i.n),h('video',{src:url,controls:true,autoplay:true})]);
 if(AUD.test(i.n))return modal([h('h3',{},i.n),h('audio',{src:url,controls:true,autoplay:true})]);
 if(TXT.test(i.n)&&i.s<=1048576)return edit(i,p);
 location.href=url+'&dl=1'}
async function edit(i,p){let text;try{const r=await fetch(`/api/m/${S.m.id}/get?path=${enc(p)}&dl=1`);if(!r.ok)throw 0;text=await r.text()}catch(e){return toast('Cannot read file')}
 if(text.includes('\0')){location.href=`/api/m/${S.m.id}/get?path=${enc(p)}&dl=1`;return}
 const ta=h('textarea',{spellcheck:'false'});ta.value=text;let m;
 const save=h('button',{class:'pri',onclick:async()=>{save.disabled=true;try{await put(p,new Blob([ta.value]),true);toast('Saved');m.close();reload()}catch(e){toast(e.message);save.disabled=false}}},'Save');
 ta.onkeydown=e=>{if((e.ctrlKey||e.metaKey)&&e.key==='s'){e.preventDefault();save.click()}};
 m=modal([h('h3',{},p),ta,h('div',{class:'row'},h('button',{onclick:()=>m.close()},'Close'),save)])}
function put(p,body,overwrite,onprog){return new Promise((res,rej)=>{const x=new XMLHttpRequest();x.open('PUT',`/api/m/${S.m.id}/put?path=${enc(p)}${overwrite?'&overwrite=1':''}`);x.setRequestHeader('X-Requested-With','fetch');
 if(onprog)x.upload.onprogress=e=>e.lengthComputable&&onprog(e.loaded,e.total);
 x.onload=()=>{let d={};try{d=JSON.parse(x.responseText)}catch(e){}x.status===200&&d.ok!==false?res(d):rej(new Error(d.error||'upload failed ('+x.status+')'))};
 x.onerror=()=>rej(new Error('network error'));x.send(body)})}
const op=async b=>{const d=await api(`/api/m/${S.m.id}/op`,b);return d};
function report(d,what){if(d.errors&&d.errors.length)toast(`${what}: ${d.errors.length} failed - ${d.errors[0].error}`)}

async function newFolder(){const n=await ask('New folder','New folder','Create');if(!n)return;try{await op({op:'mkdir',path:join(S.path,n)});reload()}catch(e){toast(e.message)}}
async function rename(){const i=selItems()[0];if(!i)return;const n=await ask('Rename',i.n,'Rename');if(!n||n===i.n)return;try{await op({op:'rename',path:join(S.path,i.n),name:n});reload()}catch(e){toast(e.message)}}
async function del(){const it=selItems();if(!it.length)return;
 const ok=await confirmBox('Delete permanently?',it.length===1?it[0].n:it.length+' items (folders are deleted with everything inside)','Delete',true);if(!ok)return;
 try{const d=await op({op:'delete',paths:it.map(i=>join(S.path,i.n))});report(d,'Delete')}catch(e){toast(e.message)}reload()}
function download(){selItems().filter(i=>!i.d).forEach((i,k)=>setTimeout(()=>{const a=h('a',{href:`/api/m/${S.m.id}/get?path=${enc(join(S.path,i.n))}&dl=1`,download:i.n});document.body.append(a);a.click();a.remove()},k*300))}
function setClip(cut){const it=selItems();if(!it.length)return;S.clip={cut,m:S.m.id,mname:S.m.name,dir:S.path,items:it.map(i=>({p:join(S.path,i.n),d:i.d&&!i.l}))};updateBar();toast((cut?'Cut ':'Copied ')+it.length+' item(s)')}
async function paste(){const c=S.clip;if(!c)return;
 if(c.m===S.m.id){if(c.cut&&c.dir===S.path)return toast('Already here');
  try{const d=await op({op:c.cut?'move':'copy',paths:c.items.map(i=>i.p),dest:S.path});report(d,c.cut?'Move':'Copy');if(c.cut)S.clip=null}catch(e){toast(e.message)}return reload()}
 try{const d=await api('/api/m/xfer',{src:c.m,dst:S.m.id,items:c.items,dir:S.path,move:c.cut});if(c.cut)S.clip=null;track(d.job,(c.cut?'Moving ':'Copying ')+c.items.length+' item(s) → '+S.m.name)}catch(e){toast(e.message)}}
function track(jid,title){const bar=h('i'),line=h('div',{},''),cancel=h('button',{style:'padding:0 6px',onclick:()=>api('/api/m/job/'+jid,{})},'✕');
 const el=h('div',{class:'job'},h('div',{style:'display:flex;justify-content:space-between;gap:6px'},h('b',{},title),cancel),line,h('div',{class:'bar2'},bar));$('tx').append(el);
 const tick=async()=>{try{const j=await api('/api/m/job/'+jid);line.textContent=`${j.state} · ${j.files} file(s) · ${fmt(j.bytes)}${j.cur?' · '+j.cur:''}`;bar.style.width=j.state==='running'?'50%':'100%';
  if(j.state==='running')return setTimeout(tick,1000);
  cancel.remove();if(j.errors.length)toast(j.errors.length+' failed: '+j.errors[0].error);setTimeout(()=>el.remove(),j.errors.length?15000:4000);reload()}catch(e){line.textContent=e.message;setTimeout(()=>el.remove(),5000)}};tick()}

// uploads: 2 at a time, progress cards
let upq=[],upRun=0;
function upload(files){const here=S.path,mid=S.m.id,exist=new Set(S.items.map(i=>i.n));
 [...files].forEach(f=>upq.push({f,here,mid,over:exist.has(f.name)}));pump()}
async function pump(){while(upRun<2&&upq.length){const u=upq.shift();upRun++;(async()=>{
  let over=false;if(u.over){over=await confirmBox('Replace file?',u.f.name+' already exists in '+u.here,'Replace',true);if(!over){upRun--;return pump()}}
  const bar=h('i'),el=h('div',{class:'job'},h('b',{},'⬆ '+u.f.name),h('div',{class:'bar2'},bar));$('tx').append(el);
  const keep=S.m;try{S.m=S.ms.find(x=>x.id===u.mid);await put(join(u.here,u.f.name),u.f,over,(a,b)=>bar.style.width=(a/b*100)+'%')}catch(e){toast(u.f.name+': '+e.message)}finally{S.m=keep;el.remove();upRun--;if(!upRun&&!upq.length&&S.m.id===u.mid)reload();pump()}})()}}

// ───── context menu / keyboard / wiring ─────
function ctx(x,y){document.getElementById('ctx')?.remove();const n=S.sel.size,one=selItems()[0];
 const b=(l,f,dis)=>h('button',{disabled:dis?'':null,onclick:()=>{m.remove();f()}},l);
 const m=h('div',{id:'ctx'},...(n===1&&one?[b('Open',()=>openItem(one))]:[]),b('⬇ Download',download,!n||selItems().some(i=>i.d)),b('✂ Cut',()=>setClip(true),!n),b('⧉ Copy',()=>setClip(false),!n),b('✎ Rename',rename,n!==1),b('🗑 Delete',del,!n));
 m.querySelectorAll('button[disabled=""]').forEach(x=>x.disabled=true);document.body.append(m);m.style.left=Math.min(x,innerWidth-m.offsetWidth-6)+'px';m.style.top=Math.min(y,innerHeight-m.offsetHeight-6)+'px'}
document.addEventListener('click',()=>document.getElementById('ctx')?.remove());
$('list').addEventListener('contextmenu',e=>{if(e.target.closest('tr'))return;e.preventDefault();S.sel=new Set();paintSel();document.getElementById('ctx')?.remove();
 const m=h('div',{id:'ctx'},h('button',{onclick:newFolder},'＋ New folder'),h('button',{onclick:()=>$('fi').click()},'⬆ Upload'),h('button',{onclick:paste,disabled:S.clip?null:''},'📋 Paste'));if(!S.clip)m.lastChild.disabled=true;document.body.append(m);m.style.left=e.clientX+'px';m.style.top=e.clientY+'px'});
$('list').addEventListener('click',e=>{if(!e.target.closest('tr')&&!e.target.closest('th')){S.sel=new Set();paintSel()}});
$('bk').onclick=()=>{const p=S.hist.pop();if(p)go(p,true)};$('up').onclick=()=>S.parent&&go(S.parent);$('rf').onclick=reload;
$('addr').onkeydown=e=>{if(e.key==='Enter')go($('addr').value.trim()||'/')};
$('q').oninput=e=>{S.filter=e.target.value;render()};
$('nf').onclick=newFolder;$('upl').onclick=()=>$('fi').click();$('fi').onchange=e=>{upload(e.target.files);e.target.value=''};
$('cut').onclick=()=>setClip(true);$('cpy').onclick=()=>setClip(false);$('pst').onclick=paste;$('ren').onclick=rename;$('dl').onclick=download;$('del').onclick=del;
$('bm').onclick=()=>$('side').classList.toggle('open');
document.addEventListener('keydown',e=>{if(document.querySelector('.modal')||/INPUT|TEXTAREA/.test(document.activeElement.tagName))return;
 const c=e.ctrlKey||e.metaKey;
 if(c&&e.key==='a'){e.preventDefault();S.sel=new Set(sorted().map(i=>i.n));paintSel()}
 else if(c&&e.key==='c')setClip(false);else if(c&&e.key==='x')setClip(true);else if(c&&e.key==='v')paste();
 else if(e.key==='Delete')del();else if(e.key==='F2')rename();else if(e.key==='F5'){e.preventDefault();reload()}
 else if(e.key==='Backspace'||(e.altKey&&e.key==='ArrowLeft')){e.preventDefault();$('bk').click()}
 else if(e.key==='Enter'&&S.sel.size===1)openItem(selItems()[0])});
const pane=$('pane');let dc=0;
pane.addEventListener('dragenter',e=>{if([...e.dataTransfer.types].includes('Files')){dc++;$('drop').style.display='flex'}});
pane.addEventListener('dragleave',()=>{if(--dc<=0){dc=0;$('drop').style.display='none'}});
pane.addEventListener('dragover',e=>e.preventDefault());
pane.addEventListener('drop',e=>{e.preventDefault();dc=0;$('drop').style.display='none';if(S.m&&e.dataTransfer.files.length)upload(e.dataTransfer.files)});
if(location.search.includes('embed=1')){const b=$('back');b.textContent='✕ Close';b.removeAttribute('href');b.style.cursor='pointer';b.onclick=()=>parent.closeMachines&&parent.closeMachines()}
loadMachines();
})();
</script></body></html>"""
