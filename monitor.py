"""
/memory  - one public, read-only page with the live stats of ALL your servers.

  * this server  : accurate container stats (cgroup, see sysstats.py), same numbers as the hermes repo
  * other servers: polled over HTTP from their own stats JSON, nothing has to be changed on them
        hermes-ts (SSH terminal)  -> <url>/api          hermes (PicoClaw)  -> <url>/api/stats
    (the format is detected automatically; a full API url like https://x.app/api/stats also works)

Add servers
  * env  MONITOR_SERVERS="Terminal=https://hermesterminal.onrender.com, Hermes=https://xxx.koyeb.app"
  * or from the Telegram bot (admins only):  /addserver Terminal https://...   /delserver Terminal   /servers
    (bot-added servers are saved in MongoDB if MONGO_URI is set, else in /data/monitor_servers.json)

Cost: stdlib only. While nobody has the page open NOTHING is polled and nothing is sampled except one cgroup file read
every 10 s (sysstats). While it is open: this server is sampled every 2 s and the others are polled in parallel every
5 s (4 s timeout). ~15 s after the last visit everything goes idle again. The page never shows the servers' URLs.
Routes: /memory (page)   /memory/api (JSON)
"""
import ipaddress
import json
import os
import re
import socket
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor

from sysstats import Stats

SELF_NAME = os.environ.get("MONITOR_SELF_NAME", "").strip() or "tgstorage"
POLL_EVERY = float(os.environ.get("MONITOR_POLL_SECONDS", "5") or 5)
TIMEOUT = 4.0                      # per remote request
MAX_BYTES = 512 * 1024             # never read more than this from a remote
ACTIVE = 15                        # seconds the pollers keep running after the last page/API request
MAX_SERVERS = 20
ALERT_RAM, ALERT_CPU, ALERT_DISK = 85.0, 85.0, 90.0
STORE_FILE = os.environ.get("MONITOR_FILE", "/data/monitor_servers.json")

STATS = Stats(disk_paths=tuple(p for p in ("/app", "/data", "/tmp", "/var/tmp", "/root") if os.path.isdir(p)) or ("/tmp",))

_lock = threading.Lock()
_state = {}                        # name -> poll state of a remote server
_last_view = [0.0]
_wake = threading.Event()
_db = [None]                       # MongoDB handle (set by app.py once the hub is loaded)
_saved = {"loaded": False, "list": []}
_started = [False]


# ──────────────────────────────── server list (env + bot) ────────────────────────────────
def _allow_private():
    return os.environ.get("MONITOR_ALLOW_PRIVATE", "") == "1"          # tests only


def check_url(url):
    """-> (ok, normalized_url_or_reason). Only http(s); never loopback / link-local / metadata addresses."""
    url = (url or "").strip().rstrip("/")
    u = urllib.parse.urlparse(url)
    if u.scheme not in ("http", "https") or not u.hostname or len(url) > 300 or re.search(r"[\s<>\"']", url):
        return False, "URL must look like https://name.example.com"
    if u.username or u.password:
        return False, "no user:password inside the URL"
    if not _allow_private():
        host = u.hostname.lower()
        if host in ("localhost", "metadata.google.internal") or host.endswith(".localhost"):
            return False, "localhost is not allowed"
        try:
            for fam, _t, _p, _c, sa in socket.getaddrinfo(host, u.port or (443 if u.scheme == "https" else 80)):
                ip = ipaddress.ip_address(sa[0])
                if ip.is_loopback or ip.is_link_local or ip.is_unspecified or ip.is_multicast:
                    return False, "that address is not allowed (loopback / link-local)"
        except socket.gaierror:
            pass                                                         # not resolvable now: the poller will report it
    return True, url


def check_name(name):
    name = re.sub(r"\s+", " ", (name or "").strip())
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9 _.\-]{0,23}", name):
        return False, "name: 1-24 letters/digits/space/._- (starts with a letter or digit)"
    return True, name


_env_cache = {"raw": None, "list": []}


