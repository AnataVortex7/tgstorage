"""
Telegram Backup Browser — app.py
Browse files backed up to Telegram via relay.py, from any browser.
"""

import os, sqlite3, hashlib, secrets, time, threading, asyncio, tempfile
from datetime import timedelta
from functools import wraps

from flask import (Flask, render_template_string, redirect, url_for,
                   session, request, Response, jsonify, stream_with_context)
from pyrogram import Client, idle
from pyrogram import raw as tg_raw

# ── Config (from Docker env vars) ───────────────────────────────────────
API_ID          = int(os.environ.get("API_ID", "0"))
API_HASH        = os.environ.get("API_HASH", "")
BOT_TOKEN       = os.environ.get("BOT_TOKEN", "")          # single bot — added to backup group as admin
BOT_NAME        = os.environ.get("BOT_NAME", "")           # @username of the bot (no @)
BACKUP_GROUP_ID = int(os.environ.get("BACKUP_GROUP_ID", "0"))
OWNER_CHAT_ID   = int(os.environ.get("OWNER_CHAT_ID", "0"))
WEB_PASSWORD    = os.environ.get("WEB_PASSWORD", "changeme")
SECRET_KEY      = os.environ.get("SECRET_KEY", secrets.token_hex(32))
WEB_URL         = os.environ.get("WEB_URL", "http://localhost:8080")

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
    return (hashlib.sha256(pwd.encode()).hexdigest() ==
            hashlib.sha256(WEB_PASSWORD.encode()).hexdigest())

def require_auth(f):
    @wraps(f)
    def wrapped(*args, **kwargs):
        if not session.get("authenticated"):
            return redirect(url_for("login_page"))
        return f(*args, **kwargs)
    return wrapped

# ── Telegram client ──────────────────────────────────────────────────────
_tg_loop   = None
_tg_client = None
_tg_ready  = threading.Event()

def run_telegram():
    global _tg_loop, _tg_client
    _tg_loop = asyncio.new_event_loop()
    asyncio.set_event_loop(_tg_loop)

    async def _start():
        global _tg_client
        from pyrogram import filters

        _tg_client = Client(
            name="tg_browser",
            api_id=API_ID,
            api_hash=API_HASH,
            bot_token=BOT_TOKEN,
            in_memory=True,
        )

        # ── /start TOKEN  — web login via deep link ──────────────────────
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
            verify_url = f"{WEB_URL}/verify?token={token}"
            await msg.reply(
                f"✅ **Login approved!**\n\n"
                f"[🌐 Open Backup Browser]({verify_url})\n\n"
                "_Single-use link, valid for 5 minutes._"
            )

        # ── /unblock [IP]  — unblock a blocked IP ────────────────────────
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

        # ── /status  — index + DB stats ──────────────────────────────────
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

        await _tg_client.start()
        _tg_ready.set()
        print("[tg] Client ready", flush=True)
        await idle()

    _tg_loop.run_until_complete(_start())

def tg(coro, timeout=120):
    """Run a coroutine on the Telegram event loop from any thread."""
    if not _tg_ready.is_set():
        raise RuntimeError("Telegram client not ready yet")
    return asyncio.run_coroutine_threadsafe(coro, _tg_loop).result(timeout=timeout)



# ── Indexer ──────────────────────────────────────────────────────────────
_index_status = {"status": "idle", "progress": "", "last_run": 0}

async def _fetch_topics():
    """Fetch all forum topics from backup group and store in DB."""
    peer = await _tg_client.resolve_peer(BACKUP_GROUP_ID)
    all_topics = []
    offset_topic = 0
    while True:
        r = await _tg_client.invoke(tg_raw.functions.channels.GetForumTopics(
            channel=peer,
            q="",
            offset_date=0,
            offset_id=0,
            offset_topic=offset_topic,
            limit=100,
        ))
        if not r.topics:
            break
        all_topics.extend(r.topics)
        if len(r.topics) < 100:
            break
        offset_topic = r.topics[-1].id

    for t in all_topics:
        # Skip the "General" topic (id=1)
        if t.id == 1:
            continue
        raw_name = t.title
        # Normalize Windows backslash path → forward slash
        path = raw_name.replace("\\", "/").replace("//", "/").strip("/")
        parts = path.rsplit("/", 1)
        parent = parts[0] if len(parts) > 1 else ""
        depth  = path.count("/")
        db_exec("""
            INSERT INTO topics (topic_id, name, path, parent_path, depth, updated_at)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(topic_id) DO UPDATE SET
                name=excluded.name, path=excluded.path,
                parent_path=excluded.parent_path, depth=excluded.depth,
                updated_at=excluded.updated_at
        """, (t.id, raw_name, path, parent, depth, time.time()))
    return len(all_topics)

