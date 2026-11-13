"""
tgweb.py — Render/Koyeb वर अनेक Telegram accounts चालवणारा web client (backend).

• Account login server वर (Telethon) — Phone OTP + 2FA आणि QR Code login.
• Session string Fernet ने encrypt होऊन MongoDB मध्ये.
• UI: /app (tgui.py) — फक्त bot ने दिलेल्या एकदाच चालणाऱ्या link ने; बाकी सगळ्यांना रिकामा 404.
• Multi-Threaded Parallel Downloader:
  - फाइल कितीही मोठी असली तरी (2 GB / 4 GB+ अमर्यादित) अनेक समांतर threads/workers (default 8) वापरून
    Telegram वरून अतिवेगाने server वर download होते.
  - Browser मध्ये कोणतीही video / media / file उघडली किंवा Download दाबलं की ती आपोआप server वर
    multi-thread वेगाने download होते आणि server वरून थेट (progressive streaming + 1 MB disk buffer)
    युजरच्या browser मध्ये जाते.
  - ठरलेल्या वेळेनंतर (Fixed Time TTL, default 60 मिनिटं — Settings मधून बदलता येतो) server वरच्या
    cached फाइल्स आपोआप साफ (auto-clear) होतात.
"""
import os, io, re, time, json, uuid, atexit, shutil, asyncio, tempfile, threading, secrets, collections, urllib.parse
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from flask import Blueprint, request, session, jsonify, Response, abort

try:
    from cryptography.fernet import Fernet
except Exception:                                   # pragma: no cover
    Fernet = None
try:
    import cryptg as _cryptg  # noqa: F401
except Exception:
    try:
        import sys as _sys, types as _pytypes, tgcrypto as _tgcrypto
        _cg = _pytypes.ModuleType("cryptg")
        _cg.encrypt_ige = lambda plain, key, iv: _tgcrypto.ige256_encrypt(bytes(plain), bytes(key), bytes(bytearray(iv)))
        _cg.decrypt_ige = lambda cipher, key, iv: _tgcrypto.ige256_decrypt(bytes(cipher), bytes(key), bytes(bytearray(iv)))
        _sys.modules["cryptg"] = _cg
    except Exception:
        pass
try:
    from telethon import TelegramClient, events, functions, types, utils
    from telethon.sessions import StringSession
except Exception:                                   # pragma: no cover
    TelegramClient = events = functions = types = utils = StringSession = None
try:
    from tgui import UI_PAGE, ENTER_PAGE
except Exception:                                   # pragma: no cover
    UI_PAGE = ENTER_PAGE = '<!doctype html>UI missing'

SESSION_KEY = os.environ.get('SESSION_KEY', '').strip()
DEF_API_ID = os.environ.get('TG_API_ID', '').strip()
DEF_API_HASH = os.environ.get('TG_API_HASH', '').strip()
MB = 1024 * 1024
# Limits are OFF by default (0 = unlimited). Set ENFORCE_LIMITS=1 to bring BW_LIMIT_GB / MAX_ACCOUNTS back.
LIMITS_ON = os.environ.get('ENFORCE_LIMITS', '0').strip() == '1'
BW_LIMIT = int(float(os.environ.get('BW_LIMIT_GB', '4')) * 1024 ** 3) if LIMITS_ON else 0
MAX_ACCOUNTS = int(os.environ.get('MAX_ACCOUNTS', '6')) if LIMITS_ON else 0
TG_LIMIT, TG_LIMIT_PREMIUM = 2000 * MB, 4000 * MB          # Telegram ची खरी upload मर्यादा
MAX_UPLOAD = int(float(os.environ.get('MAX_UPLOAD_MB', '4000')) * MB)
# Unlimited media download size by default (0 = no cap, any size file can be downloaded/streamed)
MAX_MEDIA = int(float(os.environ.get('MAX_MEDIA_MB', '0')) * MB) if LIMITS_ON else 0
UI_SESSION_SECS = 86400 * 30
LEASE_SECS = 45
CHUNK = 8 * MB                                              # browser → server upload तुकडा
REQ = 512 * 1024                                            # Telegram MTProto 512 KB chunk
DISK_READ_BUF = 1024 * 1024                                 # 1 MB fast server → browser read buffer
UPDIR = os.path.join(tempfile.gettempdir(), 'tgup')
CACHE_DIR = os.path.join(tempfile.gettempdir(), 'tgcache')  # Telegram → server disk (अर्धवट = .part)
DISK_CACHE = int(float(os.environ.get('DISK_CACHE_GB', '20')) * 1024 ** 3)

# Multi-thread & Fixed-Time Auto-Clear settings (configurable via env & live from UI)
DL_CFG = {
    'threads': max(1, min(8, int(os.environ.get('DL_THREADS', '4') or 4))),
    'ttl': max(60, int(float(os.environ.get('CACHE_TTL_MINUTES', '60') or 60) * 60)),  # seconds before auto-clear
    'auto_cache': os.environ.get('AUTO_SERVER_CACHE', '1').strip() != '0',
    'anti_delete': True,   # Ghost Mode: keep deleted messages visible instead of erasing them
}
SHARE_LINKS, SHARE_LOCK = {}, threading.Lock()   # token -> {acc, chat, id, exp, name}
AUTO_REPLY = {}                                  # aid -> {'enabled': False, 'text': '', 'only_private': True, 'sent_to': {}}
MSG_RECENT = collections.OrderedDict()           # (aid, chat, mid) -> msg_dict (last 3000 messages for Anti-Delete Vault)
DELETED_VAULT = collections.deque(maxlen=100)    # recently deleted messages captured by Ghost Mode
PWA_LOGS = collections.deque(maxlen=50)          # live log of manifest/icon/SW requests (including Google WebAPK builder)

M = None
reason = ''
TOKENS, TOKENS_LOCK = {}, threading.Lock()
FAILS = collections.defaultdict(list)
BW = {'month': '', 'bytes': 0, 'dirty': False}
BW_LOCK = threading.Lock()
UP, UP_LOCK = {}, threading.Lock()
DL, DL_LOCK = {}, threading.Lock()          # चालू/झालेले server-download jobs
CACHE_META, CACHE_META_LOCK = {}, threading.Lock()  # key -> {name, mime, size, created_at, acc, chat, id}
STREAMS = threading.Semaphore(32)           # allow many concurrent Range streams (seeking, parallel browser connections)
_DISK_POOL = ThreadPoolExecutor(max_workers=16, thread_name_prefix='tg-disk')
_F = [None]


class ApiError(Exception):
    def __init__(self, msg, code=400):
        super().__init__(msg)
        self.code = code


def _pwrite(fd, data, offset):
    """Thread-safe positional write without mutating shared file pointer."""
    if hasattr(os, 'pwrite'):
        view = memoryview(data)
        written = 0
        while written < len(view):
            n = os.pwrite(fd, view[written:], offset + written)
            if n <= 0:
                raise IOError('disk write failed')
            written += n
    else:
        with DL_LOCK:
            os.lseek(fd, offset, os.SEEK_SET)
            os.write(fd, data)


# ───────────────────────── crypto / bandwidth ─────────────────────────
def _fernet():
    if _F[0] is None:
        _F[0] = Fernet(SESSION_KEY.encode())
    return _F[0]

def enc(s):
    return _fernet().encrypt(str(s).encode()).decode()

def dec(t):
    return _fernet().decrypt(t.encode()).decode()

def _month():
    return time.strftime('%Y-%m')

def bw_add(n):
    with BW_LOCK:
        if BW['month'] != _month():
            BW.update(month=_month(), bytes=0)
        BW['bytes'] += int(n)
        BW['dirty'] = True

def bw_status():
    with BW_LOCK:
        if BW['month'] != _month():
            BW.update(month=_month(), bytes=0)
        return {'used': BW['bytes'], 'limit': BW_LIMIT, 'month': BW['month']}


# ───────────────────────── serialization ─────────────────────────
def _t(x):
    return x if isinstance(x, str) else (getattr(x, 'text', '') or '')

def _name(e):
    if e is None:
        return ''
    t = getattr(e, 'title', None)
    if t:
        return str(t)
    return ' '.join(str(x) for x in (getattr(e, 'first_name', None), getattr(e, 'last_name', None)) if x) \
        or str(getattr(e, 'username', '') or '')

def _ts(d):
    if d is None:
        return 0
    return int(d.timestamp()) if hasattr(d, 'timestamp') else int(d)

def media_info(m):
    if not getattr(m, 'media', None):
        return None
    f = getattr(m, 'file', None)
    kind = 'other'
    for attr, k in (('photo', 'photo'), ('sticker', 'sticker'), ('voice', 'voice'), ('video_note', 'videonote'),
                    ('gif', 'gif'), ('video', 'video'), ('audio', 'audio'), ('document', 'file')):
        if getattr(m, attr, None):
            kind = k
            break
    return {'kind': kind, 'size': int(getattr(f, 'size', 0) or 0), 'name': str(getattr(f, 'name', '') or ''),
            'mime': str(getattr(f, 'mime_type', '') or ''), 'dur': int(getattr(f, 'duration', 0) or 0),
            'w': int(getattr(f, 'width', 0) or 0), 'h': int(getattr(f, 'height', 0) or 0),
            'emoji': str(getattr(f, 'emoji', '') or '')}

def ser_entities(m):
    out = []
    for e in (getattr(m, 'entities', None) or []):
        t = type(e).__name__.replace('MessageEntity', '').lower()
        d = {'t': t, 'o': int(e.offset), 'l': int(e.length)}
        if t == 'texturl':
            d['u'] = getattr(e, 'url', '')
        out.append(d)
    return out

def ser_reactions(m):
    r = getattr(m, 'reactions', None)
    if not r or not getattr(r, 'results', None):
        return None
    out = []
    for x in r.results:
        em = getattr(x.reaction, 'emoticon', None)
        if em:
            out.append({'e': em, 'n': int(x.count), 'me': getattr(x, 'chosen_order', None) is not None})
    return out or None

def ser_poll(m):
    mp = getattr(m, 'poll', None)
    if not mp:
        return None
    p, res = mp.poll, getattr(mp, 'results', None)
    rmap = {}
    for r in (getattr(res, 'results', None) or []):
        rmap[bytes(r.option)] = r
    answers = []
    for i, a in enumerate(p.answers):
        r = rmap.get(bytes(a.option))
        answers.append({'i': i, 't': _t(a.text), 'n': int(r.voters) if r else 0,
                        'me': bool(r and r.chosen), 'ok': bool(r and getattr(r, 'correct', False))})
    return {'q': _t(p.question), 'a': answers, 'closed': bool(p.closed), 'multi': bool(p.multiple_choice),
            'quiz': bool(p.quiz), 'public': bool(getattr(p, 'public_voters', False)),
            'solution': _t(getattr(res, 'solution', '')),
            'total': int(getattr(res, 'total_voters', 0) or 0)}

def ser_web(m):
    w = getattr(m, 'web_preview', None)
    if not w or not getattr(w, 'url', None):
        return None
    return {'url': w.url, 'title': _t(getattr(w, 'title', '')), 'desc': _t(getattr(w, 'description', ''))[:300],
            'site': _t(getattr(w, 'site_name', '')), 'photo': bool(getattr(w, 'photo', None))}

def ser_buttons(m):
    rm = getattr(m, 'reply_markup', None)
    if not rm or type(rm).__name__ != 'ReplyInlineMarkup':
        return None
    rows = []
    for row in (getattr(m, 'buttons', None) or []):
        rows.append([{'t': b.text, 'u': getattr(b, 'url', None) or None, 'cb': bool(getattr(b, 'data', None))} for b in row])
    return rows or None

ACTIONS = {'ChatAddUser': 'joined the group', 'ChatJoinedByLink': 'joined via invite link', 'ChatDeleteUser': 'left the group',
           'ChatEditTitle': 'changed the group name', 'ChatEditPhoto': 'changed the group photo', 'PinMessage': 'pinned a message',
           'ChatCreate': 'created the group', 'ChannelCreate': 'created the channel', 'ContactSignUp': 'joined Telegram',
           'HistoryClear': 'history cleared', 'PhoneCall': 'call', 'ChatJoinedByRequest': 'joined by request'}

def action_text(a):
    n = type(a).__name__.replace('MessageAction', '')
    return ACTIONS.get(n) or re.sub(r'(?<!^)(?=[A-Z])', ' ', n).lower()

def ser_msg(m):
    rt = getattr(m, 'reply_to', None)
    text = getattr(m, 'message', '') or ''
    service = bool(getattr(m, 'action', None))
    if service and not text:
        text = action_text(m.action)
    fw = getattr(m, 'forward', None)
    geo = getattr(m, 'geo', None)
    ct = getattr(m, 'contact', None)
    dice = getattr(m, 'dice', None)
    venue = getattr(m, 'venue', None)
    top_id = None
    if rt:
        if getattr(rt, 'forum_topic', False):
            top_id = getattr(rt, 'reply_to_top_id', None) or getattr(rt, 'reply_to_msg_id', None)
    return {'id': m.id, 'out': bool(getattr(m, 'out', False)), 'ts': _ts(getattr(m, 'date', None)), 'text': text,
            'service': service, 'from': _name(getattr(m, 'sender', None)), 'from_id': getattr(m, 'sender_id', None),
            'reply': getattr(rt, 'reply_to_msg_id', None) if rt else None,
            'topic': top_id,
            'edited': bool(getattr(m, 'edit_date', None)),
            'fwd': (getattr(fw, 'from_name', None) or _name(getattr(fw, 'sender', None)) or _name(getattr(fw, 'chat', None)) or 'Unknown') if fw else '',
            'via': _name(getattr(m, 'via_bot', None)), 'media': media_info(m), 'ents': ser_entities(m),
            'react': ser_reactions(m), 'poll': ser_poll(m), 'web': ser_web(m), 'btn': ser_buttons(m),
            'geo': {'lat': geo.lat, 'lon': geo.long, 'title': getattr(venue, 'title', '')} if geo else None,
            'contact': {'name': f"{getattr(ct, 'first_name', '')} {getattr(ct, 'last_name', '')}".strip(), 'phone': getattr(ct, 'phone_number', '')} if ct else None,
            'dice': {'e': dice.emoticon, 'v': dice.value} if dice else None,
            'grp': getattr(m, 'grouped_id', None), 'views': getattr(m, 'views', None), 'pinned': bool(getattr(m, 'pinned', False))}

def _muted(d):
    ns = getattr(getattr(d, 'dialog', None), 'notify_settings', None)
    mu = getattr(ns, 'mute_until', None)
    return bool(mu and _ts(mu) > time.time())

def ser_dialog(d):
    m = d.message
    last = (getattr(m, 'message', '') or '') if m else ''
    if m and not last and getattr(m, 'action', None):
        last = action_text(m.action)
    if m and not last and getattr(m, 'media', None):
        mi = media_info(m) or {}
        last = '📎 ' + (mi.get('emoji') or mi.get('name') or mi.get('kind', 'media'))
    ent, raw = getattr(d, 'entity', None), getattr(d, 'dialog', None)
    st = type(getattr(ent, 'status', None)).__name__
    kind = 'user' if d.is_user else ('group' if d.is_group else 'channel')
    return {'id': d.id, 'name': 'Saved Messages' if getattr(ent, 'is_self', False) else (d.name or ''),
            'username': str(getattr(ent, 'username', '') or ''),
            'self': bool(getattr(ent, 'is_self', False)), 'unread': d.unread_count or 0,
            'mentions': int(getattr(raw, 'unread_mentions_count', 0) or 0), 'last': last[:90], 'ts': _ts(getattr(m, 'date', None)) if m else 0,
            'type': kind, 'pinned': bool(getattr(d, 'pinned', False)), 'out': bool(getattr(m, 'out', False)) if m else False,
            'muted': _muted(d), 'bot': bool(getattr(ent, 'bot', False)), 'verified': bool(getattr(ent, 'verified', False)),
            'premium': bool(getattr(ent, 'premium', False)), 'forum': bool(getattr(ent, 'forum', False)),
            'online': st == 'UserStatusOnline', 'read_out': int(getattr(raw, 'read_outbox_max_id', 0) or 0),
            'read_in': int(getattr(raw, 'read_inbox_max_id', 0) or 0), 'archived': bool(getattr(d, 'archived', False)),
            'owner': bool(getattr(ent, 'creator', False)),
            'admin': bool(getattr(ent, 'creator', False)) or getattr(ent, 'admin_rights', None) is not None}

def status_text(ent):
    st = type(getattr(ent, 'status', None)).__name__
    if st == 'UserStatusOnline':
        return 'online'
    if st == 'UserStatusOffline':
        w = getattr(ent.status, 'was_online', None)
        return 'last seen ' + (datetime.fromtimestamp(_ts(w)).strftime('%d %b %H:%M') if w else 'recently')
    return {'UserStatusRecently': 'last seen recently', 'UserStatusLastWeek': 'last seen within a week',
            'UserStatusLastMonth': 'last seen within a month'}.get(st, '')

def _thumb_index(m):
    media = getattr(m, 'photo', None) or getattr(m, 'document', None) or getattr(getattr(m, 'web_preview', None), 'photo', None)
    sizes = getattr(media, 'sizes', None) or getattr(media, 'thumbs', None) or []
    return min(2, len(sizes) - 1) if len(sizes) else None


def default_factory(sess, api_id, api_hash):
    return TelegramClient(StringSession(sess or ''), int(api_id), api_hash, device_model='Desktop',
                          app_version='4.16.8 x64', system_version='Windows 10 x64', lang_code='en',
                          system_lang_code='en-US', connection_retries=None, retry_delay=3,
                          auto_reconnect=True, flood_sleep_threshold=120)

client_factory = default_factory

STORAGE = None            # storage_app module (plugged in by app.py) — the Backup Browser's user account

def STORAGE_UID():
    try:
        return (STORAGE.user_info() or {}).get('uid') if STORAGE else None
    except Exception:
        return None

REVOKED = {'AuthKeyUnregisteredError', 'SessionRevokedError', 'UserDeactivatedError',
           'UserDeactivatedBanError', 'AuthKeyDuplicatedError', 'SessionExpiredError', 'PermissionError'}

FILTERS = {'media': 'InputMessagesFilterPhotoVideo', 'files': 'InputMessagesFilterDocument', 'links': 'InputMessagesFilterUrl',
           'voice': 'InputMessagesFilterVoice', 'music': 'InputMessagesFilterMusic', 'gifs': 'InputMessagesFilterGif'}


def _lru_put(od, key, val, cap_items=300, cap_bytes=16 * MB):
    od[key] = val
    od.move_to_end(key)
    total = sum(len(v or b'') for v in od.values())
    while od and (len(od) > cap_items or total > cap_bytes):
        _, v = od.popitem(last=False)
        total -= len(v or b'')


# ───────────────────────── Server Disk Cache & Fixed-Time Auto-Clear ─────────────────────────
def _ckey(aid, chat, mid):
    return f'{aid}_{chat}_{mid}'.replace('-', 'm')

def _cpath(key):
    return os.path.join(CACHE_DIR, key)

def _mpath(key):
    return os.path.join(CACHE_DIR, key + '.meta')

