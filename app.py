"""
Telegram Backup Browser — app.py
Browse files backed up to Telegram via relay.py, from any browser.
"""

import os, sqlite3, hashlib, hmac, secrets, time, threading, asyncio, mimetypes
from datetime import timedelta
from functools import wraps

from flask import (Flask, render_template_string, redirect, url_for,
                   session, request, Response, jsonify, stream_with_context)
from pyrogram import Client
from pyrogram import raw as tg_raw
from pyrogram.errors import FloodWait

# ── Config (from env vars) ───────────────────────────────────────────────
API_ID          = int(os.environ.get("API_ID", "0"))
API_HASH        = os.environ.get("API_HASH", "")
BOT_TOKEN       = os.environ.get("BOT_TOKEN", "")
BOT_NAME        = os.environ.get("BOT_NAME", "").lstrip("@")
BACKUP_GROUP_ID = int(os.environ.get("BACKUP_GROUP_ID", "0"))
OWNER_CHAT_ID   = int(os.environ.get("OWNER_CHAT_ID", "0"))
WEB_PASSWORD    = os.environ.get("WEB_PASSWORD", "changeme")
SECRET_KEY      = os.environ.get("SECRET_KEY", secrets.token_hex(32))
WEB_URL         = os.environ.get("WEB_URL", "http://localhost:8080")
MONGO_URI       = os.environ.get("MONGO_URI", "")      # optional: persists index across restarts
MONGO_DB        = os.environ.get("MONGO_DB", "tgbackup")

MAX_FAIL        = 2         # wrong password attempts before block
BLOCK_SECS      = 86400     # 24 hours

DB_PATH  = "/data/browser.db"
TMP_DIR  = "/tmp/tg_dl"
os.makedirs("/data",  exist_ok=True)
os.makedirs(TMP_DIR,  exist_ok=True)

# ── Flask ────────────────────────────────────────────────────────────────
app = Flask(__name__)
app.secret_key = SECRET_KEY
app.permanent_session_lifetime = timedelta(days=7)
app.config.update(
    SESSION_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_SECURE=WEB_URL.startswith("https"),
    SESSION_COOKIE_HTTPONLY=True,
)
print(
    f"[env] WEB_PASSWORD len={len(WEB_PASSWORD)} "
    f"default={WEB_PASSWORD == 'changeme'} | "
    f"BACKUP_GROUP_ID={BACKUP_GROUP_ID} | "
    f"SECRET_KEY_from_env={'SECRET_KEY' in os.environ} | "
    f"WEB_URL={WEB_URL}",
    flush=True,
)

# ── Database ─────────────────────────────────────────────────────────────
_db_lock = threading.Lock()

