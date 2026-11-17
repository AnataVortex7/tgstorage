# Worker Hub + Backup Browser (एकच server, एकच bot)

दोन्ही repo एका process मध्ये चालतात:

| File | काम |
|---|---|
| `app.py` | entrypoint — दोन्ही भाग सुरू करतो, web request योग्य भागाकडे पाठवतो |
| `hub_app.py` | Worker Hub: control bot (password + 2FA), PC worker manager (`/api/sync`, `/i`, `/f`) |
| `tgweb.py`, `tgui.py` | अनेक Telegram accounts चा engine + Telegram-सारखा UI (`/app`) |
| `storage_app.py` | Backup Browser (tgstorage): groups browse/stream, backup API (`/hapi/...`), website login |
| `files/` | PC installer / worker / watermark (जुना code तसाच) |

## एकच bot कसा चालतो
* **Worker Hub** = bot चं main menu + login (Bot API). कुणीही bot ला message केलं की password (+2FA) मागतो.
* **Backup Browser** = त्याच token वर Pyrogram client (group files वाचणं/stream करणं).
* **एकदा Hub मध्ये login** केलं की Backup Browser चे commands पण चालतात (वेगळा owner check नाही).
  अनोळखी माणसाला Backup Browser काहीच उत्तर देत नाही.
* Bot menu → **📦 Storage**: website login link (एकदाचा, 2FA लागतो), Telegram user-account login, status.
  Commands: `/userlogin /userlogout /usercancel /unblock /status`.
* `/start` = Hub चा menu. (`/start <token>` = Backup Browser website चा "bot ने login" approve.)

## URLs
| Path | कुणाचं |
|---|---|
| `/`, `/login`, `/browse`, `/api/*`, `/download`, `/stream`, `/hapi/*` | Backup Browser |
| `/app` (+ `/app/...`) | Telegram accounts UI (bot menu → Telegram Accounts → UI उघडा) |
| `/api/sync`, `/i/<tok>`, `/f/<tok>/...` | Worker PCs |
| `/healthz`, `/health` | `ok` — health check / heartbeat साठी **`/healthz`** वापरा |
| `/memory` (+ `/memory/api` JSON) | **Public, read-only** — सगळ्या servers चे live stats एकाच page वर (खाली "Server monitor") |

> जुना Hub heartbeat `/` वर होता; आता `/` Backup Browser चा आहे (login वर redirect). Pinger `/healthz` वर लावा.

## Environment variables
**आवश्यक** (नसेल तर app सुरू होतच नाही आणि कमतरता print करतो):

| Key | अर्थ |
|---|---|
| `BOT_TOKEN` (किंवा `TELEGRAM_BOT_TOKEN`) | एकच bot |
| `API_ID`, `API_HASH` (किंवा `TG_API_ID`, `TG_API_HASH`) | my.telegram.org |
| `WEB_PASSWORD` | Backup Browser website password (किमान 10 अक्षरं) |
| `ADMIN_PASSWORD` | bot login password (किमान 10 अक्षरं) |
| `COLAB_SECRET`, `PANEL_PASSWORD` | Worker PCs |

**जोरदार सुचवलेले:** `MONGO_URI` (किंवा `MONGODB_URI`) — Koyeb/Render free वर restart नंतर index, admin login, accounts टिकण्यासाठी ·
`SESSION_KEY` (Telegram accounts engine साठी, Fernet key) · `ADMIN_TOTP_SECRET` (2FA) · `SECRET_KEY` (64 random) ·
`WEB_URL` (public https URL — `PUBLIC_URL` आपोआप तोच) · `BOT_NAME` · `BACKUP_GROUP_ID` · `OWNER_CHAT_ID`
(Backup Browser "send to bot" साठी; रिकामं असेल तर पहिला admin).

बाकी optional env जुन्या दोन्ही README प्रमाणेच (`BW_LIMIT_GB`, `MAX_ACCOUNTS`, `DISK_CACHE_GB`, `STEALTH_TRIGGER`,
`LOCKOUT_HOURS`, `POLL_INTERVAL`, `CACHE_MAX_GB`, `BACKUP_TOKEN` …). `BACKUP_API.md` पहा.