def _save_cmeta(key, meta):
    with CACHE_META_LOCK:
        CACHE_META[key] = meta
    try:
        os.makedirs(CACHE_DIR, exist_ok=True)
        with open(_mpath(key), 'w', encoding='utf-8') as f:
            json.dump(meta, f)
    except Exception:
        pass

def _get_cmeta(key, path=None):
    with CACHE_META_LOCK:
        m = CACHE_META.get(key)
    if m:
        return m
    mp = _mpath(key)
    if os.path.isfile(mp):
        try:
            with open(mp, 'r', encoding='utf-8') as f:
                m = json.load(f)
            with CACHE_META_LOCK:
                CACHE_META[key] = m
            return m
        except Exception:
            pass
    p = path or _cpath(key)
    try:
        st = os.stat(p)
        m = {'key': key, 'name': key, 'size': st.st_size, 'created_at': st.st_mtime}
        with CACHE_META_LOCK:
            CACHE_META[key] = m
        return m
    except OSError:
        return {'key': key, 'name': key, 'size': 0, 'created_at': time.time()}

def _remove_cached(key):
    for p in (_cpath(key), _cpath(key) + '.part', _mpath(key)):
        try:
            if os.path.exists(p):
                os.remove(p)
        except OSError:
            pass
    with CACHE_META_LOCK:
        CACHE_META.pop(key, None)

def cache_files():
    """Returns (list of (created_at, size, path, key), total_part_bytes)."""
    done, part = [], 0
    try:
        for f in os.listdir(CACHE_DIR):
            if f.endswith('.meta'):
                continue
            p = os.path.join(CACHE_DIR, f)
            if not os.path.isfile(p):
                continue
            try:
                sz = os.path.getsize(p)
                if f.endswith('.part'):
                    part += sz
                else:
                    meta = _get_cmeta(f, p)
                    done.append((float(meta.get('created_at') or os.path.getmtime(p)), sz, p, f))
            except OSError:
                pass
    except FileNotFoundError:
        pass
    return done, part

def cache_usage():
    done, part = cache_files()
    return sum(s for _, s, _, _ in done) + part, len(done)

def cache_evict(need=0):
    """Deletes expired files first, then oldest files if disk space is needed."""
    done, part = cache_files()
    now = time.time()
    ttl = DL_CFG['ttl']
    total = sum(s for _, s, _, _ in done) + part
    # 1) Remove any files that have exceeded the fixed auto-clear TTL
    remaining = []
    for ctime, s, p, key in sorted(done):
        if ttl > 0 and now - ctime >= ttl:
            _remove_cached(key)
            total = max(0, total - s)
        else:
            remaining.append((ctime, s, p, key))
    # 2) If more room is needed, evict oldest files
    free_min = 180 * MB
    for ctime, s, p, key in remaining:
        try:
            free_disk = shutil.disk_usage(CACHE_DIR).free
        except Exception:
            free_disk = DISK_CACHE
        if (DISK_CACHE <= 0 or total + need <= DISK_CACHE) and free_disk >= need + free_min:
            break
        _remove_cached(key)
        total = max(0, total - s)

def cache_cleanup():
    """Runs every 30 seconds: enforces Fixed-Time Auto-Clear (TTL) and cleans stale .part files."""
    cache_evict(0)
    try:
        now = time.time()
        ttl = DL_CFG['ttl']
        for f in os.listdir(CACHE_DIR):
            p = os.path.join(CACHE_DIR, f)
            if f.endswith('.part'):
                key = f[:-5]
                with DL_LOCK:
                    j = DL.get(key)
                active = j and j.get('state') == 'downloading'
                if not active and now - os.path.getmtime(p) > max(1800, ttl):
                    _remove_cached(key)
    except FileNotFoundError:
        pass