def _parse_env():
    """MONITOR_SERVERS items: 'Name=https://url', 'Name|https://url' or just 'https://url'. Cached (DNS check runs once)."""
    raw = os.environ.get("MONITOR_SERVERS", "")
    if _env_cache["raw"] == raw:
        return _env_cache["list"]
    out = []
    for item in re.split(r"[,\n;]+", raw):
        item = item.strip()
        if not item:
            continue
        m = re.match(r"^([^=|/:\s][^=|]*?)\s*[=|]\s*(https?://\S+)$", item)
        name, url = (m.group(1), m.group(2)) if m else ("", item)
        if not name.strip():
            name = (urllib.parse.urlparse(url).hostname or url).split(".")[0]
        ok_n, n = check_name(name)
        ok_u, u = check_url(url)
        if ok_n and ok_u:
            out.append({"name": n, "url": u, "src": "env"})
        else:
            print(f"[monitor] ignoring MONITOR_SERVERS item {item!r}: {n if not ok_n else u}", flush=True)
    _env_cache.update(raw=raw, list=out)
    return out


def _load_saved():
    if _saved["loaded"]:
        return
    lst = None
    try:
        if _db[0] is not None:
            doc = _db[0]["monitor"].find_one({"_id": "servers"})
            if doc:
                lst = doc.get("list")
    except Exception as e:                                               # noqa: BLE001
        print(f"[monitor] mongo read: {e}", flush=True)
    if lst is None:
        try:
            with open(STORE_FILE, encoding="utf-8") as f:
                lst = json.load(f)
        except Exception:                                                # noqa: BLE001
            lst = []
    _saved["list"] = [x for x in lst if isinstance(x, dict) and x.get("name") and x.get("url")]
    _saved["loaded"] = True


def _save():
    lst = [{"name": s["name"], "url": s["url"]} for s in _saved["list"]]
    try:
        if _db[0] is not None:
            _db[0]["monitor"].replace_one({"_id": "servers"}, {"_id": "servers", "list": lst}, upsert=True)
    except Exception as e:                                               # noqa: BLE001
        print(f"[monitor] mongo write: {e}", flush=True)
    try:
        os.makedirs(os.path.dirname(STORE_FILE) or ".", exist_ok=True)
        tmp = STORE_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(lst, f)
        os.replace(tmp, STORE_FILE)
    except Exception as e:                                               # noqa: BLE001
        print(f"[monitor] file write: {e}", flush=True)


def set_db(db):
    """Called by app.py once the hub has connected to MongoDB: re-read the saved list from there."""
    _db[0] = db
    _saved["loaded"] = False


def servers():
    with _lock:
        _load_saved()
        out, seen = [], set()
        for s in _parse_env() + [dict(x, src="bot") for x in _saved["list"]]:
            if s["name"].lower() not in seen:
                seen.add(s["name"].lower())
                out.append(s)
        return out


def add_server(name, url):
    """-> (ok, message). Admin-only (called from the bot)."""
    ok, n = check_name(name)
    if not ok:
        return False, n
    ok, u = check_url(url)
    if not ok:
        return False, u
    if n.lower() == SELF_NAME.lower():
        return False, f"'{SELF_NAME}' is this server's own name"
    with _lock:
        _load_saved()
        if any(s["name"].lower() == n.lower() for s in _parse_env()):
            return False, f"'{n}' already comes from the MONITOR_SERVERS env var (change it there)"
        if len(_saved["list"]) + len(_parse_env()) >= MAX_SERVERS:
            return False, f"max {MAX_SERVERS} servers"
        _saved["list"] = [s for s in _saved["list"] if s["name"].lower() != n.lower()] + [{"name": n, "url": u}]
        _save()
    _wake.set()
    return True, n


def del_server(name):
    with _lock:
        _load_saved()
        before = len(_saved["list"])
        _saved["list"] = [s for s in _saved["list"] if s["name"].lower() != (name or "").strip().lower()]
        changed = len(_saved["list"]) != before
        if changed:
            _save()
    with _lock:
        for k in [k for k in _state if k.lower() == (name or "").strip().lower()]:
            _state.pop(k, None)
    return changed


# ──────────────────────────────── normalising the different stats formats ────────────────────────────────
def _pct(used, total):
    return round(min(100.0, 100.0 * used / total), 1) if total else 0.0


def _procs(lst, key):
    out = []
    for p in (lst or [])[:6]:
        out.append({"name": str(p.get("command") or p.get("name") or p.get("pid") or "?")[:40],
                    "cpu": p.get("cpu", 0), "rss_mb": p.get("rss_mb", round((p.get("rss") or 0) / 1048576, 1))})
    return sorted(out, key=lambda r: -(r["cpu"] if key == "cpu" else r["rss_mb"]))