**2FA bypass password (optional):** `ADMIN_2FA_PASSWORD` (किमान 8 अक्षरं). Bot ने "code पाठवा" म्हटल्यावर Authenticator चा 6 अंकी code
किंवा हा password — दोन्हीपैकी काहीही चालतं. Bot चा मजकूर बदलत नाही. (`ADMIN_TOTP_SECRET` किंवा `ADMIN_2FA_PASSWORD` यापैकी काहीही सेट असेल तर 2FA step नेहमी येतो.)

**Server Terminal (ऐच्छिक, DEFAULT बंद):** `TERMINAL_PASSWORD` (किमान 12 अक्षरं) सेट केलं तर website च्या
`⋯` menu मध्ये **💻 Server Terminal** दिसेल — `/term` वर खरा shell (bash) browser मधून उघडतो, redeploy न करता
server वर file बदलणं / package install करणं / debug करणं यासाठी. हे काम करण्याआधी website login (`WEB_PASSWORD`)
+ वेगळा `TERMINAL_PASSWORD` दोन्ही लागतात; shell उघडला की bot owner ला Telegram वर notification जातं; 30 मिनिटं
निष्क्रिय राहिला तर आपोआप बंद होतो.
⚠️ **हे server चा full root access आहे** — हा password जो कोणाकडे जाईल तो या container वर काहीही करू शकतो
(सगळे secrets वाचणं, files delete करणं, नवीन software चालवणं). `WEB_PASSWORD` पेक्षा वेगळा, लांब, फक्त इथेच
वापरलेला password ठेव; गरज नसेल तेव्हा `TERMINAL_PASSWORD` env काढून हे पूर्ण बंद कर.
(Terminal UI साठी लागणारी xterm.js library `term_assets/` मध्ये bundled आहे — कोणत्याही बाहेरच्या CDN वर अवलंबून नाही.)

## 🖥 Machines (`/machines`) — सगळ्या SSH terminals चा file manager
Website च्या `⋯` menu → **🖥 Machines**. PC सारखा file manager: folders, open/edit text, image/video preview, upload (drag-drop), download,
new folder, rename, cut / copy / paste, delete — आणि **एका machine वरून दुसऱ्या machine वर** copy/move (clipboard machines मध्ये चालतं).
* Website login (`WEB_PASSWORD`) लागतो — हा server public आहे, म्हणून. Machines वर स्वतः कोणताही token/password नाही (tailnet ही security).
* ही env टाका: `TAILSCALE_AUTHKEY` (hermes terminal साठी वापरलेलीच key). `TS_HOSTNAME` (default `tgstorage`), `TS_IDLE_MIN` (10), `FS_PORT` (8765),
  `MACHINES="name=host:port"` (auto-discovery शिवाय हातानं जोडायला).
* **RAM/CPU:** tailscaled (~30 MB) फक्त Machines page उघडल्यावर सुरू होतो आणि `TS_IDLE_MIN` मिनिटं वापर नसेल तर बंद होतो. Files नेहमी 256 KB
  तुकड्यांनी stream होतात — मोठ्या files साठी पण RAM वाढत नाही. Upload दरम्यान body आधी disk वर (RAM नाही) येते.
* प्रत्येक SSH terminal repo (`hermesterminal`) मध्ये `fsd.py` असावा — तो tailnet-only आहे, बाहेरून दिसत नाही.

## 📊 Server monitor (`/memory`)
एकाच public page वर: **हा server** (container चे अचूक CPU / RAM / Disk, `sysstats.py`) + तुमचे **इतर servers**
(SSH terminal `hermes-ts`, `hermes` PicoClaw …). त्या servers च्या repo मध्ये काहीच बदलावं लागत नाही — monitor त्यांचं
स्वतःचं stats JSON वाचतो (`<url>/api` किंवा `<url>/api/stats`, format आपोआप ओळखतो).