# ───────────────────────── Manager ─────────────────────────
class Manager:
    def __init__(self, db, notify):
        self.db, self.notify = db, notify
        self.acc, self.meta = db['tgacc'], db['meta']
        self.loop = asyncio.new_event_loop()
        self.clients, self.state, self.next_try = {}, {}, {}
        self.logins, self.cache, self.msg_cache = {}, {}, {}
        self.events = collections.deque(maxlen=300)
        self.seq = 0
        self.cond = threading.Condition()
        self.lease = False
        self.last_ping = {}
        self.premium = {}
        self.instance = uuid.uuid4().hex[:8]
        self.avatars, self.thumbs = collections.OrderedDict(), collections.OrderedDict()
        self.up_tasks = {}
        self.dl_tasks = {}

    # --- thread / loop
    def start(self):
        try:
            d = self.meta.find_one({'_id': 'bw:' + _month()}) or {}
            with BW_LOCK:
                BW.update(month=_month(), bytes=int(d.get('bytes', 0)), dirty=False)
        except Exception as e:
            print('bw load failed:', e)
        atexit.register(self._release)
        threading.Thread(target=self._run, daemon=True, name='tg-loop').start()
        threading.Thread(target=self._ttl_reaper, daemon=True, name='tg-cache-reaper').start()

    def _ttl_reaper(self):
        """Dedicated fixed-time server cache cleaner running every 20 seconds."""
        while True:
            time.sleep(20)
            try:
                cache_cleanup()
            except Exception as e:
                print('cache_cleanup error:', e)

    def _run(self):
        asyncio.set_event_loop(self.loop)
        self.avatar_sem = asyncio.Semaphore(4)
        self.thumb_sem = asyncio.Semaphore(3)
        self.dl_sem = asyncio.Semaphore(6)
        self.loop.create_task(self.supervisor())
        self.loop.run_forever()

    def call(self, coro, timeout=60):
        if not self.loop.is_running():
            coro.close()
            raise ApiError('Telegram engine अजून सुरू झालं नाही — थोड्या वेळाने प्रयत्न करा', 503)
        return asyncio.run_coroutine_threadsafe(coro, self.loop).result(timeout)

    # --- lease
    def _lease(self):
        now = time.time()
        try:
            from pymongo import ReturnDocument
            r = self.meta.find_one_and_update(
                {'_id': 'lease', '$or': [{'owner': self.instance}, {'until': {'$lt': now}}]},
                {'$set': {'owner': self.instance, 'until': now + LEASE_SECS}},
                upsert=True, return_document=ReturnDocument.AFTER)
            if r and r.get('owner') == self.instance:
                self._lease_miss = 0
                return True
            return False
        except Exception as e:
            # DuplicateKeyError means another active owner holds {'_id': 'lease'} with until >= now
            if 'DuplicateKeyError' in type(e).__name__ or 'E11000' in str(e):
                self._lease_miss = getattr(self, '_lease_miss', 0) + 1
                return self._lease_miss < 2
            # Transient MongoDB Atlas network blip / timeout -> keep existing lease state so accounts are NOT stopped!
            return True if self.lease else False

    def _release(self):
        try:
            self.meta.update_one({'_id': 'lease', 'owner': self.instance}, {'$set': {'until': 0}})
        except Exception:
            pass

    def _docs(self):
        return sorted(self.acc.find({}), key=lambda d: d.get('created', 0))

    def _flush_bw(self):
        with BW_LOCK:
            if not BW['dirty']:
                return
            month, n = BW['month'], BW['bytes']
            BW['dirty'] = False
        try:
            self.meta.replace_one({'_id': 'bw:' + month}, {'_id': 'bw:' + month, 'bytes': n}, upsert=True)
        except Exception as e:
            print('bw flush failed:', e)

    def _cleanup_uploads(self):
        try:
            now = time.time()
            for d in os.listdir(UPDIR):
                p = os.path.join(UPDIR, d)
                if now - os.path.getmtime(p) > 6 * 3600:
                    shutil.rmtree(p, ignore_errors=True)
                    with UP_LOCK:
                        UP.pop(d, None)
        except FileNotFoundError:
            pass
        except Exception as e:
            print('upload cleanup:', e)
        try:
            cache_cleanup()
        except Exception as e:
            print('cache cleanup:', e)

    # --- supervisor
    async def supervisor(self):
        loop = asyncio.get_running_loop()
        last_bw = last_clean = 0
        while True:
            try:
                got = await loop.run_in_executor(None, self._lease)
                if not got and self.lease:
                    await self.stop_all()
                    self.notify('⚠️ Telegram accounts थांबवले — दुसरा instance चालू आहे (lease गेली).')
                self.lease = got
                if got:
                    await self.reconcile(await loop.run_in_executor(None, self._docs))
                self.expire_logins()
                if time.time() - last_bw > 30:
                    await loop.run_in_executor(None, self._flush_bw)
                    last_bw = time.time()
                if time.time() - last_clean > 60:
                    await loop.run_in_executor(None, self._cleanup_uploads)
                    last_clean = time.time()
            except Exception as e:
                print('supervisor error:', e)
            await asyncio.sleep(15)

    async def reconcile(self, docs):
        want = {d['_id']: d for d in docs if d.get('enabled', True)}
        for aid in [a for a in self.clients if a not in want]:
            await self.stop_client(aid)
        for aid, doc in want.items():
            if aid in self.clients:
                cl = self.clients[aid]
                if time.time() - self.last_ping.get(aid, 0) > 600:
                    self.last_ping[aid] = time.time()
                    try:
                        await asyncio.wait_for(cl.get_me(), 30)
                        self.state[aid] = {'state': 'online', 'err': ''}
                    except Exception as e:
                        await self._fail(aid, doc, e)
                elif not cl.is_connected():
                    self.state[aid] = {'state': 'reconnecting', 'err': ''}
                continue
            if self.state.get(aid, {}).get('state') == 'revoked' or time.time() < self.next_try.get(aid, 0):
                continue
            await self.start_client(doc)
        for d in docs:
            if not d.get('enabled', True):
                self.state[d['_id']] = {'state': 'disabled', 'err': ''}

    async def _fail(self, aid, doc, e):
        revoked = type(e).__name__ in REVOKED
        was = self.state.get(aid, {}).get('state')
        self.state[aid] = {'state': 'revoked' if revoked else 'error', 'err': f'{type(e).__name__}: {e}'[:140]}
        cl = self.clients.pop(aid, None)
        if cl:
            try:
                await cl.disconnect()
            except Exception:
                pass
        self.next_try[aid] = time.time() + 60
        if revoked and was != 'revoked':
            self.notify(f"🚨 Telegram account <b>{doc.get('label', aid)}</b> चं session रद्द झालं ({type(e).__name__}). "
                        f"UI मधून परत login करावं लागेल.")

    async def start_client(self, doc):
        aid, cl = doc['_id'], None
        self.state[aid] = {'state': 'connecting', 'err': ''}
        try:
            cl = client_factory(dec(doc['session_enc']), dec(doc['api_id_enc']), dec(doc['api_hash_enc']))
            await asyncio.wait_for(cl.connect(), 45)
            if not await cl.is_user_authorized():
                raise PermissionError('session रद्द / लॉगआउट झालं')
            me = await cl.get_me()
            self.premium[aid] = bool(getattr(me, 'premium', False))
            ds0 = await asyncio.wait_for(cl.get_dialogs(limit=120), 90)
            try:
                out0 = [ser_dialog(d) for d in ds0 if not getattr(d, 'archived', False)]
                out1 = [dict(ser_dialog(d), archived=True) for d in ds0 if getattr(d, 'archived', False)]
                self.cache[(aid, False)] = (time.time(), out0)
                if out1:
                    self.cache[(aid, True)] = (time.time(), out1)
            except Exception:
                pass
            self._hook(aid, cl)
            self.clients[aid] = cl
            self.state[aid] = {'state': 'online', 'err': ''}
            self.last_ping[aid] = time.time()
        except Exception as e:
            if cl is not None:
                self.clients[aid] = cl
            await self._fail(aid, doc, e)

    async def stop_client(self, aid):
        cl = self.clients.pop(aid, None)
        if cl:
            try:
                await cl.disconnect()
            except Exception:
                pass
        self.state[aid] = {'state': 'disabled', 'err': ''}

    async def stop_all(self):
        for aid in list(self.clients):
            await self.stop_client(aid)

    # --- live updates + Ghost Anti-Delete Vault + Smart Auto-Reply
    def _hook(self, aid, cl):
        async def on_new(ev):
            sm = ser_msg(ev.message)
            cid = getattr(ev, 'chat_id', None)
            if cid and sm.get('id'):
                MSG_RECENT[(aid, cid, sm['id'])] = dict(sm, chat=cid, acc=aid)
                while len(MSG_RECENT) > 800:
                    MSG_RECENT.popitem(last=False)
            self.push(aid, 'new', cid, sm)
            # Smart Auto-Reply in private chats (with human typing simulation + anti-ban rate limit)
            ar = AUTO_REPLY.get(aid)
            if ar and ar.get('enabled') and ar.get('text') and not sm.get('out') and not sm.get('service'):
                if getattr(ev, 'is_private', False):
                    sender = getattr(ev.message, 'sender', None) or await ev.get_sender()
                    if sender and not getattr(sender, 'bot', False) and not getattr(sender, 'is_self', False):
                        if ar.get('contacts_only') and not getattr(sender, 'contact', False):
                            return
                        now = time.time()
                        sent_map = ar.setdefault('sent_to', {})
                        if len(sent_map) > 500:
                            for ok_k in [k0 for k0, t0 in list(sent_map.items()) if now - t0 > 86400]:
                                sent_map.pop(ok_k, None)
                        win = [t0 for t0 in ar.setdefault('win', []) if now - t0 < 60]
                        ar['win'] = win
                        if len(win) < 4 and now - sent_map.get(cid, 0) > int(ar.get('cooldown', 3600)):
                            sent_map[cid] = now
                            win.append(now)
                            try:
                                async with cl.action(cid, 'typing'):
                                    await asyncio.sleep(2.2)
                                rm = await cl.send_message(cid, ar['text'], reply_to=sm['id'])
                                self.push(aid, 'new', cid, ser_msg(rm))
                            except Exception:
                                pass

        async def on_edit(ev):
            sm = ser_msg(ev.message)
            cid = getattr(ev, 'chat_id', None)
            if cid and sm.get('id'):
                MSG_RECENT[(aid, cid, sm['id'])] = dict(sm, chat=cid, acc=aid)
            self.push(aid, 'edit', cid, sm)

        async def on_del(ev):
            cid = getattr(ev, 'chat_id', None)
            dids = list(getattr(ev, 'deleted_ids', []) or [])
            for mid in dids:
                found = MSG_RECENT.get((aid, cid, mid))
                if not found:
                    # Also search by mid if cid was None (common in non-channel private chats)
                    for (ka, kc, km), mv in list(MSG_RECENT.items()):
                        if ka == aid and km == mid:
                            found = mv
                            cid = cid or kc
                            break
                if found and not found.get('out'):
                    DELETED_VAULT.appendleft(dict(found, deleted_at=int(time.time())))
            self.push(aid, 'del', cid, None, dids, ghost=bool(DL_CFG.get('anti_delete', True)))
        async def on_read(ev):
            if getattr(ev, 'outbox', False):
                self.push(aid, 'read', getattr(ev, 'chat_id', None), None, None, max_id=getattr(ev, 'max_id', 0))
        async def on_user(ev):
            act = 'typing' if getattr(ev, 'typing', False) else 'recording' if getattr(ev, 'recording', False) \
                else 'uploading' if getattr(ev, 'uploading', False) else ''
            cid = getattr(ev, 'chat_id', None) or getattr(ev, 'user_id', None)
            if not cid:
                return
            who = _name(getattr(ev, 'user', None)) if act else ''
            self.push(aid, 'typing', cid, None, None,
                      action=act, who=who, online=bool(getattr(ev, 'online', False)))
        cl.add_event_handler(on_new, events.NewMessage())
        cl.add_event_handler(on_edit, events.MessageEdited())
        cl.add_event_handler(on_del, events.MessageDeleted())
        cl.add_event_handler(on_read, events.MessageRead())
        cl.add_event_handler(on_user, events.UserUpdate())

    def _update_cached_dialog(self, aid, kind, chat_id, msg, **extra):
        if chat_id is None:
            return
        cid = int(chat_id)
        for arch in (False, True):
            entry = self.cache.get((aid, arch))
            if not entry:
                continue
            _, dlist = entry
            for d in dlist:
                if d.get('id') == cid:
                    if kind == 'new' and isinstance(msg, dict):
                        d['last'] = (msg.get('text') or '📎 Media')[:90]
                        d['ts'] = int(msg.get('ts') or time.time())
                        d['out'] = bool(msg.get('out', False))
                        if not d['out']:
                            d['unread'] = int(d.get('unread') or 0) + 1
                    elif kind == 'read':
                        mx = int(extra.get('max_id') or 0)
                        if mx:
                            d['read_out'] = max(int(d.get('read_out') or 0), mx)
                    break

    def push(self, aid, kind, chat_id, msg, ids=None, **extra):
        if kind in ('new', 'edit', 'del', 'read'):
            self._update_cached_dialog(aid, kind, chat_id, msg, **extra)
            if chat_id is not None:
                for k in [k for k in list(self.msg_cache) if k[0] == aid and k[1] == int(chat_id)]:
                    self.msg_cache.pop(k, None)
        with self.cond:
            self.seq += 1
            self.events.append({'seq': self.seq, 'acc': aid, 'type': kind, 'chat': chat_id, 'msg': msg, 'ids': ids, **extra})
            self.cond.notify_all()

    def _cl(self, aid):
        cl = self.clients.get(aid)
        if not cl:
            raise ApiError('हा account सध्या online नाही', 409)
        return cl

    def limit_for(self, aid):
        return min(MAX_UPLOAD, TG_LIMIT_PREMIUM if self.premium.get(aid) else TG_LIMIT)

    # ═══════════ API coroutines ═══════════
    async def a_dialogs(self, aid, archived=False):
        key = (aid, bool(archived))
        now = time.time()
        c = self.cache.get(key)
        ttl = 180 if archived else 45
        if c and now - c[0] < ttl:
            return c[1]
        cl = self._cl(aid)
        if archived:
            try:
                ds = await asyncio.wait_for(cl.get_dialogs(limit=60, folder=1), 15)
                out = [dict(ser_dialog(d), archived=True) for d in ds]
            except Exception:
                out = c[1] if c else []
            self.cache[key] = (time.time(), out)
            return out
        res0 = await asyncio.wait_for(cl.get_dialogs(limit=100, folder=0), 25)
        out0 = [ser_dialog(d) for d in res0 if not getattr(d, 'archived', False)]
        out1_from_0 = [dict(ser_dialog(d), archived=True) for d in res0 if getattr(d, 'archived', False)]
        self.cache[(aid, False)] = (time.time(), out0)
        c_arch = self.cache.get((aid, True))
        if not c_arch or now - c_arch[0] >= 180:
            await asyncio.sleep(0.35)
            try:
                res1 = await asyncio.wait_for(cl.get_dialogs(limit=60, folder=1), 15)
            except Exception:
                res1 = []
            out1 = list(out1_from_0)
            seen = {x['id'] for x in out1}
            for d in (res1 or []):
                sd = ser_dialog(d)
                sd['archived'] = True
                if sd['id'] not in seen:
                    out1.append(sd)
            self.cache[(aid, True)] = (time.time(), out1)
        return out0

    async def a_messages(self, aid, chat, before, limit, topic_id=0):
        chat_int, top_int = int(chat), int(topic_id or 0)
        ck = (aid, chat_int, top_int)
        if not before:
            cached = self.msg_cache.get(ck)
            if cached and time.time() - cached[0] < 30:
                return cached[1]
        cl = self._cl(aid)
        kw = {'limit': limit, 'offset_id': before or 0}
        if top_int:
            kw['reply_to'] = top_int
        if before:
            ms = await cl.get_messages(chat_int, **kw)
            s_list = [ser_msg(m) for m in reversed(list(ms))]
            for sm in s_list:
                MSG_RECENT[(aid, chat_int, sm['id'])] = dict(sm, chat=chat_int, acc=aid)
            while len(MSG_RECENT) > 800:
                MSG_RECENT.popitem(last=False)
            return {'messages': s_list}

        async def _get_pinned():
            pm = await cl.get_messages(chat_int, limit=1, filter=types.InputMessagesFilterPinned())
            return {'id': pm[0].id, 'text': (pm[0].message or '📎 Media')[:100]} if pm else None

        async def _get_reads():
            peer = await cl.get_input_entity(chat_int)
            pd = await cl(functions.messages.GetPeerDialogsRequest(peers=[types.InputDialogPeer(peer=peer)]))
            return int(pd.dialogs[0].read_outbox_max_id), int(pd.dialogs[0].read_inbox_max_id)

        ms, pin_res, rd_res = await asyncio.gather(
            cl.get_messages(chat_int, **kw),
            _get_pinned(),
            _get_reads(),
            return_exceptions=True
        )
        if isinstance(ms, Exception):
            raise ms
        s_list = [ser_msg(m) for m in reversed(list(ms))]
        for sm in s_list:
            MSG_RECENT[(aid, chat_int, sm['id'])] = dict(sm, chat=chat_int, acc=aid)
        while len(MSG_RECENT) > 800:
            MSG_RECENT.popitem(last=False)
        ro, ri = (0, 0) if isinstance(rd_res, Exception) else rd_res
        res = {
            'messages': s_list,
            'pinned': None if isinstance(pin_res, Exception) else pin_res,
            'read_out': ro,
            'read_in': ri
        }
        self.msg_cache[ck] = (time.time(), res)
        while len(self.msg_cache) > 60:
            self.msg_cache.pop(next(iter(self.msg_cache)), None)
        return res

    async def a_pinned_list(self, aid, chat):
        cl = self._cl(aid)
        ms = await cl.get_messages(chat, limit=50, filter=types.InputMessagesFilterPinned())
        return [ser_msg(m) for m in ms if m]

    async def a_bydate(self, aid, chat, ts):
        """Newest message on or before the given time (used by 'Jump to date')."""
        ms = await self._cl(aid).get_messages(chat, limit=1, offset_date=datetime.fromtimestamp(ts, timezone.utc))
        return {'id': ms[0].id if ms else 0}

    async def a_around(self, aid, chat, mid):
        ms = await self._cl(aid).get_messages(chat, limit=30, offset_id=mid, add_offset=-15)
        return [ser_msg(m) for m in reversed(list(ms))]

    async def a_search(self, aid, q, chat):
        cl = self._cl(aid)
        ms = await cl.get_messages(chat if chat else None, search=q, limit=40)
        out = []
        for m in ms:
            d = ser_msg(m)
            d['chat'] = getattr(m, 'chat_id', None)
            d['chat_name'] = _name(getattr(m, 'chat', None))
            out.append(d)
        return out

    async def a_send(self, aid, chat, text, reply_to, md, silent, schedule, preview, topic_id=None):
        kw = {}
        if schedule:
            kw['schedule'] = datetime.fromtimestamp(int(schedule), tz=timezone.utc)
        target_reply = reply_to or topic_id or None
        m = await self._cl(aid).send_message(chat, text, reply_to=target_reply, parse_mode='md' if md else None,
                                             silent=bool(silent), link_preview=bool(preview), **kw)
        bw_add(len(text.encode()) + 300)
        return ser_msg(m)

    async def a_edit(self, aid, chat, mid, text, md):
        m = await self._cl(aid).edit_message(chat, mid, text, parse_mode='md' if md else None)
        bw_add(len(text.encode()) + 300)
        return ser_msg(m)

    async def a_delete(self, aid, chat, ids, revoke):
        await self._cl(aid).delete_messages(chat, ids, revoke=bool(revoke))

    async def a_forward(self, aid, from_chat, ids, to_chats, hide, comment=''):
        cl = self._cl(aid)
        if not isinstance(to_chats, list):
            to_chats = [to_chats]
        out_msgs = []
        for idx, tc in enumerate(to_chats[:10]):
            if idx > 0:
                await asyncio.sleep(0.65)
            tc_int = int(tc)
            if comment and comment.strip():
                await cl.send_message(tc_int, comment.strip()[:2000])
                await asyncio.sleep(0.25)
            ms = await cl.forward_messages(tc_int, ids, from_peer=from_chat, drop_author=bool(hide))
            ms = ms if isinstance(ms, list) else [ms]
            out_msgs.extend([ser_msg(m) for m in ms if m])
        return out_msgs

    async def a_pin(self, aid, chat, mid, unpin, notify_users=False):
        cl = self._cl(aid)
        if unpin:
            await cl.unpin_message(chat, mid)
        else:
            await cl.pin_message(chat, mid, notify=bool(notify_users))

    async def a_react(self, aid, chat, mid, emoji):
        cl = self._cl(aid)
        peer = await cl.get_input_entity(chat)
        await cl(functions.messages.SendReactionRequest(
            peer=peer, msg_id=mid, reaction=[types.ReactionEmoji(emoticon=emoji)] if emoji else []))
        m = await cl.get_messages(chat, ids=mid)
        return ser_msg(m) if m else None

    async def a_vote(self, aid, chat, mid, idxs):
        cl = self._cl(aid)
        m = await cl.get_messages(chat, ids=mid)
        if not m or not getattr(m, 'poll', None):
            raise ApiError('poll सापडला नाही', 404)
        answers = m.poll.poll.answers
        opts = [answers[i].option for i in idxs if 0 <= i < len(answers)]
        peer = await cl.get_input_entity(chat)
        await cl(functions.messages.SendVoteRequest(peer=peer, msg_id=mid, options=opts))
        m = await cl.get_messages(chat, ids=mid)
        return ser_msg(m)

    async def a_send_poll(self, aid, chat, question, options, multi=False, quiz=False, public=False, correct_idx=0, solution='', reply_to=None):
        cl = self._cl(aid)
        question = (question or '').strip()[:255]
        opts = [str(o).strip()[:100] for o in (options or []) if str(o).strip()][:10]
        if not question or len(opts) < 2:
            raise ApiError('प्रश्न आणि किमान 2 पर्याय द्या')
        def _twa(s):
            if hasattr(types, 'TextWithEntities'):
                return types.TextWithEntities(text=s, entities=[])
            return s
        poll_answers = [types.PollAnswer(text=_twa(o), option=bytes([i])) for i, o in enumerate(opts)]
        poll = types.Poll(
            id=secrets.randbits(62),
            question=_twa(question),
            answers=poll_answers,
            multiple_choice=bool(multi and not quiz),
            quiz=bool(quiz),
            public_voters=bool(public),
        )
        correct = [bytes([int(correct_idx)])] if quiz and 0 <= int(correct_idx) < len(opts) else None
        media = types.InputMediaPoll(
            poll=poll,
            correct_answers=correct,
            solution=(solution or '').strip()[:200] if quiz and solution else None,
            solution_entities=[] if quiz and solution else None
        )
        m = await cl.send_file(chat, file=media, reply_to=reply_to or None)
        return ser_msg(m)

    async def a_close_poll(self, aid, chat, mid):
        cl = self._cl(aid)
        m = await cl.get_messages(chat, ids=mid)
        if not m or not getattr(m, 'poll', None):
            raise ApiError('poll सापडला नाही', 404)
        p = m.poll.poll
        p.closed = True
        await cl.edit_message(chat, mid, file=types.InputMediaPoll(poll=p))
        m2 = await cl.get_messages(chat, ids=mid)
        return ser_msg(m2)

    async def a_scheduled_list(self, aid, chat):
        cl = self._cl(aid)
        peer = await cl.get_input_entity(chat)
        r = await cl(functions.messages.GetScheduledHistoryRequest(peer=peer, hash=0))
        msgs = getattr(r, 'messages', []) or []
        return [ser_msg(m) for m in reversed(list(msgs))]

    async def a_scheduled_delete(self, aid, chat, ids):
        cl = self._cl(aid)
        peer = await cl.get_input_entity(chat)
        await cl(functions.messages.DeleteScheduledMessagesRequest(peer=peer, id=[int(x) for x in ids]))
        return {'ok': True}

    async def a_scheduled_sendnow(self, aid, chat, ids):
        cl = self._cl(aid)
        peer = await cl.get_input_entity(chat)
        await cl(functions.messages.SendScheduledMessagesRequest(peer=peer, id=[int(x) for x in ids]))
        return {'ok': True}

    async def a_translate(self, aid, chat, mid, to_lang='en'):
        cl = self._cl(aid)
        peer = await cl.get_input_entity(chat)
        to_lang = re.sub(r'[^a-zA-Z\-]', '', (to_lang or 'en'))[:8] or 'en'
        try:
            r = await cl(functions.messages.TranslateTextRequest(peer=peer, id=[int(mid)], to_lang=to_lang))
            res = getattr(r, 'result', None) or []
            if res:
                return {'text': _t(res[0])}
        except Exception as e:
            raise ApiError(f'Translation उपलब्ध नाही: {e}'[:140])
        raise ApiError('Translation मिळालं नाही')

    async def a_click(self, aid, chat, mid, i, j):
        cl = self._cl(aid)
        m = await cl.get_messages(chat, ids=mid)
        if not m:
            raise ApiError('message सापडला नाही', 404)
        res = await m.click(i, j)
        return {'text': getattr(res, 'message', None), 'alert': bool(getattr(res, 'alert', False)), 'url': getattr(res, 'url', None)}

    async def a_read(self, aid, chat, max_id):
        await self._cl(aid).send_read_acknowledge(chat, max_id=max_id, clear_mentions=True)

    async def a_typing(self, aid, chat):
        cl = self._cl(aid)
        peer = await cl.get_input_entity(chat)
        await cl(functions.messages.SetTypingRequest(peer=peer, action=types.SendMessageTypingAction()))

    async def a_mute(self, aid, chat, mute):
        cl = self._cl(aid)
        peer = await cl.get_input_entity(chat)
        await cl(functions.account.UpdateNotifySettingsRequest(
            peer=types.InputNotifyPeer(peer=peer), settings=types.InputPeerNotifySettings(mute_until=2147483647 if mute else 0)))
        self.cache.pop((aid, False), None)

    async def a_chatpin(self, aid, chat, pin):
        cl = self._cl(aid)
        peer = await cl.get_input_entity(chat)
        await cl(functions.messages.ToggleDialogPinRequest(peer=types.InputDialogPeer(peer=peer), pinned=bool(pin)))
        self.cache.clear()

    async def a_archive(self, aid, chat, archive):
        await self._cl(aid).edit_folder(chat, 1 if archive else 0)
        self.cache.clear()

    async def a_chatdelete(self, aid, chat):
        await self._cl(aid).delete_dialog(chat)
        self.cache.clear()

    def _dlg(self, ent):
        kind = 'user' if type(ent).__name__ == 'User' else ('channel' if getattr(ent, 'broadcast', False) else 'group')
        return {'id': utils.get_peer_id(ent), 'name': _name(ent), 'type': kind, 'username': getattr(ent, 'username', '') or '',
                'unread': 0, 'last': '', 'ts': 0, 'pinned': False, 'muted': False, 'out': False, 'bot': bool(getattr(ent, 'bot', False)),
                'verified': bool(getattr(ent, 'verified', False)), 'forum': bool(getattr(ent, 'forum', False)),
                'online': False, 'mentions': 0, 'read_out': 0, 'read_in': 0, 'self': False,
                'owner': bool(getattr(ent, 'creator', False)),
                'admin': bool(getattr(ent, 'creator', False)) or getattr(ent, 'admin_rights', None) is not None}

    async def a_create_chat(self, aid, title, about, kind):
        cl = self._cl(aid)
        title = (title or '').strip()[:128]
        if not title:
            raise ApiError('नाव टाक')
        megagroup = kind != 'channel'
        try:
            r = await cl(functions.channels.CreateChannelRequest(
                title=title, about=(about or '').strip()[:255], megagroup=megagroup))
        except Exception as e:
            raise ApiError(f'{e}'[:160])
        chat = r.chats[0]
        d = self._dlg(chat)
        d['owner'] = d['admin'] = True
        self.cache.clear()
        return {'dialog': d}

    async def a_edit_chat_info(self, aid, chat, title, about):
        cl = self._cl(aid)
        ent = await cl.get_entity(chat)
        title = (title or '').strip()[:128]
        try:
            if title and title != _name(ent):
                if type(ent).__name__ == 'Channel':
                    await cl(functions.channels.EditTitleRequest(ent, title))
                else:
                    await cl(functions.messages.EditChatTitleRequest(chat_id=ent.id, title=title))
            await cl(functions.messages.EditChatAboutRequest(ent, (about or '').strip()[:255]))
        except Exception as e:
            raise ApiError(f'{e}'[:160])
        self.cache.clear()
        return {'ok': True}

    async def a_chat_invites(self, aid, chat):
        cl = self._cl(aid)
        peer = await cl.get_input_entity(chat)
        me = await cl.get_input_entity('me')
        try:
            r = await cl(functions.messages.GetExportedChatInvitesRequest(
                peer=peer, admin_id=me, limit=20))
            out = []
            for inv in (getattr(r, 'invites', None) or []):
                if hasattr(inv, 'link'):
                    out.append({
                        'link': inv.link,
                        'title': getattr(inv, 'title', '') or '',
                        'usage': int(getattr(inv, 'usage', 0) or 0),
                        'limit': int(getattr(inv, 'usage_limit', 0) or 0),
                        'permanent': bool(getattr(inv, 'permanent', False)),
                    })
            return out
        except Exception as e:
            raise ApiError(f'{e}'[:140])

    async def a_chat_invite_create(self, aid, chat, title='', limit=0):
        cl = self._cl(aid)
        peer = await cl.get_input_entity(chat)
        kw = {'peer': peer}
        if title:
            kw['title'] = str(title)[:32]
        if limit and int(limit) > 0:
            kw['usage_limit'] = int(limit)
        try:
            inv = await cl(functions.messages.ExportChatInviteRequest(**kw))
            return {'link': getattr(inv, 'link', '')}
        except Exception as e:
            raise ApiError(f'{e}'[:140])

    async def a_chat_slowmode(self, aid, chat, seconds=0):
        cl = self._cl(aid)
        ent = await cl.get_entity(chat)
        try:
            await cl(functions.channels.ToggleSlowModeRequest(channel=ent, seconds=int(seconds)))
            return {'ok': True}
        except Exception as e:
            raise ApiError(f'{e}'[:140])

    async def a_add_member(self, aid, chat, q):
        cl = self._cl(aid)
        ent = await cl.get_entity(chat)
        q = re.sub(r'^https?://t\.me/', '', (q or '').strip()).lstrip('@')
        if not q:
            raise ApiError('username / phone द्या')
        try:
            user = await cl.get_entity(q if not q.lstrip('+').isdigit() or q.startswith('+') else int(q))
        except Exception as e:
            raise ApiError(f'User सापडला नाही: {e}'[:140], 404)
        try:
            if type(ent).__name__ == 'Channel':
                await cl(functions.channels.InviteToChannelRequest(ent, [user]))
            else:
                await cl(functions.messages.AddChatUserRequest(chat_id=ent.id, user_id=user, fwd_limit=100))
        except Exception as e:
            raise ApiError(f'{e}'[:160])
        return {'ok': True, 'name': _name(user)}

    async def a_kick_member(self, aid, chat, uid):
        cl = self._cl(aid)
        try:
            await cl.kick_participant(chat, int(uid))
        except Exception as e:
            raise ApiError(f'{e}'[:160])
        return {'ok': True}

    async def a_set_admin(self, aid, chat, uid, promote):
        cl = self._cl(aid)
        try:
            await cl.edit_admin(chat, int(uid), is_admin=bool(promote))
        except Exception as e:
            raise ApiError(f'{e}'[:160])
        return {'ok': True}

    async def a_toggle_forum(self, aid, chat, enabled):
        cl = self._cl(aid)
        ent = await cl.get_entity(chat)
        if type(ent).__name__ != 'Channel':
            raise ApiError('Topics फक्त supergroup/channel मध्येच असतात (आधी Settings → आणखी members add करून supergroup बनव)')
        try:
            await cl(functions.channels.ToggleForumRequest(ent, enabled=bool(enabled), tabs=False))
        except Exception as e:
            raise ApiError(f'{e}'[:160])
        self.cache.clear()
        return {'ok': True}

    async def a_topics(self, aid, chat):
        cl = self._cl(aid)
        ent = await cl.get_entity(chat)
        try:
            r = await cl(functions.messages.GetForumTopicsRequest(
                ent, offset_date=None, offset_id=0, offset_topic=0, limit=100))
        except Exception as e:
            raise ApiError(f'{e}'[:160])
        out = []
        for t in r.topics:
            if type(t).__name__ != 'ForumTopic':
                continue
            out.append({'id': t.id, 'title': t.title, 'closed': bool(getattr(t, 'closed', False)),
                        'pinned': bool(getattr(t, 'pinned', False)), 'unread': int(getattr(t, 'unread_count', 0) or 0)})
        return out

    async def a_create_topic(self, aid, chat, title):
        cl = self._cl(aid)
        ent = await cl.get_entity(chat)
        title = (title or '').strip()[:128]
        if not title:
            raise ApiError('नाव टाक')
        try:
            await cl(functions.messages.CreateForumTopicRequest(ent, title=title, random_id=secrets.randbits(63)))
        except Exception as e:
            raise ApiError(f'{e}'[:160])
        return {'ok': True}

    async def a_edit_topic(self, aid, chat, topic_id, title, closed):
        cl = self._cl(aid)
        ent = await cl.get_entity(chat)
        kw = {'peer': ent, 'topic_id': int(topic_id)}
        if title is not None:
            kw['title'] = (title or '').strip()[:128]
        if closed is not None:
            kw['closed'] = bool(closed)
        try:
            await cl(functions.messages.EditForumTopicRequest(**kw))
        except Exception as e:
            raise ApiError(f'{e}'[:160])
        return {'ok': True}

    async def a_delete_topic(self, aid, chat, topic_id):
        cl = self._cl(aid)
        ent = await cl.get_entity(chat)
        try:
            await cl(functions.messages.DeleteTopicHistoryRequest(ent, top_msg_id=int(topic_id)))
        except Exception as e:
            raise ApiError(f'{e}'[:160])
        return {'ok': True}

    async def a_resolve(self, aid, q):
        q = q.strip()
        if not q:
            raise ApiError('नाव / username द्या')
        q = re.sub(r'^https?://t\.me/', '', q).lstrip('@')
        try:
            ent = await self._cl(aid).get_entity(q if not q.lstrip('+').isdigit() or q.startswith('+') else int(q))
        except ApiError:
            raise
        except Exception as e:
            raise ApiError(f'सापडलं नाही: {e}'[:140], 404)
        return self._dlg(ent)

    async def a_join(self, aid, q):
        cl = self._cl(aid)
        q = q.strip()
        m = re.search(r'(?:t\.me/\+|joinchat/)([\w-]+)', q)
        if m:
            upd = await cl(functions.messages.ImportChatInviteRequest(m.group(1)))
            ent = upd.chats[0] if getattr(upd, 'chats', None) else None
        else:
            ent = await cl.get_entity(re.sub(r'^https?://t\.me/', '', q).lstrip('@'))
            await cl(functions.channels.JoinChannelRequest(ent))
        if ent is None:
            raise ApiError('join झालं पण chat मिळाला नाही')
        self.cache.clear()
        return self._dlg(ent)

    async def a_contacts(self, aid):
        r = await self._cl(aid)(functions.contacts.GetContactsRequest(hash=0))
        return sorted(({'id': u.id, 'name': _name(u), 'username': getattr(u, 'username', '') or '',
                        'phone': getattr(u, 'phone', '') or '',
                        'online': type(getattr(u, 'status', None)).__name__ == 'UserStatusOnline'}
                       for u in r.users), key=lambda x: x['name'].lower())

    async def a_contact_add(self, aid, user_id_or_q, first_name, last_name='', phone=''):
        cl = self._cl(aid)
        first_name = (first_name or '').strip()[:64]
        if not first_name:
            raise ApiError('नाव टाक')
        try:
            q = str(user_id_or_q).strip()
            ent = await cl.get_input_entity(int(q) if q.lstrip('-').isdigit() else q)
            await cl(functions.contacts.AddContactRequest(
                id=ent, first_name=first_name, last_name=(last_name or '').strip()[:64],
                phone=(phone or '').strip()[:32], add_phone_privacy_exception=False))
            return {'ok': True}
        except Exception as e:
            raise ApiError(f'{e}'[:140])

    async def a_contact_delete(self, aid, uid):
        cl = self._cl(aid)
        try:
            ent = await cl.get_input_entity(int(uid))
            await cl(functions.contacts.DeleteContactsRequest(id=[ent]))
            return {'ok': True}
        except Exception as e:
            raise ApiError(f'{e}'[:140])

    async def a_user_block(self, aid, uid, block=True):
        cl = self._cl(aid)
        try:
            ent = await cl.get_input_entity(int(uid))
            if block:
                await cl(functions.contacts.BlockRequest(id=ent))
            else:
                await cl(functions.contacts.UnblockRequest(id=ent))
            return {'ok': True, 'blocked': bool(block)}
        except Exception as e:
            raise ApiError(f'{e}'[:140])

    async def a_common_chats(self, aid, uid):
        cl = self._cl(aid)
        try:
            ent = await cl.get_input_entity(int(uid))
            r = await cl(functions.messages.GetCommonChatsRequest(user_id=ent, max_id=0, limit=50))
            return [self._dlg(c) for c in (getattr(r, 'chats', None) or [])]
        except Exception as e:
            raise ApiError(f'{e}'[:140])

    async def a_chatinfo(self, aid, chat):
        cl = self._cl(aid)
        ent = await cl.get_entity(chat)
        kind = type(ent).__name__
        info = {'id': chat, 'name': _name(ent), 'username': getattr(ent, 'username', '') or '', 'phone': getattr(ent, 'phone', '') or '',
                'about': '', 'members': None, 'status': status_text(ent),
                'type': 'user' if kind == 'User' else ('channel' if getattr(ent, 'broadcast', False) else 'group'),
                'bot': bool(getattr(ent, 'bot', False)), 'verified': bool(getattr(ent, 'verified', False)),
                'premium': bool(getattr(ent, 'premium', False)), 'forum': bool(getattr(ent, 'forum', False)),
                'contact': bool(getattr(ent, 'contact', False)), 'blocked': False, 'slowmode': 0,
                'self': bool(getattr(ent, 'is_self', False))}
        try:
            if kind == 'User':
                f = await cl(functions.users.GetFullUserRequest(ent))
                info['about'] = getattr(f.full_user, 'about', '') or ''
                info['blocked'] = bool(getattr(f.full_user, 'blocked', False))
                info['common_count'] = int(getattr(f.full_user, 'common_chats_count', 0) or 0)
            elif kind == 'Channel':
                f = await cl(functions.channels.GetFullChannelRequest(ent))
                info['about'] = getattr(f.full_chat, 'about', '') or ''
                info['members'] = getattr(f.full_chat, 'participants_count', None)
                info['slowmode'] = int(getattr(f.full_chat, 'slowmode_seconds', 0) or 0)
            else:
                f = await cl(functions.messages.GetFullChatRequest(chat_id=ent.id))
                info['about'] = getattr(f.full_chat, 'about', '') or ''
                info['members'] = len(getattr(getattr(f.full_chat, 'participants', None), 'participants', []) or [])
        except Exception:
            pass
        if info['type'] == 'group' and info['members']:
            info['status'] = f"{info['members']} members"
        elif info['type'] == 'channel' and info['members']:
            info['status'] = f"{info['members']} subscribers"
        return info

    async def a_members(self, aid, chat, q):
        ps = await self._cl(aid).get_participants(chat, limit=120, search=q or '')
        return [{'id': p.id, 'name': _name(p), 'username': getattr(p, 'username', '') or '', 'bot': bool(getattr(p, 'bot', False)),
                 'status': status_text(p)} for p in ps]

    async def a_shared(self, aid, chat, kind, before):
        if kind not in FILTERS:
            raise ApiError('चुकीचा प्रकार')
        ms = await self._cl(aid).get_messages(chat, limit=30, offset_id=before or 0, filter=getattr(types, FILTERS[kind])())
        return [ser_msg(m) for m in ms]

    async def a_sessions(self, aid):
        r = await self._cl(aid)(functions.account.GetAuthorizationsRequest())
        return [{'hash': str(a.hash), 'device': a.device_model, 'platform': a.platform, 'app': f'{a.app_name} {a.app_version}',
                 'ip': a.ip, 'country': a.country, 'created': _ts(a.date_created), 'active': _ts(a.date_active), 'current': bool(a.current)}
                for a in r.authorizations]

    async def a_terminate(self, aid, h):
        await self._cl(aid)(functions.account.ResetAuthorizationRequest(hash=int(h)))

    async def a_setme(self, aid, first, last, about, username=None):
        cl = self._cl(aid)
        await cl(functions.account.UpdateProfileRequest(first_name=first or None, last_name=last, about=about))
        if username is not None:
            u = re.sub(r'^@', '', str(username).strip())
            try:
                await cl(functions.account.UpdateUsernameRequest(username=u))
            except Exception as e:
                raise ApiError(f'Username बदलता आला नाही: {e}'[:140])

    async def a_set_profile_photo(self, aid, raw_bytes):
        cl = self._cl(aid)
        f = await cl.upload_file(raw_bytes, file_name='avatar.jpg')
        await cl(functions.photos.UploadProfilePhotoRequest(file=f))
        me = await cl.get_me()
        self.avatars.pop((aid, me.id), None)
        return {'ok': True}

    async def a_set_chat_photo(self, aid, chat, raw_bytes):
        cl = self._cl(aid)
        ent = await cl.get_entity(chat)
        f = await cl.upload_file(raw_bytes, file_name='chat.jpg')
        if type(ent).__name__ == 'Channel':
            await cl(functions.channels.EditPhotoRequest(channel=ent, photo=types.InputChatUploadedPhoto(file=f)))
        else:
            await cl(functions.messages.EditChatPhotoRequest(chat_id=ent.id, photo=types.InputChatUploadedPhoto(file=f)))
        self.avatars.pop((aid, int(chat)), None)
        return {'ok': True}

    async def a_avatar(self, aid, chat):
        key = (aid, chat)
        if key in self.avatars:
            return self.avatars[key]
        async with self.avatar_sem:
            if key in self.avatars:
                return self.avatars[key]
            cl = self._cl(aid)
            try:
                peer = await cl.get_input_entity(chat)
                data = await cl.download_profile_photo(peer, file=bytes, download_big=False)
            except Exception:
                data = b''
            _lru_put(self.avatars, key, data or b'', 250, 6 * MB)
            return data or b''

    async def a_thumb(self, aid, chat, mid):
        key = (aid, chat, mid)
        if key in self.thumbs:
            if not self.thumbs[key]:
                raise ApiError('thumb नाही', 404)
            return self.thumbs[key]
        async with self.thumb_sem:
            if key in self.thumbs:
                if not self.thumbs[key]:
                    raise ApiError('thumb नाही', 404)
                return self.thumbs[key]
            cl = self._cl(aid)
            m = await cl.get_messages(chat, ids=mid)
            info = media_info(m) if m else None
            if not info:
                _lru_put(self.thumbs, key, b'', 400, 16 * MB)
                raise ApiError('media नाही', 404)
            idx = _thumb_index(m)
            if info['kind'] == 'sticker' and 0 < info['size'] < 400 * 1024 and 'webp' in info['mime']:
                data = (await cl.download_media(m, file=bytes),)
            elif idx is not None:
                data = (await cl.download_media(m, file=bytes, thumb=idx),)
            elif info['kind'] == 'photo' and 0 < info['size'] < 300 * 1024:
                data = (await cl.download_media(m, file=bytes),)
            else:
                _lru_put(self.thumbs, key, b'', 400, 16 * MB)
                raise ApiError('thumb नाही', 404)
            await asyncio.sleep(0.05)
            if not data[0]:
                raise ApiError('thumb अयशस्वी', 502)
            _lru_put(self.thumbs, key, data[0], 400, 16 * MB)
            return data[0]

    async def a_info(self, aid, chat, mid):
        m = await self._cl(aid).get_messages(chat, ids=mid)
        info = media_info(m) if m else None
        if not info:
            raise ApiError('media सापडला नाही', 404)
        return info

    # ═══════════ Multi-Threaded Parallel Stream & Server Downloader ═══════════
    async def a_iter(self, aid, chat, mid, offset, size):
        """Fast multi-worker prefetch iterator for direct range requests (e.g., video seeking or uncached streaming)."""
        cl = self._cl(aid)
        m = await cl.get_messages(chat, ids=mid)
        if not m or not getattr(m, 'media', None):
            raise ApiError('media सापडला नाही', 404)
        it = cl.iter_download(m.media, offset=offset, chunk_size=REQ, request_size=REQ, file_size=size)
        return it.__aiter__()

    async def a_next(self, it):
        try:
            return await it.__anext__()
        except StopAsyncIteration:
            return None

    async def a_close(self, it):
        try:
            c = getattr(it, 'close', None)
            if c:
                r = c()
                if asyncio.iscoroutine(r):
                    await r
        except Exception:
            pass

    async def a_download(self, key):
        """
        Internal Multi-Threaded Parallel Downloader:
        Splits the file into stripes (2 MB = 4 x 512 KB chunks) and runs up to `DL_CFG['threads']`
        (default 8, up to 16) concurrent async workers in parallel, writing out-of-order chunks to the
        pre-allocated `.part` file via a thread pool (`os.pwrite`) while tracking both `done` (total bytes)
        and `contig` (contiguous bytes from offset 0) so the browser can stream from `.part` in real time!
        """
        with DL_LOCK:
            j = DL.get(key)
        if not j:
            return
        final = _cpath(key)
        part = final + '.part'
        cond = j['cond']

        def ev(state, **kw):
            self.push(
                j['acc'], 'dl', j['chat'], None, None,
                mid=j['id'], state=state, done=j['done'], contig=j.get('contig', 0),
                total=j['size'], speed=j.get('speed', 0), threads=j.get('threads', 1),
                expires_in=DL_CFG['ttl'], **kw
            )

        fd = None
        try:
            cl = self._cl(j['acc'])
            m = await cl.get_messages(j['chat'], ids=j['id'])
            if not m or not getattr(m, 'media', None):
                raise ApiError('media सापडला नाही', 404)
            info = media_info(m) or {}
            size = int(j['size'] or info.get('size') or 0)
            j['size'] = size
            if info.get('name') and not j.get('name'):
                j['name'] = info['name']
            if info.get('mime') and not j.get('mime'):
                j['mime'] = info['mime']

            os.makedirs(CACHE_DIR, exist_ok=True)
            total_chunks = (size + REQ - 1) // REQ
            resume_off = 0
            if os.path.exists(part):
                try:
                    existing_sz = os.path.getsize(part)
                    if 0 < existing_sz < size:
                        resume_off = existing_sz - (existing_sz % REQ)
                except OSError:
                    resume_off = 0

            flags = os.O_CREAT | os.O_RDWR
            fd = os.open(part, flags, 0o644)
            start_chunk = resume_off // REQ
            done_chunks = set(range(start_chunk))
            contig_chunk = start_chunk
            j['done'] = resume_off
            j['contig'] = resume_off

            # Determine stripe size (chunks per worker claim) and number of parallel workers (capped safely for account protection)
            remaining_chunks = max(0, total_chunks - start_chunk)
            stripe_chunks = 4 if remaining_chunks >= 16 else (2 if remaining_chunks >= 4 else 1)
            max_workers = max(1, min(int(DL_CFG.get('threads', 4)), 8, (remaining_chunks + stripe_chunks - 1) // max(1, stripe_chunks)))
            j['threads'] = max_workers

            next_chunk_idx = [start_chunk]
            idx_lock = asyncio.Lock()
            ref_lock = asyncio.Lock()
            last_ref_ts = [0.0]
            flood_until = [0.0]
            first_chunk_ev = asyncio.Event()
            t0 = time.time()
            speed_window = collections.deque(maxlen=24)
            speed_window.append((t0, resume_off))
            last_ev = [0.0]
            loop = asyncio.get_running_loop()

            def _record_chunk(c_idx, c_len):
                nonlocal contig_chunk
                now = time.time()
                with cond:
                    if c_idx not in done_chunks:
                        done_chunks.add(c_idx)
                        j['done'] = min(size, j['done'] + c_len)
                    while contig_chunk in done_chunks and contig_chunk < total_chunks:
                        contig_chunk += 1
                    j['contig'] = min(size, contig_chunk * REQ)
                    speed_window.append((now, j['done']))
                    if len(speed_window) >= 2:
                        dt = max(0.05, speed_window[-1][0] - speed_window[0][0])
                        db_bytes = max(0, speed_window[-1][1] - speed_window[0][1])
                        j['speed'] = int(db_bytes / dt)
                    cond.notify_all()
                if now - last_ev[0] > 0.45:
                    last_ev[0] = now
                    ev('downloading')

            async def _worker(wid):
                nonlocal m
                if wid > 0:
                    try:
                        await asyncio.wait_for(first_chunk_ev.wait(), timeout=4.0)
                    except asyncio.TimeoutError:
                        pass
                while not j.get('cancel'):
                    async with idx_lock:
                        c_start = next_chunk_idx[0]
                        if c_start >= total_chunks:
                            return
                        c_end = min(total_chunks, c_start + stripe_chunks)
                        next_chunk_idx[0] = c_end

                    byte_start = c_start * REQ
                    byte_end = min(size, c_end * REQ)
                    want_bytes = byte_end - byte_start
                    cur_off = byte_start
                    got_bytes = 0

                    for attempt in range(4):
                        if j.get('cancel'):
                            raise asyncio.CancelledError()
                        wait_fw = flood_until[0] - time.time()
                        if wait_fw > 0:
                            await asyncio.sleep(min(wait_fw, 60.0))
                        try:
                            async with self.dl_sem:
                                async for chunk in cl.iter_download(
                                    m.media, offset=cur_off, chunk_size=REQ, request_size=REQ, file_size=size
                                ):
                                    if j.get('cancel'):
                                        raise asyncio.CancelledError()
                                    if not chunk:
                                        break
                                    take = chunk[:want_bytes - got_bytes]
                                    if not take:
                                        break
                                    c_idx = cur_off // REQ
                                    await loop.run_in_executor(_DISK_POOL, _pwrite, fd, take, cur_off)
                                    cur_off += len(take)
                                    got_bytes += len(take)
                                    if not first_chunk_ev.is_set():
                                        first_chunk_ev.set()
                                    _record_chunk(c_idx, len(take))
                                    if got_bytes >= want_bytes:
                                        break
                            if got_bytes >= want_bytes:
                                break
                        except asyncio.CancelledError:
                            raise
                        except Exception as w_err:
                            fw_sec = int(getattr(w_err, 'seconds', 0) or 0)
                            if fw_sec > 0:
                                flood_until[0] = max(flood_until[0], time.time() + min(fw_sec + 2, 90))
                                await asyncio.sleep(min(fw_sec + 2, 60))
                            else:
                                if attempt == 3:
                                    raise w_err
                                await asyncio.sleep(1.2 * (attempt + 1))
                            # Refresh message reference on FileReferenceExpiredError (deduplicated across workers)
                            async with ref_lock:
                                if time.time() - last_ref_ts[0] > 3.0:
                                    last_ref_ts[0] = time.time()
                                    try:
                                        m_ref = await cl.get_messages(j['chat'], ids=j['id'])
                                        if m_ref and getattr(m_ref, 'media', None):
                                            m = m_ref
                                    except Exception:
                                        pass

            if remaining_chunks > 0:
                await asyncio.gather(*(_worker(w) for w in range(max_workers)))

            if j.get('cancel'):
                raise asyncio.CancelledError()
            if j['done'] < size:
                raise IOError(f"अपूर्ण download ({j['done']}/{size})")

            os.ftruncate(fd, size)
            os.close(fd)
            fd = None
            os.replace(part, final)
            now = time.time()
            meta = {
                'key': key, 'acc': j['acc'], 'chat': j['chat'], 'id': j['id'],
                'name': j.get('name') or f"file_{j['id']}",
                'mime': j.get('mime') or 'application/octet-stream',
                'size': size, 'created_at': now
            }
            _save_cmeta(key, meta)
            with cond:
                j.update(state='cached', done=size, contig=size, speed=0, created_at=now)
                cond.notify_all()
            ev('cached')
            await loop.run_in_executor(None, cache_evict, 0)
        except asyncio.CancelledError:
            if fd is not None:
                try:
                    os.close(fd)
                except OSError:
                    pass
                fd = None
            with cond:
                j['state'] = 'none'
                cond.notify_all()
            ev('none')
            raise
        except Exception as e:
            if fd is not None:
                try:
                    os.close(fd)
                except OSError:
                    pass
                fd = None
            with cond:
                j.update(state='error', err=f'{e}'[:140], speed=0)
                cond.notify_all()
            ev('error', err=j['err'])
        finally:
            if fd is not None:
                try:
                    os.close(fd)
                except OSError:
                    pass
            self.dl_tasks.pop(key, None)

    # --- मोठ्या फाइलचा upload (disk वरून Telegram ला, progress सकट)
    async def a_send_file(self, up_id):
        with UP_LOCK:
            up = UP.get(up_id)
        if not up:
            return
        last = [0.0]
        def prog(sent, total):
            up['sent'] = int(sent)
            if time.time() - last[0] > 0.6:
                last[0] = time.time()
                self.push(up['acc'], 'up', up['chat'], None, None, uid=up_id, sent=int(sent), total=int(total), state='sending', name=up['name'])
        try:
            kw = {}
            if up.get('schedule'):
                kw['schedule'] = datetime.fromtimestamp(int(up['schedule']), tz=timezone.utc)
            if up.get('is_voice'):
                kw['voice_note'] = True
            if up.get('is_round'):
                kw['video_note'] = True
            target_reply = up.get('reply_to') or up.get('topic_id') or None
            m = await self._cl(up['acc']).send_file(
                up['chat'], up['path'], caption=up.get('caption') or None,
                force_document=bool(up.get('as_file') and not up.get('is_voice') and not up.get('is_round')),
                reply_to=target_reply, silent=bool(up.get('silent')), supports_streaming=True,
                parse_mode='md' if up.get('md') else None, progress_callback=prog, **kw)
            m = m[0] if isinstance(m, list) else m
            bw_add(up['size'] + 500)
            up.update(state='done', msg=ser_msg(m))
            self.push(up['acc'], 'up', up['chat'], up['msg'], None, uid=up_id, sent=up['size'], total=up['size'], state='done', name=up['name'])
        except asyncio.CancelledError:
            up['state'] = 'canceled'
            self.push(up['acc'], 'up', up['chat'], None, None, uid=up_id, state='canceled', name=up['name'])
            raise
        except Exception as e:
            up.update(state='error', err=f'{e}'[:160])
            self.push(up['acc'], 'up', up['chat'], None, None, uid=up_id, state='error', err=up['err'], name=up['name'])
        finally:
            shutil.rmtree(os.path.dirname(up['path']), ignore_errors=True)
            self.up_tasks.pop(up_id, None)

    # --- one login for both halves (QR-token approval: no second OTP / password typing)
    async def a_accept_token(self, aid, token):
        """This (already logged-in) account approves a QR login token requested by Storage's Pyrogram client."""
        cl = self._cl(aid)
        try:
            await cl(functions.auth.AcceptLoginTokenRequest(token=bytes(token)))
        except Exception as e:
            raise ApiError(f'Telegram ने token स्वीकारला नाही: {type(e).__name__}'[:160])

    async def a_qr_begin(self, label, api_id=None, api_hash=None):
        if MAX_ACCOUNTS and len(self.acc_ids()) >= MAX_ACCOUNTS:
            raise ApiError(f'जास्तीत जास्त {MAX_ACCOUNTS} accounts (MAX_ACCOUNTS env)')
        aid_use = str(api_id or DEF_API_ID).strip()
        ahash_use = str(api_hash or DEF_API_HASH).strip()
        if not (aid_use and ahash_use):
            raise ApiError('TG_API_ID / TG_API_HASH सेट नाहीत')
        if len(self.logins) >= 4:
            raise ApiError('आधीचे login पूर्ण करा / 10 मिनिटं थांबा')
        cl = client_factory('', aid_use, ahash_use)
        try:
            await asyncio.wait_for(cl.connect(), 30)
            qr = await cl.qr_login()
        except Exception as e:
            await self._quiet_disc(cl)
            raise ApiError(f'{e}'[:160])
        lid = secrets.token_urlsafe(9)
        self.logins[lid] = {'cl': cl, 'phone': '', 'hash': '', 'label': (label or '')[:30], 'qr': qr,
                            'api': (aid_use, ahash_use), 'exp': time.time() + 600, 'tries': 0}
        return {'login_id': lid, 'token': qr.token, 'url': qr.url}

    async def a_qr_check_once(self, lid):
        """Non-blocking single check for UI QR login polling."""
        v = self._login(lid)
        cl = v['cl']
        req = functions.auth.ExportLoginTokenRequest(int(v['api'][0]), v['api'][1], [])
        try:
            resp = await cl(req)
            if isinstance(resp, types.auth.LoginTokenMigrateTo):
                await cl._switch_dc(resp.dc_id)
                resp = await cl(functions.auth.ImportLoginTokenRequest(resp.token))
            if isinstance(resp, types.auth.LoginTokenSuccess):
                user = resp.authorization.user
                await cl._on_login(user)
                r = await self._finish(lid, v)
                r['login_id'] = lid
                return r
            return {'next': 'waiting', 'login_id': lid}
        except Exception as e:
            if type(e).__name__ == 'SessionPasswordNeededError':
                return {'next': 'password', 'login_id': lid}
            raise ApiError(f'{type(e).__name__}: {e}'[:160])

    async def a_qr_wait(self, lid):
        """Poll (do NOT wait for Telethon's update event): the other side usually approves the token BEFORE we
        start waiting, so that event would already be gone."""
        v = self._login(lid)
        cl = v['cl']
        req = functions.auth.ExportLoginTokenRequest(int(v['api'][0]), v['api'][1], [])
        user, deadline = None, time.time() + 50
        try:
            while time.time() < deadline:
                resp = await cl(req)
                if isinstance(resp, types.auth.LoginTokenMigrateTo):      # account lives on another data centre
                    await cl._switch_dc(resp.dc_id)
                    resp = await cl(functions.auth.ImportLoginTokenRequest(resp.token))
                if isinstance(resp, types.auth.LoginTokenSuccess):
                    user = resp.authorization.user
                    await cl._on_login(user)
                    break
                await asyncio.sleep(1.5)
        except Exception as e:
            if type(e).__name__ == 'SessionPasswordNeededError':
                return {'next': 'password', 'login_id': lid}
            self.logins.pop(lid, None)
            await self._quiet_disc(cl)
            print(f'[qr-import] failed: {type(e).__name__}: {e}', flush=True)
            raise ApiError(f'{type(e).__name__}: {e}'[:160])
        if user is None:
            self.logins.pop(lid, None)
            await self._quiet_disc(cl)
            raise ApiError('Storage account ने login approve केला नाही (वेळ संपली) — परत try करा')
        r = await self._finish(lid, v)
        r['login_id'] = lid
        return r

    # --- login
    def expire_logins(self):
        for lid in [l for l, v in self.logins.items() if v['exp'] < time.time()]:
            v = self.logins.pop(lid)
            asyncio.ensure_future(self._quiet_disc(v['cl']), loop=self.loop)

    async def _quiet_disc(self, cl):
        try:
            await cl.disconnect()
        except Exception:
            pass

    async def a_login_start(self, label, phone, api_id, api_hash):
        if MAX_ACCOUNTS and len(self.acc_ids()) >= MAX_ACCOUNTS:
            raise ApiError(f'जास्तीत जास्त {MAX_ACCOUNTS} accounts (MAX_ACCOUNTS env)')
        if len(self.logins) >= 4:
            raise ApiError('आधीचे login पूर्ण करा / 10 मिनिटं थांबा')
        phone = re.sub(r'[^\d+]', '', phone)
        if not re.fullmatch(r'\+\d{8,15}', phone):
            raise ApiError('फोन नंबर +देशकोड सकट द्या (उदा. +919876543210)')
        cl = client_factory('', api_id, api_hash)
        try:
            await asyncio.wait_for(cl.connect(), 30)
            sent = await cl.send_code_request(phone)
        except Exception as e:
            await self._quiet_disc(cl)
            raise ApiError(f'{e}'[:160])
        lid = secrets.token_urlsafe(9)
        self.logins[lid] = {'cl': cl, 'phone': phone, 'hash': sent.phone_code_hash, 'label': label[:30],
                            'api': (str(api_id), api_hash), 'exp': time.time() + 600, 'tries': 0}
        return {'login_id': lid, 'next': 'code'}

    async def a_login_code(self, lid, code):
        v = self._login(lid)
        try:
            await v['cl'].sign_in(phone=v['phone'], code=re.sub(r'\D', '', code), phone_code_hash=v['hash'])
        except Exception as e:
            if type(e).__name__ == 'SessionPasswordNeededError':
                return {'next': 'password'}
            self._bump(lid, v)
            raise ApiError(f'{e}'[:160])
        return await self._finish(lid, v)

    async def a_login_password(self, lid, pw):
        v = self._login(lid)
        try:
            await v['cl'].sign_in(password=pw)
        except Exception as e:
            self._bump(lid, v)
            raise ApiError(f'{e}'[:160])
        return await self._finish(lid, v)

    def _login(self, lid):
        v = self.logins.get(lid)
        if not v or v['exp'] < time.time():
            raise ApiError('login ची वेळ संपली — परत सुरू करा', 410)
        return v

    def _bump(self, lid, v):
        v['tries'] += 1
        if v['tries'] >= 5:
            self.logins.pop(lid, None)
            asyncio.ensure_future(self._quiet_disc(v['cl']), loop=self.loop)

    async def _finish(self, lid, v):
        cl = v['cl']
        me = await cl.get_me()
        loop = asyncio.get_running_loop()
        if any(d.get('uid') == me.id for d in await loop.run_in_executor(None, self._docs)):
            self.logins.pop(lid, None)
            await self._quiet_disc(cl)
            raise ApiError('हा Telegram account आधीच जोडलेला आहे')
        aid = 'a_' + secrets.token_hex(4)
        name = _name(me)
        phone = v.get('phone') or (f"+{me.phone}" if getattr(me, 'phone', None) else '')
        doc = {'_id': aid, 'label': v['label'] or name, 'name': name, 'phone': phone, 'uid': me.id,
               'username': getattr(me, 'username', '') or '',
               'api_id_enc': enc(v['api'][0]), 'api_hash_enc': enc(v['api'][1]),
               'session_enc': enc(cl.session.save()), 'created': time.time(), 'enabled': True}
        await loop.run_in_executor(None, self.acc.insert_one, doc)
        self.logins.pop(lid, None)
        self.premium[aid] = bool(getattr(me, 'premium', False))
        await asyncio.sleep(1.2)
        self._hook(aid, cl)
        self.clients[aid] = cl
        self.state[aid] = {'state': 'online', 'err': ''}
        return {'next': 'done', 'account': aid, 'name': name}

    # --- bot / UI साठी sync entry points
    def acc_ids(self):
        return [d['_id'] for d in self._docs()]

    def accounts_view(self):
        out = []
        for d in self._docs():
            st = self.state.get(d['_id'], {'state': 'connecting' if d.get('enabled', True) else 'disabled', 'err': ''})
            if not self.lease and d.get('enabled', True):
                st = {'state': 'standby', 'err': 'दुसरा instance चालवतोय'}
            out.append({'storage': bool(STORAGE_UID() and d.get('uid') == STORAGE_UID()),
                        'id': d['_id'], 'label': d.get('label', ''), 'name': d.get('name', ''), 'phone': d.get('phone', ''),
                        'username': d.get('username', ''),
                        'uid': d.get('uid'), 'enabled': d.get('enabled', True), 'state': st['state'], 'err': st.get('err', ''),
                        'premium': self.premium.get(d['_id'], False), 'max_upload': self.limit_for(d['_id'])})
        return out

    def set_enabled(self, aid, on):
        r = self.acc.update_one({'_id': aid}, {'$set': {'enabled': bool(on)}})
        if not r.matched_count:
            return False
        if not on:
            self.call(self.stop_client(aid))
        else:
            self.next_try.pop(aid, None)
            self.state.pop(aid, None)
        return True

    def remove_account(self, aid, logout):
        doc = self.acc.find_one({'_id': aid})
        if not doc:
            return False
        async def go():
            cl = self.clients.pop(aid, None)
            if cl:
                try:
                    await (cl.log_out() if logout else cl.disconnect())
                except Exception:
                    pass
        self.call(go())
        self.acc.delete_one({'_id': aid})
        self.state.pop(aid, None)
        return True


# ───────────────────────── Flask blueprint (/app) ─────────────────────────
bp = Blueprint('tgapp', __name__, url_prefix='/app')

def new_token():
    t = secrets.token_urlsafe(24)
    with TOKENS_LOCK:
        TOKENS[t] = time.time() + 120
    return t

def _ip():
    return request.headers.get('X-Forwarded-For', '').split(',')[0].strip() or request.remote_addr or '?'

def _make_dl_tok(ttl=86400 * 7):
    import hmac, hashlib
    exp = int(time.time() + ttl)
    sig = hmac.new(SESSION_KEY.encode(), f'dl:{exp}'.encode(), hashlib.sha256).hexdigest()[:32]
    return f'{exp}.{sig}'

def _verify_dl_tok(tok):
    import hmac, hashlib
    if not tok or '.' not in str(tok):
        return False
    exp_s, _, sig = str(tok).partition('.')
    if not exp_s.isdigit() or int(exp_s) < time.time():
        return False
    want = hmac.new(SESSION_KEY.encode(), f'dl:{exp_s}'.encode(), hashlib.sha256).hexdigest()[:32]
    return secrets.compare_digest(sig, want)

def session_ok():
    if bool(session.get('tg_ok')) and time.time() < session.get('tg_exp', 0):
        return True
    if request.method in ('GET', 'HEAD') and request.path.rstrip('/') in ('/app/api/file', '/app/api/thumb', '/app/api/avatar'):
        if _verify_dl_tok(request.args.get('dt', '')):
            return True
    return False

def _sec_headers():
    return {'Cache-Control': 'no-store', 'X-Content-Type-Options': 'nosniff',
            'Content-Security-Policy': "default-src 'self'; connect-src 'self'; img-src 'self' data: blob:; "
                                       "media-src 'self' data: blob:; font-src 'self' data:; style-src 'self' 'unsafe-inline'; "
                                       "script-src 'self' 'unsafe-inline'; worker-src 'self'; manifest-src 'self'; "
                                       "frame-src 'self'; base-uri 'self'; form-action 'self'; frame-ancestors 'self'"}

_APP_ICON_SVG = '''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 512 512">
<defs><linearGradient id="g" x1="0%" y1="0%" x2="100%" y2="100%"><stop offset="0%" stop-color="#2AABEE"/><stop offset="100%" stop-color="#229ED9"/></linearGradient></defs>
<rect width="512" height="512" rx="115" fill="url(#g)"/>
<path d="M119 251l234-90c11-4 20 3 17 19l-40 188c-3 13-11 16-22 10l-62-46-30 29c-3 3-6 6-13 6l4-63 115-104c5-4-1-7-8-3L172 287l-61-19c-13-4-13-13 8-17z" fill="#fff"/>
</svg>'''


def _make_apk_png(size=192, bg=(36, 129, 204), fg=(255, 255, 255), maskable=False):
    """Ultra-fast pure-Python RGBA PNG generator for 192x192 & 512x512 WebAPK icons (any + maskable safe-zone)."""
    import zlib, struct
    w = h = int(size)
    bg_px = bytes((bg[0], bg[1], bg[2], 255))
    ac_px = bytes((max(0, bg[0] - 14), max(0, bg[1] - 18), max(0, bg[2] - 22), 255))
    fg_px = bytes((fg[0], fg[1], fg[2], 255))
    full_bg_row = b"\x00" + bg_px * w
    rows = []
    pad = 0.30 if maskable else 0.24
    m0, m1 = int(h * pad), int(h * (1.0 - pad))
    c0, c1 = int(h * (pad - 0.08)), int(h * (1.0 - pad + 0.08))
    for y in range(h):
        if m0 <= y < m1:
            rel = abs(y - (h // 2)) / float(max(1, (m1 - m0) // 2))
            x_start = int(w * (0.35 if maskable else 0.31))
            x_end = max(x_start + 2, int(w * (0.70 if maskable else 0.73) - rel * (w * 0.34)))
            left = x_start
            mid = x_end - x_start
            right = w - x_end
            rows.append(b"\x00" + bg_px * left + fg_px * mid + bg_px * right)
        elif c0 <= y < c1:
            rows.append(b"\x00" + ac_px * w)
        else:
            rows.append(full_bg_row)
    raw = b"".join(rows)
    def _chunk(tag, data):
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data) & 0xffffffff)
    ihdr = struct.pack(">IIBBBBB", w, h, 8, 6, 0, 0, 0)
    return b"\x89PNG\r\n\x1a\n" + _chunk(b"IHDR", ihdr) + _chunk(b"IDAT", zlib.compress(raw, 6)) + _chunk(b"IEND", b"")

_PNG_CACHE = {}
def _get_apk_png(size, bg=(36, 129, 204), maskable=False):
    k = (size, bg, bool(maskable))
    if k not in _PNG_CACHE:
        _PNG_CACHE[k] = _make_apk_png(size, bg=bg, maskable=maskable)
    return _PNG_CACHE[k]

_APP_MANIFEST = {
    "id": "/app/?v=5",
    "name": "Telegram Hub Pro",
    "short_name": "TG Hub",
    "description": "Multi-Account Telegram Manager & High-Speed Downloader",
    "lang": "en",
    "dir": "ltr",
    "start_url": "/app/",
    "scope": "/app",
    "display": "standalone",
    "display_override": ["standalone", "minimal-ui"],
    "orientation": "portrait",
    "background_color": "#0b121a",
    "theme_color": "#151f2b",
    "categories": ["communication", "productivity", "utilities"],
    "prefer_related_applications": False,
    "icons": [
        {"src": "/app/icon-192.png", "sizes": "192x192", "type": "image/png", "purpose": "any"},
        {"src": "/app/icon-512.png", "sizes": "512x512", "type": "image/png", "purpose": "any"},
        {"src": "/app/icon-maskable-192.png", "sizes": "192x192", "type": "image/png", "purpose": "maskable"},
        {"src": "/app/icon-maskable-512.png", "sizes": "512x512", "type": "image/png", "purpose": "maskable"}
    ],
    "shortcuts": [
        {"name": "Open Chats", "short_name": "Chats", "url": "/app/", "icons": [{"src": "/app/icon-192.png", "sizes": "192x192", "type": "image/png"}]}
    ]
}

_APP_SW_JS = """const CACHE_NAME = 'tghub-pwa-v5';
const SHELL_ASSETS = ['/app/manifest.json', '/app/icon-192.png', '/app/icon-512.png', '/app/icon.svg'];
self.addEventListener('install', e => {
  e.waitUntil(caches.open(CACHE_NAME).then(c => c.addAll(SHELL_ASSETS)).catch(() => {}));
  self.skipWaiting();
});
self.addEventListener('activate', e => {
  e.waitUntil((
    caches.keys().then(ks => Promise.all(ks.filter(k => k !== CACHE_NAME).map(k => caches.delete(k))))
  ).then(() => self.clients.claim()));
});
self.addEventListener('fetch', e => {
  if (e.request.method !== 'GET') return;
  const url = new URL(e.request.url);
  if (SHELL_ASSETS.includes(url.pathname)) {
    e.respondWith(caches.match(e.request).then(r => r || fetch(e.request)));
    return;
  }
  if (e.request.mode === 'navigate') {
    e.respondWith(
      fetch(e.request).catch(() => new Response(
        '<!doctype html><meta charset=utf-8><meta name=viewport content="width=device-width,initial-scale=1"><title>TG Hub Offline</title><body style="background:#0b121a;color:#f1f5f9;font:15px system-ui;display:flex;align-items:center;justify-content:center;height:100vh;margin:0;text-align:center"><div><h3>📡 You are offline</h3><p>Check your internet connection and tap retry.</p><button onclick="location.reload()" style="padding:10px 18px;border-radius:8px;border:0;background:#2481cc;color:#fff;font-weight:700">🔄 Retry</button></div></body>',
        {status: 200, headers: {'Content-Type': 'text/html; charset=utf-8'}}
      ))
    );
  }
});
"""

@bp.get('/manifest.json')
def pwa_manifest():
    body = json.dumps(_APP_MANIFEST).encode('utf-8')
    return Response(body, mimetype='application/manifest+json',
                    headers={'Content-Length': str(len(body)), 'Cache-Control': 'public, max-age=300'})

@bp.get('/sw.js')
def pwa_sw():
    body = _APP_SW_JS.encode('utf-8')
    return Response(body, mimetype='application/javascript',
                    headers={'Content-Length': str(len(body)), 'Cache-Control': 'no-cache', 'Service-Worker-Allowed': '/'})

@bp.get('/icon.svg')
def pwa_icon():
    body = _APP_ICON_SVG.encode('utf-8')
    return Response(body, mimetype='image/svg+xml',
                    headers={'Content-Length': str(len(body)), 'Cache-Control': 'public, max-age=86400'})

@bp.get('/icon-192.png')
def pwa_icon_192():
    body = _get_apk_png(192, maskable=False)
    return Response(body, mimetype='image/png',
                    headers={'Content-Length': str(len(body)), 'Cache-Control': 'public, max-age=86400'})

@bp.get('/icon-512.png')
def pwa_icon_512():
    body = _get_apk_png(512, maskable=False)
    return Response(body, mimetype='image/png',
                    headers={'Content-Length': str(len(body)), 'Cache-Control': 'public, max-age=86400'})

@bp.get('/icon-maskable-192.png')
def pwa_icon_maskable_192():
    body = _get_apk_png(192, maskable=True)
    return Response(body, mimetype='image/png',
                    headers={'Content-Length': str(len(body)), 'Cache-Control': 'public, max-age=86400'})

@bp.get('/icon-maskable-512.png')
def pwa_icon_maskable_512():
    body = _get_apk_png(512, maskable=True)
    return Response(body, mimetype='image/png',
                    headers={'Content-Length': str(len(body)), 'Cache-Control': 'public, max-age=86400'})

@bp.before_request
def gate():
    if M is None:
        abort(404)
    p = request.path.rstrip('/')
    is_read = request.method in ('GET', 'HEAD')
    if is_read and p in ('/app/manifest.json', '/app/sw.js', '/app/icon.svg', '/app/icon-192.png', '/app/icon-512.png', '/app/icon-maskable-192.png', '/app/icon-maskable-512.png'):
        ua = request.headers.get('User-Agent', '')[:120]
        PWA_LOGS.appendleft({'ts': int(time.time()), 'method': request.method, 'path': request.full_path.rstrip('?'), 'ip': _ip(), 'ua': ua, 'is_webapk_server': ('WebAPK' in ua or 'Google' in ua or 'Dalvik' in ua)})
        return None
    # Critical PWA Scope Fix: always redirect /app (without trailing slash) to /app/ so the page is inside scope '/app/'!
    if is_read and request.path == '/app':
        qs = request.query_string.decode('utf-8', 'ignore')
        loc = '/app/' + ('?' + qs if qs else '')
        return Response('', status=302, headers={'Location': loc, 'Cache-Control': 'no-store'})
    if is_read and p == '/app' and (request.args.get('k') or request.args.get('dt')):
        return Response(ENTER_PAGE, mimetype='text/html', headers=_sec_headers())
    if is_read and p.startswith('/app/s/'):
        return None
    if request.method == 'POST' and p == '/app/enter':
        return None
    if not session_ok():
        # Allow GET/HEAD on /app (/app/) so Android WebAPK installer & installed Home Screen icon always get 200 OK!
        if is_read and p == '/app':
            return Response(ENTER_PAGE, mimetype='text/html', headers=_sec_headers())
        abort(404)
    # Auto-extend session expiry so installed Mobile/PC app stays logged in
    if session.get('tg_ok') and time.time() - (session.get('tg_exp', 0) - UI_SESSION_SECS) > 3600:
        session.permanent = True
        session['tg_exp'] = time.time() + UI_SESSION_SECS
    if request.method == 'POST':
        if request.headers.get('X-Requested-With') != 'app':
            abort(404)
        o = request.headers.get('Origin')
        if o and o.split('://', 1)[-1] != request.host:
            abort(404)
    return None

@bp.post('/enter')
def enter():
    d = request.get_json(silent=True) or {}
    tok, dt_tok, k, now = str(d.get('k', '')).strip(), str(d.get('dt', '')).strip(), _ip(), time.time()
    FAILS[k] = [t for t in FAILS[k] if now - t < 300]
    if len(FAILS[k]) >= 8:
        abort(404)
    # Allow re-authenticating installed PWA via saved valid dt_tok (HMAC signed)
    if dt_tok and _verify_dl_tok(dt_tok):
        session.clear()
        session.permanent = True
        session['tg_ok'], session['tg_exp'], session['a'] = True, now + UI_SESSION_SECS, True
        return jsonify(ok=1, dt=_make_dl_tok(UI_SESSION_SECS))
    hit = None
    with TOKENS_LOCK:
        for t in [t for t, e in TOKENS.items() if e < now]:
            del TOKENS[t]
        for t in list(TOKENS):
            if secrets.compare_digest(t.encode(), tok.encode()):
                hit = t
        if hit:
            del TOKENS[hit]
    if not hit:
        FAILS[k].append(now)
        time.sleep(1)
        abort(404)
    session.clear()
    session.permanent = True
    session['tg_ok'], session['tg_exp'], session['a'] = True, now + UI_SESSION_SECS, True
    return jsonify(ok=1, dt=_make_dl_tok(UI_SESSION_SECS))

def api(fn):
    import functools
    @functools.wraps(fn)
    def w(*a, **k):
        try:
            return fn(*a, **k)
        except ApiError as e:
            return jsonify(error=str(e)), e.code
        except Exception as e:
            return jsonify(error=f'{type(e).__name__}: {e}'[:200]), 500
    return w

def _int(name, default=None, src=None):
    v = (src if src is not None else request.values).get(name, default)
    try:
        return int(v)
    except (TypeError, ValueError):
        raise ApiError(f'{name} चुकीचा')

def _json():
    return request.get_json(silent=True) or {}

def _acc(d=None):
    aid = (d or {}).get('acc') or request.values.get('acc')
    if not aid or not re.fullmatch(r'a_[0-9a-f]{8}', str(aid)):
        raise ApiError('account निवडा')
    return aid

def _ids(d, key='ids'):
    v = d.get(key)
    if not isinstance(v, list) or not v or len(v) > 100:
        raise ApiError('ids चुकीचे')
    return [int(x) for x in v]

@bp.get('', strict_slashes=False)
def ui():
    return Response(UI_PAGE, mimetype='text/html', headers=_sec_headers())

def _all_dls_snapshot():
    out = {}
    done, _ = cache_files()
    now = time.time()
    ttl = DL_CFG['ttl']
    for ctime, sz, p, k in done:
        meta = _get_cmeta(k, p)
        acc, chat, mid = meta.get('acc'), meta.get('chat'), meta.get('id')
        exp_in = max(0, int(ttl - (now - ctime))) if ttl > 0 else 0
        if acc and chat is not None and mid and (ttl <= 0 or exp_in > 0):
            out[f'{acc}:{chat}:{mid}'] = {
                'state': 'cached', 'done': sz, 'contig': sz, 'size': sz,
                'speed': 0, 'threads': DL_CFG['threads'], 'expires_in': exp_in,
                'name': meta.get('name', ''), 'acc': acc, 'chat': chat, 'id': mid
            }
    with DL_LOCK:
        for k, j in list(DL.items()):
            if j.get('state') in ('downloading', 'error'):
                ui_k = f"{j['acc']}:{j['chat']}:{j['id']}"
                out[ui_k] = {
                    'state': j['state'], 'done': j.get('done', 0), 'contig': j.get('contig', 0),
                    'size': j.get('size', 0), 'speed': j.get('speed', 0),
                    'threads': j.get('threads', DL_CFG['threads']), 'err': j.get('err', ''),
                    'name': j.get('name', ''), 'expires_in': ttl,
                    'acc': j['acc'], 'chat': j['chat'], 'id': j['id']
                }
    return out

@bp.get('/api/pwa/diag')
@api
def api_pwa_diag():
    return jsonify(logs=list(PWA_LOGS)[:30], manifest=_APP_MANIFEST, host=request.host, scheme=request.headers.get('X-Forwarded-Proto', request.scheme))

@bp.get('/api/state')
@api
def api_state():
    return jsonify(accounts=M.accounts_view(), bw=bw_status(), default_api=bool(DEF_API_ID and DEF_API_HASH),
                   chunk=CHUNK, seq=M.seq, dl_cfg=DL_CFG, dl_tok=_make_dl_tok(), downloads=_all_dls_snapshot())

@bp.get('/api/storage/state')
@api
def api_storage_state():
    info = STORAGE.user_info() if STORAGE else {'ready': False}
    linked = next((a['id'] for a in M.accounts_view() if a.get('storage')), None)
    return jsonify(available=STORAGE is not None, ready=bool(info.get('ready')), me=info.get('me', ''),
                   uid=info.get('uid'), linked=linked, error=info.get('error', ''))

@bp.post('/api/storage/link')
@api
def api_storage_link():
    """UI account -> Storage: Storage asks Telegram for a login token, this account approves it."""
    if STORAGE is None:
        raise ApiError('Storage बंद आहे')
    d = _json()
    lid = d.get('link_id')
    if not lid:
        aid = _acc(d)
        a = next((x for x in M.accounts_view() if x['id'] == aid), None)
        if a and a.get('storage'):
            return jsonify(ok=True, already=True)
        lid, token = STORAGE.qr_begin()
        M.call(M.a_accept_token(aid, token), 30)
    return jsonify(STORAGE.qr_finish(lid, d.get('password') or None))

@bp.post('/api/storage/import')
@api
def api_storage_import():
    """Storage account -> UI: this server asks for a token, Storage's user account approves it."""
    if STORAGE is None or not (STORAGE.user_info() or {}).get('ready'):
        raise ApiError('Storage मध्ये user account login नाही')
    d = _json()
    lid = d.get('login_id')
    if not lid:
        r = M.call(M.a_qr_begin(str(d.get('label') or 'Storage')), 40)
        STORAGE.user_accept_token(r['token'])
        lid = r['login_id']
    return jsonify(M.call(M.a_qr_wait(lid), 150))

@bp.get('/api/dialogs')
@api
def api_dialogs():
    aid = _acc()
    want_arch = request.args.get('archived') == '1'
    dl = M.call(M.a_dialogs(aid, want_arch))
    arch = M.call(M.a_dialogs(aid, True)) if not want_arch else dl
    return jsonify(dialogs=dl, archived=arch)

@bp.get('/api/messages')
@api
def api_messages():
    lim = max(1, min(_int('limit', 40), 80))
    topic_id = _int('topic', 0)
    return jsonify(M.call(M.a_messages(_acc(), _int('chat'), _int('before', 0), lim, topic_id)))

@bp.get('/api/pinned')
@api
def api_pinned():
    return jsonify(messages=M.call(M.a_pinned_list(_acc(), _int('chat'))))

@bp.get('/api/bydate')
@api
def api_bydate():
    return jsonify(M.call(M.a_bydate(_acc(), _int('chat'), _int('ts'))))

@bp.get('/api/around')
@api
def api_around():
    return jsonify(messages=M.call(M.a_around(_acc(), _int('chat'), _int('id'))))

@bp.get('/api/search')
@api
def api_search():
    q = request.args.get('q', '').strip()[:100]
    if len(q) < 2:
        raise ApiError('कमीत कमी 2 अक्षरं')
    chat = request.args.get('chat')
    return jsonify(messages=M.call(M.a_search(_acc(), q, int(chat) if chat else None)))

@bp.get('/api/chatinfo')
@api
def api_chatinfo():
    return jsonify(info=M.call(M.a_chatinfo(_acc(), _int('chat'))))

@bp.get('/api/members')
@api
def api_members():
    return jsonify(members=M.call(M.a_members(_acc(), _int('chat'), request.args.get('q', '')[:50])))

@bp.get('/api/shared')
@api
def api_shared():
    return jsonify(messages=M.call(M.a_shared(_acc(), _int('chat'), request.args.get('kind', ''), _int('before', 0))))

@bp.get('/api/contacts')
@api
def api_contacts():
    return jsonify(contacts=M.call(M.a_contacts(_acc())))

@bp.post('/api/contact/add')
@api
def api_contact_add():
    d = _json()
    return jsonify(M.call(M.a_contact_add(_acc(d), d.get('user'), d.get('first', ''), d.get('last', ''), d.get('phone', ''))))

@bp.post('/api/contact/delete')
@api
def api_contact_delete():
    d = _json()
    return jsonify(M.call(M.a_contact_delete(_acc(d), _int('user', src=d))))

@bp.post('/api/user/block')
@api
def api_user_block():
    d = _json()
    return jsonify(M.call(M.a_user_block(_acc(d), _int('user', src=d), bool(d.get('block', True)))))

@bp.get('/api/user/common')
@api
def api_user_common():
    return jsonify(chats=M.call(M.a_common_chats(_acc(), _int('user'))))

@bp.get('/api/sessions')
@api
def api_sessions():
    return jsonify(sessions=M.call(M.a_sessions(_acc())))

@bp.post('/api/sessions/terminate')
@api
def api_terminate():
    d = _json()
    if not str(d.get('hash', '')).lstrip('-').isdigit():
        raise ApiError('hash चुकीचा')
    M.call(M.a_terminate(_acc(d), d['hash']))
    return jsonify(ok=1)

@bp.post('/api/send')
@api
def api_send():
    d = _json()
    text = str(d.get('text', ''))
    if not text.strip() or len(text) > 4096:
        raise ApiError('text रिकामा किंवा 4096 पेक्षा मोठा')
    return jsonify(message=M.call(M.a_send(_acc(d), int(d['chat']), text, d.get('reply_to'), d.get('md'), d.get('silent'),
                                           d.get('schedule'), d.get('preview', True), d.get('topic_id'))))

@bp.post('/api/edit')
@api
def api_edit():
    d = _json()
    text = str(d.get('text', ''))
    if not text.strip() or len(text) > 4096:
        raise ApiError('text रिकामा किंवा मोठा')
    return jsonify(message=M.call(M.a_edit(_acc(d), int(d['chat']), int(d['id']), text, d.get('md'))))

@bp.post('/api/delete')
@api
def api_delete():
    d = _json()
    M.call(M.a_delete(_acc(d), int(d['chat']), _ids(d), d.get('revoke', True)))
    return jsonify(ok=1)

@bp.post('/api/forward')
@api
def api_forward():
    d = _json()
    if isinstance(d.get('to_chats'), list) and d.get('to_chats'):
        to_chats = [int(x) for x in d['to_chats'][:10]]
    elif d.get('to_chat') is not None:
        to_chats = [int(d['to_chat'])]
    else:
        raise ApiError('target chat निवडा')
    return jsonify(messages=M.call(M.a_forward(_acc(d), int(d['from_chat']), _ids(d), to_chats, d.get('hide_author'), str(d.get('comment', '')))))

@bp.post('/api/pin')
@api
def api_pin():
    d = _json()
    M.call(M.a_pin(_acc(d), int(d['chat']), int(d['id']), d.get('unpin'), d.get('notify')))
    return jsonify(ok=1)

@bp.post('/api/react')
@api
def api_react():
    d = _json()
    em = str(d.get('emoji') or '')[:8]
    return jsonify(message=M.call(M.a_react(_acc(d), int(d['chat']), int(d['id']), em)))

@bp.post('/api/vote')
@api
def api_vote():
    d = _json()
    return jsonify(message=M.call(M.a_vote(_acc(d), int(d['chat']), int(d['id']), [int(x) for x in d.get('options', [])][:10])))

@bp.post('/api/poll/create')
@api
def api_poll_create():
    d = _json()
    return jsonify(message=M.call(M.a_send_poll(
        _acc(d), int(d['chat']), str(d.get('question', '')), d.get('options', []),
        bool(d.get('multi')), bool(d.get('quiz')), bool(d.get('public')),
        int(d.get('correct', 0) or 0), str(d.get('solution', '')), d.get('reply_to')
    )))

@bp.post('/api/poll/close')
@api
def api_poll_close():
    d = _json()
    return jsonify(message=M.call(M.a_close_poll(_acc(d), int(d['chat']), int(d['id']))))

@bp.get('/api/scheduled')
@api
def api_scheduled():
    return jsonify(messages=M.call(M.a_scheduled_list(_acc(), _int('chat'))))

@bp.post('/api/scheduled/delete')
@api
def api_scheduled_delete():
    d = _json()
    return jsonify(M.call(M.a_scheduled_delete(_acc(d), int(d['chat']), _ids(d))))

@bp.post('/api/scheduled/sendnow')
@api
def api_scheduled_sendnow():
    d = _json()
    return jsonify(M.call(M.a_scheduled_sendnow(_acc(d), int(d['chat']), _ids(d))))

@bp.post('/api/translate')
@api
def api_translate():
    d = _json()
    return jsonify(M.call(M.a_translate(_acc(d), int(d['chat']), int(d['id']), str(d.get('to', 'en')))))

@bp.post('/api/click')
@api
def api_click():
    d = _json()
    return jsonify(M.call(M.a_click(_acc(d), int(d['chat']), int(d['id']), int(d['i']), int(d['j']))))

@bp.post('/api/read')
@api
def api_read():
    d = _json()
    M.call(M.a_read(_acc(d), int(d['chat']), int(d.get('max_id', 0))))
    return jsonify(ok=1)

@bp.post('/api/typing')
@api
def api_typing():
    d = _json()
    M.call(M.a_typing(_acc(d), int(d['chat'])))
    return jsonify(ok=1)

@bp.post('/api/chat/mute')
@api
def api_chat_mute():
    d = _json()
    M.call(M.a_mute(_acc(d), int(d['chat']), d.get('mute')))
    return jsonify(ok=1)

@bp.post('/api/chat/pin')
@api
def api_chat_pin():
    d = _json()
    M.call(M.a_chatpin(_acc(d), int(d['chat']), d.get('pin')))
    return jsonify(ok=1)

@bp.post('/api/chat/archive')
@api
def api_chat_archive():
    d = _json()
    M.call(M.a_archive(_acc(d), int(d['chat']), d.get('archive')))
    return jsonify(ok=1)

@bp.post('/api/chat/delete')
@api
def api_chat_delete():
    d = _json()
    M.call(M.a_chatdelete(_acc(d), int(d['chat'])))
    return jsonify(ok=1)

@bp.post('/api/chat/create')
@api
def api_chat_create():
    d = _json()
    kind = d.get('kind')
    if kind not in ('group', 'channel'):
        raise ApiError('bad kind', 400)
    return jsonify(M.call(M.a_create_chat(_acc(d), d.get('title'), d.get('about'), kind), 40))

@bp.post('/api/chat/edit')
@api
def api_chat_edit():
    d = _json()
    return jsonify(M.call(M.a_edit_chat_info(_acc(d), _int('chat', src=d), d.get('title'), d.get('about')), 30))

@bp.get('/api/chat/invites')
@api
def api_chat_invites():
    return jsonify(invites=M.call(M.a_chat_invites(_acc(), _int('chat')), 25))

@bp.post('/api/chat/invite/create')
@api
def api_chat_invite_create():
    d = _json()
    return jsonify(M.call(M.a_chat_invite_create(_acc(d), _int('chat', src=d), d.get('title', ''), d.get('limit', 0)), 25))

@bp.post('/api/chat/slowmode')
@api
def api_chat_slowmode():
    d = _json()
    return jsonify(M.call(M.a_chat_slowmode(_acc(d), _int('chat', src=d), _int('seconds', 0, src=d)), 25))

@bp.post('/api/chat/member/add')
@api
def api_member_add():
    d = _json()
    return jsonify(M.call(M.a_add_member(_acc(d), _int('chat', src=d), str(d.get('q', ''))), 30))

@bp.post('/api/chat/member/kick')
@api
def api_member_kick():
    d = _json()
    return jsonify(M.call(M.a_kick_member(_acc(d), _int('chat', src=d), _int('uid', src=d)), 30))

@bp.post('/api/chat/member/admin')
@api
def api_member_admin():
    d = _json()
    return jsonify(M.call(M.a_set_admin(_acc(d), _int('chat', src=d), _int('uid', src=d), bool(d.get('promote'))), 30))

@bp.post('/api/chat/forum/toggle')
@api
def api_forum_toggle():
    d = _json()
    return jsonify(M.call(M.a_toggle_forum(_acc(d), _int('chat', src=d), bool(d.get('enabled'))), 30))

@bp.get('/api/chat/topics')
@api
def api_topics():
    return jsonify(topics=M.call(M.a_topics(_acc(), _int('chat')), 20))

@bp.post('/api/chat/topic/create')
@api
def api_topic_create():
    d = _json()
    return jsonify(M.call(M.a_create_topic(_acc(d), _int('chat', src=d), d.get('title')), 30))

@bp.post('/api/chat/topic/edit')
@api
def api_topic_edit():
    d = _json()
    return jsonify(M.call(M.a_edit_topic(_acc(d), _int('chat', src=d), _int('topic_id', src=d),
                                         d.get('title'), d.get('closed')), 30))

@bp.post('/api/chat/topic/delete')
@api
def api_topic_delete():
    d = _json()
    return jsonify(M.call(M.a_delete_topic(_acc(d), _int('chat', src=d), _int('topic_id', src=d)), 30))

@bp.post('/api/resolve')
@api
def api_resolve():
    d = _json()
    return jsonify(dialog=M.call(M.a_resolve(_acc(d), str(d.get('q', '')))))

@bp.post('/api/join')
@api
def api_join():
    d = _json()
    return jsonify(dialog=M.call(M.a_join(_acc(d), str(d.get('q', '')))))

@bp.post('/api/me')
@api
def api_me():
    d = _json()
    M.call(M.a_setme(_acc(d), str(d.get('first', ''))[:64], str(d.get('last', ''))[:64],
                     str(d.get('about', ''))[:140], d.get('username')))
    return jsonify(ok=1)

@bp.post('/api/me/photo')
@api
def api_me_photo():
    aid = _acc()
    raw = request.get_data()
    if not raw or len(raw) > 10 * MB:
        raise ApiError('फोटो 10 MB पेक्षा लहान असावा')
    return jsonify(M.call(M.a_set_profile_photo(aid, raw), 45))

@bp.post('/api/chat/photo')
@api
def api_chat_photo():
    aid, chat = _acc(), _int('chat')
    raw = request.get_data()
    if not raw or len(raw) > 10 * MB:
        raise ApiError('फोटो 10 MB पेक्षा लहान असावा')
    return jsonify(M.call(M.a_set_chat_photo(aid, chat, raw), 45))

# --- images: avatar / thumb
def _img(data, mime, days=7):
    return Response(data, mimetype=mime, headers={'Cache-Control': f'private, max-age={days * 86400}',
                                                  'X-Content-Type-Options': 'nosniff', 'Content-Security-Policy': "default-src 'none'; sandbox"})

@bp.get('/api/avatar')
@api
def api_avatar():
    data = M.call(M.a_avatar(_acc(), _int('chat')))
    if not data:
        return Response(status=204, headers={'Cache-Control': 'private, max-age=3600'})
    return _img(data, 'image/jpeg')

@bp.get('/api/thumb')
@api
def api_thumb():
    data = M.call(M.a_thumb(_acc(), _int('chat'), _int('id')), 90)
    mime = 'image/webp' if data[:4] == b'RIFF' else ('image/png' if data[:4] == b'\x89PNG' else 'image/jpeg')
    return _img(data, mime, 1)

# ───────────────────────── Fast File Viewing, Multi-Thread Caching & Progressive Streaming ─────────────────────────
INLINE_OK = {'image/jpeg', 'image/png', 'image/webp', 'image/gif', 'video/mp4', 'video/webm', 'audio/mpeg', 'audio/ogg',
             'audio/mp4', 'audio/webm', 'audio/wav', 'audio/x-wav', 'audio/aac'}

def parse_range(h, size):
    if not h:
        return 0, size - 1, False
    m = re.fullmatch(r'bytes=(\d*)-(\d*)', h.strip())
    if not m or (m.group(1) == '' and m.group(2) == ''):
        return 0, size - 1, False
    a, b = m.groups()
    if a == '':
        start, end = max(0, size - int(b)), size - 1
    else:
        start, end = int(a), (int(b) if b else size - 1)
    if start >= size or start > end:
        raise ApiError('range चुकीचा', 416)
    return start, min(end, size - 1), True

TEXT_EXT = {'.txt', '.md', '.json', '.csv', '.log', '.py', '.js', '.ts', '.css', '.xml', '.yml', '.yaml', '.ini', '.conf',
            '.sh', '.sql', '.c', '.cpp', '.h', '.java', '.go', '.rs', '.php', '.rb', '.html', '.htm', '.env', '.toml'}

def view_mime(info):
    """Content-Type that may be shown inline in the browser (None = download only)."""
    m, name = (info.get('mime') or '').lower(), (info.get('name') or '').lower()
    if m in INLINE_OK:
        return m
    if m == 'application/pdf' or name.endswith('.pdf'):
        return 'application/pdf'
    if m.startswith('text/') or m in ('application/json', 'application/xml') or os.path.splitext(name)[1] in TEXT_EXT:
        return 'text/plain'
    return None

def _file_headers(info, mid, start, end, size, partial):
    length = end - start + 1
    vm = view_mime(info)
    mime = vm or 'application/octet-stream'
    dl = request.args.get('dl') == '1'
    disp = 'attachment' if dl or not vm else 'inline'
    fname = urllib.parse.quote(re.sub(r'[\r\n"\\]', '_', info['name'] or f'file_{mid}'))
    etag = f'"{mid}-{size}"'
    h = {'Content-Length': str(length), 'Accept-Ranges': 'bytes', 'ETag': etag,
         'Last-Modified': 'Wed, 01 Jan 2025 00:00:00 GMT',
         'Content-Disposition': f"{disp}; filename*=UTF-8''{fname}",
         'X-Content-Type-Options': 'nosniff', 'Cache-Control': 'private, max-age=3600', 'X-BW-Counted': '1'}
    if mime != 'application/pdf' or disp == 'attachment':
        h['Content-Security-Policy'] = "default-src 'none'; sandbox"
    if partial:
        h['Content-Range'] = f'bytes {start}-{end}/{size}'
    return mime, h

def _dl_state(aid, chat, mid):
    key = _ckey(aid, chat, mid)
    cp = _cpath(key)
    if os.path.isfile(cp):
        sz = os.path.getsize(cp)
        meta = _get_cmeta(key, cp)
        ctime = float(meta.get('created_at') or os.path.getmtime(cp))
        ttl = DL_CFG['ttl']
        exp_in = max(0, int(ttl - (time.time() - ctime))) if ttl > 0 else 0
        if ttl > 0 and exp_in <= 0:
            _remove_cached(key)
        else:
            return {
                'state': 'cached', 'done': sz, 'contig': sz, 'size': sz,
                'speed': 0, 'threads': DL_CFG['threads'], 'expires_in': exp_in,
                'name': meta.get('name', '')
            }
    with DL_LOCK:
        j = DL.get(key)
    if j and j['state'] in ('downloading', 'error'):
        return {
            'state': j['state'], 'done': j['done'], 'contig': j.get('contig', 0),
            'size': j['size'], 'speed': j.get('speed', 0), 'threads': j.get('threads', DL_CFG['threads']),
            'err': j.get('err', ''), 'name': j.get('name', ''), 'expires_in': DL_CFG['ttl']
        }
    return {'state': 'none', 'done': 0, 'contig': 0, 'size': 0, 'speed': 0, 'threads': DL_CFG['threads']}

def _start_server_dl_job(aid, chat, mid, info=None, force=False):
    """Starts a multi-threaded background download job to CACHE_DIR if not already running/cached."""
    key = _ckey(aid, chat, mid)
    cur = _dl_state(aid, chat, mid)
    if cur['state'] in ('cached', 'downloading'):
        with DL_LOCK:
            return DL.get(key), cur
    if info is None:
        info = M.call(M.a_info(aid, chat, mid))
    size = int(info.get('size') or 0)
    if size <= 0:
        return None, cur
    os.makedirs(CACHE_DIR, exist_ok=True)
    cache_evict(size)
    try:
        free_space = shutil.disk_usage(CACHE_DIR).free
    except Exception:
        free_space = size + 500 * MB
    if free_space < size + 80 * MB:
        if force:
            raise ApiError('server वर पुरेशी disk जागा नाही — जुना cache साफ करा किंवा थेट stream होईल', 507)
        return None, cur
    with DL_LOCK:
        active = sum(1 for x in DL.values() if x.get('state') == 'downloading')
        if active >= 10:
            if force:
                raise ApiError('एकाच वेळी जास्तीत जास्त 10 फाइल्स server वर download होऊ शकतात', 429)
            return None, cur
        j = DL.get(key)
        if not j or j.get('state') not in ('downloading', 'cached'):
            j = {
                'key': key, 'acc': aid, 'chat': chat, 'id': mid,
                'name': info.get('name') or f'file_{mid}',
                'mime': info.get('mime') or 'application/octet-stream',
                'size': size, 'done': 0, 'contig': 0, 'speed': 0,
                'threads': DL_CFG['threads'], 'state': 'downloading', 'err': '',
                'started_at': time.time(), 'cond': threading.Condition()
            }
            DL[key] = j
            M.dl_tasks[key] = asyncio.run_coroutine_threadsafe(M.a_download(key), M.loop)
        st = {
            'state': j['state'], 'done': j['done'], 'contig': j['contig'],
            'size': j['size'], 'speed': j['speed'], 'threads': j['threads'],
            'expires_in': DL_CFG['ttl']
        }
        return j, st


@bp.get('/api/file')
@api
def api_file():
    aid, chat, mid = _acc(), _int('chat'), _int('id')
    info = M.call(M.a_info(aid, chat, mid))
    size = info['size']
    if size <= 0:
        raise ApiError('फाइल रिकामी आहे', 404)
    if MAX_MEDIA and size > MAX_MEDIA:
        raise ApiError(f'फाइल {MAX_MEDIA // MB} MB पेक्षा मोठी आहे', 413)
    start, end, partial = parse_range(request.headers.get('Range'), size)
    length = end - start + 1
    st = bw_status()
    if st['limit'] and st['used'] + length > st['limit']:
        raise ApiError('या महिन्याची bandwidth मर्यादा संपली — फक्त text चालतो', 429)
    mime, h = _file_headers(info, mid, start, end, size, partial)
    key = _ckey(aid, chat, mid)
    cp = _cpath(key)
    part = cp + '.part'

    # 1) Already cached on server disk -> serve immediately with 1 MB high-speed buffer!
    cur_st = _dl_state(aid, chat, mid)
    if cur_st['state'] == 'cached' and os.path.isfile(cp) and os.path.getsize(cp) == size:
        def gen_disk():
            remaining = length
            with open(cp, 'rb') as f:
                f.seek(start)
                while remaining > 0:
                    chunk = f.read(min(DISK_READ_BUF, remaining))
                    if not chunk:
                        break
                    remaining -= len(chunk)
                    bw_add(len(chunk))
                    yield chunk
        return Response(gen_disk(), status=206 if partial else 200, mimetype=mime, headers=h, direct_passthrough=True)

    # 2) Whenever a file/video/media is opened in the browser OR Download (?dl=1) is clicked,
    #    automatically start the multi-threaded server download in the background!
    is_probe = (start == 0 and end == 0)
    j = None
    if DL_CFG.get('auto_cache', True) and not is_probe:
        try:
            j, _ = _start_server_dl_job(aid, chat, mid, info=info, force=False)
        except Exception:
            j = None
    else:
        with DL_LOCK:
            j = DL.get(key)

    # 3) If the multi-threaded server download is active and the requested `start` is within
    #    (or close to) the contiguous downloaded region (`contig`), stream directly from the
    #    growing server disk file (.part / final) at full multi-threaded speed!
    if j and not is_probe and j.get('state') in ('downloading', 'cached') and start <= j.get('contig', 0) + 12 * MB:
        def gen_from_server_job():
            pos = start
            remaining = length
            cond = j['cond']
            while remaining > 0:
                with cond:
                    while True:
                        st_now = j.get('state')
                        contig_now = j.get('contig', 0)
                        if st_now == 'cached' or contig_now > pos or st_now in ('error', 'none') or j.get('cancel'):
                            break
                        cond.wait(timeout=0.5)
                    st_now = j.get('state')
                    contig_now = j['size'] if st_now == 'cached' else j.get('contig', 0)
                if st_now in ('error', 'none') or j.get('cancel'):
                    # Fallback to direct Telegram stream for remaining bytes so browser download never breaks
                    if remaining > 0 and not j.get('cancel'):
                        try:
                            it_fb = M.call(M.a_iter(aid, chat, mid, pos - pos % REQ, size))
                            skip_fb = pos % REQ
                            try:
                                while remaining > 0:
                                    ch_fb = M.call(M.a_next(it_fb), 120)
                                    if not ch_fb:
                                        break
                                    if skip_fb:
                                        ch_fb, skip_fb = ch_fb[skip_fb:], 0
                                    ch_fb = ch_fb[:remaining]
                                    remaining -= len(ch_fb)
                                    bw_add(len(ch_fb))
                                    yield ch_fb
                            finally:
                                try:
                                    M.call(M.a_close(it_fb), 10)
                                except Exception:
                                    pass
                        except Exception:
                            pass
                    break
                target_file = cp if (st_now == 'cached' and os.path.isfile(cp)) else part
                avail = min(remaining, max(0, contig_now - pos))
                if avail <= 0:
                    if st_now == 'cached':
                        break
                    continue
                read_len = min(DISK_READ_BUF, avail)
                try:
                    with open(target_file, 'rb') as f:
                        f.seek(pos)
                        chunk = f.read(read_len)
                except OSError:
                    if os.path.isfile(cp):
                        with open(cp, 'rb') as f:
                            f.seek(pos)
                            chunk = f.read(read_len)
                    else:
                        break
                if not chunk:
                    break
                pos += len(chunk)
                remaining -= len(chunk)
                bw_add(len(chunk))
                yield chunk
        return Response(gen_from_server_job(), status=206 if partial else 200, mimetype=mime, headers=h, direct_passthrough=True)

    # 4) Fallback / Random-access Range seek (e.g. MP4 moov atom at end of file before server download reaches end,
    #    or when disk is full): stream directly from Telegram while background server caching continues!
    if not STREAMS.acquire(timeout=25):
        raise ApiError('खूप downloads चालू आहेत — थोड्या वेळाने', 503)
    try:
        it = M.call(M.a_iter(aid, chat, mid, start - start % REQ, size))
    except Exception:
        STREAMS.release()
        raise

    def gen():
        skip, remaining = start % REQ, length
        try:
            while remaining > 0:
                chunk = M.call(M.a_next(it), 120)
                if not chunk:
                    break
                if skip:
                    chunk, skip = chunk[skip:], 0
                chunk = chunk[:remaining]
                remaining -= len(chunk)
                bw_add(len(chunk))
                yield chunk
        finally:
            try:
                M.call(M.a_close(it), 10)
            except Exception:
                pass
            STREAMS.release()

    return Response(gen(), status=206 if partial else 200, mimetype=mime, headers=h, direct_passthrough=True)


# --- Server disk cache & Download Manager APIs
@bp.post('/api/dl/start')
@api
def api_dl_start():
    d = _json()
    aid, chat, mid = _acc(d), int(d['chat']), int(d['id'])
    cur = _dl_state(aid, chat, mid)
    if cur['state'] in ('cached', 'downloading'):
        return jsonify(cur)
    info = M.call(M.a_info(aid, chat, mid))
    if info['size'] <= 0:
        raise ApiError('फाइल रिकामी आहे', 404)
    _, st = _start_server_dl_job(aid, chat, mid, info=info, force=True)
    return jsonify(st)

@bp.post('/api/dl/cancel')
@api
def api_dl_cancel():
    d = _json()
    key = _ckey(_acc(d), int(d['chat']), int(d['id']))
    with DL_LOCK:
        j = DL.get(key)
    if j:
        j['cancel'] = True
        with j['cond']:
            j['cond'].notify_all()
    _remove_cached(key)
    return jsonify(ok=1)

@bp.get('/api/dl/status')
@api
def api_dl_status():
    aid, chat = _acc(), _int('chat')
    ids = [int(x) for x in request.args.get('ids', '').split(',') if x.strip().isdigit()][:80]
    return jsonify(items={str(i): _dl_state(aid, chat, i) for i in ids})

@bp.get('/api/dl/list')
@api
def api_dl_list():
    """Returns all active downloads and all server-cached files with countdown timers."""
    cache_cleanup()
    active = []
    with DL_LOCK:
        for k, j in list(DL.items()):
            if j.get('state') in ('downloading', 'error'):
                active.append({
                    'key': k, 'acc': j['acc'], 'chat': j['chat'], 'id': j['id'],
                    'name': j.get('name') or f"file_{j['id']}",
                    'size': j.get('size', 0), 'done': j.get('done', 0),
                    'contig': j.get('contig', 0), 'speed': j.get('speed', 0),
                    'threads': j.get('threads', DL_CFG['threads']),
                    'state': j['state'], 'err': j.get('err', '')
                })
    done, _ = cache_files()
    now = time.time()
    ttl = DL_CFG['ttl']
    cached = []
    for ctime, sz, p, k in sorted(done, reverse=True):
        meta = _get_cmeta(k, p)
        exp_in = max(0, int(ttl - (now - ctime))) if ttl > 0 else 0
        cached.append({
            'key': k, 'acc': meta.get('acc', ''), 'chat': meta.get('chat', 0), 'id': meta.get('id', 0),
            'name': meta.get('name') or k, 'size': sz, 'created_at': int(ctime),
            'expires_in': exp_in
        })
    used, n = cache_usage()
    return jsonify(active=active, cached=cached, used=used, limit=DISK_CACHE, files=n, cfg=DL_CFG)

@bp.get('/api/cache')
@api
def api_cache():
    cache_cleanup()
    used, n = cache_usage()
    return jsonify(used=used, limit=DISK_CACHE, files=n, cfg=DL_CFG)

@bp.post('/api/cache/settings')
@api
def api_cache_settings():
    d = _json()
    if 'threads' in d:
        DL_CFG['threads'] = max(1, min(8, int(d['threads'])))
    if 'ttl_minutes' in d:
        DL_CFG['ttl'] = max(60, int(float(d['ttl_minutes']) * 60))
    if 'auto_cache' in d:
        DL_CFG['auto_cache'] = bool(d['auto_cache'])
    cache_cleanup()
    return jsonify(ok=1, cfg=DL_CFG)

@bp.post('/api/cache/delete')
@api
def api_cache_delete():
    d = _json()
    key = str(d.get('key', '')).strip()
    if key and re.fullmatch(r'[A-Za-z0-9_]+', key):
        _remove_cached(key)
    return jsonify(ok=1)

@bp.post('/api/cache/clear')
@api
def api_cache_clear():
    done, _ = cache_files()
    for _, _, _, k in done:
        _remove_cached(k)
    return jsonify(ok=1, removed=len(done))


# --- मोठ्या फाइलचा upload: 8 MB तुकडे → disk → Telegram
@bp.post('/api/up/start')
@api
def api_up_start():
    d = _json()
    aid, chat = _acc(d), int(d['chat'])
    size = int(d.get('size', 0))
    name = re.sub(r'[^\w.\- ()\[\]]', '_', os.path.basename(str(d.get('name', 'file'))))[:120] or 'file'
    if aid not in M.clients:
        raise ApiError('हा account सध्या online नाही', 409)
    lim = M.limit_for(aid)
    if size <= 0:
        raise ApiError('फाइल रिकामी आहे')
    if size > lim:
        raise ApiError(f'फाइल {lim // MB} MB पेक्षा मोठी आहे (या account ची Telegram मर्यादा)', 413)
    st = bw_status()
    if st['limit'] and st['used'] + size > st['limit']:
        raise ApiError('या महिन्याची bandwidth मर्यादा संपली', 429)
    with UP_LOCK:
        if sum(1 for u in UP.values() if u['state'] in ('receiving', 'sending')) >= 5:
            raise ApiError('आधीचे upload पूर्ण होऊ द्या (एका वेळी 5)', 429)
    os.makedirs(UPDIR, exist_ok=True)
    if shutil.disk_usage(UPDIR).free < size * 1.02 + 100 * MB:
        raise ApiError('server वर जागा कमी (disk) — लहान फाइल पाठवा', 507)
    uid = secrets.token_hex(6)
    folder = os.path.join(UPDIR, uid)
    os.makedirs(folder)
    with UP_LOCK:
        UP[uid] = {'acc': aid, 'chat': chat, 'name': name, 'size': size, 'written': 0, 'sent': 0, 'path': os.path.join(folder, name),
                   'caption': str(d.get('caption', ''))[:1000], 'reply_to': d.get('reply_to'), 'topic_id': d.get('topic_id'),
                   'as_file': bool(d.get('as_file')), 'is_voice': bool(d.get('is_voice')), 'is_round': bool(d.get('is_round')),
                   'silent': bool(d.get('silent')), 'schedule': d.get('schedule'), 'md': bool(d.get('md')), 'state': 'receiving', 'err': ''}
    return jsonify(id=uid, chunk=CHUNK)

def _up(uid):
    with UP_LOCK:
        up = UP.get(uid)
    if not up:
        raise ApiError('upload सापडला नाही', 404)
    return up

@bp.post('/api/up/chunk')
@api
def api_up_chunk():
    up = _up(request.args.get('id', ''))
    off = _int('off')
    if up['state'] != 'receiving':
        raise ApiError('upload आता चालू नाही', 409)
    if off != up['written']:
        return jsonify(error='offset जुळत नाही', written=up['written']), 409
    data = request.get_data()
    if not data or up['written'] + len(data) > up['size']:
        raise ApiError('तुकडा चुकीचा')
    with open(up['path'], 'ab') as f:
        f.write(data)
    up['written'] += len(data)
    return jsonify(written=up['written'])

@bp.post('/api/up/finish')
@api
def api_up_finish():
    up_id = str(_json().get('id', ''))
    up = _up(up_id)
    if up['written'] != up['size']:
        raise ApiError('फाइल पूर्ण आलेली नाही', 409)
    up['state'] = 'sending'
    M.up_tasks[up_id] = asyncio.run_coroutine_threadsafe(M.a_send_file(up_id), M.loop)
    return jsonify(ok=1)

@bp.post('/api/up/cancel')
@api
def api_up_cancel():
    up_id = str(_json().get('id', ''))
    up = _up(up_id)
    fut = M.up_tasks.pop(up_id, None)
    if fut:
        fut.cancel()
    up['state'] = 'canceled'
    shutil.rmtree(os.path.dirname(up['path']), ignore_errors=True)
    return jsonify(ok=1)

@bp.get('/api/up/status')
@api
def api_up_status():
    up = _up(request.args.get('id', ''))
    return jsonify(state=up['state'], written=up['written'], sent=up['sent'], size=up['size'], err=up['err'], message=up.get('msg'))

@bp.get('/api/events')
@api
def api_events():
    since = _int('since', 0)
    end = time.time() + max(0.0, min(float(request.args.get('wait', 20)), 25.0))
    with M.cond:
        while True:
            ev = [e for e in M.events if e['seq'] > since]
            if ev or time.time() >= end:
                break
            M.cond.wait(timeout=max(0.05, end - time.time()))
        seq = M.seq
    return jsonify(seq=seq, events=ev)

def _login_guard(kind, limit):
    k, now = f'{kind}:' + _ip(), time.time()
    FAILS[k] = [t for t in FAILS[k] if now - t < 600]
    if len(FAILS[k]) >= limit:
        raise ApiError('खूप प्रयत्न — 10 मिनिटं थांबा', 429)
    FAILS[k].append(now)

@bp.post('/api/login/start')
@api
def api_login_start():
    _login_guard('ls', 10)
    d = _json()
    if d.get('api') == 'custom':
        api_id, api_hash = str(d.get('api_id', '')).strip(), str(d.get('api_hash', '')).strip()
        if not api_id.isdigit() or not re.fullmatch(r'[0-9a-f]{32}', api_hash):
            raise ApiError('api_id (संख्या) आणि api_hash (32 अक्षरं) बरोबर द्या')
    else:
        api_id, api_hash = DEF_API_ID, DEF_API_HASH
        if not (api_id and api_hash):
            raise ApiError('default API सेट नाही — "वेगळा API" निवडा')
    return jsonify(M.call(M.a_login_start(str(d.get('label', '')).strip(), str(d.get('phone', '')), api_id, api_hash)))

@bp.post('/api/login/qr/start')
@api
def api_login_qr_start():
    _login_guard('ls', 10)
    d = _json()
    if d.get('api') == 'custom':
        api_id, api_hash = str(d.get('api_id', '')).strip(), str(d.get('api_hash', '')).strip()
        if not api_id.isdigit() or not re.fullmatch(r'[0-9a-f]{32}', api_hash):
            raise ApiError('api_id (संख्या) आणि api_hash (32 अक्षरं) बरोबर द्या')
    else:
        api_id, api_hash = DEF_API_ID, DEF_API_HASH
        if not (api_id and api_hash):
            raise ApiError('default API सेट नाही — "वेगळा API" निवडा')
    r = M.call(M.a_qr_begin(str(d.get('label', '')).strip(), api_id, api_hash), 35)
    return jsonify(login_id=r['login_id'], url=r['url'])

@bp.post('/api/login/qr/check')
@api
def api_login_qr_check():
    d = _json()
    return jsonify(M.call(M.a_qr_check_once(str(d.get('login_id', ''))), 20))

@bp.post('/api/login/code')
@api
def api_login_code():
    _login_guard('lc', 15)
    d = _json()
    return jsonify(M.call(M.a_login_code(str(d.get('login_id', '')), str(d.get('code', '')))))

@bp.post('/api/login/password')
@api
def api_login_password():
    _login_guard('lc', 15)
    d = _json()
    return jsonify(M.call(M.a_login_password(str(d.get('login_id', '')), str(d.get('password', '')))))


# ───────────────────────── Premium & Innovative Features ─────────────────────────
@bp.post('/api/share/create')
@api
def api_share_create():
    """Creates a temporary public direct stream/download link (for VLC, MX Player, IDM, or browser sharing)."""
    d = _json()
    aid, chat, mid = _acc(d), int(d['chat']), int(d['id'])
    hours = max(1, min(168, int(d.get('hours', 24) or 24)))
    info = M.call(M.a_info(aid, chat, mid))
    if info['size'] <= 0:
        raise ApiError('या मेसेजमध्ये फाइल नाही', 404)
    tok = secrets.token_urlsafe(18)
    fname = re.sub(r'[^\w.\-]', '_', info.get('name') or f'file_{mid}')[:80] or f'file_{mid}'
    exp = time.time() + hours * 3600
    with SHARE_LOCK:
        now = time.time()
        for k in [k for k, v in SHARE_LINKS.items() if v['exp'] < now]:
            del SHARE_LINKS[k]
        SHARE_LINKS[tok] = {'acc': aid, 'chat': chat, 'id': mid, 'exp': exp, 'name': fname, 'info': info}
    # Pre-warm server multi-threaded download so anyone opening the share link gets instant speed
    try:
        _start_server_dl_job(aid, chat, mid, info=info, force=False)
    except Exception:
        pass
    return jsonify(token=tok, path=f'/app/s/{tok}/{fname}', expires_in=hours * 3600, name=fname)

@bp.get('/s/<token>', defaults={'name': ''})
@bp.get('/s/<token>/<path:name>')
@api
def public_share_stream(token, name):
    """Public streaming & download endpoint for VLC / MX Player / IDM / browser without requiring admin login."""
    with SHARE_LOCK:
        item = SHARE_LINKS.get(token)
        if item and item['exp'] < time.time():
            SHARE_LINKS.pop(token, None)
            item = None
    if not item:
        raise ApiError('ही Share Link संपली आहे किंवा चुकीची आहे', 404)
    aid, chat, mid, info = item['acc'], item['chat'], item['id'], item['info']
    size = info['size']
    start, end, partial = parse_range(request.headers.get('Range'), size)
    length = end - start + 1
    mime, h = _file_headers(info, mid, start, end, size, partial)
    key = _ckey(aid, chat, mid)
    cp = _cpath(key)
    part = cp + '.part'
    cur_st = _dl_state(aid, chat, mid)
    if cur_st['state'] == 'cached' and os.path.isfile(cp) and os.path.getsize(cp) == size:
        def gen_disk():
            remaining = length
            with open(cp, 'rb') as f:
                f.seek(start)
                while remaining > 0:
                    chunk = f.read(min(DISK_READ_BUF, remaining))
                    if not chunk:
                        break
                    remaining -= len(chunk)
                    bw_add(len(chunk))
                    yield chunk
        return Response(gen_disk(), status=206 if partial else 200, mimetype=mime, headers=h, direct_passthrough=True)
    try:
        j, _ = _start_server_dl_job(aid, chat, mid, info=info, force=False)
    except Exception:
        j = None
    if j and j.get('state') in ('downloading', 'cached') and start <= j.get('contig', 0) + 12 * MB:
        def gen_prog():
            pos, remaining, cond = start, length, j['cond']
            while remaining > 0:
                with cond:
                    while True:
                        st_now = j.get('state')
                        contig_now = j.get('contig', 0)
                        if st_now == 'cached' or contig_now > pos or st_now in ('error', 'none') or j.get('cancel'):
                            break
                        cond.wait(timeout=0.5)
                    st_now = j.get('state')
                    contig_now = j['size'] if st_now == 'cached' else j.get('contig', 0)
                if st_now in ('error', 'none') or j.get('cancel'):
                    break
                target_file = cp if (st_now == 'cached' and os.path.isfile(cp)) else part
                avail = min(remaining, max(0, contig_now - pos))
                if avail <= 0:
                    if st_now == 'cached':
                        break
                    continue
                with open(target_file, 'rb') as f:
                    f.seek(pos)
                    chunk = f.read(min(DISK_READ_BUF, avail))
                if not chunk:
                    break
                pos += len(chunk)
                remaining -= len(chunk)
                bw_add(len(chunk))
                yield chunk
        return Response(gen_prog(), status=206 if partial else 200, mimetype=mime, headers=h, direct_passthrough=True)
    if not STREAMS.acquire(timeout=25):
        raise ApiError('Server busy', 503)
    try:
        it = M.call(M.a_iter(aid, chat, mid, start - start % REQ, size))
    except Exception:
        STREAMS.release()
        raise
    def gen_direct():
        skip, remaining = start % REQ, length
        try:
            while remaining > 0:
                chunk = M.call(M.a_next(it), 120)
                if not chunk:
                    break
                if skip:
                    chunk, skip = chunk[skip:], 0
                chunk = chunk[:remaining]
                remaining -= len(chunk)
                bw_add(len(chunk))
                yield chunk
        finally:
            try:
                M.call(M.a_close(it), 10)
            except Exception:
                pass
            STREAMS.release()
    return Response(gen_direct(), status=206 if partial else 200, mimetype=mime, headers=h, direct_passthrough=True)

@bp.get('/api/ghost/list')
@api
def api_ghost_list():
    """Returns deleted messages captured by Anti-Delete Ghost Vault and current Auto-Reply settings."""
    aid = request.args.get('acc', '')
    items = [x for x in list(DELETED_VAULT) if not aid or x.get('acc') == aid][:100]
    ar = AUTO_REPLY.get(aid, {'enabled': False, 'text': '', 'cooldown': 3600, 'contacts_only': False})
    return jsonify(deleted=items, anti_delete=bool(DL_CFG.get('anti_delete', True)),
                   auto_reply={'enabled': bool(ar.get('enabled')), 'text': ar.get('text', ''),
                               'cooldown': int(ar.get('cooldown', 3600)), 'contacts_only': bool(ar.get('contacts_only', False))})

@bp.post('/api/ghost/settings')
@api
def api_ghost_settings():
    d = _json()
    aid = _acc(d)
    if 'anti_delete' in d:
        DL_CFG['anti_delete'] = bool(d['anti_delete'])
    if 'clear_vault' in d and d['clear_vault']:
        DELETED_VAULT.clear()
    if 'auto_reply_enabled' in d or 'auto_reply_text' in d or 'contacts_only' in d:
        ar = AUTO_REPLY.setdefault(aid, {'enabled': False, 'text': '', 'cooldown': 3600, 'contacts_only': False, 'sent_to': {}})
        if 'auto_reply_enabled' in d:
            ar['enabled'] = bool(d['auto_reply_enabled'])
        if 'auto_reply_text' in d:
            ar['text'] = str(d['auto_reply_text'])[:1000].strip()
        if 'cooldown' in d:
            ar['cooldown'] = max(60, min(86400, int(d['cooldown'] or 3600)))
        if 'contacts_only' in d:
            ar['contacts_only'] = bool(d['contacts_only'])
    return jsonify(ok=1)

@bp.get('/api/cross_search')
@api
def api_cross_search():
    """Searches for messages/files across ALL connected Telegram accounts simultaneously."""
    q = request.args.get('q', '').strip()[:100]
    if len(q) < 2:
        raise ApiError('कमीत कमी 2 अक्षरं टाका')
    async def _run_all():
        tasks = []
        aids = []
        for a in M.accounts_view():
            if a.get('state') == 'online':
                aids.append((a['id'], a.get('label') or a.get('name') or a['id']))
                tasks.append(M.a_search(a['id'], q, None))
        if not tasks:
            return []
        res_list = await asyncio.gather(*tasks, return_exceptions=True)
        combined = []
        for (aid, aname), r in zip(aids, res_list):
            if isinstance(r, list):
                for m in r:
                    m['acc'] = aid
                    m['acc_name'] = aname
                    combined.append(m)
        combined.sort(key=lambda x: x.get('ts', 0), reverse=True)
        return combined[:80]
    return jsonify(messages=M.call(_run_all(), 45))

@bp.get('/api/analytics')
@api
def api_analytics():
    """Computes rich Chat Analytics (top senders, media breakdown, total size, peak activity) from recent messages."""
    aid, chat = _acc(), _int('chat')
    async def _calc():
        cl = M._cl(aid)
        ms = await cl.get_messages(chat, limit=200)
        senders = collections.Counter()
        sender_names = {}
        kinds = collections.Counter()
        total_bytes = 0
        hours = collections.Counter()
        for m in ms:
            if getattr(m, 'action', None):
                continue
            sid = getattr(m, 'sender_id', None) or (0 if getattr(m, 'out', False) else -1)
            sname = 'You' if getattr(m, 'out', False) else (_name(getattr(m, 'sender', None)) or f'User {sid}')
            senders[sid] += 1
            sender_names[sid] = sname
            dt = getattr(m, 'date', None)
            if dt:
                hours[dt.hour] += 1
            mi = media_info(m)
            if mi:
                kinds[mi['kind']] += 1
                total_bytes += int(mi.get('size') or 0)
            else:
                kinds['text'] += 1
        top_senders = [{'name': sender_names[k], 'count': v} for k, v in senders.most_common(8)]
        return {
            'sampled': len(ms),
            'total_media_bytes': total_bytes,
            'kinds': dict(kinds),
            'top_senders': top_senders,
            'peak_hour': hours.most_common(1)[0][0] if hours else None
        }
    return jsonify(M.call(_calc(), 35))

@bp.get('/api/storage/groups')
@api
def api_storage_groups():
    """Returns Backup Browser groups so the user can 1-click forward/backup any file to a Storage Group."""
    if STORAGE is None:
        return jsonify(groups=[])
    try:
        rows = STORAGE.list_groups()
        return jsonify(groups=[{'id': r['group_id'], 'title': r['title'] or f"Group {r['group_id']}"} for r in rows])
    except Exception:
        return jsonify(groups=[])

def count_bw(resp):
    try:
        if request.path.startswith('/app'):
            if resp.headers.pop('X-BW-Counted', None):
                return resp                              # stream: तुकड्यांनुसार आधीच मोजलं
            if resp.content_length:
                bw_add(resp.content_length)
    except Exception:
        pass
    return resp


# ───────────────────────── init ─────────────────────────
def init(app, db, notify):
    global M, reason
    if Fernet is None or TelegramClient is None:
        reason = 'telethon / cryptography packages नाहीत'
    elif db is None:
        reason = 'MONGODB_URI लागतो'
    elif not SESSION_KEY:
        reason = 'SESSION_KEY लागतो'
    else:
        try:
            _fernet()
        except Exception:
            reason = 'SESSION_KEY चुकीचा (Fernet key हवी)'
        else:
            M = Manager(db, notify)
            app.register_blueprint(bp)
            app.after_request(count_bw)
            reason = ''
    return M is not None

def start():
    if M:
        M.start()