async def _index_topic_files(topic_id, path):
    """Index all file messages in a specific forum topic."""
    peer = await _tg_client.resolve_peer(BACKUP_GROUP_ID)
    offset_id = 0
    count = 0
    while True:
        r = await _tg_client.invoke(tg_raw.functions.messages.GetReplies(
            peer=peer,
            msg_id=topic_id,
            offset_id=offset_id,
            offset_date=0,
            add_offset=0,
            limit=100,
            max_id=0,
            min_id=0,
            hash=0,
        ))
        if not r.messages:
            break
        for raw_msg in r.messages:
            if not hasattr(raw_msg, "media"):
                continue
            if not isinstance(raw_msg.media, tg_raw.types.MessageMediaDocument):
                continue
            doc = raw_msg.media.document
            filename = f"file_{raw_msg.id}"
            size = getattr(doc, "size", 0)
            mime = getattr(doc, "mime_type", "") or ""
            for attr in getattr(doc, "attributes", []):
                if isinstance(attr, tg_raw.types.DocumentAttributeFilename):
                    filename = attr.file_name
                    break
            db_exec("""
                INSERT OR IGNORE INTO files (msg_id, topic_id, folder_path, filename, size, mime_type)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (raw_msg.id, topic_id, path, filename, size, mime))
            count += 1
        if len(r.messages) < 100:
            break
        offset_id = r.messages[-1].id

    db_exec("UPDATE topics SET msg_count=?, files_indexed=1, updated_at=? WHERE topic_id=?",
            (count, time.time(), topic_id))
    return count

async def _full_index():
    global _index_status
    _index_status["status"] = "indexing topics..."
    n = await _fetch_topics()
    _index_status["progress"] = f"{n} topics loaded"

    topics = db_many("SELECT topic_id, path FROM topics WHERE files_indexed=0")
    total = len(topics)
    for i, t in enumerate(topics):
        _index_status["status"] = f"indexing files {i+1}/{total}"
        _index_status["progress"] = t["path"]
        try:
            await _index_topic_files(t["topic_id"], t["path"])
        except Exception as e:
            print(f"[index] Error indexing topic {t['path']}: {e}", flush=True)
        await asyncio.sleep(0.3)  # avoid flood

    _index_status["status"] = "done"
    _index_status["last_run"] = time.time()
    print(f"[index] Full index complete. {total} topics.", flush=True)

def run_indexer():
    """Runs on startup and every 30 minutes."""
    _tg_ready.wait()
    while True:
        try:
            asyncio.run_coroutine_threadsafe(_full_index(), _tg_loop).result(timeout=3600)
        except Exception as e:
            _index_status["status"] = f"error: {e}"
            print(f"[index] Error: {e}", flush=True)
        time.sleep(1800)

# ── Browser helpers ───────────────────────────────────────────────────────
def build_tree(path=""):
    """Return (subfolders, files) for a given path."""
    subfolders = db_many(
        "SELECT * FROM topics WHERE parent_path=? ORDER BY path",
        (path,)
    )
    topic = db_one("SELECT topic_id, files_indexed FROM topics WHERE path=?", (path,))
    files = []
    if topic:
        if not topic["files_indexed"]:
            # Lazy index on first open
            try:
                tg(asyncio.ensure_future(_index_topic_files(topic["topic_id"], path), loop=_tg_loop), timeout=60)
            except Exception:
                pass
        files = db_many(
            "SELECT * FROM files WHERE topic_id=? ORDER BY filename COLLATE NOCASE",
            (topic["topic_id"],)
        )
    return list(subfolders), list(files)

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
    ⛔ Too many wrong attempts.<br>Blocked for 24 hours.<br><br>
    <small>Send <code>/unblock &lt;your IP&gt;</code> to the auth bot to unblock early.</small>
  </div>
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
let tries = 0;
function poll() {
  fetch('/check_token?t={{ token }}')
    .then(r => r.json())
    .then(d => {
      if (d.ok) {
        document.getElementById('status').innerHTML = '<span class="success">✅ Authenticated! Redirecting...</span>';
        setTimeout(() => window.location.href = '/', 800);
      } else if (tries++ < 60) {
        setTimeout(poll, 3000);
      } else {
        document.getElementById('status').textContent = '⏰ Timed out. Please go back and try again.';
      }
    }).catch(() => { if (tries++ < 60) setTimeout(poll, 5000); });
}
window.onload = () => setTimeout(poll, 2000);
</script>
</head>
<body>
<div class="card">
  <div style="font-size:2.5rem;margin-bottom:.5rem">🤖</div>
  <h2>Login via Telegram</h2>
  <p>Click the button below to authenticate using Telegram.<br>
  The bot will send you a login link.</p>
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

/* ── Header ── */
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

/* ── Breadcrumb ── */
.bc{padding:.6rem 1.5rem;font-size:.875rem;color:var(--muted);display:flex;
  flex-wrap:wrap;gap:.25rem;align-items:center}
.bc a{color:var(--blue);text-decoration:none}.bc a:hover{text-decoration:underline}
.bc .sep{color:#484f58}

/* ── Table ── */
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

/* ── Search results header ── */
.srh{padding:.5rem 1.5rem .2rem;font-size:.875rem;color:var(--muted)}
.srh b{color:var(--text)}
.srh a{color:var(--blue);font-size:.82rem;margin-left:.5rem;text-decoration:none}

/* ── Empty ── */
.empty{text-align:center;padding:4rem;color:var(--muted)}

/* ── Toast ── */
#toast{position:fixed;bottom:1.5rem;right:1.5rem;background:#238636;color:#fff;
  padding:.7rem 1.2rem;border-radius:8px;font-size:.9rem;display:none;
  box-shadow:0 4px 12px rgba(0,0,0,.4);z-index:100}

/* ── Status bar ── */
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
        <a class="btn-s btn-dl"
           href="/download/{{ fi.msg_id }}/{{ fi.filename|urlencode }}">⬇ Download</a>
        <a class="btn-s btn-tg"
           href="#"
           onclick="sendToBot({{ fi.msg_id }}, this); return false;">📤 Send</a>
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

@app.route("/browse/", defaults={"path": ""})
@app.route("/browse/<path:path>")
@require_auth
def browse(path):
    path = path.strip("/")
    folders, files = build_tree(path)
    crumbs = breadcrumbs(path)
    parent_url = ("/" if not path else
                  "/browse/" + "/".join(path.split("/")[:-1]) if "/" in path else "/")
    total_files = db_one("SELECT COUNT(*) as c FROM files")["c"]
    return render_template_string(
        BROWSER_HTML,
        path=path, folders=folders, files=files,
        crumbs=crumbs, parent_url=parent_url,
        search_mode=False, q="",
        fmt_size=fmt_size,
        idx_status=_index_status["status"],
        total_files=total_files,
    )

@app.route("/search")
@require_auth
def search():
    from urllib.parse import quote
    q = request.args.get("q", "").strip()
    files = []
    if q:
        files = db_many(
            "SELECT * FROM files WHERE filename LIKE ? ORDER BY filename COLLATE NOCASE LIMIT 300",
            (f"%{q}%",)
        )
    total_files = db_one("SELECT COUNT(*) as c FROM files")["c"]
    return render_template_string(
        BROWSER_HTML,
        path="", folders=[], files=files,
        crumbs=[], parent_url="/",
        search_mode=True, q=q,
        fmt_size=fmt_size,
        idx_status=_index_status["status"],
        total_files=total_files,
    )

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

@app.route("/sendtobot/<int:msg_id>")
@require_auth
def sendtobot(msg_id):
    try:
        tg(_tg_client.forward_messages(
            chat_id=OWNER_CHAT_ID,
            from_chat_id=BACKUP_GROUP_ID,
            message_ids=msg_id,
        ), timeout=30)
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"ok": False, "msg": str(e)}), 500

@app.route("/reindex")
@require_auth
def reindex():
    # Reset files_indexed so full re-index runs
    db_exec("UPDATE topics SET files_indexed=0")
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
            blocked=True, error=None, auth_bot_name=BOT_NAME)
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
                blocked=True, error=None, auth_bot_name=BOT_NAME)
        return render_template_string(LOGIN_HTML, blocked=False,
            error=f"❌ Wrong password. {remaining} attempt{'s' if remaining != 1 else ''} remaining.",
            auth_bot_name=BOT_NAME)
    return render_template_string(LOGIN_HTML,
        blocked=False, error=None, auth_bot_name=BOT_NAME)

@app.route("/bot_login")
def bot_login():
    ip = get_ip()
    if is_blocked(ip):
        return redirect("/login")
    if not BOT_NAME:
        return redirect("/login")
    token = secrets.token_urlsafe(32)
    db_exec("INSERT INTO login_tokens (token, created_at) VALUES (?, ?)",
            (token, time.time()))
    bot_url = f"https://t.me/{BOT_NAME}?start={token}"
    return render_template_string(BOT_LOGIN_HTML,
        token=token, bot_url=bot_url, auth_bot_name=BOT_NAME)

@app.route("/check_token")
def check_token():
    token = request.args.get("t", "")
    row = db_one("SELECT validated, created_at FROM login_tokens WHERE token=?", (token,))
    if row and row["validated"] and time.time() - row["created_at"] < 600:
        session.permanent = True
        session["authenticated"] = True
        db_exec("DELETE FROM login_tokens WHERE token=?", (token,))
        return jsonify({"ok": True})
    return jsonify({"ok": False})

@app.route("/verify")
def verify():
    """Called from auth bot's clickable link — instant login."""
    token = request.args.get("token", "")
    row = db_one("SELECT validated, created_at FROM login_tokens WHERE token=?", (token,))
    if row and row["validated"] and time.time() - row["created_at"] < 600:
        session.permanent = True
        session["authenticated"] = True
        db_exec("DELETE FROM login_tokens WHERE token=?", (token,))
        return redirect("/")
    return redirect("/login")

@app.route("/logout")
def logout():
    session.clear()
    return redirect("/login")

@app.route("/api/status")
@require_auth
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
    app.run(host="0.0.0.0", port=8080, debug=False, threaded=True)