**Server जोडणं (दोन मार्ग):**
* env: `MONITOR_SERVERS="Terminal=https://hermesterminal.onrender.com, Hermes=https://xxx.koyeb.app"`
* bot (फक्त logged-in admins): `/addserver Terminal https://hermesterminal.onrender.com` · `/delserver Terminal` · `/servers`
  (bot ने जोडलेले MongoDB मध्ये — नसेल तर `/data/monitor_servers.json` मध्ये — save होतात). Bot menu मध्ये **📊 सगळ्या Servers चे stats** बटन पण येतं.

**खर्च कमी:** page कोणी उघडला नसेल तर इतर servers ना **एकही request जात नाही** आणि इथे sampling पण थांबतं (दर 10 s ला एक cgroup file वाचन).
Page उघडला की इथलं sampling 2 s ला आणि इतर servers चं polling 5 s ला (parallel, 4 s timeout); शेवटच्या visit नंतर ~15 s नी पुन्हा idle.
Page कधीच servers चे URL दाखवत नाही; redirect follow होत नाहीत; loopback / link-local / metadata address नाकारले जातात.
Render free वरचा server झोपला असेल तर page उघडल्यावर तो "waking up…" दिसतो आणि जागा होताच आकडे येतात.

Env (optional): `MONITOR_SELF_NAME` (default `tgstorage`), `MONITOR_POLL_SECONDS` (5), `MONITOR_FILE`, `RAM_LIMIT_MB` / `CPU_LIMIT` / `DISK_LIMIT_MB`
(हा server आणि बाकी — plan ची limit दिसली नाही तर default 512 MB / 0.1 vCPU / 15 GB).

**Limits बंद आहेत:** bandwidth limit आणि account limit default ने लागू होत नाहीत. परत हवे असतील तर `ENFORCE_LIMITS=1`
(मग `BW_LIMIT_GB` आणि `MAX_ACCOUNTS` लागू होतात).

Secret बनवण्याच्या commands:
* `SESSION_KEY`: `python -c "from cryptography.fernet import Fernet;print(Fernet.generate_key().decode())"`
* `ADMIN_TOTP_SECRET`: `python -c "import secrets,base64;print(base64.b32encode(secrets.token_bytes(20)).decode())"`

## Deploy (Koyeb / Render / Docker)
1. हा folder private GitHub repo मध्ये टाका (`Dockerfile` वरच्या पातळीवर).
2. Docker service बनवा, port = `PORT` (default 8080), health check path `/healthz`.
3. वरचे env टाका. Bot ला backup group मध्ये admin करा (Post messages + Manage topics).
4. Bot ला message करा → password → 2FA code → menu.

## लक्षात ठेवा
* एका वेळी एकच instance (Telegram sessions / Mongo lease) — redeploy वेळी जुना बंद होईपर्यंत नवा थांबतो.
* RAM: Backup Browser + Telegram accounts (प्रत्येकी ~30-50 MB). Free 512 MB वर `MAX_ACCOUNTS` कमी ठेवा.
* Free tier ची disk ephemeral आहे — म्हणूनच Mongo आवश्यक.

## Koyeb / 512 MB — troubleshooting
* **Port सुरुवातीलाच उघडतो** (`/healthz` लगेच `ok`), मग Mongo/Telegram भाग हळूहळू सुरू होतात. Boot वेळी बाकी paths `503 starting` देतात.
* **`getUpdates failed: HTTP 409`** = दुसरा instance त्याच `BOT_TOKEN` वर bot चालवतोय (जुना Render Worker Hub!) किंवा webhook लागलेला.
  जुना Render service **Suspend/Delete** करा. Redeploy वेळी काही सेकंद 409 येणं normal आहे.
* **`Waiting for N seconds (channels.GetMessages)`** = पहिल्या वेळचं index scan (Telegram flood-limit). Snapshot save झाल्यावर पुढच्या start ला फक्त नवीन files scan होतात (Mongo आवश्यक).
* 512 MB ओळखलं तर defaults आपोआप: `MAX_ACCOUNTS=3`, `DISK_CACHE_GB=1`. Outbound limit नसल्याने `BW_LIMIT_GB=100000`. सगळे env ने बदलता येतात.
* Bot → 📦 Storage मध्ये 🧠 RAM वापर दिसतो. 400 MB च्या वर गेला तर `MAX_ACCOUNTS` कमी करा.