def get_db():
    conn = sqlite3.connect(DB_PATH, check_same_thread=False, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn

def db_exec(sql, params=()):
    with _db_lock:
        conn = get_db()
        conn.execute(sql, params)
        conn.commit()
        conn.close()

def db_many(sql, params=()):
    with _db_lock:
        conn = get_db()
        rows = conn.execute(sql, params).fetchall()
        conn.close()
        return rows

def db_one(sql, params=()):
    with _db_lock:
        conn = get_db()
        row = conn.execute(sql, params).fetchone()
        conn.close()
        return row

def init_db():
    with _db_lock:
        conn = get_db()
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS topics (
                topic_id    INTEGER PRIMARY KEY,
                name        TEXT NOT NULL,
                path        TEXT NOT NULL,
                parent_path TEXT NOT NULL DEFAULT '',
                depth       INTEGER DEFAULT 0,
                msg_count   INTEGER DEFAULT 0,
                files_indexed INTEGER DEFAULT 0,
                updated_at  REAL DEFAULT 0
            );
            CREATE TABLE IF NOT EXISTS files (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                msg_id      INTEGER NOT NULL,
                topic_id    INTEGER NOT NULL,
                folder_path TEXT NOT NULL,
                filename    TEXT NOT NULL,
                size        INTEGER DEFAULT 0,
                mime_type   TEXT DEFAULT '',
                UNIQUE(msg_id, topic_id)
            );
            CREATE INDEX IF NOT EXISTS idx_files_topic  ON files(topic_id);
            CREATE INDEX IF NOT EXISTS idx_files_path   ON files(folder_path);
            CREATE INDEX IF NOT EXISTS idx_files_name   ON files(filename COLLATE NOCASE);
            CREATE TABLE IF NOT EXISTS login_tokens (
                token       TEXT PRIMARY KEY,
                created_at  REAL NOT NULL,
                validated   INTEGER DEFAULT 0
            );
            CREATE TABLE IF NOT EXISTS meta (
                key   TEXT PRIMARY KEY,
                value TEXT
            );
            CREATE TABLE IF NOT EXISTS blocked_ips (
                ip            TEXT PRIMARY KEY,
                attempts      INTEGER DEFAULT 0,
                blocked_until REAL DEFAULT 0
            );
        """)
        conn.commit()
        conn.close()

init_db()

# ── Auth helpers ─────────────────────────────────────────────────────────
def get_ip():
    return (request.headers.get("X-Forwarded-For", request.remote_addr or "")
            .split(",")[0].strip())

def is_blocked(ip):
    row = db_one("SELECT attempts, blocked_until FROM blocked_ips WHERE ip=?", (ip,))
    if not row:
        return False
    if row["blocked_until"] > time.time():
        return True
    if row["attempts"] >= MAX_FAIL:
        db_exec("UPDATE blocked_ips SET attempts=0, blocked_until=0 WHERE ip=?", (ip,))
    return False

def record_fail(ip):
    db_exec("""
        INSERT INTO blocked_ips (ip, attempts, blocked_until)
        VALUES (?, 1, 0)
        ON CONFLICT(ip) DO UPDATE SET
            attempts = attempts + 1,
            blocked_until = CASE
                WHEN attempts + 1 >= ? THEN ? ELSE blocked_until
            END
    """, (ip, MAX_FAIL, time.time() + BLOCK_SECS))

def unblock_ip(ip):
    db_exec("UPDATE blocked_ips SET attempts=0, blocked_until=0 WHERE ip=?", (ip,))

def check_password(pwd):
    a = hashlib.sha256((pwd or "").strip().encode()).digest()
    b = hashlib.sha256((WEB_PASSWORD or "").strip().strip('"').strip("'").encode()).digest()
    return hmac.compare_digest(a, b)

def require_auth(f):
    @wraps(f)
    def wrapped(*args, **kwargs):
        if not session.get("authenticated"):
            return redirect(url_for("login_page"))
        return f(*args, **kwargs)
    return wrapped

def require_api(f):
    @wraps(f)
    def wrapped(*args, **kwargs):
        if not session.get("authenticated"):
            return jsonify({"ok": False, "msg": "login required"}), 401
        if request.method == "POST" and request.headers.get("X-Requested-With") != "fetch":
            return jsonify({"ok": False, "msg": "bad request"}), 400
        return f(*args, **kwargs)
    return wrapped

# ── Telegram client ──────────────────────────────────────────────────────
_tg_loop   = None
_tg_client = None
_tg_ready  = threading.Event()

def _load_session():
    """Saved bot session string (Mongo, or SESSION_STRING env). Reusing it
    avoids auth.ImportBotAuthorization on every restart — that call is what
    Telegram flood-limits when you redeploy several times."""
    env = os.environ.get("SESSION_STRING", "").strip()
    if env:
        return env
    if not MONGO_URI:
        return ""
    try:
        from pymongo import MongoClient
        db = MongoClient(MONGO_URI, serverSelectionTimeoutMS=8000)[MONGO_DB]
        doc = db["session"].find_one({"_id": "bot"})
        return (doc or {}).get("session_string", "")
    except Exception as e:
        print(f"[session] load failed: {e}", flush=True)
        return ""

def _save_session(session_string):
    if not MONGO_URI or not session_string:
        return
    try:
        from pymongo import MongoClient
        db = MongoClient(MONGO_URI, serverSelectionTimeoutMS=8000)[MONGO_DB]
        db["session"].replace_one({"_id": "bot"},
                                  {"_id": "bot", "session_string": session_string},
                                  upsert=True)
        print("[session] saved", flush=True)
    except Exception as e:
        print(f"[session] save failed: {e}", flush=True)

def run_telegram():
    global _tg_loop, _tg_client
    _tg_loop = asyncio.new_event_loop()
    asyncio.set_event_loop(_tg_loop)

    async def _start():
        global _tg_client
        from pyrogram import filters

        _loop = asyncio.get_running_loop()
        sess = await _loop.run_in_executor(None, _load_session)
        print(f"[session] using saved session: {bool(sess)}", flush=True)

        _tg_client = Client(
            name="tg_browser",
            api_id=API_ID,
            api_hash=API_HASH,
            bot_token=BOT_TOKEN,
            session_string=sess or None,
            in_memory=True,
            sleep_threshold=120,
        )

        # ── /start TOKEN — approve web login ─────────────────────────────
        @_tg_client.on_message(filters.command("start") & filters.private)
        async def on_start(client, msg):
            if msg.from_user.id != OWNER_CHAT_ID:
                await msg.reply("⛔ Access denied.")
                return
            args = msg.text.split(maxsplit=1)
            token = args[1].strip() if len(args) > 1 else ""
            if not token:
                await msg.reply(
                    "👋 **Backup Browser**\n\n"
                    "Use the **Login via Telegram** button on the web UI to get your login link."
                )
                return
            row = db_one("SELECT * FROM login_tokens WHERE token=? AND validated=0", (token,))
            if not row:
                await msg.reply("❌ Invalid or already-used link. Request a new one from the browser.")
                return
            if time.time() - row["created_at"] > 300:
                await msg.reply("⏰ Link expired (5 min). Request a new one.")
                return
            db_exec("UPDATE login_tokens SET validated=1 WHERE token=?", (token,))
            await msg.reply(
                "✅ **Login approved!**\n\n"
                "आता ज्या browser मध्ये login page उघडलं होतं तिथे परत जा, "
                "ते आपोआप login होईल."
            )

        # ── /unblock [IP] ────────────────────────────────────────────────
        @_tg_client.on_message(filters.command("unblock") & filters.private)
        async def on_unblock(client, msg):
            if msg.from_user.id != OWNER_CHAT_ID:
                return
            args = msg.text.split(maxsplit=1)
            if len(args) < 2:
                rows = db_many(
                    "SELECT ip, blocked_until FROM blocked_ips WHERE blocked_until > ?",
                    (time.time(),)
                )
                if not rows:
                    await msg.reply("✅ No IPs are currently blocked.")
                else:
                    from datetime import datetime
                    lines = [
                        f"🚫 `{r['ip']}` — until {datetime.fromtimestamp(r['blocked_until']).strftime('%d/%m %H:%M')}"
                        for r in rows
                    ]
                    await msg.reply(
                        "**Blocked IPs:**\n" + "\n".join(lines) +
                        "\n\nUse `/unblock <IP>` to unblock."
                    )
                return
            ip = args[1].strip()
            unblock_ip(ip)
            await msg.reply(f"✅ `{ip}` unblocked.")

        # ── /status ──────────────────────────────────────────────────────
        @_tg_client.on_message(filters.command("status") & filters.private)
        async def on_status(client, msg):
            if msg.from_user.id != OWNER_CHAT_ID:
                return
            topics_count = db_one("SELECT COUNT(*) as c FROM topics")["c"]
            files_count  = db_one("SELECT COUNT(*) as c FROM files")["c"]
            await msg.reply(
                f"📊 **Backup Browser**\n\n"
                f"🗂 Topics indexed: `{topics_count}`\n"
                f"📄 Files indexed:  `{files_count}`\n"
                f"🔄 Indexer: `{_index_status['status']}`\n"
                f"🌐 URL: {WEB_URL}"
            )

        from pyrogram.handlers import RawUpdateHandler
        # group=-1 so it runs alongside (not instead of) the command handlers
        _tg_client.add_handler(RawUpdateHandler(_on_raw_update), group=-1)
        while True:
            try:
                await _tg_client.start()
                break
            except FloodWait as e:
                wait = int(getattr(e, "value", 60)) + 5
                _index_status["status"] = f"telegram login flood wait {wait}s"
                print(f"[tg] login FloodWait {wait}s — waiting (don't redeploy)", flush=True)
                try:
                    await _tg_client.disconnect()
                except Exception:
                    pass
                await asyncio.sleep(wait)
        if not sess:
            try:
                new_sess = await _tg_client.export_session_string()
                await _loop.run_in_executor(None, _save_session, new_sess)
            except Exception as e:
                print(f"[session] export failed: {e}", flush=True)
        _tg_ready.set()
        print("[tg] Client ready", flush=True)
        await asyncio.Event().wait()

    _tg_loop.run_until_complete(_start())

def tg(coro, timeout=120):
    """Run a coroutine on the Telegram event loop from any thread."""
    if not _tg_ready.is_set():
        raise RuntimeError("Telegram client not ready yet")
    return asyncio.run_coroutine_threadsafe(coro, _tg_loop).result(timeout=timeout)

# ── Indexer ──────────────────────────────────────────────────────────────
# Design (bots can't read history or list topics, and Telegram flood-limits
# GetMessages):
#   1. The index (topics + file name/size/msg_id) lives in SQLite and is
#      mirrored to MongoDB (MONGO_URI) so it survives Render restarts.
#   2. On startup: restore snapshot -> scan ONLY ids newer than the saved
#      cursor (usually nothing) -> done. No periodic re-scanning.
#   3. New files/topics are indexed live from incoming group updates
#      (no API calls at all).
#   4. Telegram is only contacted on demand: download / send-to-bot.
_index_status = {"status": "idle", "progress": "", "last_run": 0}
_topics = {}                 # topic_id -> normalized path
_scanning = False
_snapshot_dirty = False
GROUP_CHANNEL_ID = (-1000000000000 - BACKUP_GROUP_ID) if BACKUP_GROUP_ID < -1000000000000 else 0

def meta_get(key, default=0):
    row = db_one("SELECT value FROM meta WHERE key=?", (key,))
    return int(row["value"]) if row else default

def meta_set(key, value):
    db_exec("""
        INSERT INTO meta (key, value) VALUES (?, ?)
        ON CONFLICT(key) DO UPDATE SET value=excluded.value
    """, (key, str(value)))

def _norm_path(title):
    return title.replace("\\", "/").replace("//", "/").strip("/")

def _save_topic(tid, title):
    global _snapshot_dirty
    path = _norm_path(title)
    parts = path.rsplit("/", 1)
    parent = parts[0] if len(parts) > 1 else ""
    depth = path.count("/")
    db_exec("""
        INSERT INTO topics (topic_id, name, path, parent_path, depth, files_indexed, updated_at)
        VALUES (?, ?, ?, ?, ?, 1, ?)
        ON CONFLICT(topic_id) DO UPDATE SET
            name=excluded.name, path=excluded.path,
            parent_path=excluded.parent_path, depth=excluded.depth,
            updated_at=excluded.updated_at
    """, (tid, title, path, parent, depth, time.time()))
    db_exec("UPDATE files SET folder_path=? WHERE topic_id=?", (path, tid))
    _topics[tid] = path
    _snapshot_dirty = True
    return path

def _topic_id_of(m):
    rt = getattr(m, "reply_to", None)
    if rt is None:
        return None
    return getattr(rt, "reply_to_top_id", None) or getattr(rt, "reply_to_msg_id", None)

def _handle_raw_message(m):
    """Index one raw Telegram message (topic service msg or document)."""
    global _snapshot_dirty
    if isinstance(m, tg_raw.types.MessageService):
        act = m.action
        if isinstance(act, tg_raw.types.MessageActionTopicCreate):
            _save_topic(m.id, act.title)
        elif isinstance(act, tg_raw.types.MessageActionTopicEdit):
            new_title = getattr(act, "title", None)
            tid = _topic_id_of(m)
            if new_title and tid:
                _save_topic(tid, new_title)
        return False

    media = getattr(m, "media", None)
    if not isinstance(media, tg_raw.types.MessageMediaDocument):
        return False
    doc = media.document
    if doc is None or not hasattr(doc, "attributes"):
        return False
    tid = _topic_id_of(m)
    if tid not in _topics:
        return False
    filename = f"file_{m.id}"
    for attr in doc.attributes:
        if isinstance(attr, tg_raw.types.DocumentAttributeFilename):
            filename = attr.file_name
            break
    db_exec("""
        INSERT OR IGNORE INTO files
            (msg_id, topic_id, folder_path, filename, size, mime_type)
        VALUES (?, ?, ?, ?, ?, ?)
    """, (m.id, tid, _topics[tid], filename,
          getattr(doc, "size", 0) or 0,
          getattr(doc, "mime_type", "") or ""))
    db_exec("UPDATE topics SET msg_count=(SELECT COUNT(*) FROM files WHERE topic_id=?) WHERE topic_id=?",
            (tid, tid))
    _snapshot_dirty = True
    return True

async def _on_raw_update(client, update, users, chats):
    """Live indexing: new group messages arrive as updates — zero API calls."""
    try:
        if not isinstance(update, tg_raw.types.UpdateNewChannelMessage):
            return
        m = update.message
        peer = getattr(m, "peer_id", None)
        if GROUP_CHANNEL_ID == 0 or getattr(peer, "channel_id", None) != GROUP_CHANNEL_ID:
            return
        _handle_raw_message(m)
        if not _scanning and m.id == meta_get("scan_cursor", 0) + 1:
            meta_set("scan_cursor", m.id)
    except Exception as e:
        print(f"[live] {e}", flush=True)

# ── Persistence (MongoDB snapshot) ───────────────────────────────────────
def _mongo_coll():
    from pymongo import MongoClient
    return MongoClient(MONGO_URI, serverSelectionTimeoutMS=8000)[MONGO_DB]["index_snapshot"]

CHUNK = 20000

def snapshot_save():
    if not MONGO_URI:
        return
    coll = _mongo_coll()
    topics = [{k: r[k] for k in ("topic_id", "name", "path", "parent_path",
                                 "depth", "msg_count", "updated_at")}
              for r in db_many("SELECT * FROM topics")]
    rows = [[r["msg_id"], r["topic_id"], r["folder_path"], r["filename"],
             r["size"], r["mime_type"]]
            for r in db_many("SELECT msg_id, topic_id, folder_path, filename, size, mime_type FROM files")]
    n = 0
    for i in range(0, len(rows), CHUNK):
        coll.replace_one({"_id": f"files_{n}"},
                         {"_id": f"files_{n}", "rows": rows[i:i + CHUNK]}, upsert=True)
        n += 1
    coll.delete_many({"_id": {"$in": [f"files_{k}" for k in range(n, n + 50)]}})
    coll.replace_one({"_id": "meta"},
                     {"_id": "meta", "cursor": meta_get("scan_cursor", 0),
                      "topics": topics, "nchunks": n, "saved_at": time.time()},
                     upsert=True)
    print(f"[snapshot] saved {len(topics)} topics, {len(rows)} files", flush=True)

def snapshot_load():
    """Restore index into empty SQLite after a restart."""
    if not MONGO_URI:
        print("[snapshot] MONGO_URI not set — index will NOT survive restarts", flush=True)
        return
    if db_one("SELECT COUNT(*) AS c FROM topics")["c"] > 0:
        return
    coll = _mongo_coll()
    meta = coll.find_one({"_id": "meta"})
    if not meta:
        print("[snapshot] no snapshot yet", flush=True)
        return
    with _db_lock:
        conn = get_db()
        conn.executemany("""
            INSERT OR REPLACE INTO topics
              (topic_id, name, path, parent_path, depth, msg_count, files_indexed, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, 1, ?)
        """, [(t["topic_id"], t["name"], t["path"], t["parent_path"],
               t["depth"], t["msg_count"], t["updated_at"]) for t in meta["topics"]])
        for i in range(meta.get("nchunks", 0)):
            doc = coll.find_one({"_id": f"files_{i}"})
            if doc:
                conn.executemany("""
                    INSERT OR IGNORE INTO files
                      (msg_id, topic_id, folder_path, filename, size, mime_type)
                    VALUES (?, ?, ?, ?, ?, ?)
                """, doc["rows"])
        conn.commit()
        conn.close()
    meta_set("scan_cursor", meta.get("cursor", 0))
    print(f"[snapshot] restored {len(meta['topics'])} topics, cursor={meta.get('cursor')}", flush=True)

def run_snapshotter():
    """Saves the snapshot at most every 2 minutes, only when something changed."""
    global _snapshot_dirty
    while True:
        time.sleep(120)
        if _snapshot_dirty and MONGO_URI:
            _snapshot_dirty = False
            try:
                snapshot_save()
            except Exception as e:
                _snapshot_dirty = True
                print(f"[snapshot] save failed: {e}", flush=True)

# ── Catch-up scan (only ids newer than cursor) ───────────────────────────
async def _invoke_safe(req):
    """invoke() that waits out FloodWait instead of failing."""
    while True:
        try:
            return await _tg_client.invoke(req)
        except FloodWait as e:
            wait = int(getattr(e, "value", 30)) + 2
            _index_status["status"] = f"scanning (flood wait {wait}s)"
            print(f"[index] FloodWait {wait}s", flush=True)
            await asyncio.sleep(wait)

async def _latest_msg_id():
    """Bots can't read history, so post a temp message to learn the newest id."""
    m = await _tg_client.send_message(BACKUP_GROUP_ID, "⏳ indexing…")
    mid = m.id
    try:
        await _tg_client.delete_messages(BACKUP_GROUP_ID, mid)
    except Exception:
        pass
    return mid

async def _scan_group():
    global _scanning, _snapshot_dirty
    _scanning = True
    try:
        peer = await _tg_client.resolve_peer(BACKUP_GROUP_ID)
        ch = tg_raw.types.InputChannel(channel_id=peer.channel_id,
                                       access_hash=peer.access_hash)
        start = meta_get("scan_cursor", 0) + 1
        latest = await _latest_msg_id()
        if start >= latest:
            print("[index] up to date, nothing to scan", flush=True)
            return
        BATCH = 100            # Telegram max ids per GetMessages call
        found = 0
        for lo in range(start, latest + 1, BATCH):
            hi = min(lo + BATCH, latest + 1)
            _index_status["status"] = f"scanning {lo}/{latest}"
            ids = [tg_raw.types.InputMessageID(id=i) for i in range(lo, hi)]
            r = await _invoke_safe(
                tg_raw.functions.channels.GetMessages(channel=ch, id=ids))
            for m in r.messages:
                if _handle_raw_message(m):
                    found += 1
            meta_set("scan_cursor", hi - 1)
            await asyncio.sleep(2.0)      # gentle pace, avoids FloodWait
        _snapshot_dirty = True
        print(f"[index] scan done up to msg {latest}: +{found} files, "
              f"{len(_topics)} topics", flush=True)
    finally:
        _scanning = False

async def _full_index():
    if _scanning:
        return
    _index_status["status"] = "scanning..."
    try:
        await _scan_group()
        _index_status["status"] = "done"
        _index_status["last_run"] = time.time()
    except Exception as e:
        _index_status["status"] = f"error: {e}"
        print(f"[index] Error: {e}", flush=True)

def run_indexer():
    """Startup only: restore snapshot, catch up on new ids, then rely on live updates."""
    _tg_ready.wait()
    try:
        snapshot_load()
    except Exception as e:
        print(f"[snapshot] load failed: {e}", flush=True)
    for r in db_many("SELECT topic_id, path FROM topics"):
        _topics[r["topic_id"]] = r["path"]
    try:
        asyncio.run_coroutine_threadsafe(_full_index(), _tg_loop).result(timeout=7200)
    except Exception as e:
        _index_status["status"] = f"error: {e}"
        print(f"[index] Error: {e}", flush=True)

# ── Browser helpers ───────────────────────────────────────────────────────
def build_tree(path=""):
    """Return (subfolders, files) for a given path. Intermediate folders that
    have no topic of their own are synthesized from deeper topic paths."""
    prefix = f"{path}/" if path else ""
    children = {}
    for t in db_many("SELECT path, msg_count FROM topics"):
        p = t["path"]
        if not p.startswith(prefix) or p == path:
            continue
        child = p[len(prefix):].split("/")[0]
        if not child:
            continue
        children[child] = children.get(child, 0) + (t["msg_count"] or 0)
    subfolders = [{"path": prefix + name, "msg_count": cnt}
                  for name, cnt in sorted(children.items(), key=lambda x: x[0].lower())]
    files = []
    if path:
        files = db_many(
            "SELECT * FROM files WHERE folder_path=? ORDER BY filename COLLATE NOCASE",
            (path,)
        )
    return subfolders, list(files)

def breadcrumbs(path):
    crumbs = []
    if path:
        parts = path.split("/")
        for i in range(len(parts) - 1):
            crumbs.append({"name": parts[i], "path": "/".join(parts[:i+1])})
    return crumbs

def fmt_size(n):
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024:
            return f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} TB"

