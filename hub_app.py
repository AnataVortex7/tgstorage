"""
Render server = मूक मध्यस्थ (silent middleman) + Telegram admin bot.

• बाहेरच्या कुणालाही काहीही दिसत नाही — सगळे routes रिकामा 404 देतात
  (फक्त /, /health, /healthz → "ok", तुमच्या heartbeat / health check साठी).
• Bot: कुणीही message केलं की PASSWORD मागतो (4 प्रयत्न). बरोबर password = admin.
  Admin ids आता fix नाहीत → admin चा Telegram account गेला तरी नव्या account ने
  password टाकून परत access मिळतो.
• PC → server: दर PC long-poll करतो (/api/sync). Button दाबताच command लगेच PC ला जातो.

Environment variables (Render dashboard → Environment):
  TELEGRAM_BOT_TOKEN  तुमच्या control bot चा token                         [required]
  ADMIN_PASSWORD      bot login password (किमान 10 अक्षरं, लांब ठेवा)       [required]
  COLAB_SECRET        तुमची secret (नवीन PC च्या .env मध्ये जाते)           [required]
  PANEL_PASSWORD      सगळ्या PC च्या web terminal चा password               [required]
  MONGODB_URI         Atlas URI — admin login/lockout restart नंतरही राहतात [strongly recommended]
  KOYEB_URL           default job/poll server URL (नवीन PC साठी)            [optional]
  SESSION_KEY         Telegram sessions encrypt करणारी Fernet key           [Telegram accounts साठी]
  TG_API_ID/TG_API_HASH  default api (login वेळी वेगळा देता येतो)          [optional]
  BW_LIMIT_GB / MAX_ACCOUNTS / MAX_UPLOAD_MB / MAX_MEDIA_MB                  [optional]
  ADMIN_CHAT_ID       (optional) कायमचे admin ids, password शिवाय — रिकामं सोडा
  LOCKOUT_HOURS       4 चुकीच्या प्रयत्नांनंतर block किती तास (default 24, 0 = कायमचं)
  ADMIN_SESSION_DAYS  login किती दिवस टिकतो (default 30, 0 = कधीच संपत नाही)
  LOGIN_GLOBAL_MAX    तासाला सगळ्यांचे मिळून चुकीचे प्रयत्न (default 20) → login 1 तास बंद
  AGENT_MASTER / PUBLIC_URL / MONGODB_DB                                     [optional]
"""
import os, re, hmac, html, json, time, base64, struct, hashlib, secrets, threading, collections
import urllib.request, urllib.parse
from pathlib import Path
from flask import Flask, request, Response, abort, jsonify, has_request_context
try:
    import tgweb                                    # Telegram accounts (optional)
except Exception as _e:                             # pragma: no cover
    tgweb = None
    print('tgweb load failed:', _e)

app = Flask(__name__)
STORAGE = None            # storage_app module, plugged in by app.py (Backup Browser, same bot)
FILES = Path(__file__).parent / 'files'

COLAB_SECRET = os.environ['COLAB_SECRET']
PANEL_PASSWORD = os.environ['PANEL_PASSWORD']
TG_TOKEN = os.environ['TELEGRAM_BOT_TOKEN']
ENV_ADMINS = {int(x) for x in os.environ.get('ADMIN_CHAT_ID', '').replace(' ', '').split(',') if x.strip().isdigit()}
ADMIN_PASSWORD = os.environ.get('ADMIN_PASSWORD', '')
if len(ADMIN_PASSWORD) < 10:
    raise RuntimeError('ADMIN_PASSWORD env लागतो आणि किमान 10 अक्षरांचा हवा (लांब, वेगळा password ठेवा)')
MAX_TRIES = 4
LOCK_SECS = int(float(os.environ.get('LOCKOUT_HOURS', '24')) * 3600)     # 0 = कायमचं
SESSION_DAYS = int(os.environ.get('ADMIN_SESSION_DAYS', '30'))           # 0 = कधीच संपत नाही
GLOBAL_MAX = int(os.environ.get('LOGIN_GLOBAL_MAX', '20'))
FOREVER = 32503680000                                                    # सन 3000
ADMIN_TOTP_SECRET = re.sub(r'\s+', '', os.environ.get('ADMIN_TOTP_SECRET', '')).upper()
# 2FA password: typed instead of the Authenticator code, it passes the 2FA step too (works even without an Authenticator secret).
# The bot's prompt stays exactly the same — the user may type this password instead of the 6 digits.
ADMIN_2FA_PASSWORD = os.environ.get('ADMIN_2FA_PASSWORD', '').strip()
if ADMIN_2FA_PASSWORD and len(ADMIN_2FA_PASSWORD) < 8:
    print('[security] ADMIN_2FA_PASSWORD is shorter than 8 characters — ignored', flush=True)
    ADMIN_2FA_PASSWORD = ''
TOTP_ON = bool(ADMIN_TOTP_SECRET) or bool(ADMIN_2FA_PASSWORD)    # the 2FA step is always asked when either is set
if ADMIN_TOTP_SECRET:
    try:
        base64.b32decode(ADMIN_TOTP_SECRET + '=' * (-len(ADMIN_TOTP_SECRET) % 8))
    except Exception:
        raise RuntimeError('ADMIN_TOTP_SECRET चुकीचा आहे (base32 अक्षरं A-Z आणि 2-7 हवीत)')
STEALTH_TRIGGER = os.environ.get('STEALTH_TRIGGER', '').strip()          # असेल तर अनोळखीला उत्तरच नाही
STEPUP_SECS = int(float(os.environ.get('STEPUP_MINUTES', '10')) * 60)
SENSITIVE = {'t', 'u', 'ua', 'n', 'bk', 'ar', 'aa', 'tg', 'tr', 'tx', 'sl'}                            # यासाठी ताजा 2FA code लागतो
KOYEB_DEFAULT = [os.environ.get('KOYEB_URL', '').rstrip('/')]
AGENT_MASTER = os.environ.get('AGENT_MASTER') or hashlib.sha256(
    f'agent|{COLAB_SECRET}|{TG_TOKEN}'.encode()).hexdigest()
SYNC_WAIT = float(os.environ.get('SYNC_WAIT', '20'))
BOOT = time.time()

PCS = {}                         # pcid -> {name, st, ts, url, ev, cmds, offline}
TOKENS = {}                      # one-time install tokens
PENDING = {}                     # admin user id -> ('seturl', pcid|'ALL')
LOCK = threading.Lock()

# ── PC terminal (reverse-proxy via /api/sync long-poll) ──────────────────
# shell command output is routed back here so the browser can poll it.
# _SHELL_OUT[pcid] = deque of {id, line, ts}  (last 500 lines per PC)
_SHELL_OUT = {}                  # pcid -> deque
_SHELL_LOCK = threading.Lock()
_SHELL_OUT_MAX = 500
BAD = collections.defaultdict(list)


# ───────────────────────── helpers ─────────────────────────
def eq(a, b):
    return hmac.compare_digest(str(a).encode(), str(b).encode())

def esc(s):
    return html.escape(str(s), quote=False)

def client_ip():
    return (request.headers.get('X-Forwarded-For', '').split(',')[0].strip()
            or request.remote_addr or '?')

def throttle_check():
    ip, now = client_ip(), time.time()
    BAD[ip] = [t for t in BAD[ip] if now - t < 600]
    if len(BAD[ip]) >= 10:
        abort(404)                           # lockout सुद्धा 404 — काहीच कळू देत नाही

def bad_attempt():
    BAD[client_ip()].append(time.time())
    abort(404)

def root_url():
    u = (os.environ.get('PUBLIC_URL') or os.environ.get('RENDER_EXTERNAL_URL') or '').rstrip('/')
    if not u and has_request_context():
        u = request.url_root.rstrip('/')
    if u.startswith('http://') and 'localhost' not in u and '127.0.0.1' not in u:
        u = 'https://' + u[len('http://'):]
    return u

def agent_key(pcid):
    return hmac.new(AGENT_MASTER.encode(), pcid.encode(), hashlib.sha256).hexdigest()[:40]

def text_resp(body):
    return Response(body, mimetype='text/plain', headers={'Cache-Control': 'no-store'})

try:                                # gunicorn स्वतः "Server: gunicorn" लिहितो — त्याचं नाव बदलतो
    import gunicorn.http.wsgi as _gw
    _gw.SERVER = 'web'
except Exception:
    pass

@app.after_request
def _hygiene(resp):
    resp.headers['Server'] = 'web'                         # gunicorn/Flask चं नाव लपवतो
    resp.headers['X-Content-Type-Options'] = 'nosniff'
    resp.headers['Referrer-Policy'] = 'no-referrer'
    return resp