def _derive_alerts(cpu, ram, disk):
    a = []
    if cpu["percent"] >= ALERT_CPU:
        a.append(f"CPU high: {cpu['percent']}%")
    if ram["percent"] >= ALERT_RAM:
        a.append(f"RAM high: {ram['percent']}%")
    if disk["measured"] and disk["percent"] >= ALERT_DISK:
        a.append(f"Disk almost full: {disk['percent']}%")
    return a


def _n_hermes(d):
    """hermes / sysstats.Stats.snapshot() format"""
    m, c, k = d["memory"], d["cpu"], d.get("disk") or {}
    cpu = {"percent": c.get("percent", 0), "limit": c.get("limit_cores", 0), "note": f"{c.get('cores_used', 0)} vCPU used"}
    ram = {"used_mb": m.get("used_mb", 0), "total_mb": m.get("total_mb", 0), "percent": m.get("percent", 0)}
    disk = {"used_mb": k.get("used_mb", 0), "total_mb": k.get("total_mb", 0), "percent": k.get("percent", 0),
            "measured": bool(k.get("used_mb"))}
    procs = d.get("processes") or []
    chips = []
    if d.get("platform"):
        chips.append(str(d["platform"]))
    if (d.get("startup") or {}).get("phase") not in (None, "ready"):
        chips.append("restoring state…")
    if m.get("oom_kills"):
        chips.append(f"OOM kills: {m['oom_kills']}")
    return {"cpu": cpu, "ram": ram, "disk": disk, "uptime_s": int(d.get("uptime_seconds", 0)),
            "alerts": list(d["alerts"]) if "alerts" in d else _derive_alerts(cpu, ram, disk),
            "top_cpu": _procs(d.get("processes_cpu") or procs, "cpu"), "top_mem": _procs(procs, "mem"), "chips": chips}


def _n_ts(d):
    """hermes-ts (SSH terminal) /api format"""
    cpu = {"percent": d.get("c_cpu", 0), "limit": d.get("cpus", 0), "note": ""}
    ram = {"used_mb": round(d.get("c_mem", 0) / 1048576, 1), "total_mb": round(d.get("c_mem_limit", 0) / 1048576, 1),
           "percent": _pct(d.get("c_mem", 0), d.get("c_mem_limit", 0))}
    disk = {"used_mb": round(d.get("disk", 0) / 1048576, 1), "total_mb": round(d.get("disk_total", 0) / 1048576, 1),
            "percent": _pct(d.get("disk", 0), d.get("disk_total", 0)), "measured": bool(d.get("disk_at"))}
    chips = [f"Tailscale: {(d.get('ts') or {}).get('state', '?')}", "OOM guard: " + ("active" if d.get("guard") else "NOT running")]
    if d.get("kills"):
        chips.append(f"OOM kills: {len(d['kills'])} recent")
    return {"cpu": cpu, "ram": ram, "disk": disk, "uptime_s": int(d.get("uptime", 0)),
            "alerts": list(d["alerts"]) if "alerts" in d else _derive_alerts(cpu, ram, disk),
            "top_cpu": _procs(d.get("top_cpu"), "cpu"), "top_mem": _procs(d.get("top_mem"), "mem"), "chips": chips}


def normalize(d):
    if not isinstance(d, dict):
        raise ValueError("not a JSON object")
    if isinstance(d.get("memory"), dict) and isinstance(d.get("cpu"), dict):
        return _n_hermes(d)
    if "c_cpu" in d and "c_mem" in d:
        return _n_ts(d)
    raise ValueError("unknown stats format")


# ──────────────────────────────── polling the other servers ────────────────────────────────
class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a, **k):
        return None                                                      # a redirect could point at an internal address


_opener = urllib.request.build_opener(_NoRedirect)


def _fetch(url):
    req = urllib.request.Request(url, headers={"User-Agent": "tgstorage-monitor/1.0", "Accept": "application/json"})
    with _opener.open(req, timeout=TIMEOUT) as r:
        return r.read(MAX_BYTES).decode("utf-8", "replace")


def _candidates(url, kind):
    p = urllib.parse.urlparse(url).path.rstrip("/")
    if p:                                                                # a full API url was given: use it as it is
        return [url]
    if kind == "hermes":
        return [url + "/api/stats"]
    if kind == "ts":
        return [url + "/api"]
    return [url + "/api/stats", url + "/api"]