# ── HTML Templates ────────────────────────────────────────────────────────
LOGIN_HTML = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Backup Browser — Login</title>
<style>
*{margin:0;padding:0;box-sizing:border-box}
body{background:#0d1117;color:#e6edf3;font-family:system-ui,sans-serif;
  min-height:100vh;display:flex;align-items:center;justify-content:center}
.card{background:#161b22;border:1px solid #30363d;border-radius:12px;
  padding:2rem 2.5rem;width:100%;max-width:380px}
.logo{text-align:center;font-size:2.5rem;margin-bottom:.5rem}
h2{text-align:center;font-size:1.3rem;color:#58a6ff;margin-bottom:1.5rem}
label{font-size:.85rem;color:#8b949e;display:block;margin-bottom:.4rem}
input[type=password]{width:100%;padding:.7rem 1rem;background:#0d1117;
  border:1px solid #30363d;border-radius:8px;color:#e6edf3;font-size:1rem;
  outline:none;margin-bottom:1rem}
input:focus{border-color:#58a6ff}
.btn{width:100%;padding:.75rem;border:none;border-radius:8px;font-size:1rem;
  cursor:pointer;font-weight:600;margin-bottom:.6rem;letter-spacing:.02em}
.btn-pw{background:#238636;color:#fff}
.btn-pw:hover{background:#2ea043}
.btn-tg{background:#1a5276;color:#58a6ff;border:1px solid #1e6091}
.btn-tg:hover{background:#1e6091;color:#fff}
.divider{text-align:center;color:#6e7681;font-size:.85rem;margin:.6rem 0}
.err{background:#3d1a1a;border:1px solid #7d2d2d;color:#f87171;
  padding:.7rem 1rem;border-radius:8px;margin-bottom:1rem;font-size:.9rem}
.blocked{background:#2d1f00;border:1px solid #7d5a00;color:#fbbf24;
  padding:.7rem 1rem;border-radius:8px;font-size:.9rem;text-align:center}
</style>
</head>
<body>
<div class="card">
  <div class="logo">📦</div>
  <h2>Backup Browser</h2>
  {% if blocked %}
  <div class="blocked">
    ⛔ Too many wrong attempts. Blocked for 24 hours.<br><br>
    {% if client_ip %}
    <small>Your IP: <code>{{ client_ip }}</code><br>
    Send <code>/unblock {{ client_ip }}</code> to the bot.</small>
    {% endif %}
  </div>
  {% if auth_bot_name %}
  <div style="margin-top:1rem">
    <div class="divider">— or login via Telegram to unblock —</div>
    <a href="/bot_login" style="text-decoration:none">
      <button class="btn btn-tg">🤖 Login via Telegram Bot</button>
    </a>
  </div>
  {% endif %}
  {% else %}
    {% if error %}<div class="err">{{ error }}</div>{% endif %}
    <form method="POST" action="/login">
      <label>Password</label>
      <input type="password" name="password" placeholder="Enter password" autofocus>
      <button class="btn btn-pw" type="submit">🔑 Login</button>
    </form>
    {% if auth_bot_name %}
    <div class="divider">— or —</div>
    <a href="/bot_login" style="text-decoration:none">
      <button class="btn btn-tg">🤖 Login via Telegram Bot</button>
    </a>
    {% endif %}
  {% endif %}
</div>
</body>
</html>"""

BOT_LOGIN_HTML = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Telegram Login</title>
<style>
*{margin:0;padding:0;box-sizing:border-box}
body{background:#0d1117;color:#e6edf3;font-family:system-ui,sans-serif;
  min-height:100vh;display:flex;align-items:center;justify-content:center}
.card{background:#161b22;border:1px solid #30363d;border-radius:12px;
  padding:2rem 2.5rem;width:100%;max-width:380px;text-align:center}
h2{color:#58a6ff;margin-bottom:.75rem;font-size:1.2rem}
p{color:#8b949e;font-size:.9rem;line-height:1.6}
.btn{display:inline-block;background:#0088cc;color:#fff;padding:.75rem 1.5rem;
  border-radius:8px;text-decoration:none;font-weight:600;margin:1.2rem 0;font-size:1rem}
.btn:hover{background:#0099dd}
.spin{display:inline-block;animation:spin 1s linear infinite}
@keyframes spin{to{transform:rotate(360deg)}}
.status{color:#6e7681;font-size:.85rem;margin-top:.5rem}
.success{color:#3fb950;font-size:1rem;font-weight:600}
</style>
<script>
const TOKEN = "{{ token }}";
let tries = 0;
function poll() {
  fetch('/check_token?t=' + TOKEN, {credentials: 'same-origin'})
    .then(r => r.json())
    .then(d => {
      if (d.ok) {
        document.getElementById('status').innerHTML = '<span class="success">✅ Authenticated! Redirecting...</span>';
        setTimeout(() => window.location.href = '/', 600);
      } else if (tries++ < 100) {
        setTimeout(poll, 3000);
      } else {
        document.getElementById('status').textContent = '⏰ Timed out. Please go back and try again.';
      }
    }).catch(() => { if (tries++ < 100) setTimeout(poll, 5000); });
}
window.onload = () => setTimeout(poll, 2000);
</script>
</head>
<body>
<div class="card">
  <div style="font-size:2.5rem;margin-bottom:.5rem">🤖</div>
  <h2>Login via Telegram</h2>
  <p>Click the button below and press <b>Start</b> in Telegram.<br>
  Then come back to this tab — it logs in automatically.</p>
  <a class="btn" href="{{ bot_url }}" target="_blank">Open @{{ auth_bot_name }}</a>
  <p id="status"><span class="spin">⏳</span> Waiting for Telegram authentication…</p>
  <p style="margin-top:1rem;font-size:.8rem;color:#484f58">
    This page auto-redirects once you confirm in Telegram.
  </p>
</div>
</body>
</html>"""

BROWSER_HTML = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{{ path or 'Root' }} — Backup Browser</title>
<style>
*{margin:0;padding:0;box-sizing:border-box}
:root{--bg:#0d1117;--surf:#161b22;--border:#30363d;--text:#e6edf3;
  --muted:#8b949e;--blue:#58a6ff;--green:#3fb950;--yellow:#d29922}
body{background:var(--bg);color:var(--text);font-family:system-ui,sans-serif;min-height:100vh}

header{background:var(--surf);border-bottom:1px solid var(--border);
  padding:.7rem 1.5rem;display:flex;align-items:center;gap:1rem;
  position:sticky;top:0;z-index:20}
.logo{font-size:1.1rem;color:var(--blue);font-weight:700;white-space:nowrap}
.search-wrap{flex:1;max-width:520px}
.search-wrap form{display:flex;gap:.5rem}
.search-wrap input{flex:1;padding:.45rem .9rem;background:var(--bg);
  border:1px solid var(--border);border-radius:6px;color:var(--text);font-size:.9rem;outline:none}
.search-wrap input:focus{border-color:var(--blue)}
.search-wrap button{padding:.45rem .9rem;background:var(--blue);color:#0d1117;
  border:none;border-radius:6px;cursor:pointer;font-weight:600;font-size:.85rem}
.hdr-right{display:flex;gap:1rem;align-items:center;white-space:nowrap}
.idx-badge{font-size:.75rem;color:var(--muted);background:#21262d;
  padding:.25rem .6rem;border-radius:999px}
.idx-badge.done{color:var(--green)}
.idx-badge.indexing{color:var(--yellow)}
a.logout{color:var(--muted);font-size:.85rem;text-decoration:none}
a.logout:hover{color:var(--text)}

.bc{padding:.6rem 1.5rem;font-size:.875rem;color:var(--muted);display:flex;
  flex-wrap:wrap;gap:.25rem;align-items:center}
.bc a{color:var(--blue);text-decoration:none}.bc a:hover{text-decoration:underline}
.bc .sep{color:#484f58}

.wrap{max-width:1200px;margin:0 auto;padding:.5rem 1.5rem 4rem}
table{width:100%;border-collapse:collapse}
thead th{text-align:left;padding:.5rem 1rem;color:var(--muted);font-size:.75rem;
  text-transform:uppercase;letter-spacing:.06em;border-bottom:1px solid var(--border)}
tbody td{padding:.55rem 1rem;border-bottom:1px solid #1c2128;vertical-align:middle}
tbody tr:hover td{background:#1c2128}
.name-cell{display:flex;align-items:center;gap:.6rem}
.icon{font-size:1rem;flex-shrink:0;width:1.2rem;text-align:center}
a.folder{color:var(--blue);text-decoration:none;font-weight:500}
a.folder:hover{text-decoration:underline}
.fname{color:var(--text);font-size:.92rem;word-break:break-all}
.sz{color:var(--muted);font-size:.82rem;white-space:nowrap}
.acts{white-space:nowrap;text-align:right}
.btn-s{padding:.28rem .65rem;border-radius:5px;font-size:.78rem;cursor:pointer;
  text-decoration:none;display:inline-block;border:1px solid var(--border);
  background:transparent;color:var(--text);transition:all .15s;line-height:1.5}
.btn-dl{border-color:#238636;color:var(--green)}.btn-dl:hover{background:#238636;color:#fff}
.btn-tg{border-color:#1e6091;color:var(--blue);margin-left:.4rem}
.btn-tg:hover{background:#1e6091;color:#fff}

.btn-open{border-color:#d29922;color:#d29922;margin-right:.4rem}
.btn-open:hover{background:#d29922;color:#0d1117}
.srh{padding:.5rem 1.5rem .2rem;font-size:.875rem;color:var(--muted)}
.srh b{color:var(--text)}
.srh a{color:var(--blue);font-size:.82rem;margin-left:.5rem;text-decoration:none}

.empty{text-align:center;padding:4rem;color:var(--muted)}

#toast{position:fixed;bottom:1.5rem;right:1.5rem;background:#238636;color:#fff;
  padding:.7rem 1.2rem;border-radius:8px;font-size:.9rem;display:none;
  box-shadow:0 4px 12px rgba(0,0,0,.4);z-index:100}

.statusbar{position:fixed;bottom:0;left:0;right:0;background:var(--surf);
  border-top:1px solid var(--border);padding:.35rem 1.5rem;font-size:.78rem;
  color:var(--muted);display:flex;gap:1.5rem;align-items:center}
.statusbar a{color:var(--muted);text-decoration:none}.statusbar a:hover{color:var(--text)}

@media(max-width:600px){
  header{padding:.6rem 1rem;gap:.5rem}
  .logo{display:none}
  .idx-badge{display:none}
  .wrap{padding:.5rem .75rem 4rem}
  thead th:nth-child(2){display:none}
  tbody td:nth-child(2){display:none}
}
</style>
</head>
<body>

<header>
  <span class="logo">📦 Backup</span>
  <div class="search-wrap">
    <form method="GET" action="/search">
      <input type="search" name="q" placeholder="Search files…" value="{{ q or '' }}">
      <button type="submit">🔍</button>
    </form>
  </div>
  <div class="hdr-right">
    <span class="idx-badge {{ 'done' if idx_status == 'done' else 'indexing' }}">
      {{ '✓ indexed' if idx_status == 'done' else '⟳ ' + idx_status }}
    </span>
    <a class="logout" href="/logout">logout</a>
  </div>
</header>

{% if search_mode %}
<div class="srh">
  {{ files|length }} result{{ 's' if files|length != 1 else '' }} for <b>{{ q }}</b>
  <a href="/">← Clear</a>
</div>
{% else %}
<nav class="bc">
  <a href="/">🏠 Root</a>
  {% for c in crumbs %}
  <span class="sep">›</span>
  <a href="/browse/{{ c.path }}">{{ c.name }}</a>
  {% endfor %}
  {% if path %}
  <span class="sep">›</span>
  <span>{{ path.split('/')[-1] }}</span>
  {% endif %}
</nav>
{% endif %}

<div class="wrap">
<table>
  <thead>
    <tr>
      <th>Name</th>
      <th>Size</th>
      <th style="text-align:right">Actions</th>
    </tr>
  </thead>
  <tbody>
    {% if not search_mode and path %}
    <tr>
      <td colspan="3">
        <div class="name-cell">
          <span class="icon">⬆️</span>
          <a class="folder" href="{{ parent_url }}">.. (go up)</a>
        </div>
      </td>
    </tr>
    {% endif %}

    {% for f in folders %}
    <tr>
      <td>
        <div class="name-cell">
          <span class="icon">📁</span>
          <a class="folder" href="/browse/{{ f.path }}">{{ f.path.split('/')[-1] }}</a>
        </div>
      </td>
      <td class="sz">{{ f.msg_count }} file{{ 's' if f.msg_count != 1 else '' }}</td>
      <td class="acts"></td>
    </tr>
    {% endfor %}

    {% for fi in files %}
    <tr>
      <td>
        <div class="name-cell">
          <span class="icon">{{ '🎬' if 'video' in fi.mime_type else '🎵' if 'audio' in fi.mime_type else '🖼️' if 'image' in fi.mime_type else '📄' }}</span>
          <span class="fname">
            {% if search_mode %}
            <small style="color:var(--muted);display:block;font-size:.75rem">📁 {{ fi.folder_path }}</small>
            {% endif %}
            {{ fi.filename }}
          </span>
        </div>
      </td>
      <td class="sz">{{ fmt_size(fi.size) }}</td>
      <td class="acts">
        {% if can_view(fi) %}
        <a class="btn-s btn-open" target="_blank" rel="noopener"
           href="/stream/{{ fi.msg_id }}/{{ fi.filename|urlencode }}">▶ Open</a>
        {% endif %}
        <a class="btn-s btn-dl"
           href="/download/{{ fi.msg_id }}/{{ fi.filename|urlencode }}">⬇ Download</a>
        <a class="btn-s btn-tg"
           href="#"
           onclick="sendToBot({{ fi.msg_id }}, this); return false;">📤 Telegram</a>
      </td>
    </tr>
    {% endfor %}

    {% if not folders and not files %}
    <tr><td colspan="3" class="empty">📭 Empty folder</td></tr>
    {% endif %}
  </tbody>
</table>
</div>

<div class="statusbar">
  <span>📁 {{ folders|length }} folder{{ 's' if folders|length != 1 else '' }}</span>
  <span>📄 {{ files|length }} file{{ 's' if files|length != 1 else '' }}</span>
  <a href="/reindex">↻ Re-index</a>
  {% if not search_mode %}
  <span style="margin-left:auto;color:#484f58">
    Total files in DB: {{ total_files }}
  </span>
  {% endif %}
</div>

<div id="toast"></div>

<script>
function sendToBot(msgId, btn) {
  const orig = btn.textContent;
  btn.textContent = '⏳…';
  btn.style.opacity = '.6';
  fetch('/sendtobot/' + msgId)
    .then(r => r.json())
    .then(d => {
      btn.textContent = orig;
      btn.style.opacity = '1';
      showToast(d.ok ? '✅ Sent to your chat!' : '❌ ' + d.msg);
    })
    .catch(() => { btn.textContent = orig; btn.style.opacity = '1'; showToast('❌ Error'); });
}
function showToast(msg) {
  const t = document.getElementById('toast');
  t.textContent = msg;
  t.style.display = 'block';
  setTimeout(() => t.style.display = 'none', 3000);
}
</script>
</body>
</html>"""

# ── Flask routes ──────────────────────────────────────────────────────────
@app.route("/")
@require_auth
def index():
    return redirect(url_for("browse", path=""))

@app.route("/favicon.ico")
def favicon():
    return Response(status=204)

EXPLORER_HTML = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Backup Explorer</title>
<style>
:root{--bg:#f3f3f3;--content:#fff;--text:#1b1b1b;--muted:#6b6b6b;--line:#e1e1e1;--hover:#eaf3fb;--sel:#cce8ff;--selb:#99d1ff;--menu:#fff;--shadow:0 8px 24px rgba(0,0,0,.18)}
@media(prefers-color-scheme:dark){:root{--bg:#202020;--content:#191919;--text:#f1f1f1;--muted:#a0a0a0;--line:#333;--hover:#2a2d31;--sel:#0b3a5e;--selb:#1a6aa8;--menu:#2b2b2b;--shadow:0 8px 24px rgba(0,0,0,.6)}}
*{box-sizing:border-box;margin:0}
html,body{height:100%}
body{font:14px/1.35 "Segoe UI",system-ui,-apple-system,Roboto,sans-serif;background:var(--bg);color:var(--text);overflow:hidden}
#app{display:flex;flex-direction:column;height:100vh;height:100dvh}
.bar{display:flex;align-items:center;gap:4px;padding:6px 10px;background:var(--bg)}
.ib,.cb{background:none;border:0;color:inherit;border-radius:6px;cursor:pointer;font:inherit}
.ib{width:32px;height:32px;font-size:16px;flex:none}
.ib:hover,.cb:hover:not(:disabled){background:rgba(127,127,127,.18)}
.ib.on{background:rgba(127,127,127,.28)}
.cb{display:flex;align-items:center;gap:6px;padding:6px 10px;height:34px;white-space:nowrap}
.cb:disabled{opacity:.38;cursor:default}
.addr{flex:1;min-width:0;display:flex;align-items:center;height:34px;padding:0 6px;background:var(--content);border:1px solid var(--line);border-radius:6px;overflow-x:auto;white-space:nowrap;scrollbar-width:none}
.addr a{padding:3px 6px;border-radius:4px;cursor:pointer}
.addr a:hover{background:var(--hover)}
.addr .sep{color:var(--muted)}
#q{width:260px;height:34px;padding:0 10px;border:1px solid var(--line);border-radius:6px;background:var(--content);color:inherit;font:inherit}
.cmd{border-bottom:1px solid var(--line);overflow-x:auto;scrollbar-width:none}
.vsep{width:1px;height:20px;background:var(--line);margin:0 4px;flex:none}
.sp{flex:1}
.main{flex:1;display:flex;min-height:0;background:var(--content)}
#nav{width:240px;overflow:auto;padding:6px;border-right:1px solid var(--line);background:var(--content);flex:none}
.tn{display:flex;align-items:center;gap:2px;padding:4px 4px 4px calc(4px + var(--d,0) * 14px);border-radius:6px;cursor:pointer;white-space:nowrap}
.tn:hover{background:var(--hover)}
.tn.cur{background:var(--sel)}
.ar{width:18px;text-align:center;color:var(--muted);font-size:11px;flex:none}
.tl{overflow:hidden;text-overflow:ellipsis}
.tc[hidden]{display:none}
#content{flex:1;overflow:auto;padding:8px;outline:0}
#content.icons{display:flex;flex-wrap:wrap;align-content:flex-start;gap:4px}
.it{user-select:none;-webkit-user-select:none;-webkit-touch-callout:none;border:1px solid transparent;cursor:default}
.it:hover{background:var(--hover)}
.it.sel{background:var(--sel);border-color:var(--selb)}
.it.cut{opacity:.45}
.icons .it{width:112px;padding:8px 4px 6px;text-align:center;border-radius:6px}
.icons .ic{width:64px;height:64px;margin:0 auto 4px}
.icons .nm{display:-webkit-box;-webkit-line-clamp:3;-webkit-box-orient:vertical;overflow:hidden;word-break:break-word;font-size:13px}
.icons .sz,.icons .ty,.icons .hd{display:none}
.nm small{display:block;color:var(--muted);font-size:11px;overflow:hidden;text-overflow:ellipsis}
.ic svg{width:100%;height:100%;display:block}
.details .hd,.details .it{display:grid;grid-template-columns:28px 1fr 110px 120px;align-items:center;gap:8px;padding:4px 8px;border-radius:4px}
.details .hd{position:sticky;top:-8px;background:var(--content);border-bottom:1px solid var(--line);color:var(--muted);z-index:2;margin:-8px -8px 4px;padding:8px 16px;border-radius:0}
.hd div{cursor:pointer}
.details .ic{width:22px;height:22px}
.details .nm{overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.details .sz,.details .ty{color:var(--muted);font-size:13px}
.empty{color:var(--muted);padding:30px;text-align:center;width:100%}
#preview{width:340px;border-left:1px solid var(--line);padding:10px;overflow:auto;flex:none}
#preview[hidden]{display:none}
#preview video,#preview img,#preview iframe,#preview audio{width:100%;border:0;border-radius:6px}
#preview video,#preview iframe{max-height:60vh;background:#000}
#preview iframe{height:60vh;background:#fff}
.pvi{margin-top:10px;font-size:13px;color:var(--muted);word-break:break-word}
.pvi b{color:var(--text);display:block;margin-bottom:2px}
.status{display:flex;align-items:center;gap:14px;padding:3px 12px;background:var(--content);border-top:1px solid var(--line);color:var(--muted);font-size:12px;height:30px;white-space:nowrap}
.status .ib{width:26px;height:24px;font-size:14px}
#menu{position:fixed;z-index:60;min-width:210px;background:var(--menu);border:1px solid var(--line);border-radius:8px;padding:4px;box-shadow:var(--shadow)}
#menu[hidden]{display:none}
.mi{padding:7px 12px;border-radius:5px;cursor:pointer;white-space:nowrap}
.mi:hover{background:var(--hover)}
.mi.dis{opacity:.4;pointer-events:none}
.mi.b{font-weight:600}
.ms{height:1px;background:var(--line);margin:4px 2px}
dialog{margin:auto;border:1px solid var(--line);border-radius:10px;background:var(--menu);color:var(--text);padding:18px;width:min(92vw,420px);box-shadow:var(--shadow)}
dialog::backdrop{background:rgba(0,0,0,.35)}
dialog h3{font-size:16px;margin-bottom:8px}
dialog p{color:var(--muted);margin-bottom:10px;font-size:13px;word-break:break-word}
dialog input{width:100%;padding:8px 10px;border:1px solid var(--line);border-radius:6px;background:var(--content);color:inherit;font:inherit;margin-bottom:12px}
.btns{display:flex;flex-direction:row-reverse;gap:8px}
.btns button{padding:7px 16px;border-radius:6px;border:1px solid var(--line);background:var(--content);color:inherit;font:inherit;cursor:pointer}
.btns .primary{background:#0067c0;border-color:#0067c0;color:#fff}
.btns .danger{background:#c42b1c;border-color:#c42b1c;color:#fff}
#toast{position:fixed;left:50%;bottom:44px;transform:translateX(-50%);background:#323232;color:#fff;padding:9px 16px;border-radius:8px;font-size:13px;z-index:80;max-width:90vw;box-shadow:var(--shadow)}
#toast[hidden]{display:none}
#toast.err{background:#b3261e}
#navbtn{display:none}
@media(max-width:760px){
  #navbtn{display:inline-block}
  #nav{position:fixed;left:0;top:96px;bottom:30px;z-index:40;display:none;box-shadow:var(--shadow);width:270px}
  #nav.open{display:block}
  #q{width:92px;padding:0 6px}
  .lbl{display:none}
  .cb{padding:6px 9px}
  #preview{display:none!important}
  .icons .it{width:96px}
  .details .hd,.details .it{grid-template-columns:28px 1fr 80px}
  .details .ty,.details .hd div:nth-child(4){display:none}
}
</style>
</head>
<body>
<div id="app">
  <div class="bar">
    <button class="ib" id="navbtn" title="Folders">☰</button>
    <button class="ib" id="bk" title="Back">←</button>
    <button class="ib" id="fw" title="Forward">→</button>
    <button class="ib" id="up" title="Up">↑</button>
    <button class="ib" id="rf" title="Refresh">⟳</button>
    <div class="addr" id="addr"></div>
    <input id="q" type="search" placeholder="Search files…" autocomplete="off">
  </div>
  <div class="bar cmd">
    <button class="cb" id="c-cut"><span>✂</span><span class="lbl">Cut</span></button>
    <button class="cb" id="c-copy"><span>⧉</span><span class="lbl">Copy</span></button>
    <button class="cb" id="c-paste"><span>📋</span><span class="lbl">Paste</span></button>
    <button class="cb" id="c-ren"><span>✎</span><span class="lbl">Rename</span></button>
    <button class="cb" id="c-tg"><span>📤</span><span class="lbl">Telegram</span></button>
    <button class="cb" id="c-dl"><span>⬇</span><span class="lbl">Download</span></button>
    <button class="cb" id="c-del"><span>🗑</span><span class="lbl">Delete</span></button>
    <span class="vsep"></span>
    <button class="cb" id="c-sort"><span>⇅</span><span class="lbl">Sort</span></button>
    <button class="cb" id="c-view"><span>▦</span><span class="lbl">View</span></button>
    <button class="cb" id="c-more"><span>⋯</span></button>
    <span class="sp"></span>
    <button class="cb" id="c-prev"><span>◧</span><span class="lbl">Preview</span></button>
  </div>
  <div class="main">
    <aside id="nav"><div id="tree"></div></aside>
    <section id="content" class="icons" tabindex="0"></section>
    <aside id="preview" hidden></aside>
  </div>
  <div class="status">
    <span id="st-n"></span>
    <span id="st-s"></span>
    <span class="sp"></span>
    <span id="st-i"></span>
    <button class="ib" id="v-d" title="Details">☰</button>
    <button class="ib" id="v-i" title="Large icons">▦</button>
  </div>
</div>
<div id="menu" hidden></div>
<div id="toast" hidden></div>
<dialog id="dlg">
  <form method="dialog">
    <h3 id="dt"></h3>
    <p id="dx"></p>
    <input id="di" autocomplete="off">
    <div class="btns">
      <button id="dok" value="ok" class="primary">OK</button>
      <button value="cancel">Cancel</button>
    </div>
  </form>
</dialog>

<script>
(() => {
'use strict';
const $ = s => document.querySelector(s);
const $$ = s => [...document.querySelectorAll(s)];
const esc = s => String(s).replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const enc = p => p.split('/').map(encodeURIComponent).join('/');
const sleep = ms => new Promise(r => setTimeout(r, ms));
const fmt = n => { n = +n || 0; for (const u of ['B','KB','MB','GB']) { if (n < 1024) return (u === 'B' ? n : n.toFixed(1)) + ' ' + u; n /= 1024; } return n.toFixed(1) + ' TB'; };
const COARSE = matchMedia('(pointer: coarse)').matches;
const store = { get(k, d) { try { return localStorage.getItem(k) || d; } catch (e) { return d; } }, set(k, v) { try { localStorage.setItem(k, v); } catch (e) {} } };

const S = { path: '', search: '', folders: [], files: [], items: [], map: new Map(), topicId: null,
  sel: new Set(), anchor: null, view: store.get('xview', 'icons'), sort: store.get('xsort', 'name'),
  desc: store.get('xdesc', '') === '1', clip: null, preview: false, idx: '' };

/* ---------- icons ---------- */
const FOLDER = '<svg viewBox="0 0 64 64"><path d="M4 14a4 4 0 0 1 4-4h14l6 6h28a4 4 0 0 1 4 4v32a4 4 0 0 1-4 4H8a4 4 0 0 1-4-4z" fill="#f0b93a"/><path d="M4 24h56v28a4 4 0 0 1-4 4H8a4 4 0 0 1-4-4z" fill="#fcd462"/></svg>';
const EC = { pdf:'#d93025', py:'#3572a5', js:'#c9a400', ts:'#2b7489', json:'#6d6d6d', html:'#e34c26', css:'#563d7c', md:'#555', txt:'#777',
  csv:'#1d7a45', xlsx:'#1d7a45', xls:'#1d7a45', doc:'#2b579a', docx:'#2b579a', ppt:'#d24726', pptx:'#d24726',
  zip:'#8a6d3b', rar:'#8a6d3b', '7z':'#8a6d3b', apk:'#3ddc84', exe:'#0067c0', sh:'#444', bat:'#444' };
function colorFor(e) {
  if (EC[e]) return EC[e];
  if (/^(mp4|mkv|avi|mov|webm|m4v)$/.test(e)) return '#7b3fe4';
  if (/^(mp3|wav|m4a|ogg|flac|aac)$/.test(e)) return '#e8710a';
  if (/^(png|jpe?g|gif|webp|bmp|svg)$/.test(e)) return '#1a9c5b';
  return '#8a8f98';
}
function fileIcon(e) {
  const t = (e || 'file').toUpperCase().slice(0, 4);
  return '<svg viewBox="0 0 64 64"><path d="M14 4h26l14 14v38a4 4 0 0 1-4 4H14a4 4 0 0 1-4-4V8a4 4 0 0 1 4-4z" fill="#fff" stroke="#c4c4c4" stroke-width="2"/><path d="M40 4v14h14" fill="#e6e6e6" stroke="#c4c4c4" stroke-width="2"/><rect x="5" y="30" width="42" height="18" rx="3" fill="' + colorFor(e) + '"/><text x="26" y="43" font-size="' + (t.length > 3 ? 11 : 13) + '" font-weight="700" fill="#fff" text-anchor="middle" font-family="Arial,sans-serif">' + esc(t) + '</text></svg>';
}
function kind(x) {
  const m = (x.mime || '').toLowerCase(), e = x.ext;
  if (m.startsWith('video/') || /^(mp4|webm|mov|m4v|mkv)$/.test(e)) return 'video';
  if (m.startsWith('audio/') || /^(mp3|m4a|wav|ogg|flac|aac)$/.test(e)) return 'audio';
  if (m.startsWith('image/') || /^(png|jpe?g|gif|webp|bmp)$/.test(e)) return 'image';
  if (m === 'application/pdf' || e === 'pdf') return 'pdf';
  return 'text';
}

/* ---------- api / ui helpers ---------- */
async function api(url, body) {
  const o = { headers: { 'X-Requested-With': 'fetch' } };
  if (body) { o.method = 'POST'; o.headers['Content-Type'] = 'application/json'; o.body = JSON.stringify(body); }
  let r;
  try { r = await fetch(url, o); } catch (e) { return { ok: false, msg: 'network error' }; }
  if (r.status === 401) { location.href = '/login'; return { ok: false, msg: 'login required' }; }
  try { return await r.json(); } catch (e) { return { ok: false, msg: 'bad response (' + r.status + ')' }; }
}
let toastT;
function toast(msg, err) {
  const t = $('#toast'); t.textContent = msg; t.className = err ? 'err' : ''; t.hidden = false;
  clearTimeout(toastT); toastT = setTimeout(() => { t.hidden = true; }, err ? 5000 : 2600);
}
function dialog(o) {
  return new Promise(res => {
    const d = $('#dlg'), inp = $('#di'), ok = $('#dok');
    $('#dt').textContent = o.title; $('#dx').textContent = o.text || '';
    inp.style.display = o.input != null ? '' : 'none'; inp.value = o.input || '';
    ok.textContent = o.ok || 'OK'; ok.className = o.danger ? 'danger' : 'primary';
    d.returnValue = '';
    d.onclose = () => res(d.returnValue === 'ok' ? (o.input != null ? inp.value.trim() : true) : null);
    d.showModal();
    if (o.input != null) { inp.focus(); const dot = o.input.lastIndexOf('.'); inp.setSelectionRange(0, dot > 0 ? dot : o.input.length); }
  });
}

/* ---------- data ---------- */
function tag() {
  S.folders.forEach(f => { f.key = 'd:' + f.path; f.type = 'dir'; });
  S.files.forEach(f => { f.key = 'f:' + f.id; f.type = 'file'; });
}
const cmpName = (a, b) => a.name.localeCompare(b.name, undefined, { numeric: true, sensitivity: 'base' });
function buildItems() {
  const items = [...S.folders, ...S.files];
  items.sort((a, b) => {
    if (a.type !== b.type) return a.type === 'dir' ? -1 : 1;
    let r;
    if (S.sort === 'size') r = (a.size || 0) - (b.size || 0) || cmpName(a, b);
    else if (S.sort === 'type') r = (a.ext || '').localeCompare(b.ext || '') || cmpName(a, b);
    else r = cmpName(a, b);
    return S.desc ? -r : r;
  });
  return items;
}
const selItems = () => [...S.sel].map(k => S.map.get(k)).filter(Boolean);
const selFiles = () => selItems().filter(i => i.type === 'file');

async function go(path, push = true) {
  const r = await api('/api/list?path=' + encodeURIComponent(path));
  if (r.ok === false) { toast('❌ ' + r.msg, true); return; }
  S.search = ''; $('#q').value = '';
  S.path = r.path; S.topicId = r.topic_id; S.folders = r.folders; S.files = r.files; tag();
  S.sel.clear(); S.anchor = null;
  if (push) history.pushState({ p: S.path }, '', '/browse/' + enc(S.path));
  document.title = (S.path.split('/').pop() || 'Backup') + ' — Backup Explorer';
  render(); drawAddr(); revealTree(S.path); closeNav();
}
async function runSearch(q) {
  const r = await api('/api/search?q=' + encodeURIComponent(q));
  if (r.ok === false) { toast('❌ ' + r.msg, true); return; }
  S.search = q; S.folders = []; S.files = r.files; S.topicId = null; tag(); S.sel.clear(); S.anchor = null;
  render(); drawAddr();
}
const refresh = () => S.search ? runSearch(S.search) : go(S.path, false);
const parentOf = p => p.includes('/') ? p.slice(0, p.lastIndexOf('/')) : '';

/* ---------- render ---------- */
function itemHtml(i) {
  const ic = i.type === 'dir' ? FOLDER : fileIcon(i.ext);
  const cut = S.clip && S.clip.mode === 'cut' && i.type === 'file' && S.clip.set.has(i.id) ? ' cut' : '';
  const sub = i.type === 'file' && S.search ? '<small>' + esc(i.folder) + '</small>' : '';
  const size = i.type === 'dir' ? '' : fmt(i.size);
  const typ = i.type === 'dir' ? 'File folder' : (i.ext ? i.ext.toUpperCase() + ' file' : 'File');
  return '<div class="it' + (S.sel.has(i.key) ? ' sel' : '') + cut + '" data-key="' + esc(i.key) + '" title="' + esc(i.name) + '">' +
    '<div class="ic">' + ic + '</div><div class="nm">' + esc(i.name) + sub + '</div><div class="sz">' + size + '</div><div class="ty">' + typ + '</div></div>';
}
function render() {
  S.items = buildItems(); S.map = new Map(S.items.map(i => [i.key, i]));
  for (const k of [...S.sel]) if (!S.map.has(k)) S.sel.delete(k);
  const c = $('#content'); c.className = S.view;
  let h = '';
  if (S.view === 'details') {
    const ar = k => S.sort === k ? (S.desc ? ' ▾' : ' ▴') : '';
    h += '<div class="hd"><div></div><div data-sort="name">Name' + ar('name') + '</div><div data-sort="size">Size' + ar('size') + '</div><div data-sort="type">Type' + ar('type') + '</div></div>';
  }
  for (const i of S.items) h += itemHtml(i);
  if (!S.items.length) h += '<div class="empty">' + (S.search ? 'No files found.' : 'This folder is empty.') + '</div>';
  c.innerHTML = h;
  $('#v-d').classList.toggle('on', S.view === 'details'); $('#v-i').classList.toggle('on', S.view === 'icons');
  $('#c-prev').classList.toggle('on', S.preview);
  updateBar();
}
function paintSel() { $$('#content .it').forEach(el => el.classList.toggle('sel', S.sel.has(el.dataset.key))); }
function updateBar() {
  const f = selFiles(), n = S.sel.size;
  $('#c-cut').disabled = $('#c-copy').disabled = $('#c-del').disabled = $('#c-tg').disabled = $('#c-dl').disabled = !f.length;
  $('#c-ren').disabled = !(n === 1 && f.length === 1);
  $('#c-paste').disabled = !canPaste();
  $('#st-n').textContent = S.items.length + ' item' + (S.items.length === 1 ? '' : 's');
  $('#st-s').textContent = n ? n + ' selected' + (f.length ? '  ·  ' + fmt(f.reduce((a, x) => a + (x.size || 0), 0)) : '') : '';
  drawPreview();
}
function drawAddr() {
  const a = $('#addr'); let h = '<a data-p="">🏠 Backup</a>';
  if (S.search) h += '<span class="sep">›</span><span style="padding:3px 6px">Search: ' + esc(S.search) + '</span>';
  else { let acc = ''; for (const p of (S.path ? S.path.split('/') : [])) { acc = acc ? acc + '/' + p : p; h += '<span class="sep">›</span><a data-p="' + esc(acc) + '">' + esc(p) + '</a>'; } }
  a.innerHTML = h; a.scrollLeft = a.scrollWidth;
}
function drawIdx() {
  const s = S.idx;
  $('#st-i').textContent = !s || s === 'idle' ? '' : s === 'done' ? '✓ indexed' : '⟳ ' + s;
}

/* ---------- selection / open ---------- */
function select(key, e) {
  const keys = S.items.map(i => i.key);
  if (e.shiftKey && S.anchor && S.map.has(S.anchor)) {
    const a = keys.indexOf(S.anchor), b = keys.indexOf(key), lo = Math.min(a, b), hi = Math.max(a, b);
    S.sel = new Set(keys.slice(lo, hi + 1));
  } else if (e.ctrlKey || e.metaKey) { S.sel.has(key) ? S.sel.delete(key) : S.sel.add(key); S.anchor = key; }
  else { S.sel = new Set([key]); S.anchor = key; }
  paintSel(); updateBar();
}
const clearSel = () => { S.sel.clear(); S.anchor = null; paintSel(); updateBar(); };
function openItem(i) {
  if (!i) return;
  if (i.type === 'dir') return go(i.path);
  if (i.view) window.open('/stream/' + i.id + '/' + encodeURIComponent(i.name), '_blank', 'noopener');
  else downloadFiles([i]);
}

/* ---------- actions ---------- */
function downloadFiles(files) {
  files.forEach((f, n) => setTimeout(() => {
    const a = document.createElement('a');
    a.href = '/download/' + f.id + '/' + encodeURIComponent(f.name); a.download = f.name;
    document.body.appendChild(a); a.click(); a.remove();
  }, n * 700));
}
async function sendTG(files) {
  let ok = 0;
  for (const f of files) {
    const r = await api('/sendtobot/' + f.id);
    if (r.ok) ok++; else toast('❌ ' + (r.msg || 'failed'), true);
    await sleep(350);
  }
  if (ok) toast('✅ ' + ok + ' file(s) Telegram वर पाठवल्या');
}
async function delFiles(files) {
  if (!files.length) return;
  const yes = await dialog({ title: 'Delete ' + files.length + ' file' + (files.length > 1 ? 's' : '') + '?',
    text: 'Telegram group मधून कायमच्या delete होतील. हे परत करता येणार नाही.', ok: 'Delete', danger: true });
  if (!yes) return;
  const ids = files.map(f => f.id); let gone = 0;
  for (let i = 0; i < ids.length; i += 50) {
    const b = ids.slice(i, i + 50), r = await api('/api/delete', { ids: b });
    if (!r.ok) { toast('❌ ' + r.msg, true); break; }
    const dead = new Set(r.deleted || b); gone += dead.size;
    S.files = S.files.filter(f => !dead.has(f.id));
  }
  S.sel.clear(); render();
  if (gone) toast('🗑 ' + gone + ' deleted');
}
async function renameSel() {
  const f = selFiles(); if (S.sel.size !== 1 || f.length !== 1) return;
  const nm = await dialog({ title: 'Rename', text: 'फक्त या browser मधलं नाव बदलतं. Telegram मधलं मूळ नाव तसंच राहतं.', input: f[0].name, ok: 'Rename' });
  if (!nm || nm === f[0].name) return;
  const r = await api('/api/rename', { id: f[0].id, name: nm });
  if (r.ok) { f[0].name = r.name; f[0].ext = r.ext; render(); toast('✎ Renamed'); } else toast('❌ ' + r.msg, true);
}
function setClip(mode) {
  const f = selFiles(); if (!f.length) return;
  S.clip = { mode, ids: f.map(x => x.id), set: new Set(f.map(x => x.id)) };
  render(); toast((mode === 'cut' ? '✂ Cut: ' : '⧉ Copied: ') + f.length + ' — folder उघडून Paste कर');
}
const canPaste = () => !!(S.clip && S.topicId != null && !S.search);
async function paste() {
  if (!S.clip) return;
  if (!canPaste()) { toast('इथे paste करता येत नाही — हा topic folder नाही', true); return; }
  const { mode, ids } = S.clip; let done = 0;
  toast((mode === 'cut' ? 'Moving ' : 'Copying ') + ids.length + ' file(s)…');
  for (let i = 0; i < ids.length; i += 15) {
    const r = await api('/api/paste', { ids: ids.slice(i, i + 15), topic_id: S.topicId, mode: mode === 'cut' ? 'move' : 'copy' });
    if (!r.results) { toast('❌ ' + r.msg, true); break; }
    r.results.forEach(x => { if (x.ok) done++; else toast('❌ ' + x.msg, true); });
  }
  if (mode === 'cut') S.clip = null;
  await refresh(); toast('✅ ' + done + ' file(s) ' + (mode === 'cut' ? 'moved' : 'copied'));
}
function setView(v) { S.view = v; store.set('xview', v); render(); }
function sortBy(k) { if (S.sort === k) S.desc = !S.desc; else { S.sort = k; S.desc = false; } store.set('xsort', S.sort); store.set('xdesc', S.desc ? '1' : ''); render(); }
async function reindex() {
  const yes = await dialog({ title: 'Re-index everything?', text: 'Index साफ होऊन Telegram group पुन्हा पूर्ण scan होईल (वेळ लागतो). Rename केलेली नावं जातील.', ok: 'Re-index', danger: true });
  if (!yes) return;
  const r = await api('/api/reindex', {});
  toast(r.ok ? '⟳ Re-index सुरू' : '❌ ' + r.msg, !r.ok); if (r.ok) { S.idx = 'scanning'; drawIdx(); }
}

/* ---------- menus ---------- */
const menu = $('#menu');
function hideMenu() { menu.hidden = true; }
function drawMenu(x, y, items) {
  menu.innerHTML = items.map((m, n) => m === '-' ? '<div class="ms"></div>' : '<div class="mi' + (m[2] ? ' dis' : '') + (m[3] ? ' b' : '') + '" data-n="' + n + '">' + esc(m[0]) + '</div>').join('');
  menu.hidden = false;
  const w = menu.offsetWidth, h = menu.offsetHeight;
  menu.style.left = Math.max(4, Math.min(x, innerWidth - w - 4)) + 'px';
  menu.style.top = Math.max(4, Math.min(y, innerHeight - h - 4)) + 'px';
  menu.onclick = e => { const el = e.target.closest('.mi'); if (!el) return; const it = items[+el.dataset.n]; hideMenu(); it[1](); };
}
function showMenu(x, y, kind) {
  const f = selFiles(), it = selItems()[0], one = S.sel.size === 1; let m = [];
  if (kind === 'item') {
    if (one && it.type === 'dir') m = [['Open', () => go(it.path), 0, 1]];
    else if (f.length) {
      if (one) m.push([f[0].view ? 'Open' : 'Download', () => openItem(f[0]), 0, 1]);
      if (one && S.search) m.push(['📂 Open file location', () => go(f[0].folder)]);
      m.push(['⬇ Download', () => downloadFiles(f)], ['📤 Send to Telegram', () => sendTG(f)], '-',
        ['✂ Cut', () => setClip('cut')], ['⧉ Copy', () => setClip('copy')], '-');
      if (one) m.push(['✎ Rename', renameSel]);
      m.push(['🗑 Delete', () => delFiles(f)]);
    }
  } else {
    m = [['📋 Paste', paste, !canPaste()], ['⟳ Refresh', refresh], '-', ['▦ Large icons', () => setView('icons')], ['☰ Details', () => setView('details')]];
  }
  if (m.length) drawMenu(x, y, m);
}
function btnMenu(btn, items) { const r = btn.getBoundingClientRect(); drawMenu(r.left, r.bottom + 4, items); }

/* ---------- tree ---------- */
const treeCache = new Map();
async function kids(path) {
  if (treeCache.has(path)) return treeCache.get(path);
  const r = await api('/api/list?dirs=1&path=' + encodeURIComponent(path));
  const k = r.folders || []; treeCache.set(path, k); return k;
}
const nodeHtml = (f, d) => '<div class="tn" data-path="' + esc(f.path) + '" style="--d:' + d + '"><span class="ar" data-ar>▸</span><span class="tl">📁 ' + esc(f.name) + '</span></div><div class="tc" hidden></div>';
async function openNode(tn) {
  const box = tn.nextElementSibling;
  if (!box.dataset.loaded) {
    const k = await kids(tn.dataset.path), d = (+tn.style.getPropertyValue('--d') || 0) + 1;
    box.innerHTML = k.map(x => nodeHtml(x, d)).join(''); box.dataset.loaded = '1';
    if (!k.length) tn.querySelector('.ar').textContent = '';
  }
  if (box.innerHTML) { box.hidden = false; tn.querySelector('.ar').textContent = '▾'; }
}
async function toggleNode(tn) {
  const box = tn.nextElementSibling;
  if (box.dataset.loaded && !box.hidden) { box.hidden = true; tn.querySelector('.ar').textContent = '▸'; } else await openNode(tn);
}
async function revealTree(path) {
  $$('#tree .tn.cur').forEach(n => n.classList.remove('cur'));
  let cur = $('#tree .tn'); let acc = '';
  if (!cur) return;
  for (const p of (path ? path.split('/') : [])) {
    acc = acc ? acc + '/' + p : p;
    await openNode(cur);
    const next = [...cur.nextElementSibling.children].find(n => n.classList.contains('tn') && n.dataset.path === acc);
    if (!next) break; cur = next;
  }
  if (S.path === path) { $$('#tree .tn.cur').forEach(n => n.classList.remove('cur')); cur.classList.add('cur'); cur.scrollIntoView({ block: 'nearest' }); }
}
async function initTree() {
  $('#tree').innerHTML = '<div class="tn" data-path="" style="--d:0"><span class="ar">▾</span><span class="tl">🏠 Backup</span></div><div class="tc"></div>';
  const root = $('#tree .tn'); await openNode(root);
}
const closeNav = () => $('#nav').classList.remove('open');

/* ---------- preview ---------- */
function drawPreview() {
  const p = $('#preview'); p.hidden = !S.preview;
  p.querySelectorAll('video,audio').forEach(v => { v.pause(); v.removeAttribute('src'); v.load(); });
  if (!S.preview) { p.innerHTML = ''; return; }
  const f = selFiles();
  if (S.sel.size !== 1 || f.length !== 1) { p.innerHTML = '<div class="empty">Select a file to preview</div>'; return; }
  const x = f[0], url = '/stream/' + x.id + '/' + encodeURIComponent(x.name), k = kind(x);
  let body = '';
  if (!x.view) body = '<div class="empty">Preview उपलब्ध नाही — Download वापर</div>';
  else if (k === 'video') body = '<video controls preload="metadata" src="' + url + '"></video>';
  else if (k === 'audio') body = '<audio controls preload="metadata" src="' + url + '"></audio>';
  else if (k === 'image') body = '<img alt="" src="' + url + '">';
  else body = '<iframe src="' + url + '"></iframe>';
  p.innerHTML = body + '<div class="pvi"><b>' + esc(x.name) + '</b>' + fmt(x.size) + (x.ext ? ' · ' + esc(x.ext.toUpperCase()) : '') + '</div>';
}

/* ---------- events ---------- */
let swallow = false, lp = null;
const content = $('#content');
content.addEventListener('click', e => {
  const sh = e.target.closest('[data-sort]'); if (sh) { sortBy(sh.dataset.sort); return; }
  const el = e.target.closest('.it');
  if (swallow) { swallow = false; return; }
  if (!el) { if (!(e.ctrlKey || e.metaKey)) clearSel(); return; }
  if (COARSE && !e.ctrlKey && !e.shiftKey) { openItem(S.map.get(el.dataset.key)); return; }
  select(el.dataset.key, e);
});
content.addEventListener('dblclick', e => { const el = e.target.closest('.it'); if (el && !COARSE) openItem(S.map.get(el.dataset.key)); });
content.addEventListener('contextmenu', e => {
  e.preventDefault();
  const el = e.target.closest('.it');
  if (el) { const k = el.dataset.key; if (!S.sel.has(k)) { S.sel = new Set([k]); S.anchor = k; paintSel(); updateBar(); } } else clearSel();
  showMenu(e.clientX, e.clientY, el ? 'item' : 'blank');
});
content.addEventListener('touchstart', e => {
  const t = e.touches[0], el = e.target.closest('.it');
  lp = setTimeout(() => {
    lp = null; swallow = true; setTimeout(() => { swallow = false; }, 900);
    if (el) { const k = el.dataset.key; if (!S.sel.has(k)) { S.sel = new Set([k]); S.anchor = k; paintSel(); updateBar(); } }
    showMenu(t.clientX, t.clientY, el ? 'item' : 'blank');
  }, 550);
}, { passive: true });
['touchend', 'touchmove', 'touchcancel'].forEach(ev => content.addEventListener(ev, () => { if (lp) { clearTimeout(lp); lp = null; } }, { passive: true }));

document.addEventListener('click', e => { if (!e.target.closest('#menu')) hideMenu(); });
document.addEventListener('scroll', hideMenu, true);
window.addEventListener('resize', hideMenu);
$('#addr').addEventListener('click', e => { const a = e.target.closest('a[data-p]'); if (a) go(a.dataset.p); });
$('#tree').addEventListener('click', e => {
  const tn = e.target.closest('.tn'); if (!tn) return;
  if (e.target.closest('.ar')) toggleNode(tn); else { go(tn.dataset.path); openNode(tn); }
});
$('#navbtn').onclick = e => { e.stopPropagation(); $('#nav').classList.toggle('open'); };
$('#bk').onclick = () => history.back();
$('#fw').onclick = () => history.forward();
$('#up').onclick = () => { if (S.search) go(S.path, false); else if (S.path) go(parentOf(S.path)); };
$('#rf').onclick = refresh;
$('#c-cut').onclick = () => setClip('cut');
$('#c-copy').onclick = () => setClip('copy');
$('#c-paste').onclick = paste;
$('#c-ren').onclick = renameSel;
$('#c-tg').onclick = () => sendTG(selFiles());
$('#c-dl').onclick = () => downloadFiles(selFiles());
$('#c-del').onclick = () => delFiles(selFiles());
$('#c-prev').onclick = () => { S.preview = !S.preview; $('#c-prev').classList.toggle('on', S.preview); drawPreview(); };
$('#v-d').onclick = () => setView('details');
$('#v-i').onclick = () => setView('icons');
$('#c-view').onclick = e => { e.stopPropagation(); btnMenu(e.currentTarget, [[(S.view === 'icons' ? '✓ ' : '') + 'Large icons', () => setView('icons')], [(S.view === 'details' ? '✓ ' : '') + 'Details', () => setView('details')]]); };
$('#c-sort').onclick = e => {
  e.stopPropagation();
  const c = (k, l) => [(S.sort === k ? '✓ ' : '') + l, () => { S.sort = k; store.set('xsort', k); render(); }];
  btnMenu(e.currentTarget, [c('name', 'Name'), c('size', 'Size'), c('type', 'Type'), '-',
    [(!S.desc ? '✓ ' : '') + 'Ascending', () => { S.desc = false; store.set('xdesc', ''); render(); }],
    [(S.desc ? '✓ ' : '') + 'Descending', () => { S.desc = true; store.set('xdesc', '1'); render(); }]]);
};
$('#c-more').onclick = e => {
  e.stopPropagation();
  btnMenu(e.currentTarget, [['Select all', () => { S.sel = new Set(S.items.map(i => i.key)); paintSel(); updateBar(); }], ['Re-index', reindex], '-', ['Logout', () => { location.href = '/logout'; }]]);
};
$('#q').addEventListener('keydown', e => {
  if (e.key === 'Enter') { const q = e.target.value.trim(); if (q) runSearch(q); else go(S.path, false); }
  else if (e.key === 'Escape') { e.target.value = ''; if (S.search) go(S.path, false); }
});
window.addEventListener('popstate', e => go(e.state && e.state.p != null ? e.state.p : pathFromUrl(), false));
document.addEventListener('keydown', e => {
  if (e.target.closest('input,textarea,dialog')) return;
  const mod = e.ctrlKey || e.metaKey, k = e.key.toLowerCase();
  if (k === 'delete') delFiles(selFiles());
  else if (k === 'f2') renameSel();
  else if (k === 'enter') { const i = selItems(); if (i.length === 1) openItem(i[0]); }
  else if (k === 'escape') { hideMenu(); clearSel(); }
  else if (k === 'backspace' || (e.altKey && k === 'arrowup')) { e.preventDefault(); if (S.path && !S.search) go(parentOf(S.path)); }
  else if (mod && k === 'a') { e.preventDefault(); S.sel = new Set(S.items.map(i => i.key)); paintSel(); updateBar(); }
  else if (mod && k === 'c') { if (selFiles().length) { e.preventDefault(); setClip('copy'); } }
  else if (mod && k === 'x') { if (selFiles().length) { e.preventDefault(); setClip('cut'); } }
  else if (mod && k === 'v') { e.preventDefault(); paste(); }
});

/* ---------- index status ---------- */
async function pollStatus() {
  const r = await api('/api/status');
  if (r && r.index) {
    const was = S.idx; S.idx = r.index.status; drawIdx();
    if (was && was !== S.idx && S.idx === 'done' && !S.search) refresh();
  }
  setTimeout(pollStatus, S.idx === 'done' ? 30000 : 4000);
}

/* ---------- init ---------- */
function pathFromUrl() {
  const m = location.pathname.match(/^\/browse\/(.*)$/);
  try { return m ? decodeURIComponent(m[1]).replace(/^\/+|\/+$/g, '') : ''; } catch (e) { return ''; }
}
(async function init() {
  const p = pathFromUrl();
  history.replaceState({ p }, '');
  await initTree();
  await go(p, false);
  pollStatus();
})();
})();
</script>
</body>
</html>
"""

@app.route("/browse/", defaults={"path": ""})
@app.route("/browse/<path:path>")
@require_auth
def browse(path):
    return Response(EXPLORER_HTML, mimetype="text/html")

@app.route("/search")
@require_auth
def search():
    return redirect("/browse/")

def _file_json(r):
    name = r["filename"]
    ext = os.path.splitext(name)[1].lstrip(".").lower()
    return {
        "id": r["msg_id"], "name": name, "ext": ext,
        "size": r["size"] or 0, "mime": r["mime_type"] or "",
        "folder": r["folder_path"],
        "view": _view_mime(name, r["mime_type"] or "") is not None,
    }

@app.route("/api/list")
@require_api
def api_list():
    path = (request.args.get("path", "") or "").strip("/")
    dirs_only = request.args.get("dirs") == "1"
    prefix = f"{path}/" if path else ""
    children = {}
    for t in db_many("SELECT path, msg_count FROM topics"):
        p = t["path"]
        if not p.startswith(prefix) or p == path:
            continue
        child = p[len(prefix):].split("/")[0]
        if child:
            children[child] = children.get(child, 0) + (t["msg_count"] or 0)
    folders = [{"name": n, "path": prefix + n, "count": c}
               for n, c in sorted(children.items(), key=lambda x: x[0].lower())]
    if dirs_only:
        return jsonify({"ok": True, "folders": folders})
    topic = db_one("SELECT topic_id FROM topics WHERE path=?", (path,)) if path else None
    files = []
    if path:
        files = [_file_json(r) for r in db_many(
            "SELECT * FROM files WHERE folder_path=? ORDER BY filename COLLATE NOCASE", (path,))]
    return jsonify({"ok": True, "path": path,
                    "topic_id": topic["topic_id"] if topic else None,
                    "folders": folders, "files": files})

@app.route("/api/search")
@require_api
def api_search():
    q = (request.args.get("q", "") or "").strip()
    if not q:
        return jsonify({"ok": True, "files": []})
    like = "%" + q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
    rows = db_many(
        "SELECT * FROM files WHERE filename LIKE ? ESCAPE '\\' "
        "ORDER BY filename COLLATE NOCASE LIMIT 300", (like,))
    return jsonify({"ok": True, "files": [_file_json(r) for r in rows]})

def _ids(data, limit=100):
    ids = data.get("ids") or []
    if not isinstance(ids, list) or not ids or len(ids) > limit:
        return None
    try:
        return [int(i) for i in ids]
    except Exception:
        return None

def _recount(topic_ids):
    for tid in set(topic_ids):
        db_exec("UPDATE topics SET msg_count=(SELECT COUNT(*) FROM files WHERE topic_id=?) "
                "WHERE topic_id=?", (tid, tid))

@app.route("/api/delete", methods=["POST"])
@require_api
def api_delete():
    global _snapshot_dirty
    ids = _ids(request.get_json(silent=True) or {})
    if not ids:
        return jsonify({"ok": False, "msg": "no files selected"}), 400
    rows = [r for r in (db_one("SELECT msg_id, topic_id FROM files WHERE msg_id=?", (i,)) for i in ids) if r]
    if not rows:
        return jsonify({"ok": False, "msg": "files not found in index"}), 404

    async def _del():
        return await _tg_client.delete_messages(BACKUP_GROUP_ID, [r["msg_id"] for r in rows])
    try:
        tg(_del(), timeout=60)
    except Exception as e:
        return jsonify({"ok": False, "msg": f"Telegram delete failed: {e} "
                        "(bot ला group मध्ये 'Delete messages' admin right हवा)"}), 500
    for r in rows:
        db_exec("DELETE FROM files WHERE msg_id=?", (r["msg_id"],))
    _recount([r["topic_id"] for r in rows])
    _snapshot_dirty = True
    return jsonify({"ok": True, "deleted": [r["msg_id"] for r in rows]})

@app.route("/api/rename", methods=["POST"])
@require_api
def api_rename():
    global _snapshot_dirty
    d = request.get_json(silent=True) or {}
    try:
        mid = int(d.get("id"))
    except Exception:
        return jsonify({"ok": False, "msg": "bad id"}), 400
    name = str(d.get("name", "")).strip()
    if not name or len(name) > 255 or any(c in name for c in '/\\\0'):
        return jsonify({"ok": False, "msg": "invalid name"}), 400
    if not db_one("SELECT 1 FROM files WHERE msg_id=?", (mid,)):
        return jsonify({"ok": False, "msg": "file not found"}), 404
    db_exec("UPDATE files SET filename=? WHERE msg_id=?", (name, mid))
    _snapshot_dirty = True
    return jsonify({"ok": True, "name": name,
                    "ext": os.path.splitext(name)[1].lstrip(".").lower()})

@app.route("/api/paste", methods=["POST"])
@require_api
def api_paste():
    """Copy / move files into another topic folder (server-side copy on Telegram,
    no download/upload through this server)."""
    global _snapshot_dirty
    d = request.get_json(silent=True) or {}
    ids = _ids(d, 20)
    mode = d.get("mode")
    try:
        tid = int(d.get("topic_id"))
    except Exception:
        return jsonify({"ok": False, "msg": "bad target folder"}), 400
    if not ids or mode not in ("copy", "move"):
        return jsonify({"ok": False, "msg": "bad request"}), 400
    target = db_one("SELECT topic_id, path FROM topics WHERE topic_id=?", (tid,))
    if not target:
        return jsonify({"ok": False, "msg": "target folder not found"}), 404

    async def _copy(mid):
        m = await _tg_client.copy_message(chat_id=BACKUP_GROUP_ID, from_chat_id=BACKUP_GROUP_ID,
                                          message_id=mid, message_thread_id=tid)
        return m.id

    async def _rm(mid):
        await _tg_client.delete_messages(BACKUP_GROUP_ID, mid)

    results, touched = [], [tid]
    for mid in ids:
        row = db_one("SELECT * FROM files WHERE msg_id=?", (mid,))
        if not row:
            results.append({"id": mid, "ok": False, "msg": "file not in index"}); continue
        if mode == "move" and row["topic_id"] == tid:
            results.append({"id": mid, "ok": True, "new_id": mid}); continue
        try:
            new_id = tg(_copy(mid), timeout=60)
        except Exception as e:
            results.append({"id": mid, "ok": False, "msg": f"{row['filename']}: {e}"}); continue
        db_exec("""INSERT OR IGNORE INTO files (msg_id, topic_id, folder_path, filename, size, mime_type)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (new_id, tid, target["path"], row["filename"], row["size"], row["mime_type"]))
        if mode == "move":
            try:
                tg(_rm(mid), timeout=30)
                db_exec("DELETE FROM files WHERE msg_id=?", (mid,))
                touched.append(row["topic_id"])
            except Exception as e:
                results.append({"id": mid, "ok": False,
                                "msg": f"copied, but original not deleted: {e}"}); continue
        results.append({"id": mid, "ok": True, "new_id": new_id})
        time.sleep(0.4)
    _recount(touched)
    _snapshot_dirty = True
    return jsonify({"ok": True, "results": results})

@app.route("/api/reindex", methods=["POST"])
@require_api
def api_reindex():
    db_exec("DELETE FROM files")
    db_exec("DELETE FROM topics")
    meta_set("scan_cursor", 0)
    _topics.clear()
    _index_status["status"] = "idle"
    threading.Thread(
        target=lambda: asyncio.run_coroutine_threadsafe(_full_index(), _tg_loop).result(timeout=7200),
        daemon=True).start()
    return jsonify({"ok": True})

@app.route("/download/<int:msg_id>/<path:filename>")
@require_auth
def download(msg_id, filename):
    """Download a file by streaming it from Telegram through a temp file."""
    tmp_path = os.path.join(TMP_DIR, f"{msg_id}_{secrets.token_hex(4)}")

    async def _dl():
        msg = await _tg_client.get_messages(BACKUP_GROUP_ID, msg_id)
        await _tg_client.download_media(msg, file_name=tmp_path)

    try:
        tg(_dl(), timeout=180)
    except Exception as e:
        return jsonify({"error": str(e)}), 500

    file_row = db_one("SELECT size FROM files WHERE msg_id=?", (msg_id,))
    size = file_row["size"] if file_row else None

    def stream_and_clean():
        try:
            with open(tmp_path, "rb") as f:
                while True:
                    chunk = f.read(65536)
                    if not chunk:
                        break
                    yield chunk
        finally:
            try:
                os.unlink(tmp_path)
            except Exception:
                pass

    safe_name = os.path.basename(filename)
    headers = {
        "Content-Disposition": f'attachment; filename="{safe_name}"',
        "Content-Type": "application/octet-stream",
    }
    if size:
        headers["Content-Length"] = str(size)

    return Response(stream_with_context(stream_and_clean()), headers=headers)

# ── Inline viewing (stream from Telegram, no full download) ──────────────
STREAM_CHUNK = 1024 * 1024      # pyrogram stream_media chunk size (1 MiB)

_TEXT_EXT = {".txt", ".md", ".json", ".py", ".js", ".ts", ".jsx", ".tsx", ".css",
             ".csv", ".log", ".yml", ".yaml", ".xml", ".sh", ".conf", ".ini",
             ".toml", ".env", ".go", ".java", ".c", ".cpp", ".h", ".sql",
             ".html", ".htm", ".rs", ".php", ".rb", ".gitignore"}

def _view_mime(filename, mime):
    """Content-Type to serve inline, or None if the file should be download-only."""
    name = (filename or "").lower()
    ext = os.path.splitext(name)[1]
    mime = (mime or "").lower()
    if ext == ".svg" or mime == "image/svg+xml":
        return None                     # SVG can carry scripts
    if ext in _TEXT_EXT or name in ("dockerfile", "makefile") or mime.startswith("text/"):
        return "text/plain; charset=utf-8"   # html etc. shown as plain text, never executed
    if mime.startswith(("video/", "audio/", "image/")) or mime == "application/pdf":
        return mime
    guessed = (mimetypes.guess_type(name)[0] or "").lower()
    if guessed.startswith(("video/", "audio/", "image/")) and guessed != "image/svg+xml":
        return guessed
    if ext == ".pdf":
        return "application/pdf"
    return None

def can_view(fi):
    return _view_mime(fi["filename"], fi["mime_type"] or "") is not None

def _parse_range(header, size):
    """Returns (start, end), None (no Range header) or 'bad'."""
    if not header or not header.startswith("bytes="):
        return None
    try:
        a, b = header[6:].split(",")[0].strip().split("-", 1)
        if a == "":
            n = int(b)
            start, end = max(0, size - n), size - 1
        else:
            start = int(a)
            end = int(b) if b else size - 1
        end = min(end, size - 1)
        if start > end or start >= size:
            return "bad"
        return start, end
    except Exception:
        return "bad"

# NOTE: every pyrogram call must be created INSIDE a coroutine that runs on the
# Telegram loop (tg(...)). Calling _tg_client.xxx() directly from a Flask thread
# makes pyrogram use another loop -> "attached to a different loop".
async def _stream_open(msg_id, first_chunk, nchunks):
    msg = await _tg_client.get_messages(BACKUP_GROUP_ID, msg_id)
    return _tg_client.stream_media(msg, limit=nchunks, offset=first_chunk)

async def _stream_next(agen):
    try:
        return await agen.__anext__()
    except StopAsyncIteration:
        return None

async def _stream_close(agen):
    try:
        await agen.aclose()
    except Exception:
        pass

@app.route("/stream/<int:msg_id>/<path:filename>")
@require_auth
def stream(msg_id, filename):
    """Open a file in the browser (video/audio/image/pdf/text) with Range support."""
    row = db_one("SELECT size, mime_type, filename FROM files WHERE msg_id=?", (msg_id,))
    if not row or not row["size"]:
        return jsonify({"error": "file not found in index"}), 404
    ctype = _view_mime(row["filename"], row["mime_type"] or "")
    if not ctype:
        return jsonify({"error": "preview not supported for this file — use Download"}), 415

    size = row["size"]
    rng = _parse_range(request.headers.get("Range"), size)
    if rng == "bad":
        return Response(status=416, headers={"Content-Range": f"bytes */{size}"})
    start, end = rng if rng else (0, size - 1)
    length = end - start + 1
    first_chunk = start // STREAM_CHUNK
    skip = start % STREAM_CHUNK
    nchunks = (skip + length + STREAM_CHUNK - 1) // STREAM_CHUNK

    def generate():
        agen = None
        try:
            agen = tg(_stream_open(msg_id, first_chunk, nchunks), timeout=30)
            sent = 0
            first = True
            while sent < length:
                chunk = tg(_stream_next(agen), timeout=60)
                if chunk is None:
                    break
                if first:
                    chunk = chunk[skip:]
                    first = False
                if len(chunk) > length - sent:
                    chunk = chunk[:length - sent]
                sent += len(chunk)
                yield chunk
        finally:
            if agen is not None:
                try:
                    tg(_stream_close(agen), timeout=10)
                except Exception:
                    pass

    headers = {
        "Content-Type": ctype,
        "Content-Length": str(length),
        "Accept-Ranges": "bytes",
        "Content-Disposition": "inline",
        "X-Content-Type-Options": "nosniff",
        "Cache-Control": "private, max-age=3600",
    }
    if rng:
        headers["Content-Range"] = f"bytes {start}-{end}/{size}"
    return Response(stream_with_context(generate()),
                    status=206 if rng else 200, headers=headers)

@app.route("/sendtobot/<int:msg_id>")
@require_auth
def sendtobot(msg_id):
    async def _fwd():
        return await _tg_client.forward_messages(
            chat_id=OWNER_CHAT_ID,
            from_chat_id=BACKUP_GROUP_ID,
            message_ids=msg_id,
        )
    try:
        tg(_fwd(), timeout=30)
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"ok": False, "msg": str(e)}), 500

@app.route("/reindex")
@require_auth
def reindex():
    db_exec("DELETE FROM files")
    db_exec("DELETE FROM topics")
    meta_set("scan_cursor", 0)
    _topics.clear()
    _index_status["status"] = "idle"
    threading.Thread(
        target=lambda: asyncio.run_coroutine_threadsafe(
            _full_index(), _tg_loop
        ).result(timeout=3600),
        daemon=True
    ).start()
    return redirect(request.referrer or "/")

# ── Login routes ───────────────────────────────────────────────────────────
@app.route("/login", methods=["GET", "POST"])
def login_page():
    if session.get("authenticated"):
        return redirect("/")
    ip = get_ip()
    if is_blocked(ip):
        return render_template_string(LOGIN_HTML,
            blocked=True, error=None, auth_bot_name=BOT_NAME, client_ip=ip)
    if request.method == "POST":
        pwd = request.form.get("password", "")
        if check_password(pwd):
            session.permanent = True
            session["authenticated"] = True
            return redirect("/")
        record_fail(ip)
        row = db_one("SELECT attempts FROM blocked_ips WHERE ip=?", (ip,))
        attempts = row["attempts"] if row else 1
        remaining = max(0, MAX_FAIL - attempts)
        if remaining == 0:
            return render_template_string(LOGIN_HTML,
                blocked=True, error=None, auth_bot_name=BOT_NAME, client_ip=ip)
        return render_template_string(LOGIN_HTML, blocked=False,
            error=f"❌ Wrong password. {remaining} attempt{'s' if remaining != 1 else ''} remaining.",
            auth_bot_name=BOT_NAME, client_ip=ip)
    return render_template_string(LOGIN_HTML,
        blocked=False, error=None, auth_bot_name=BOT_NAME, client_ip=ip)

@app.route("/bot_login")
def bot_login():
    if not BOT_NAME:
        return redirect("/login")
    db_exec("DELETE FROM login_tokens WHERE created_at < ?", (time.time() - 900,))
    token = secrets.token_urlsafe(32)
    db_exec("INSERT INTO login_tokens (token, created_at) VALUES (?, ?)",
            (token, time.time()))
    bot_url = f"https://t.me/{BOT_NAME}?start={token}"
    return render_template_string(BOT_LOGIN_HTML,
        token=token, bot_url=bot_url, auth_bot_name=BOT_NAME)

@app.route("/check_token")
def check_token():
    """Polled by the browser that opened /bot_login. Once the bot has approved
    the token, THIS browser gets the session cookie."""
    token = request.args.get("t", "")
    row = db_one("SELECT validated, created_at FROM login_tokens WHERE token=?", (token,))
    if row and row["validated"] and time.time() - row["created_at"] < 300:
        session.permanent = True
        session["authenticated"] = True
        unblock_ip(get_ip())
        db_exec("DELETE FROM login_tokens WHERE token=?", (token,))
        return jsonify({"ok": True})
    return jsonify({"ok": False})

@app.route("/logout")
def logout():
    session.clear()
    return redirect("/login")

@app.route("/api/status")
@require_api
def api_status():
    return jsonify({
        "index": _index_status,
        "topics": db_one("SELECT COUNT(*) as c FROM topics")["c"],
        "files":  db_one("SELECT COUNT(*) as c FROM files")["c"],
        "tg_ready": _tg_ready.is_set(),
    })

# ── Jinja filter ─────────────────────────────────────────────────────────
from urllib.parse import quote as urlquote
app.jinja_env.filters["urlencode"] = lambda s: urlquote(str(s), safe="")

# ── Main ──────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    threading.Thread(target=run_telegram, daemon=True, name="tg_client").start()
    threading.Thread(target=run_indexer,  daemon=True, name="indexer").start()
    threading.Thread(target=run_snapshotter, daemon=True, name="snapshotter").start()
    app.run(host="0.0.0.0", port=8080, debug=False, threaded=True)