# सगळ्या चुका रिकामा 404 — route अस्तित्वात आहे का नाही हे सुद्धा बाहेरून कळू नये
for _c in (400, 403, 404, 405, 429):
    app.register_error_handler(_c, lambda e: ('', 404))
app.register_error_handler(500, lambda e: ('', 500))


# ───────────────────────── Telegram ─────────────────────────
import http.client, socket, ssl

class _V4Conn(http.client.HTTPSConnection):
    """HTTPS connection that uses IPv4 ONLY and a short connect timeout.
    Root cause of the 45-90 s delays: api.telegram.org resolves to IPv6 first; the container has no working
    IPv6 route, so every NEW connection sat in sock.connect() until the OS gave up, and only then fell back."""
    def connect(self):
        last = None
        for fam, typ, proto, _, addr in socket.getaddrinfo(self.host, self.port, socket.AF_INET, socket.SOCK_STREAM):
            sock = None
            try:
                sock = socket.socket(fam, typ, proto)
                sock.settimeout(5)                      # connect timeout
                sock.connect(addr)
                sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
                sock.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)
                self.sock = self._context.wrap_socket(sock, server_hostname=self.host)
                self.sock.settimeout(self.timeout)
                return
            except OSError as e:
                last = e
                if sock:
                    sock.close()
        raise last or OSError('no IPv4 address for ' + str(self.host))

_TL = threading.local()
_CTX = ssl.create_default_context()

def _conn(timeout):
    c = getattr(_TL, 'c', None)
    if c is None:
        c = _TL.c = _V4Conn('api.telegram.org', 443, timeout=timeout, context=_CTX)
    c.timeout = timeout
    if c.sock is not None:
        c.sock.settimeout(timeout)
    return c

def _drop():
    c = getattr(_TL, 'c', None)
    _TL.c = None
    if c is not None:
        try:
            c.close()
        except Exception:
            pass

def tg(method, **p):
    """Bot API call. Keep-alive connection per thread (no new TCP+TLS handshake per call), IPv4 only,
    5 s connect timeout, automatic retry on a stale / broken connection."""
    data = urllib.parse.urlencode(
        {k: (json.dumps(v) if isinstance(v, (dict, list)) else v) for k, v in p.items()}).encode()
    rto = 45 if method == 'getUpdates' else 20
    hdr = {'Content-Type': 'application/x-www-form-urlencoded', 'Connection': 'keep-alive'}
    last = None
    for attempt in range(3):
        try:
            c = _conn(rto)
            c.request('POST', f'/bot{TG_TOKEN}/{method}', body=data, headers=hdr)
            r = c.getresponse()
            raw = r.read()
            if r.status == 200:
                return json.loads(raw.decode('utf-8'))
            try:
                body = json.loads(raw.decode('utf-8'))
            except Exception:
                body = {}
            if not isinstance(body, dict):
                body = {}
            body['ok'] = False
            body.setdefault('error_code', r.status)
            if 'not modified' not in str(body.get('description', '')):
                print(f"tg {method} failed: HTTP {r.status} {body.get('description', '')}", flush=True)
            return body
        except (ConnectionError, http.client.HTTPException, socket.timeout, ssl.SSLError, OSError) as e:
            last = e
            _drop()                                   # broken / stale connection -> new one next time
            if method == 'getUpdates' and isinstance(e, socket.timeout):
                return {}                             # long-poll simply timed out
            time.sleep(0.3 * (attempt + 1))
        except Exception as e:
            _drop()
            print(f'tg {method} failed: {e}', flush=True)
            return {}
    if 'not modified' not in str(last):
        print(f'tg {method} failed: {last}', flush=True)
    return {}

def notify(text, kb=None):
    def go(chat):
        tg('sendMessage', chat_id=chat, text=text[:4000], parse_mode='HTML',
           disable_web_page_preview=True, **({'reply_markup': kb} if kb else {}))
    for chat in AUTH.admin_ids():
        threading.Thread(target=go, args=(chat,), daemon=True).start()

def online(pc):
    return time.time() - pc['ts'] < 90

def yn(v):
    return '❓' if v is None else ('✅' if v else '❌')