def _poll_one(srv):
    name, url = srv["name"], srv["url"]
    with _lock:
        st = _state.setdefault(name, {"kind": None, "data": None, "ok_at": 0.0, "fail": 0, "err": "", "warming": False})
        kind = st["kind"]
    err, fmt_err = "no response", ""
    for target in _candidates(url, kind):
        try:
            d = json.loads(_fetch(target))
        except urllib.error.HTTPError as e:
            err = f"HTTP {e.code}"
            continue
        except ValueError:
            err = "answer is not JSON (is this the right URL?)"
            continue
        except Exception as e:                                           # noqa: BLE001
            err = (type(e).__name__ + ": " + str(getattr(e, "reason", e)))[:80]
            continue
        try:
            if isinstance(d, dict) and d.get("warming"):                 # hermes-ts has just woken its sampler
                with _lock:
                    st.update(kind="ts", warming=True, fail=0, err="")
                    if not st["ok_at"]:
                        st["ok_at"] = time.time()
                return
            norm = normalize(d)
        except Exception as e:                                           # noqa: BLE001
            fmt_err = str(e)[:80]                                        # JSON but not one of our formats: most useful message
            continue
        with _lock:
            st.update(kind="hermes" if "memory" in d else "ts", data=norm, ok_at=time.time(), fail=0, err="", warming=False)
        return
    with _lock:
        st["fail"] += 1
        st["err"] = fmt_err or err


def _status(st, now):
    if not st:
        return "checking"
    if not st["ok_at"]:
        return "down" if st["fail"] >= 2 else "checking"
    if st["fail"] >= 2:
        return "down"
    if st["warming"]:
        return "warming"
    if now - st["ok_at"] > 20:
        return "stale"
    return "up"


_pool = ThreadPoolExecutor(max_workers=8, thread_name_prefix="mon")


def _poller():
    while True:
        if time.time() - _last_view[0] > ACTIVE:
            _wake.wait(1.0)
            _wake.clear()
            continue
        t0 = time.time()
        srvs = servers()
        with _lock:
            for k in [k for k in _state if k.lower() not in {s["name"].lower() for s in srvs}]:
                _state.pop(k, None)
        futs = [_pool.submit(_poll_one, s) for s in srvs]
        for f in futs:
            try:
                f.result(timeout=TIMEOUT * 2 + 2)
            except Exception:                                            # noqa: BLE001
                pass
        _wake.wait(max(0.5, POLL_EVERY - (time.time() - t0)))
        _wake.clear()


def start():
    """Start the background threads (idempotent)."""
    if _started[0]:
        return
    _started[0] = True
    STATS.start()
    threading.Thread(target=_poller, daemon=True, name="monitor-poller").start()


def touch():
    was = time.time() - _last_view[0] <= ACTIVE
    _last_view[0] = time.time()
    STATS.touch()
    if not was:
        _wake.set()


# ──────────────────────────────── the aggregated view ────────────────────────────────
def _self_card():
    snap = STATS.snapshot()
    n = _n_hermes(snap)
    n.update(name=SELF_NAME, status="up", self=True, age=0, err="", has_data=True)
    return n


def overview():
    touch()
    now = time.time()
    cards = [_self_card()]
    for s in servers():
        with _lock:
            st = dict(_state.get(s["name"]) or {})
        status = _status(st, now)
        base = st.get("data") or {"cpu": {"percent": 0, "limit": 0, "note": ""}, "ram": {"used_mb": 0, "total_mb": 0, "percent": 0},
                                  "disk": {"used_mb": 0, "total_mb": 0, "percent": 0, "measured": False}, "uptime_s": 0,
                                  "alerts": [], "top_cpu": [], "top_mem": [], "chips": []}
        card = dict(base, name=s["name"], status=status, self=False, src=s.get("src", ""),
                    age=int(now - st["ok_at"]) if st.get("ok_at") else None, err=st.get("err", ""), has_data=bool(st.get("data")))
        if status in ("down", "stale"):
            card["alerts"] = [f"Server not responding ({st.get('err') or 'no data'})"]
        cards.append(card)
    up = sum(1 for c in cards if c["status"] == "up")
    return {"time": time.strftime("%H:%M:%S"), "servers": cards,
            "summary": {"total": len(cards), "up": up, "down": sum(1 for c in cards if c["status"] in ("down", "stale")),
                        "alerts": sum(len(c["alerts"]) for c in cards)}}