def fmt_dur(s):
    s = int(s or 0)
    h, m = divmod(s // 60, 60)
    return f'{h}h {m}m' if h else f'{m}m'

def main_text():
    if not PCS:
        return '🖥 <b>Worker PCs</b>\n\nअजून एकही PC जोडलेला नाही.\n➕ दाबून नवा PC install करा.'
    lines = ['🖥 <b>Worker PCs</b>', '']
    for pcid, pc in sorted(PCS.items(), key=lambda kv: kv[1]['name']):
        st = pc['st']
        w = '✅' if st.get('running') else ('⏸' if not st.get('want', True) else '❌')
        lines.append(f"{'🟢' if online(pc) else '🔴'} <b>{esc(pc['name'])}</b> — worker {w}")
    return '\n'.join(lines)

def main_kb():
    rows = [[{'text': f"{'🟢' if online(pc) else '🔴'} {pc['name']}", 'callback_data': f'p:{pcid}'}]
            for pcid, pc in sorted(PCS.items(), key=lambda kv: kv[1]['name'])]
    rows.append([{'text': '🌐 सगळ्यांचा Job URL बदला', 'callback_data': 'ua'}])
    rows.append([{'text': '👤 Telegram Accounts', 'callback_data': 'ta'}])
    rows.append([{'text': '📦 Storage (Backup Browser)', 'callback_data': 'sb'}])
    rows.append([{'text': '👥 Admins', 'callback_data': 'adm'}])
    rows.append([{'text': '💾 PC Backup command', 'callback_data': 'bk'}])
    rows.append([{'text': '➕ नवा PC install', 'callback_data': 'n'},
                 {'text': '🔄 Refresh', 'callback_data': 'm'}])
    return {'inline_keyboard': rows}

def pc_text(pcid):
    pc = PCS[pcid]
    st = pc['st']
    state = ('✅ चालू' if st.get('running')
             else ('⏸ थांबवलेला' if not st.get('want', True) else '❌ बंद / सुरू होतोय'))
    age = st.get('log_age')
    return '\n'.join([
        f"{'🟢' if online(pc) else '🔴'} <b>{esc(pc['name'])}</b>  <code>{pcid}</code>",
        f"Worker: {state} · uptime {fmt_dur(st.get('uptime'))} · restarts {st.get('restarts', 0)}",
        f"Internet: {yn(st.get('internet'))} · Job-server: {yn(st.get('koyeb_ok'))}",
        f"Job URL: <code>{esc(st.get('koyeb_url') or '-')}</code>",
        f"Last log: {'-' if age is None else str(age) + 's पूर्वी'} · Encoder: {esc(st.get('encoder') or '-')}",
        f"Last job: {esc(st.get('last_job') or '-')}",
        f"PC शेवटचा दिसला: {int(time.time() - pc['ts'])}s पूर्वी",
    ])

def pc_kb(pcid):
    rows = [
        [{'text': '🔄 Refresh', 'callback_data': f'p:{pcid}'},
         {'text': '🩺 Check worker', 'callback_data': f'c:{pcid}:check'}],
        [{'text': '▶️ Start', 'callback_data': f'c:{pcid}:start'},
         {'text': '⏹ Stop', 'callback_data': f'c:{pcid}:stop'},
         {'text': '🔁 Restart', 'callback_data': f'c:{pcid}:restart'}],
        [{'text': '📜 Logs', 'callback_data': f'c:{pcid}:logs'},
         {'text': '🌐 Job URL बदला', 'callback_data': f'u:{pcid}'}],
        [{'text': '♻️ Manager restart', 'callback_data': f'c:{pcid}:rmgr'}],
    ]
    if PCS[pcid].get('url'):
        rows.append([{'text': '🖥 Local Panel (एकदाचा link)', 'callback_data': f't:{pcid}'}])
    rows.append([{'text': '💻 Browser Terminal (server मधून)', 'callback_data': f'bt:{pcid}'}])
    rows.append([{'text': '⬅️ Back', 'callback_data': 'm'}])
    return {'inline_keyboard': rows}

def show(chat, mid, text, kb):
    if mid:
        tg('editMessageText', chat_id=chat, message_id=mid, text=text, parse_mode='HTML',
           disable_web_page_preview=True, reply_markup=kb)
    else:
        tg('sendMessage', chat_id=chat, text=text, parse_mode='HTML',
           disable_web_page_preview=True, reply_markup=kb)

def enqueue(pcid, ctype, arg='', cmd_id=None):
    with LOCK:
        pc = PCS.get(pcid)
        if not pc:
            return False
        if len(pc['cmds']) < 20:
            pc['cmds'].append({'id': cmd_id or secrets.token_hex(3), 'type': ctype, 'arg': arg})
        pc['ev'].set()
    return True

def new_token(uses=10, ttl=900):
    t = secrets.token_urlsafe(24)
    with LOCK:
        TOKENS[t] = {'exp': time.time() + ttl, 'left': uses}
    return t

URL_RE = re.compile(r'^https?://[^\s<>"\']{4,200}$')

def on_callback(cbid, uid, chat, mid, data):
    parts = data.split(':')
    kind = parts[0]
    pcid = parts[1] if len(parts) > 1 else ''
    ans = lambda text='', alert=False: (tg('answerCallbackQuery', callback_query_id=cbid, text=text,
                                           show_alert='true' if alert else 'false') if cbid else None)
    if TOTP_ON and kind in SENSITIVE and not AUTH.stepup_valid(uid):
        PENDING[uid] = ('otp', data, chat, mid, 0)
        ans('🔑 2FA code लागतो')
        tg('sendMessage', chat_id=chat, text='🔑 सुरक्षेसाठी Authenticator app मधला 6 अंकी code पाठवा.\nरद्द: /cancel')
        return
    if kind == 'ta':
        ans(); t, kb = ta_view(); show(chat, mid, t, kb); return
    if kind == 'tb':
        st = tgweb.bw_status() if TG_ON else {'used': 0, 'limit': 0, 'month': '-'}
        ans(f"📊 {st['used'] / 1048576:.1f} MB" + (f" / {st['limit'] / 1073741824:.1f} GB" if st['limit'] else ' (no limit)') + f" ({st['month']})", True); return
    if kind == 'tg':
        if not TG_ON:
            ans('Telegram accounts बंद आहेत', True); return
        tok = tgweb.new_token()
        ans('✅ Link पाठवतोय')
        send_ephemeral(chat, f"👤 <b>Telegram UI</b>\n{esc(root_url())}/app?k={tok}\n\n"
                             f"⏱ एकदाच चालतो, 2 मिनिटं. हा message 90 सेकंदांत आपोआप delete होईल.")
        return
    if kind in ('tp', 'tc', 'tr', 'tx'):
        aid = parts[1] if len(parts) > 1 else ''
        if not TG_ON or not re.fullmatch(r'a_[0-9a-f]{8}', aid):
            ans('चुकीचं', True); return
        if kind == 'tp':
            t, kb = tp_view(aid)
            ans() if t else ans('Account सापडला नाही', True)
            if t:
                show(chat, mid, t, kb)
            return
        if kind == 'tc':
            a = next((x for x in tgweb.M.accounts_view() if x['id'] == aid), None)
            if not a:
                ans('Account सापडला नाही', True); return
            tgweb.M.set_enabled(aid, not a['enabled'])
            ans('⏸ Disconnect केलं' if a['enabled'] else '▶️ Connect होतंय…')
            t, kb = tp_view(aid); show(chat, mid, t, kb); return
        if kind == 'tr':
            ans()
            show(chat, mid, f'🗑 <b>{aid}</b> काढायचा?\n\n• "फक्त server वरून" — Telegram मधलं session चालू राहतं\n'
                            '• "logout सकट" — Telegram मधलं हे session कायमचं रद्द होतं',
                 {'inline_keyboard': [[{'text': '🗑 फक्त server वरून काढा', 'callback_data': f'tx:{aid}:0'}],
                                      [{'text': '🚪 Telegram मधून logout + काढा', 'callback_data': f'tx:{aid}:1'}],
                                      [{'text': '⬅️ रद्द', 'callback_data': f'tp:{aid}'}]]}); return
        if kind == 'tx':
            ok = tgweb.M.remove_account(aid, logout=(len(parts) > 2 and parts[2] == '1'))
            ans('✅ काढला' if ok else 'सापडला नाही', not ok)
            t, kb = ta_view(); show(chat, mid, t, kb); return
    if kind == 'sb':
        ans(); t, kb = sb_view(); show(chat, mid, t, kb); return
    if kind == 'sl':
        if STORAGE is None:
            ans('Storage बंद आहे', True); return
        try:
            url = STORAGE.magic_login_url()
        except Exception as e:
            ans(f'Error: {type(e).__name__}', True); return
        ans('✅ Link पाठवतोय')
        send_ephemeral(chat, f"📦 <b>Backup Browser</b>\n{esc(url)}\n\n"
                             f"⏱ 5 मिनिटं, एकदाच चालतो. हा message 90 सेकंदांत आपोआप delete होईल.")
        return
    if kind == 'adm':
        ans(); t, kb = admins_view(uid); show(chat, mid, t, kb); return
    if kind == 'ar':
        ok = pcid.isdigit() and int(pcid) != uid and AUTH.remove(int(pcid))
        if ok:
            tg('sendMessage', chat_id=int(pcid), text='🔒 तुमचा admin access काढला गेला.')
        ans('✅ काढला' if ok else 'काढता आलं नाही'); t, kb = admins_view(uid); show(chat, mid, t, kb); return
    if kind == 'al':
        if uid in ENV_ADMINS:
            ans('हा कायमचा admin आहे (env) — logout होत नाही', True); return
        AUTH.remove(uid); ans('🚪 Logout झालं')
        tg('sendMessage', chat_id=chat, text='🚪 Logout झालं. परत login साठी password लागेल.'); return
    if kind == 'aa':
        n = AUTH.remove_others(uid); ans(f'🔒 {n} जण logout केले', True)
        t, kb = admins_view(uid); show(chat, mid, t, kb); return
    if kind == 'm':
        ans(); show(chat, mid, main_text(), main_kb()); return
    if kind == 'n':
        t = new_token()
        cmd = f'powershell -NoProfile -ExecutionPolicy Bypass -Command "irm {root_url()}/i/{t} | iex"'
        ans()
        tg('sendMessage', chat_id=chat, parse_mode='HTML', disable_web_page_preview=True, text=(
            '➕ <b>नवा PC install</b>\n\n'
            '1️⃣ त्या PC वर <b>Win+R</b> दाबा\n'
            '2️⃣ खालची command paste करा\n'
            '3️⃣ <b>Enter नाही — Ctrl+Shift+Enter</b> दाबा (UAC ला Yes)\n\n'
            f'<code>{esc(cmd)}</code>\n\n'
            '⏱ ही command फक्त 15 मिनिटं चालते. कुणाला देऊ नका.'))
        return
    if kind == 'bk':
        # short-lived link made only for the logged-in admin; the script served on it carries the
        # server-side BACKUP_TOKEN, so the PC command itself never contains a permanent secret.
        t = new_token(uses=3, ttl=600)          # script fetch + one self-elevation re-fetch (+1 spare)
        cmd = f'powershell -NoProfile -ExecutionPolicy Bypass -Command "irm {root_url()}/b/{t} | iex"'
        ans('✅ Command पाठवतोय')
        send_ephemeral(chat, '💾 <b>PC Backup</b>\n\nWin+R → खालची command paste → Enter\n\n'
                             f'<code>{esc(cmd)}</code>\n\n'
                             '⏱ 10 मिनिटं आणि फक्त 3 वेळा चालते. हा message 90 सेकंदांत आपोआप delete होईल.')
        return
    if kind == 'ua':
        PENDING[uid] = ('seturl', 'ALL')
        ans()
        tg('sendMessage', chat_id=chat, text='🌐 सगळ्या PC साठी नवा Job-server URL पाठवा (https://...)\nरद्द: /cancel')
        return
    if not re.fullmatch(r'[0-9a-f]{8}', pcid) or pcid not in PCS:
        ans('PC सापडला नाही — थोड्या वेळाने Refresh करा', True); return
    pc = PCS[pcid]
    if kind == 'p':
        ans(); show(chat, mid, pc_text(pcid), pc_kb(pcid))
    elif kind == 't':
        if not online(pc) or not pc.get('url'):
            ans('PC offline आहे किंवा URL नाही', True); return
        tok = secrets.token_urlsafe(24)
        enqueue(pcid, 'ptoken', tok)             # PC ला हा token 2 मिनिटं मान्य
        ans('✅ Link पाठवतोय')
        r = tg('sendMessage', chat_id=chat, parse_mode='HTML', disable_web_page_preview=True, text=(
            f"🖥 <b>{esc(pc['name'])}</b> terminal\n{esc(pc['url'])}/?k={tok}\n\n"
            f"⏱ एकदाच चालतो, 2 मिनिटं. हा message 90 सेकंदांत आपोआप delete होईल."))
        m2 = (r.get('result') or {}).get('message_id') if isinstance(r, dict) else None
        if m2:
            t = threading.Timer(90, tg, args=('deleteMessage',), kwargs={'chat_id': chat, 'message_id': m2})
            t.daemon = True
            t.start()
    elif kind == 'bt':
        # Browser terminal via server — no need for PC to be online right now
        term_url = f"{root_url()}/app/pc/{pcid}/term"
        ans('✅ Terminal link')
        r = tg('sendMessage', chat_id=chat, parse_mode='HTML', disable_web_page_preview=True, text=(
            f"💻 <b>{esc(pc['name'])}</b> Browser Terminal\n"
            f"<a href=\"{esc(term_url)}\">{esc(term_url)}</a>\n\n"
            f"🔐 Admin login लागेल. PC online असेल तर commands थेट चालतात.\n"
            f"हा message 2 मिनिटांत delete होईल."))
        m2 = (r.get('result') or {}).get('message_id') if isinstance(r, dict) else None
        if m2:
            td = threading.Timer(120, tg, args=('deleteMessage',), kwargs={'chat_id': chat, 'message_id': m2})
            td.daemon = True; td.start()
    elif kind == 'u':
        PENDING[uid] = ('seturl', pcid)
        ans()
        tg('sendMessage', chat_id=chat, parse_mode='HTML', text=(
            f"🌐 <b>{esc(pc['name'])}</b> साठी नवा Job-server URL पाठवा (https://...)\nरद्द: /cancel"))
    elif kind == 'c' and len(parts) > 2 and parts[2] in ('start', 'stop', 'restart', 'logs', 'check', 'rmgr'):
        enqueue(pcid, parts[2])
        ans('⏳ पाठवलं' + ('' if online(pc) else ' (PC offline — online आल्यावर चालेल)'))
    else:
        ans()

def on_message(uid, chat, text, mid=None):
    if text == '/logout':
        if uid in ENV_ADMINS:
            tg('sendMessage', chat_id=chat, text='हा कायमचा admin आहे (env) — logout होत नाही'); return
        AUTH.remove(uid)
        tg('sendMessage', chat_id=chat, text='🚪 Logout झालं. परत login साठी password लागेल.'); return
    if text in ('/cancel',):
        PENDING.pop(uid, None)
        tg('sendMessage', chat_id=chat, text='रद्द केलं.'); return
    pend = PENDING.get(uid)
    if pend and pend[0] == 'otp':
        if mid:
            tg('deleteMessage', chat_id=chat, message_id=mid)
        if AUTH.stepup_verify(uid, text):
            PENDING.pop(uid, None)
            tg('sendMessage', chat_id=chat, text='✅ Verify झालं')
            on_callback('', uid, pend[2], pend[3], pend[1]); return
        tries = pend[4] + 1
        if tries >= 3:
            PENDING.pop(uid, None)
            AUTH.remove(uid)                      # संशयास्पद → session बंद, परत password + code
            tg('sendMessage', chat_id=chat, text='🔒 3 चुकीचे code — तुमचं session बंद केलं. परत login करा.')
            notify(f'🚨 Admin <code>{uid}</code> च्या संवेदनशील कृतीसाठी 3 चुकीचे 2FA code — session बंद केलं.')
        else:
            PENDING[uid] = pend[:4] + (tries,)
            tg('sendMessage', chat_id=chat, text=f'❌ चुकीचा code. {3 - tries} प्रयत्न बाकी (किंवा /cancel).')
        return
    if pend and pend[0] == 'seturl' and text and not text.startswith('/'):
        if not URL_RE.match(text):
            tg('sendMessage', chat_id=chat, text='❌ https://... असा URL द्या (किंवा /cancel)'); return
        url = text.rstrip('/')
        PENDING.pop(uid, None)
        if pend[1] == 'ALL':
            KOYEB_DEFAULT[0] = url
            for pid in list(PCS):
                enqueue(pid, 'seturl', url)
            tg('sendMessage', chat_id=chat, text=f'✅ {len(PCS)} PC ला पाठवलं. नवीन install साठी सुद्धा हाच URL '
                                                 f'(Render restart नंतरसाठी KOYEB_URL env सुद्धा बदला).')
        else:
            ok = enqueue(pend[1], 'seturl', url)
            tg('sendMessage', chat_id=chat, text='✅ पाठवलं — result इथे येईल.' if ok else '❌ PC सापडला नाही')
        return
    show(chat, None, main_text(), main_kb())

# ───────────────────────── Admin login (password + 4 प्रयत्न) ─────────────────────────
class Store:
    """Memory मध्ये राहतो; MongoDB दिला असेल तर प्रत्येक बदल तिथे सुद्धा save होतो."""
    def __init__(self, coll=None):
        self.coll, self.data, self._warned = coll, {}, False
        if coll is not None:
            for d in coll.find({}):
                self.data[d['_id']] = d

    def get(self, k):
        return self.data.get(k)

    def put(self, k, doc):
        doc['_id'] = k
        self.data[k] = doc
        if self.coll is not None:
            try:
                self.coll.replace_one({'_id': k}, doc, upsert=True)
            except Exception as e:
                self._warn(e)

    def delete(self, k):
        self.data.pop(k, None)
        if self.coll is not None:
            try:
                self.coll.delete_one({'_id': k})
            except Exception as e:
                self._warn(e)

    def _warn(self, e):
        if not self._warned:
            self._warned = True
            print(f'⚠️ MongoDB write failed: {e}')

MONGO_DB = [None]

def make_store():
    uri = os.environ.get('MONGODB_URI', '')
    if not uri:
        print('⚠️ MONGODB_URI नाही — admin login आणि lockout फक्त memory मध्ये (Render restart नंतर जातील)')
        return Store(None)
    try:
        from pymongo import MongoClient
        c = MongoClient(uri, serverSelectionTimeoutMS=8000, socketTimeoutMS=10000)
        coll = c[os.environ.get('MONGODB_DB', 'workerhub')]['auth']
        MONGO_DB[0] = c[os.environ.get('MONGODB_DB', 'workerhub')]
        coll.find_one({})                           # connection test
        print('✅ MongoDB जोडलं')
        return Store(coll)
    except Exception as e:
        print(f'❌ MongoDB जोडता आलं नाही ({e}) — memory वर चालतोय')
        return Store(None)

def fmt_left(sec):
    sec = int(sec)
    if sec > 10 * 365 * 86400:
        return 'कायमचं'
    h, m = divmod(sec // 60, 60)
    return f'{h}h {m}m' if h else f'{max(m, 1)}m'

def fmt_ago(ts):
    sec = int(time.time() - ts)
    return f'{sec // 86400}d पूर्वी' if sec >= 86400 else (f'{sec // 3600}h पूर्वी' if sec >= 3600 else f'{max(sec // 60, 0)}m पूर्वी')

def totp_code(secret, counter):
    """RFC 6238 (SHA1, 6 अंक, 30s)."""
    key = base64.b32decode(secret + '=' * (-len(secret) % 8))
    h = hmac.new(key, struct.pack('>Q', counter), hashlib.sha1).digest()
    o = h[-1] & 15
    return f'{(struct.unpack(">I", h[o:o + 4])[0] & 0x7fffffff) % 1000000:06d}'

def totp_verify(secret, code, last_used):
    """बरोबर असेल तर त्या 30s चा counter देतो; एकदा वापरलेला code पुन्हा चालत नाही (replay)."""
    code = re.sub(r'\s+', '', str(code))
    if not re.fullmatch(r'\d{6}', code):
        return None
    now = int(time.time() // 30)
    for c in (now - 1, now, now + 1):
        if c > last_used and eq(code, totp_code(secret, c)):
            return c
    return None

def second_factor(code, last_used):
    """2FA check: Authenticator code OR the ADMIN_2FA_PASSWORD. -> counter to remember (int), or None if wrong.
    The password is reusable (no counter), so it returns last_used unchanged."""
    if ADMIN_2FA_PASSWORD and eq(str(code).strip(), ADMIN_2FA_PASSWORD):
        return last_used or 0
    if not ADMIN_TOTP_SECRET:                 # never run TOTP with an empty key
        return None
    return totp_verify(ADMIN_TOTP_SECRET, code, last_used)

class Auth:
    def __init__(self, store):
        self.s = store
        self._reply = {}

    # ---- sessions
    def is_admin(self, uid):
        if uid in ENV_ADMINS:
            return True
        a = self.s.get(f'adm:{uid}')
        if not a:
            return False
        if SESSION_DAYS and time.time() - a['since'] > SESSION_DAYS * 86400:
            self.s.delete(f'adm:{uid}')
            return False
        return True

    def admin_ids(self):
        ids = set(ENV_ADMINS)
        for k in list(self.s.data):
            if k.startswith('adm:') and self.is_admin(int(k[4:])):
                ids.add(int(k[4:]))
        return ids

    def touch(self, uid):
        a = self.s.get(f'adm:{uid}')
        if a and time.time() - a.get('last', 0) > 3600:
            a['last'] = time.time()
            self.s.put(f'adm:{uid}', a)

    def listing(self):
        rows = [{'uid': u, 'name': 'env admin', 'since': 0, 'last': 0, 'env': True} for u in ENV_ADMINS]
        for k, a in list(self.s.data.items()):
            if k.startswith('adm:') and self.is_admin(a['uid']):
                rows.append({**a, 'env': False})
        return rows

    def remove(self, uid):
        if uid in ENV_ADMINS or f'adm:{uid}' not in self.s.data:
            return False
        self.s.delete(f'adm:{uid}')
        self.s.delete(f'su:{uid}')
        return True

    def remove_others(self, me):
        n = 0
        for k in [k for k in self.s.data if k.startswith('adm:')]:
            if int(k[4:]) != me:
                self.s.delete(k)
                self.s.delete(f'su:{k[4:]}')
                n += 1
        return n

    # ---- 2FA (step-up)
    def _glob(self):
        g = self.s.get('glob') or {}
        g.setdefault('fails', []); g.setdefault('paused_until', 0); g.setdefault('totp_last', 0)
        return g

    def stepup_valid(self, uid):
        if not TOTP_ON:
            return True
        d = self.s.get(f'su:{uid}')
        return bool(d and time.time() - d['at'] < STEPUP_SECS)

    def stepup_verify(self, uid, code):
        g = self._glob()
        c = second_factor(code, g['totp_last'])
        if c is None:
            return False
        g['totp_last'] = c
        self.s.put('glob', g)
        self.s.put(f'su:{uid}', {'at': time.time()})
        return True

    # ---- अनोळखी user चा flow
    def _say(self, chat, uid, text, kind='x', gap=1.0):
        now = time.time()
        if now - self._reply.get((uid, kind), 0) < gap:
            return
        self._reply[(uid, kind)] = now
        tg('sendMessage', chat_id=chat, text=text, parse_mode='HTML')

    def _grant(self, user, chat, now):
        uid = user['id']
        name = ' '.join(x for x in (user.get('first_name'), user.get('last_name')) if x) or str(uid)
        self.s.put(f'adm:{uid}', {'uid': uid, 'name': name[:40], 'username': user.get('username', ''),
                                  'since': now, 'last': now})
        self.s.put(f'su:{uid}', {'at': now})
        self.s.delete(f'att:{uid}')
        for o in self.admin_ids() - {uid}:
            tg('sendMessage', chat_id=o, parse_mode='HTML',
               text=f"🔔 नवा admin login: <b>{esc(name)}</b> (<code>{uid}</code>)")
        tg('sendMessage', chat_id=chat, text='✅ Login झाला. तुम्ही आता admin आहात.')
        show(chat, None, main_text(), main_kb())

    def _fail(self, chat, uid, user, att, g, now):
        att['fails'] += 1
        att['awaiting_until'] = now + 600
        g['fails'] = [t for t in g['fails'] if now - t < 3600] + [now]
        who = (f"{esc(' '.join(x for x in (user.get('first_name'), user.get('last_name')) if x) or uid)}"
               f"{' @' + esc(user['username']) if user.get('username') else ''} (<code>{uid}</code>)")
        if att['fails'] >= MAX_TRIES:
            att['locked_until'] = now + (LOCK_SECS or FOREVER)
            self.s.put(f'att:{uid}', att)
            self._say(chat, uid, f"🔒 {MAX_TRIES} चुकीचे प्रयत्न — तुम्ही block झालात ({fmt_left(att['locked_until'] - now)}).", 'lock', 0)
            notify(f'🚨 Login चे {MAX_TRIES} चुकीचे प्रयत्न: {who}\nBlock केलं.')
        else:
            self.s.put(f'att:{uid}', att)
            self._say(chat, uid, f"❌ चुकीचं. {MAX_TRIES - att['fails']} प्रयत्न बाकी.", 'wrong', 0)
        if len(g['fails']) >= GLOBAL_MAX and g['paused_until'] < now:
            g['paused_until'] = now + 3600
            notify(f"🚨 तासाभरात {len(g['fails'])} चुकीचे login प्रयत्न (अनेक users) — login 1 तास बंद केला.")
        self.s.put('glob', g)

    def handle_stranger(self, m):
        user, chat = m.get('from') or {}, m['chat']['id']
        uid, mid, text = user.get('id'), m.get('message_id'), (m.get('text') or '')
        now = time.time()
        g = self._glob()
        fresh = {'fails': 0, 'locked_until': 0, 'awaiting_until': 0, 'stage': 'pw', 'otp_until': 0}
        att = self.s.get(f'att:{uid}') or dict(fresh)
        if att['locked_until'] and att['locked_until'] <= now:
            att = dict(fresh)                                              # block संपला
        awaiting = att['awaiting_until'] > now
        if STEALTH_TRIGGER and not awaiting and text.strip() != STEALTH_TRIGGER:
            return                                                         # bot "मेलेला" वाटतो
        if mid:                                                            # password/code चॅटमध्ये राहू नये
            tg('deleteMessage', chat_id=chat, message_id=mid)
        if g['paused_until'] > now:
            self._say(chat, uid, '⏳ Login सध्या तात्पुरता बंद आहे. थोड्या वेळाने प्रयत्न करा.', 'pause', 30); return
        if att['locked_until'] > now:
            self._say(chat, uid, f"🔒 तुम्ही block आहात ({fmt_left(att['locked_until'] - now)} बाकी).", 'lock', 600); return
        if not awaiting or not text or text.startswith('/'):
            att['awaiting_until'], att['stage'] = now + 600, 'pw'
            self.s.put(f'att:{uid}', att)
            self._say(chat, uid, f"🔐 <b>Password पाठवा</b>\n{MAX_TRIES - att['fails']} प्रयत्न बाकी आहेत.\n"
                                 f"<i>(तुमचा message आपोआप delete होईल)</i>", 'prompt'); return
        if att.get('stage') == 'otp':
            if att.get('otp_until', 0) < now:
                att['stage'] = 'pw'
                self.s.put(f'att:{uid}', att)
                self._say(chat, uid, '⏱ Code ची वेळ संपली. परत password पाठवा.', 'prompt'); return
            c = second_factor(text, g['totp_last'])
            if c is not None:
                g['totp_last'] = c
                self.s.put('glob', g)
                self._grant(user, chat, now); return
            self._fail(chat, uid, user, att, g, now); return
        if eq(text.strip(), ADMIN_PASSWORD):
            if TOTP_ON:
                att['stage'], att['otp_until'], att['awaiting_until'] = 'otp', now + 120, now + 600
                self.s.put(f'att:{uid}', att)
                self._say(chat, uid, '✅ Password बरोबर.\nआता Authenticator app मधला <b>6 अंकी code</b> पाठवा (2 मिनिटांत).', 'otp', 0)
                return
            self._grant(user, chat, now); return
        self._fail(chat, uid, user, att, g, now)

AUTH = Auth(make_store())

app.secret_key = hashlib.sha256(
    f"flask|{os.environ.get('SESSION_KEY') or COLAB_SECRET}|{TG_TOKEN}".encode()).hexdigest()
app.config.update(SESSION_COOKIE_NAME='hub_session',           # storage_app uses the default 'session' cookie
                  SESSION_COOKIE_SECURE=os.environ.get('COOKIE_SECURE', '1') == '1',
                  SESSION_COOKIE_HTTPONLY=True, SESSION_COOKIE_SAMESITE='Strict')
TG_ON = bool(tgweb and tgweb.init(app, MONGO_DB[0], lambda t: notify(t)))
if tgweb and TG_ON:
    app.config['MAX_CONTENT_LENGTH'] = tgweb.CHUNK + 4 * 1024 * 1024        # upload 8 MB तुकड्यांत येतो
elif tgweb:
    print(f'ℹ️ Telegram accounts बंद: {tgweb.reason}')

def send_ephemeral(chat, text, secs=90):
    """Link/माहिती पाठवतो आणि `secs` नंतर chat मधून आपोआप delete करतो."""
    r = tg('sendMessage', chat_id=chat, parse_mode='HTML', disable_web_page_preview=True, text=text)
    m2 = (r.get('result') or {}).get('message_id') if isinstance(r, dict) else None
    if m2:
        t = threading.Timer(secs, tg, args=('deleteMessage',), kwargs={'chat_id': chat, 'message_id': m2})
        t.daemon = True
        t.start()

TG_EMOJI = {'online': '🟢', 'connecting': '🟡', 'reconnecting': '🟡', 'standby': '⚪', 'disabled': '⏸',
            'error': '🟠', 'revoked': '🔴'}

def ta_view():
    back = [{'text': '⬅️ Back', 'callback_data': 'm'}]
    if not TG_ON:
        why = tgweb.reason if tgweb else 'module नाही'
        return (f'👤 <b>Telegram Accounts</b>\n\n❌ बंद आहे: <b>{esc(why)}</b>\n\n'
                'Render env मध्ये <code>SESSION_KEY</code>, <code>MONGODB_URI</code> '
                '(आणि <code>TG_API_ID</code>/<code>TG_API_HASH</code>) टाका.'), {'inline_keyboard': [back]}
    accs = tgweb.M.accounts_view()
    lines = ['👤 <b>Telegram Accounts</b>', '']
    rows = []
    for a in accs:
        lines.append(f"{TG_EMOJI.get(a['state'], '❔')} <b>{esc(a['label'] or a['name'])}</b> — {a['state']}")
        rows.append([{'text': f"{TG_EMOJI.get(a['state'], '❔')} {a['label'] or a['name']}", 'callback_data': f"tp:{a['id']}"}])
    if not accs:
        lines.append('अजून कोणताही account नाही. UI उघडून ＋ दाबा.')
    st = tgweb.bw_status()
    lines += ['', f"📊 {st['used'] / 1048576:.1f} MB" + (f" / {st['limit'] / 1073741824:.1f} GB" if st['limit'] else ' (no limit)') + f" ({st['month']})"]
    rows.append([{'text': '🌐 UI उघडा (एकदाचा link)', 'callback_data': 'tg'}])
    rows.append(back)
    return '\n'.join(lines), {'inline_keyboard': rows}

def tp_view(aid):
    a = next((x for x in tgweb.M.accounts_view() if x['id'] == aid), None)
    if not a:
        return None, None
    t = (f"{TG_EMOJI.get(a['state'], '❔')} <b>{esc(a['label'] or a['name'])}</b>  <code>{aid}</code>\n"
         f"नाव: {esc(a['name'])} · {esc(a['phone'])}\nस्थिती: {a['state']}"
         f"{(' — ' + esc(a['err'])) if a['err'] else ''}")
    kb = {'inline_keyboard': [
        [{'text': '⏸ Disconnect' if a['enabled'] else '▶️ Connect', 'callback_data': f'tc:{aid}'}],
        [{'text': '🗑 काढा', 'callback_data': f'tr:{aid}'}],
        [{'text': '⬅️ Back', 'callback_data': 'ta'}]]}
    return t, kb

def sb_view():
    back = [{'text': '⬅️ Back', 'callback_data': 'm'}]
    if STORAGE is None:
        return '📦 <b>Storage</b>\n\n❌ बंद आहे.', {'inline_keyboard': [back]}
    try:
        txt = STORAGE.status_text()
    except Exception as e:
        txt = f'status मिळाला नाही ({type(e).__name__})'
    rows = [[{'text': '🔓 Website login link (एकदाचा)', 'callback_data': 'sl'}],
            [{'text': '👤 Telegram user account login', 'callback_data': 'ul:begin'}],
            [{'text': '🔄 Refresh', 'callback_data': 'sb'}],
            back]
    return ('📦 <b>Storage — Backup Browser</b>\n\n' + esc(txt) +
            '\n\nCommands: /userlogin /userlogout /usercancel /unblock /status'), {'inline_keyboard': rows}

def admins_view(me):
    lines, rows = ['👥 <b>Admins</b>', ''], []
    for a in sorted(AUTH.listing(), key=lambda a: a['since']):
        you = ' <i>(तुम्ही)</i>' if a['uid'] == me else ''
        if a['env']:
            lines.append(f"📌 <code>{a['uid']}</code> — कायमचा (env){you}")
        else:
            lines.append(f"👤 <b>{esc(a['name'])}</b> <code>{a['uid']}</code>{you}\n    login {fmt_ago(a['since'])} · शेवटचा {fmt_ago(a['last'])}")
            if a['uid'] != me:
                rows.append([{'text': f"❌ {a['name'][:24]} काढा", 'callback_data': f"ar:{a['uid']}"}])
    rows.append([{'text': '🚪 Logout (मी)', 'callback_data': 'al'}])
    rows.append([{'text': '🔒 बाकी सगळे logout', 'callback_data': 'aa'}])
    rows.append([{'text': '⬅️ Back', 'callback_data': 'm'}])
    return '\n'.join(lines), {'inline_keyboard': rows}

def handle_update(u):
    if 'callback_query' in u:
        cb = u['callback_query']
        uid = cb['from']['id']
        if not AUTH.is_admin(uid):
            return                               # अनोळखी → काहीच उत्तर नाही
        AUTH.touch(uid)
        if STORAGE is not None and STORAGE.bot_api_claims(u):
            if not STORAGE._tg_ready.is_set():     # Pyrogram side still starting (old instance stopping) -> say so
                tg('answerCallbackQuery', callback_query_id=cb['id'], show_alert='true',
                   text='⏳ Telegram client अजून सुरू होतोय (redeploy नंतर 1–2 मिनिटं). थोड्या वेळाने पुन्हा दाबा.')
            return                               # storage (Pyrogram side) answers this button
        msg = cb.get('message') or {}
        on_callback(cb['id'], uid, msg.get('chat', {}).get('id'), msg.get('message_id'), cb.get('data', ''))
    elif 'message' in u:
        m = u['message']
        if m.get('chat', {}).get('type') != 'private' or not (m.get('from') or {}).get('id'):
            return
        uid = m['from']['id']
        if AUTH.is_admin(uid):
            AUTH.touch(uid)
            if STORAGE is not None and STORAGE.bot_api_claims(u):
                if not STORAGE._tg_ready.is_set():
                    tg('sendMessage', chat_id=m['chat']['id'],
                       text='⏳ Telegram client अजून सुरू होतोय (redeploy नंतर 1–2 मिनिटं). थोड्या वेळाने पुन्हा पाठवा.')
                return                           # storage command / user-login conversation
            on_message(uid, m['chat']['id'], (m.get('text') or '').strip(), m.get('message_id'))
        else:
            AUTH.handle_stranger(m)

BOT_ME = {}
CONFLICT = [0]          # number of consecutive 409s (another instance polling the same token)

def _bot_label():
    u = BOT_ME.get('username')
    return f"@{u}" if u else '(bot)'

def startup_notice():
    """Every (re)deploy: tell the admins which bot/server just came up, so you know which bot is live.
    If another instance is still polling the same token, warn about it in Telegram itself."""
    import socket
    ist = time.strftime('%d %b %H:%M:%S', time.gmtime(time.time() + 19800))
    inst = os.environ.get('KOYEB_SERVICE_NAME') or os.environ.get('RENDER_SERVICE_NAME') or socket.gethostname()
    time.sleep(2)
    notify(f"🟢 <b>{esc(_bot_label())}</b> सुरू झाला\n"
           f"🕒 {ist} IST\n🖥 <code>{esc(inst)}</code>\n🌐 {esc(os.environ.get('WEB_URL', '-'))}\n"
           f"👥 Admins: {len(AUTH.admin_ids())} · PCs: {len(PCS)}")
    time.sleep(75)
    if CONFLICT[0] >= 3:
        notify(f"⚠️ <b>{esc(_bot_label())}</b>: दुसरा instance त्याच token वर चालू आहे ({CONFLICT[0]}× 409).\n"
               "जुना Render/Koyeb service बंद करा किंवा BotFather मध्ये /revoke करून नवा token वापरा. "
               "तोपर्यंत commands नीट चालणार नाहीत.")

def shutdown_notice():
    """Best-effort message when the platform stops this instance (redeploy / restart)."""
    ist = time.strftime('%H:%M:%S', time.gmtime(time.time() + 19800))
    for chat in list(AUTH.admin_ids()):
        tg('sendMessage', chat_id=chat, parse_mode='HTML',
           text=f"🔴 <b>{esc(_bot_label())}</b> बंद होतोय ({ist} IST) — redeploy/restart.")

from concurrent.futures import ThreadPoolExecutor
_POOL = ThreadPoolExecutor(max_workers=12, thread_name_prefix='upd')

def _dump_stack(tid, label):
    """Called by a watchdog when an update is still running after 6s: prints WHERE it is stuck."""
    import sys, traceback
    fr = sys._current_frames().get(tid)
    if fr:
        print(f"[hub] ⏳ {label} अजून चालू आहे (6s+) — अडकलेली जागा:\n" + ''.join(traceback.format_stack(fr)[-8:]), flush=True)

def _run_update(u):
    """One update, in its own worker thread: a slow handler can no longer freeze the polling loop,
    and updates do NOT queue behind each other any more."""
    who = ((u.get('message') or {}).get('from') or (u.get('callback_query') or {}).get('from') or {}).get('id', 0)
    kind = 'cb' if 'callback_query' in u else ('msg' if 'message' in u else '?')
    label = f"{kind} from {who}" + (f" data={u['callback_query'].get('data')}" if kind == 'cb' else '')
    t0 = time.time()
    wd = threading.Timer(6, _dump_stack, args=(threading.get_ident(), label))
    wd.daemon = True
    wd.start()
    try:
        handle_update(u)
    except Exception as e:
        print('update error:', repr(e), flush=True)
    finally:
        wd.cancel()
    dt = time.time() - t0
    print(f"[hub] {label} handled in {dt:.1f}s" + (" ⚠️ SLOW" if dt > 5 else ''), flush=True)

def bot_loop():
    offset, cleared, last409, n409 = 0, False, 0, 0
    me = tg('getMe').get('result') or {}
    BOT_ME.update(me)
    print(f"[hub] polling as @{me.get('username', '?')} (id {me.get('id', '?')})", flush=True)
    threading.Thread(target=startup_notice, daemon=True, name='startup_notice').start()
    while True:
        if not cleared:                                   # retry until the webhook is really gone
            cleared = bool(tg('deleteWebhook').get('ok'))
            if not cleared:
                time.sleep(5)
                continue
        r = tg('getUpdates', offset=offset, timeout=30, allowed_updates=['message', 'callback_query'])
        if not r.get('ok'):
            if r.get('error_code') == 409:
                if 'webhook' in str(r.get('description', '')).lower():
                    cleared = False
                else:
                    n409 += 1
                    CONFLICT[0] = n409
                    if time.time() - last409 > 30:
                        last409 = time.time()
                        print(f'⚠️ 409 (x{n409}): दुसरा instance त्याच BOT_TOKEN वर getUpdates चालवतोय — '
                              'जुना Render/Koyeb service बंद करा, किंवा BotFather मध्ये /revoke करून नवा token वापरा. '
                              'तोपर्यंत bot चे messages दोन instance मध्ये वाटले जातात (commands उत्तर देत नाहीत).', flush=True)
                time.sleep(3)
            else:
                time.sleep(5)
            continue
        CONFLICT[0] = 0
        if n409:
            print(f'✅ getUpdates ठीक झालं ({n409} वेळा 409 आला होता)', flush=True)
            n409 = 0
        for u in r['result']:
            offset = u['update_id'] + 1
            _POOL.submit(_run_update, u)

def monitor_loop():
    while True:
        time.sleep(15)
        for pcid, pc in list(PCS.items()):
            if not online(pc) and not pc['offline']:
                pc['offline'] = True
                notify(f"🔴 <b>{esc(pc['name'])}</b> offline झाला")


# ───────────────────────── public (सगळं 404 / token-protected) ─────────────────────────
# Health check / heartbeat: फक्त "ok" — यातून काहीही माहिती उघड होत नाही.
@app.get('/')
@app.get('/health')
@app.get('/healthz')
def health():
    return text_resp('ok')

def use_token(t):
    throttle_check()
    ok = False
    with LOCK:
        now = time.time()
        for k in [k for k, v in TOKENS.items() if v['exp'] < now or v['left'] <= 0]:
            del TOKENS[k]
        for k, v in TOKENS.items():
            if eq(k, t):
                v['left'] -= 1
                ok = True
    if not ok:
        bad_attempt()

@app.get('/i/<token>')
def install_script(token):
    use_token(token)
    src = (FILES / 'install.ps1').read_text(encoding='utf-8')
    root = root_url()
    return text_resp(src.replace('__BASE__', f'{root}/f/{token}').replace('__SELF__', f'{root}/i/{token}'))

@app.get('/f/<token>/<name>')
def get_file(token, name):
    use_token(token)
    if name == 'env':
        pcid = secrets.token_hex(4)
        lines = [f'COLAB_SECRET={COLAB_SECRET}', f'PANEL_PASSWORD={PANEL_PASSWORD}', 'PANEL_PORT=8765',
                 f'REPORT_BASE={root_url()}', f'PC_ID={pcid}', f'PC_KEY={agent_key(pcid)}']
        if KOYEB_DEFAULT[0]:
            lines.append(f'KOYEB_URL={KOYEB_DEFAULT[0]}')
        return text_resp('\n'.join(lines) + '\n')
    if name in ('watermark.py', 'worker.py'):
        f = FILES / name
        if not f.exists():
            return text_resp(f'# {name} server वर ठेवलेला नाही\n'), 500
        return text_resp(f.read_text(encoding='utf-8'))
    if name == 'backup.ps1':
        return _serve_backup_script(f'{root_url()}/f/{token}/backup.ps1')
    abort(404)


def _serve_backup_script(self_url):
    """backup.ps1 serve करतो — __BASE__, __SELF__ आणि __TOKEN__ आपोआप fill होतात.
    BACKUP_TOKEN फक्त server च्या env मध्ये असतो; तो script मध्ये फक्त valid, कमी वेळाच्या
    link वरच जातो (URL मध्ये कधीच दिसत नाही)."""
    f = FILES / 'backup.ps1'
    if not f.exists():
        return text_resp('# backup.ps1 server वर ठेवलेला नाही\n'), 500
    backup_token = os.environ.get('BACKUP_TOKEN', '').strip()
    if not backup_token:
        return text_resp('# BACKUP_TOKEN env var set केलेला नाही — server वर set करा.\n'), 503
    src = f.read_text(encoding='utf-8')
    src = src.replace('__BASE__', root_url()).replace('__SELF__', self_url).replace('__TOKEN__', backup_token)
    return text_resp(src)


@app.get('/b/<token>')
def backup_script_short(token):
    """Short link made by the bot button '💾 PC Backup command':  irm https://server/b/<token> | iex"""
    use_token(token)
    return _serve_backup_script(f'{root_url()}/b/{token}')


@app.get('/backup.ps1')
def backup_script_direct():
    """Old long URL (?t=BACKUP_TOKEN) — kept only so existing commands keep working."""
    throttle_check()
    t = request.args.get('t', '')
    backup_token = os.environ.get('BACKUP_TOKEN', '').strip()
    if not backup_token or not t:
        abort(404)
    import hmac as _hmac
    if not _hmac.compare_digest(t.encode(), backup_token.encode()):
        bad_attempt()
    return _serve_backup_script(f'{root_url()}/backup.ps1?t={t}')


# ───────────────────────── PC ↔ server (long-poll) ─────────────────────────
@app.post('/api/sync')
def api_sync():
    throttle_check()
    pcid = request.headers.get('X-PC-Id', '')
    if not re.fullmatch(r'[0-9a-f]{8}', pcid) or not eq(request.headers.get('X-PC-Key', ''), agent_key(pcid)):
        bad_attempt()
    d = request.get_json(silent=True) or {}
    url = str(d.get('url', ''))
    url = url if url.startswith('https://') and len(url) < 200 else ''
    name = str(d.get('host', '?'))[:40] or '?'
    with LOCK:
        pc = PCS.get(pcid)
        new = pc is None
        if new:
            pc = PCS[pcid] = {'ev': threading.Event(), 'cmds': [], 'offline': False, 'url': '', 'name': name, 'st': {}}
        old_url, was_off = pc['url'], pc['offline']
        pc.update(st=d, ts=time.time(), name=name, url=url, offline=False)
    kb_pc = {'inline_keyboard': [[{'text': '📋 Menu', 'callback_data': f'p:{pcid}'}]]}
    if new and time.time() - BOOT > 90:
        notify(f'🆕 <b>{esc(name)}</b> जोडला गेला', kb_pc)
    elif was_off:
        notify(f'🟢 <b>{esc(name)}</b> परत online', kb_pc)
    elif url and url != old_url and not new:
        notify(f'🔗 <b>{esc(name)}</b> चा terminal URL बदलला', kb_pc)
    for r in (d.get('results') or [])[:10]:
        body = str(r.get('text', ''))[-3500:]
        rid  = str(r.get('id', ''))
        # shell command output → store in _SHELL_OUT (browser polls this)
        if rid.startswith('sh:'):
            if body:                              # may be empty for ptoken / housekeeping cmds
                with _SHELL_LOCK:
                    q = _SHELL_OUT.setdefault(pcid, __import__('collections').deque(maxlen=_SHELL_OUT_MAX))
                    q.append({'id': rid, 'text': body, 'ts': time.time()})
        else:
            if not body.strip():
                continue
            notify(f'🖥 <b>{esc(name)}</b>\n<pre>{esc(body)}</pre>')
    with LOCK:
        if not pc['cmds']:
            pc['ev'].clear()
        has = bool(pc['cmds'])
    if not has:
        pc['ev'].wait(SYNC_WAIT)
    with LOCK:
        cmds, pc['cmds'] = pc['cmds'][:], []
        pc['ts'] = time.time()
    return jsonify(cmds=cmds)


# ───────────────────────── PC browser terminal ──────────────────────────────

_PC_TERM_PAGE = r"""<!doctype html>
<html lang="mr"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>PC Terminal</title>
<link rel="stylesheet" href="/term/assets/xterm.css">
<style>
  body{margin:0;background:#1a1a2e;color:#eee;font-family:sans-serif;display:flex;flex-direction:column;height:100vh}
  #hdr{background:#16213e;padding:8px 14px;display:flex;align-items:center;gap:10px;flex-shrink:0}
  #hdr h2{margin:0;font-size:14px;flex:1}
  #dot{width:10px;height:10px;border-radius:50%;background:#888;flex-shrink:0}
  #dot.on{background:#4caf50} #dot.off{background:#e53935}
  #xterm-wrap{flex:1;overflow:hidden;padding:4px}
  #inp-row{display:flex;gap:6px;padding:8px;background:#16213e;flex-shrink:0}
  #sh-sel{padding:4px 6px;background:#0f3460;color:#eee;border:1px solid #444;border-radius:4px}
  #cmd-in{flex:1;padding:6px 10px;background:#0f3460;color:#eee;border:1px solid #444;border-radius:4px;font-family:monospace}
  #run-btn{padding:6px 14px;background:#4caf50;color:#fff;border:none;border-radius:4px;cursor:pointer}
  #run-btn:hover{background:#388e3c}
</style>
</head><body>
<div id="hdr">
  <div id="dot"></div>
  <h2 id="pc-name">PC Terminal</h2>
  <span id="st-txt" style="font-size:12px;color:#aaa"></span>
</div>
<div id="xterm-wrap"></div>
<div id="inp-row">
  <select id="sh-sel"><option value="cmd">cmd</option><option value="powershell">powershell</option></select>
  <input id="cmd-in" placeholder="command टाका... (Enter)" autocomplete="off" spellcheck="false">
  <button id="run-btn">▶ Run</button>
</div>
<script src="/term/assets/xterm.js"></script>
<script src="/term/assets/addon-fit.js"></script>
<script>
const PCID = location.pathname.split('/').pop();
const term = new Terminal({theme:{background:'#1a1a2e',foreground:'#eee'},cursorBlink:true,scrollback:5000,fontSize:13});
const fit  = new FitAddon.FitAddon();
term.loadAddon(fit);
term.open(document.getElementById('xterm-wrap'));
fit.fit();
window.addEventListener('resize', ()=>fit.fit());

let since = 0, polling = false;
const dot = document.getElementById('dot');
const stTxt = document.getElementById('st-txt');

async function poll(){
  if(polling) return; polling=true;
  try{
    const r = await fetch(`/app/api/pc/${PCID}/term/poll?since=${since}`);
    if(!r.ok){polling=false;return;}
    const d = await r.json();
    if(d.name) document.getElementById('pc-name').textContent = d.name + ' — Terminal';
    dot.className = 'dot ' + (d.online ? 'on' : 'off');
    stTxt.textContent = d.online ? 'online' : 'offline';
    for(const e of (d.lines||[])){
      term.writeln(e.text.replace(/\r?\n/g,'\r\n'));
      since = Math.max(since, e.seq+1);
    }
  }catch(e){}
  polling=false;
}
setInterval(poll, 1000);
poll();

async function runCmd(){
  const cmd = document.getElementById('cmd-in').value.trim();
  if(!cmd) return;
  const sh  = document.getElementById('sh-sel').value;
  document.getElementById('cmd-in').value='';
  term.writeln('\x1b[33m$ ' + cmd + '\x1b[0m');
  try{
    const r = await fetch(`/app/api/pc/${PCID}/term/run`,{
      method:'POST', headers:{'Content-Type':'application/json'},
      body:JSON.stringify({cmd,shell:sh})});
    const d = await r.json();
    if(!d.ok) term.writeln('\x1b[31m' + (d.msg||'error') + '\x1b[0m');
  }catch(e){ term.writeln('\x1b[31m'+e+'\x1b[0m'); }
}
document.getElementById('run-btn').onclick = runCmd;
document.getElementById('cmd-in').addEventListener('keydown', e=>{if(e.key==='Enter')runCmd();});
term.writeln('\x1b[36mPC Terminal — commands PC वर चालतात, output इथे दिसतो\x1b[0m');
term.writeln('\x1b[90mPC online नसेल तर commands queue होतात, PC connect झाला की चालतात.\x1b[0m');
</script>
</body></html>
"""

def _pc_term_auth():
    """Hub च्या cookie session ने check करतो — admin logged in असायला हवा."""
    from flask import session
    if not session.get('a'):
        from flask import abort
        abort(403)

@app.get('/app/pc/<pcid>/term')
def pc_term_page(pcid):
    _pc_term_auth()
    if not re.fullmatch(r'[0-9a-f]{8}', pcid):
        abort(404)
    return _PC_TERM_PAGE, 200, {'Content-Type': 'text/html; charset=utf-8'}

@app.post('/app/api/pc/<pcid>/term/run')
def pc_term_run(pcid):
    _pc_term_auth()
    if not re.fullmatch(r'[0-9a-f]{8}', pcid):
        return jsonify(ok=False, msg='bad pcid'), 400
    d = request.get_json(silent=True) or {}
    cmd   = str(d.get('cmd', '')).strip()[:2000]
    shell = d.get('shell', 'cmd')
    if shell not in ('cmd', 'powershell'):
        shell = 'cmd'
    if not cmd:
        return jsonify(ok=False, msg='command empty')
    cid = 'sh:' + secrets.token_hex(4)
    ok = enqueue(pcid, 'shell', f'{shell}\x00{cmd}', cmd_id=cid)
    if not ok:
        return jsonify(ok=False, msg='PC offline किंवा सापडला नाही')
    return jsonify(ok=True, id=cid)

_SHELL_SEQ = {}   # pcid -> int counter

@app.get('/app/api/pc/<pcid>/term/poll')
def pc_term_poll(pcid):
    _pc_term_auth()
    if not re.fullmatch(r'[0-9a-f]{8}', pcid):
        return jsonify(ok=False), 400
    try:
        since = int(request.args.get('since', 0))
    except ValueError:
        since = 0
    with LOCK:
        pc = PCS.get(pcid)
    online = pc is not None and (time.time() - pc.get('ts', 0)) < 45
    name   = pc['name'] if pc else pcid
    with _SHELL_LOCK:
        raw = list(_SHELL_OUT.get(pcid) or [])
    # assign stable seq numbers (monotone, survive deque rotation)
    with _SHELL_LOCK:
        seq_map = _SHELL_SEQ.setdefault(pcid, {})
        next_seq = max(seq_map.values(), default=-1) + 1 if seq_map else 0
        lines = []
        for entry in raw:
            eid = entry['id']
            if eid not in seq_map:
                seq_map[eid] = next_seq
                next_seq += 1
            if seq_map[eid] >= since:
                lines.append({'seq': seq_map[eid], 'text': entry['text']})
    return jsonify(ok=True, online=online, name=name, lines=lines)


# ───────────────────────── start background threads ─────────────────────────
_started = False
def start_background():
    global _started
    if _started:
        return
    _started = True
    threading.Thread(target=bot_loop, daemon=True).start()
    threading.Thread(target=monitor_loop, daemon=True).start()
    if TG_ON:
        tgweb.start()

if __name__ == '__main__':                       # standalone (without Backup Browser)
    start_background()
    app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 10000)), threaded=True)