# ──────────────────────────────── the page ────────────────────────────────
PAGE = r"""<!doctype html><html lang=en><meta charset=utf-8><meta name=viewport content="width=device-width,initial-scale=1">
<title>Server monitor</title>
<style>
:root{--bg:#0f172a;--card:#1e293b;--line:#334155;--fg:#f8fafc;--mut:#94a3b8;--ok:#34d399;--warn:#fbbf24;--bad:#f43f5e;--acc:#38bdf8}
@media(prefers-color-scheme:light){:root{--bg:#f1f5f9;--card:#fff;--line:#cbd5e1;--fg:#0f172a;--mut:#64748b;--ok:#059669;--warn:#b45309;--bad:#dc2626;--acc:#0369a1}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--fg);font:14px -apple-system,Segoe UI,Roboto,Arial,sans-serif;padding:16px}
.wrap{max-width:1100px;margin:0 auto}h1{font-size:20px;margin:0;color:var(--acc)}
.top{display:flex;justify-content:space-between;align-items:center;flex-wrap:wrap;gap:8px;margin-bottom:14px}
.sum{color:var(--mut);font-size:13px}.sum b{color:var(--fg)}
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(320px,1fr));gap:14px}
.card{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:14px}
.card.down,.card.stale{border-color:var(--bad)}.card.down .body{opacity:.45}
.h{display:flex;align-items:center;gap:8px;margin-bottom:8px}.h .n{font-weight:700;font-size:16px;flex:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.dot{width:10px;height:10px;border-radius:50%;background:var(--mut)}.up .dot{background:var(--ok)}.warming .dot,.checking .dot{background:var(--warn)}.down .dot,.stale .dot{background:var(--bad)}
.tag{font-size:11px;color:var(--mut);border:1px solid var(--line);border-radius:99px;padding:1px 8px}
.al{background:rgba(244,63,94,.12);border:1px solid var(--bad);border-radius:8px;padding:6px 10px;margin:6px 0;font-size:13px;line-height:1.6}
.m{margin:9px 0}.m .l{display:flex;justify-content:space-between;color:var(--mut);font-size:12px;margin-bottom:3px}.m .l b{color:var(--fg);font-weight:600}
.bar{height:7px;background:var(--line);border-radius:4px;overflow:hidden}.bar i{display:block;height:100%;width:0;background:var(--ok);transition:width .4s}
.bar.w i{background:var(--warn)}.bar.d i{background:var(--bad)}
.chips{display:flex;flex-wrap:wrap;gap:5px;margin-top:8px}.chip{font-size:11px;background:var(--bg);border:1px solid var(--line);border-radius:6px;padding:2px 7px;color:var(--mut)}
details{margin-top:8px}summary{cursor:pointer;color:var(--acc);font-size:12px}
table{width:100%;border-collapse:collapse;margin-top:5px;font-size:12px}td,th{padding:3px 5px;text-align:left;border-bottom:1px solid var(--line);white-space:nowrap;overflow:hidden;text-overflow:ellipsis;max-width:170px}th{color:var(--mut);font-weight:600}
.f{margin-top:9px;color:var(--mut);font-size:11px}
.empty{color:var(--mut);padding:30px;text-align:center}
</style>
<div class=wrap><div class=top><h1>📊 Server monitor</h1><div class=sum id=sum>loading…</div></div>
<div class=grid id=grid></div>
<div class=f style="margin-top:14px">Auto-refresh every 2 s while this tab is visible. Only plan allotments of each container are shown, never the host machine.</div></div>
<script>
const $=id=>document.getElementById(id);
const cls=p=>p>=85?'d':p>=65?'w':'';
const mb=v=>v>=1024?(v/1024).toFixed(2)+' GB':Math.round(v)+' MB';
function up(s){if(!s)return'–';const d=Math.floor(s/86400),h=Math.floor(s%86400/3600),m=Math.floor(s%3600/60);return(d?d+'d ':'')+h+'h '+m+'m'}
function el(t,c,x){const e=document.createElement(t);if(c)e.className=c;if(x!=null)e.textContent=x;return e}
function meter(label,pct,txt){const m=el('div','m'),l=el('div','l');l.append(el('span',null,label),el('b',null,txt));
 const b=el('div','bar '+cls(pct)),i=el('i');i.style.width=Math.min(pct,100)+'%';b.append(i);m.append(l,b);return m}
function tbl(title,rows,key){const t=el('table');const hr=t.insertRow();[title,key==='cpu'?'CPU %':'RAM'].forEach(x=>{const c=el('th',null,x);hr.append(c)});
 rows.forEach(p=>{const r=t.insertRow();r.insertCell().textContent=p.name;r.insertCell().textContent=key==='cpu'?p.cpu+'%':mb(p.rss_mb)});return t}
const ST={up:'online',warming:'waking up…',checking:'checking…',down:'DOWN',stale:'not responding'};
function card(s){
 const c=el('div','card '+s.status),h=el('div','h');h.append(el('span','dot'),el('span','n',s.name));
 if(s.self)h.append(el('span','tag','this server'));h.append(el('span','tag',ST[s.status]||s.status));c.append(h);
 if(s.alerts.length){const a=el('div','al');s.alerts.forEach((x,i)=>{if(i)a.append(document.createElement('br'));a.append('⚠ '+x)});c.append(a)}
 const b=el('div','body');
 if(!s.has_data){b.append(el('div','f',s.status==='down'?'no data yet':'waiting for the first numbers…'))}
 else{
  b.append(meter('CPU'+(s.cpu.limit?' · '+s.cpu.limit+' vCPU':''),s.cpu.percent,s.cpu.percent+'%'));
  b.append(meter('RAM',s.ram.percent,mb(s.ram.used_mb)+' / '+mb(s.ram.total_mb)));
  b.append(meter('Disk',s.disk.percent,s.disk.measured?mb(s.disk.used_mb)+' / '+mb(s.disk.total_mb):'measuring…'));
  if(s.chips.length){const ch=el('div','chips');s.chips.forEach(x=>ch.append(el('span','chip',x)));b.append(ch)}
  if(s.top_cpu.length||s.top_mem.length){const d=el('details');d.append(el('summary',null,'Processes'));d.append(tbl('Top by CPU',s.top_cpu,'cpu'),tbl('Top by RAM',s.top_mem,'mem'));b.append(d)}
 }
 c.append(b);
 const f=el('div','f');f.textContent=(s.uptime_s?'up '+up(s.uptime_s)+' · ':'')+(s.self?'live':(s.age==null?'never reached':'updated '+s.age+'s ago'));c.append(f);
 return c}
async function tick(){
 if(document.hidden){setTimeout(tick,2000);return}
 try{const d=await(await fetch('/memory/api',{cache:'no-store'})).json();
  const keep=[...document.querySelectorAll('details')].map(x=>x.open);
  const g=$('grid');g.replaceChildren(...d.servers.map(card));
  [...document.querySelectorAll('details')].forEach((x,i)=>{if(keep[i])x.open=true});
  const s=d.summary;$('sum').innerHTML='<b>'+s.up+'</b>/'+s.total+' online · '+(s.alerts?'<b style="color:var(--bad)">'+s.alerts+' alerts</b>':'no alerts')+' · '+d.time;
  document.title=(s.alerts||s.down?'⚠ ':'')+'Server monitor'}
 catch(e){$('sum').textContent='connection lost…'}
 setTimeout(tick,2000)}
tick()
</script></html>"""


# ──────────────────────────────── WSGI ────────────────────────────────
def handles(path):
    return path == "/memory" or path.startswith("/memory/")


def application(environ, start_response):
    path = environ.get("PATH_INFO", "")
    if environ.get("REQUEST_METHOD", "GET") not in ("GET", "HEAD"):
        start_response("405 Method Not Allowed", [("Allow", "GET, HEAD"), ("Content-Length", "0")])
        return [b""]
    if path in ("/memory/api", "/memory/api/"):
        body, ctype = json.dumps(overview()).encode(), "application/json"
    elif path in ("/memory", "/memory/"):
        touch()
        body, ctype = PAGE.encode("utf-8"), "text/html; charset=utf-8"
    else:
        start_response("404 Not Found", [("Content-Length", "0")])
        return [b""]
    start_response("200 OK", [("Content-Type", ctype), ("Content-Length", str(len(body))), ("Cache-Control", "no-store"),
                              ("X-Content-Type-Options", "nosniff"), ("Referrer-Policy", "no-referrer")])
    return [b"" if environ.get("REQUEST_METHOD") == "HEAD" else body]
