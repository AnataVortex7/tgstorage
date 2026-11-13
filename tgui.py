"""tgui.py — Official Telegram Web सारखा आधुनिक UI + Multi-Threaded Download & Server Cache Manager."""

ENTER_PAGE = r'''<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
<meta name="theme-color" content="#151f2b"><meta name="mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-capable" content="yes"><meta name="apple-mobile-web-app-status-bar-style" content="black-translucent">
<meta name="apple-mobile-web-app-title" content="TG Hub"><link rel="manifest" href="/app/manifest.json?v=5">
<link rel="icon" href="/app/icon-192.png" sizes="192x192" type="image/png"><link rel="icon" href="/app/icon-512.png" sizes="512x512" type="image/png"><link rel="apple-touch-icon" href="/app/icon-192.png">
<title>Telegram Hub Pro</title></head><body style="font:15px system-ui;background:#0b121a;color:#f1f5f9;display:flex;align-items:center;justify-content:center;height:100vh;margin:0;padding:20px">
<div id="box" style="background:#151f2b;border:1px solid #223042;border-radius:16px;padding:28px;max-width:380px;width:100%;text-align:center;box-shadow:0 10px 30px rgba(0,0,0,.5)">⏳ Connecting & preparing app…</div>
<script>
let swReady = Promise.resolve();
if('serviceWorker' in navigator){
 swReady = navigator.serviceWorker.register('/app/sw.js',{scope:'/app/'}).then(()=>navigator.serviceWorker.ready).catch(()=>{});
}
const sp=new URLSearchParams(location.search),k=sp.get('k')||'';
let dt=sp.get('dt')||'';
try{if(!dt)dt=JSON.parse(localStorage.getItem('tg_pwa_dt')||'""')}catch(e){}
history.replaceState(null,'','/app/');
function tryEnter(payload){
 fetch('/app/enter',{method:'POST',headers:{'Content-Type':'application/json','X-Requested-With':'app'},body:JSON.stringify(payload)})
  .then(async r=>{
   if(r.ok){
    try{const d=await r.json();if(d.dt)localStorage.setItem('tg_pwa_dt',JSON.stringify(d.dt))}catch(e){}
    await Promise.race([swReady, new Promise(res=>setTimeout(res,650))]);
    location.replace('/app/');
   }else{
    showPrompt();
   }
  }).catch(()=>showPrompt());
}
function showPrompt(){
 const b=document.getElementById('box');
 b.innerHTML='<div style="font-size:38px;margin-bottom:8px">📲</div><h3 style="margin:0 0 8px;color:#5eb5f7">Telegram Hub App</h3><p style="color:#7c90a6;font-size:13.5px;margin:0 0 14px">Bot मधून मिळालेला नवीन UI Link किंवा Token खाली पेस्ट करा (एकदा लॉगिन केल्यावर हे App ३० दिवस आपोआप चालू राहील):</p><input id="in" placeholder="Paste /app?k=... link or token" style="width:100%;padding:10px 12px;border-radius:8px;border:1px solid #2b3d52;background:#0b121a;color:#fff;margin-bottom:10px;box-sizing:border-box"><button id="go" style="width:100%;padding:10px;border-radius:8px;border:0;background:#2481cc;color:#fff;font-weight:700;cursor:pointer">🔓 Open App</button>';
 document.getElementById('go').onclick=()=>{
  const v=document.getElementById('in').value.trim();
  if(!v)return;
  const m=v.match(/[?&]k=([^&\s]+)/);
  tryEnter({k:m?m[1]:v});
 };
}
if(k||dt)tryEnter({k,dt});else showPrompt();
</script></body>'''

UI_PAGE = r'''<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,maximum-scale=1,user-scalable=no,viewport-fit=cover">
<meta name="theme-color" content="#151f2b"><meta name="mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-capable" content="yes"><meta name="apple-mobile-web-app-status-bar-style" content="black-translucent">
<meta name="apple-mobile-web-app-title" content="TG Hub"><link rel="manifest" href="/app/manifest.json?v=5"><link rel="icon" href="/app/icon-192.png" sizes="192x192" type="image/png"><link rel="icon" href="/app/icon-512.png" sizes="512x512" type="image/png"><link rel="apple-touch-icon" href="/app/icon-192.png">
<title>TG Hub</title>
<style>
:root{--bg:#e4ebf0;--side:#ffffff;--in:#ffffff;--out:#e3fdd6;--acc:#2481cc;--acc2:#1a6fb3;--fg:#0f172a;--mut:#64748b;--bd:#e2e8f0;--hov:#f1f5f9;--sel:#2481cc;--bdg:#2481cc;--bdgm:#94a3b8;--ink:#fff;--sh:0 1px 3px rgba(0,0,0,.08);--pat:rgba(0,0,0,.035);--glass:rgba(255,255,255,.92)}
[data-theme=dark]{--bg:#0b121a;--side:#151f2b;--in:#1c2938;--out:#244d75;--acc:#5eb5f7;--acc2:#3b9de8;--fg:#f1f5f9;--mut:#7c90a6;--bd:#0d1620;--hov:#1e2c3c;--sel:#25496d;--bdg:#3b9de8;--bdgm:#3a4f66;--ink:#fff;--sh:0 2px 5px rgba(0,0,0,.35);--pat:rgba(255,255,255,.028);--glass:rgba(21,31,43,.92)}
*{box-sizing:border-box;-webkit-tap-highlight-color:transparent}html,body{height:100%;height:100dvh;margin:0;overflow:hidden;overscroll-behavior:none;user-select:none;-webkit-user-select:none}.mt,input,textarea,pre{user-select:text;-webkit-user-select:text}
body{background:var(--bg);color:var(--fg);font:var(--fs,15px)/1.38 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Helvetica,Arial,sans-serif}
::-webkit-scrollbar{width:5px;height:5px}::-webkit-scrollbar-thumb{background:rgba(128,128,128,.32);border-radius:4px}
button,input,textarea,select{font:inherit;color:inherit}button{cursor:pointer;background:none;border:0;transition:background .15s,transform .1s,opacity .15s}button:active{transform:scale(.96)}
#app{display:flex;height:100%}#side{width:min(420px,34vw);min-width:310px;background:var(--side);display:flex;flex-direction:column;border-right:1px solid var(--bd);position:relative;z-index:12}
#main{flex:1;min-width:0;display:flex;flex-direction:column;background:var(--bg);position:relative}
#info{width:370px;background:var(--side);border-left:1px solid var(--bd);display:none;flex-direction:column;overflow:auto;z-index:15}
.info-on #info{display:flex}.hd{display:flex;align-items:center;gap:8px;padding:8px 12px;min-height:56px;background:var(--glass);backdrop-filter:blur(12px);border-bottom:1px solid var(--bd)}
.ib{width:38px;height:38px;border-radius:50%;display:inline-flex;align-items:center;justify-content:center;font-size:18px;color:var(--mut);flex:none;position:relative}.ib:hover{background:var(--hov);color:var(--fg)}
.ib .bd-dot{position:absolute;top:4px;right:4px;background:#10b981;color:#fff;font-size:10px;font-weight:700;border-radius:8px;padding:0 4px;min-width:15px;height:15px;display:flex;align-items:center;justify-content:center}
#accbar{display:flex;gap:6px;padding:6px 10px;overflow-x:auto;border-bottom:1px solid var(--bd);background:var(--side);scrollbar-width:none;align-items:center}
#accbar:empty,#dlbar:empty{display:none}#dlbar{padding:6px 10px;background:rgba(36,129,204,.1);border-bottom:1px solid var(--bd);display:flex;flex-direction:column;gap:4px;font-size:12.5px}
.apill{display: inline-flex;align-items:center;gap:6px;padding:4px 10px 4px 5px;border-radius:18px;background:var(--hov);font-size:12.5px;font-weight:600;white-space:nowrap;cursor:pointer;border:1.5px solid transparent}
.apill.on{border-color:var(--acc);background:rgba(51,144,236,.14);color:var(--acc)}
.apill .st-d{width:8px;height:8px;border-radius:50%;background:#94a3b8}.apill .st-d.ok{background:#10b981}
#search{flex:1;min-width:0;border:1px solid transparent;background:var(--hov);border-radius:20px;padding:8px 15px;outline:0;transition:border-color .15s,background .15s}#search:focus{border-color:var(--acc);background:var(--side)}
#folders{display:flex;gap:2px;padding:0 8px;overflow-x:auto;border-bottom:1px solid var(--bd);scrollbar-width:none;background:var(--side)}
#folders button{padding:10px 12px;color:var(--mut);font-weight:600;font-size:13.5px;border-bottom:3px solid transparent;white-space:nowrap}#folders button.on{color:var(--acc);border-color:var(--acc)}
#folders .n{background:var(--bdg);color:#fff;border-radius:10px;font-size:11px;padding:1px 6px;margin-left:5px}
#list{flex:1;overflow-y:auto;padding:4px 6px}.row{display:flex;gap:11px;padding:8px 10px;cursor:pointer;align-items:center;border-radius:12px;margin-bottom:1px}.row:hover{background:var(--hov)}.row.on{background:var(--sel);color:#fff}.row.on .mut,.row.on .t,.row.on .typ{color:#ffffffd9!important}
.av{position:relative;width:52px;height:52px;border-radius:50%;flex:none;display:flex;align-items:center;justify-content:center;color:#fff;font-weight:700;font-size:19px;overflow:hidden;box-shadow:inset 0 0 0 1px rgba(0,0,0,.06)}
.av img{position:absolute;inset:0;width:100%;height:100%;object-fit:cover}.dot{position:absolute;right:1px;bottom:1px;width:13px;height:13px;border-radius:50%;background:#10b981;border:2px solid var(--side)}
.rm{flex:1;min-width:0}.r1,.r2{display:flex;justify-content:space-between;gap:8px;align-items:center}.r1 b{white-space:nowrap;overflow:hidden;text-overflow:ellipsis;font-weight:600;font-size:15px}.t{color:var(--mut);font-size:12px;white-space:nowrap}
.r2{margin-top:2px}.r2 .mut{white-space:nowrap;overflow:hidden;text-overflow:ellipsis;font-size:13.5px}.mut{color:var(--mut)}.typ{color:var(--acc);font-weight:500}
.bd{background:var(--bdg);color:#fff;border-radius:11px;min-width:21px;height:21px;padding:0 6px;display:inline-flex;align-items:center;justify-content:center;font-size:12px;font-weight:700;flex:none}.bd.m{background:var(--bdgm)}
.sec{padding:8px 14px 4px;color:var(--acc);font-weight:700;font-size:12.5px;text-transform:uppercase;letter-spacing:.04em}
#fab{position:absolute;right:16px;bottom:16px;width:54px;height:54px;border-radius:50%;background:linear-gradient(135deg,var(--acc),var(--acc2));color:#fff;font-size:22px;box-shadow:0 4px 14px rgba(36,129,204,.45)}
#drawer{position:absolute;inset:0;background:rgba(0,0,0,.5);display:none;z-index:30;backdrop-filter:blur(2px)}#drawer.on{display:block}
#dr{width:min(320px,86%);height:100%;background:var(--side);overflow:auto;display:flex;flex-direction:column;box-shadow:4px 0 24px rgba(0,0,0,.35)}
#dr .it{display:flex;gap:14px;align-items:center;padding:11px 18px;cursor:pointer;font-weight:500}#dr .it:hover{background:var(--hov)}#dr .ac{padding:8px 18px;display:flex;gap:10px;align-items:center;cursor:pointer}#dr .ac.on{background:var(--hov)}
#cbar{display:flex;align-items:center;gap:10px;padding:6px 12px;min-height:56px;background:var(--glass);backdrop-filter:blur(12px);border-bottom:1px solid var(--bd);z-index:5}
#cbar .tt{flex:1;min-width:0;cursor:pointer}#cbar .tt b{display:block;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;font-size:15.5px}#cbar .st{font-size:12.5px;color:var(--mut)}#cbar .st.on{color:var(--acc);font-weight:500}
#back{display:none}
#tbar{display:none;gap:6px;padding:6px 12px;overflow-x:auto;background:var(--side);border-bottom:1px solid var(--bd);scrollbar-width:none;align-items:center}
#tbar button{padding:5px 12px;border-radius:16px;background:var(--hov);font-size:13px;font-weight:600;white-space:nowrap;color:var(--mut)}
#tbar button.on{background:var(--acc);color:#fff}
#pin{display:none;gap:10px;align-items:center;padding:6px 14px;background:var(--side);border-bottom:1px solid var(--bd);cursor:pointer}#pin .bar{width:3px;align-self:stretch;background:var(--acc);border-radius:2px}
#msgs{flex:1;overflow-y:auto;padding:12px 0 8px;position:relative;background-image:radial-gradient(var(--pat) 1.5px,transparent 1.5px);background-size:20px 20px}
#mi{max-width:800px;margin:0 auto;padding:0 12px;display:flex;flex-direction:column;gap:3px}
.day{align-self:center;background:rgba(0,0,0,.32);backdrop-filter:blur(4px);color:#fff;border-radius:14px;padding:3px 12px;font-size:12.5px;margin:8px 0;font-weight:600}.unr{align-self:stretch;text-align:center;background:rgba(51,144,236,.15);color:var(--acc);font-weight:600;padding:4px;font-size:13px;margin:6px -12px}
.svc{align-self:center;background:rgba(0,0,0,.32);color:#fff;border-radius:14px;padding:3px 12px;font-size:12.5px;margin:4px 0;text-align:center;max-width:88%}
.mr{display:flex;gap:8px;align-items:flex-end;position:relative}.mr.out{flex-direction:row-reverse}.mr .av{width:34px;height:34px;font-size:14px}.mr .sp{width:34px;flex:none}
.m{position:relative;max-width:min(78%,550px);background:var(--in);border-radius:14px;padding:6px 10px 5px;box-shadow:var(--sh);word-break:break-word;min-width:68px;transition:outline .12s}
.out .m{background:var(--out)}.m.tl{border-bottom-left-radius:4px}.out .m.tl{border-bottom-left-radius:14px;border-bottom-right-radius:4px}.m.hl{animation:hl 1.4s}.m.sel{outline:2.5px solid var(--acc)}
@keyframes hl{0%,60%{filter:brightness(1.45)}100%{filter:none}}
.m:hover .qact{opacity:1;pointer-events:auto}
.qact{position:absolute;top:-14px;right:6px;background:var(--side);border:1px solid var(--bd);border-radius:16px;padding:2px 5px;display:flex;gap:2px;box-shadow:0 3px 10px rgba(0,0,0,.18);opacity:0;pointer-events:none;transition:opacity .14s;z-index:4}
.out .qact{right:auto;left:6px}
.qact button{font-size:13.5px;padding:2px 5px;border-radius:10px}.qact button:hover{background:var(--hov)}
.m.nobg{background:none;box-shadow:none;padding:0}.mn{font-weight:700;font-size:13.5px;margin-bottom:2px;cursor:pointer}.mt{white-space:pre-wrap;line-height:1.4}.mt a,.mt .lk{color:var(--acc);text-decoration:none}.mt a:hover{text-decoration:underline}
.mt code{font-family:ui-monospace,Consolas,monospace;background:rgba(0,0,0,.14);border-radius:4px;padding:1px 5px;font-size:.92em}.mt pre{margin:4px 0;background:rgba(0,0,0,.18);padding:8px 10px;border-radius:8px;overflow:auto;font-size:.9em}.mt blockquote{margin:3px 0;padding:2px 0 2px 9px;border-left:3px solid var(--acc);opacity:.92}
.tr-box{margin-top:5px;padding:5px 8px;border-radius:8px;background:rgba(51,144,236,.12);border-left:3px solid var(--acc);font-size:13.5px;white-space:pre-wrap}
.sp-l{background:#888;color:transparent;border-radius:4px;cursor:pointer;user-select:none}.sp-l.rv{background:none;color:inherit}
.meta{float:right;margin:6px 0 -2px 10px;font-size:11px;color:var(--mut);display:flex;gap:4px;align-items:center;height:14px}.out .meta{color:#5a9a56}[data-theme=dark] .out .meta{color:#93bee8}
.tk{color:var(--acc);letter-spacing:-5px;margin-right:5px;font-weight:700}.fw{color:var(--acc);font-size:12.5px;font-weight:600;margin-bottom:2px}.rp{border-left:3px solid var(--acc);padding:2px 8px;margin-bottom:4px;cursor:pointer;border-radius:4px;background:rgba(0,0,0,.06)}.rp b{display:block;color:var(--acc);font-size:12.5px}.rp span{display:block;font-size:13px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;max-width:280px}
.ph,.vid{position:relative;border-radius:10px;overflow:hidden;margin:-2px -4px 4px;cursor:pointer;background:rgba(0,0,0,.18);min-width:220px;min-height:120px}.vid.playing{min-width:min(360px,78vw);width:100%;max-width:520px;background:#000;cursor:default;aspect-ratio:auto!important}.ph img,.vid img,.vid video{display:block;width:100%;max-height:440px;object-fit:cover}.vid video{object-fit:contain;background:#000;min-height:190px;width:100%}
.play{position:absolute;inset:0;display:flex;align-items:center;justify-content:center;pointer-events:none}.play i{width:56px;height:56px;border-radius:50%;background:rgba(0,0,0,.65);backdrop-filter:blur(4px);color:#fff;display:flex;align-items:center;justify-content:center;font-style:normal;font-size:22px;box-shadow:0 4px 14px rgba(0,0,0,.35)}
.chip{position:absolute;left:8px;top:8px;background:rgba(0,0,0,.62);backdrop-filter:blur(4px);color:#fff;border-radius:10px;padding:2px 8px;font-size:11.5px;font-weight:500}
.vbar{display:flex;gap:6px;align-items:center;justify-content:space-between;padding:5px 8px;background:rgba(0,0,0,.78);color:#fff;font-size:12px}
.vbar button{color:#fff;background:rgba(255,255,255,.15);border-radius:6px;padding:3px 8px;font-size:11.5px;font-weight:600}.vbar button:hover{background:rgba(255,255,255,.28)}
.stk{width:170px;max-width:60vw;display:block}.stk-e{font-size:80px;line-height:1}
.file{display:flex;gap:10px;align-items:center;min-width:230px;padding:3px 2px}.fi{width:46px;height:46px;border-radius:50%;background:linear-gradient(135deg,var(--acc),var(--acc2));color:#fff;display:flex;align-items:center;justify-content:center;font-size:20px;flex:none;box-shadow:0 2px 6px rgba(36,129,204,.3)}.file b{display:block;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;max-width:230px;font-size:14px}.file a{color:inherit;text-decoration:none}
audio{max-width:100%;height:38px;margin-top:3px}.card{border-left:3px solid var(--acc);padding:4px 9px;margin:4px 0;background:rgba(0,0,0,.06);border-radius:6px}.card b{color:var(--acc)}.card .ds{font-size:13px}
.poll b.q{display:block;margin-bottom:5px;font-size:14.5px}.po{position:relative;padding:6px 10px;margin:4px 0;border-radius:8px;background:rgba(0,0,0,.08);cursor:pointer;overflow:hidden}.po i{position:absolute;left:0;top:0;bottom:0;background:var(--acc);opacity:.26;transition:width .25s}.po span{position:relative;display:flex;justify-content:space-between;gap:10px;font-weight:500}
.rx{display:flex;flex-wrap:wrap;gap:4px;margin:5px 0 0}.rx button{background:rgba(0,0,0,.1);border-radius:12px;padding:2px 8px;font-size:13.5px;font-weight:600}.rx button.me{background:var(--acc);color:#fff}
.bt{display:flex;flex-direction:column;gap:4px;margin-top:6px;min-width:210px}.bt div{display:flex;gap:4px}.bt button{flex:1;background:rgba(0,0,0,.28);color:#fff;border-radius:8px;padding:7px 6px;font-size:13.5px;font-weight:600}
#dn{position:absolute;right:18px;bottom:14px;display:none;width:44px;height:44px;border-radius:50%;background:var(--side);box-shadow:0 3px 12px rgba(0,0,0,.28);font-size:18px;color:var(--mut);z-index:6}#dn .bd{position:absolute;top:-8px;right:-4px}
#ups{max-width:800px;margin:0 auto;width:100%;padding:0 12px}.up{background:var(--side);border-radius:10px;padding:7px 11px;margin:4px 0;font-size:13px;box-shadow:var(--sh)}.up .pb{height:5px;border-radius:3px;background:var(--bd);margin-top:5px;overflow:hidden}.up .pb i{display:block;height:100%;background:var(--acc);width:0;transition:width .2s}
#comp{background:var(--bg);padding-bottom:env(safe-area-inset-bottom,0px)}#cin{max-width:800px;margin:0 auto;padding:6px 12px 10px}#bar2{display:none;background:var(--side);border-radius:12px 12px 0 0;padding:6px 12px;gap:8px;align-items:center;border-bottom:1px solid var(--bd)}
#box{display:flex;gap:8px;align-items:flex-end}#pillbox{flex:1;display:flex;align-items:flex-end;background:var(--side);border-radius:24px;padding:2px 6px;box-shadow:var(--sh)}
#txt{flex:1;border:0;background:none;outline:0;resize:none;max-height:180px;padding:10px 6px;line-height:1.35}#send,#mic{width:46px;height:46px;border-radius:50%;background:linear-gradient(135deg,var(--acc),var(--acc2));color:#fff;font-size:19px;flex:none;box-shadow:var(--sh)}
#mic.rec{background:#ef4444;animation:pulse 1s infinite}
@keyframes pulse{0%,100%{transform:scale(1)}50%{transform:scale(1.08)}}
#emo{display:none;background:var(--side);border-radius:14px;margin-bottom:6px;padding:8px;max-height:200px;overflow:auto;grid-template-columns:repeat(auto-fill,minmax(38px,1fr));box-shadow:var(--sh)}#emo.on{display:grid}#emo button{font-size:23px;padding:4px;border-radius:6px}#emo button:hover{background:var(--hov)}
#sel{display:none;align-items:center;gap:10px;padding:8px 14px;background:var(--side);border-bottom:1px solid var(--bd)}
.menu{position:fixed;z-index:60;background:var(--side);border:1px solid var(--bd);border-radius:12px;box-shadow:0 8px 26px rgba(0,0,0,.38);padding:5px 0;min-width:200px;max-width:280px;max-height:85vh;overflow:auto}.menu .mi2{padding:8px 16px;cursor:pointer;display:flex;gap:12px;white-space:nowrap;align-items:center;font-size:14px}.menu .mi2:hover{background:var(--hov)}.menu .rb{display:flex;gap:2px;padding:4px 8px;border-bottom:1px solid var(--bd);overflow-x:auto}.menu .rb button{font-size:21px;padding:2px 4px;border-radius:6px}.menu .rb button:hover{background:var(--hov)}.menu .red{color:#ef4444}
.ov{position:fixed;inset:0;background:rgba(0,0,0,.62);backdrop-filter:blur(2px);z-index:50;display:flex;align-items:center;justify-content:center;padding:14px}.mdl{background:var(--side);border-radius:16px;width:min(480px,100%);max-height:88vh;display:flex;flex-direction:column;box-shadow:0 12px 36px rgba(0,0,0,.5);border:1px solid var(--bd)}
.mdl h3{margin:0;padding:15px 18px 8px;font-size:17px}.mb{padding:6px 18px 14px;overflow:auto;display:flex;flex-direction:column;gap:9px}.mf{display:flex;gap:8px;justify-content:flex-end;padding:8px 14px 12px;border-top:1px solid var(--bd)}.mf button{padding:8px 15px;border-radius:8px;color:var(--acc);font-weight:600}.mf button.p{background:var(--acc);color:#fff}.mf button.d{color:#ef4444}
.mdl input[type=text],.mdl input[type=password],.mdl input[type=tel],.mdl input[type=number],.mdl input:not([type]),.mdl select,.mdl textarea,.mdl input[type=datetime-local],.mdl input[type=date]{width:100%;border:1px solid var(--bd);border-radius:9px;padding:9px 12px;background:var(--bg);outline:0}
.mdl input:focus,.mdl select:focus,.mdl textarea:focus{border-color:var(--acc)}
.mdl label{display:flex;gap:8px;align-items:center;cursor:pointer}.err{color:#ef4444;min-height:1em;font-size:13px}.sw{display:flex;justify-content:space-between;align-items:center;gap:10px;padding:5px 0}
#lb{position:fixed;inset:0;background:rgba(0,0,0,.92);z-index:70;display:none;align-items:center;justify-content:center}#lb img{max-width:96vw;max-height:92vh;border-radius:4px}#lb .x{position:absolute;top:10px;right:14px;color:#fff;font-size:26px}#lb .nv{position:absolute;top:50%;color:#fff;font-size:34px;padding:10px}
#toast{position:fixed;left:50%;bottom:88px;transform:translateX(-50%);background:rgba(15,23,42,.92);color:#fff;border-radius:20px;padding:9px 18px;z-index:90;display:none;max-width:90vw;font-weight:500;box-shadow:0 4px 16px rgba(0,0,0,.35)}
#info .big{display:flex;flex-direction:column;align-items:center;padding:18px 14px 10px}#info .big .av{width:106px;height:106px;font-size:38px}.kv{padding:8px 18px}.kv small{display:block;color:var(--mut);margin-top:2px}
.tabs{display:flex;border-bottom:1px solid var(--bd);overflow-x:auto;scrollbar-width:none}.tabs button{padding:10px 12px;color:var(--mut);white-space:nowrap;font-weight:600;font-size:13px}.tabs button.on{color:var(--acc);border-bottom:2px solid var(--acc)}
.grid{display:grid;grid-template-columns:repeat(3,1fr);gap:2px;padding:2px}.grid div{aspect-ratio:1;background:rgba(0,0,0,.2);position:relative;overflow:hidden;cursor:pointer}.grid img{width:100%;height:100%;object-fit:cover}
.empty{margin:auto;color:var(--mut);padding:30px;text-align:center}
@media(max-width:820px){#side{width:100%;min-width:0}#main{position:absolute;inset:0;transform:translateX(100%);transition:transform .2s;z-index:14}.chat #main{transform:none}#back{display:inline-flex}.info-on #info{position:absolute;inset:0;width:100%;z-index:20}.m{max-width:86%}.qact{display:none}}
#fv{position:fixed;inset:0;z-index:75;background:rgba(0,0,0,.92);display:none;flex-direction:column;color:#fff}
#fv .vh{display:flex;align-items:center;gap:8px;padding:8px 12px;background:rgba(0,0,0,.65)}#fv .vh b{flex:1;min-width:0;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}#fv .vh .ib{color:#fff;text-decoration:none}
#fv .vb{flex:1;min-height:0;display:flex;align-items:center;justify-content:center;padding:12px;overflow:auto;flex-direction:column;gap:8px}#fv video{width:min(92vw,960px);max-height:82vh;min-height:240px;background:#000;border-radius:8px;display:block}#fv img{max-width:100%;max-height:84vh;border-radius:8px}#fv iframe{width:100%;height:100%;border:0;background:#fff;border-radius:8px}
#fv pre{align-self:stretch;width:100%;margin:0;background:var(--side);color:var(--fg);padding:12px;border-radius:8px;overflow:auto;white-space:pre-wrap;word-break:break-word;font:13px/1.4 ui-monospace,Consolas,monospace}
.file{cursor:pointer}.stb{border-top:1px solid var(--bd);padding:10px 18px}.stb button{display:block;text-align:left;padding:6px 0;color:var(--acc);font-weight:500}
.dl-card{background:var(--hov);border-radius:10px;padding:9px 12px;display:flex;flex-direction:column;gap:5px}
.dl-card .top{display:flex;justify-content:space-between;gap:8px;align-items:center}
.dl-card .top b{white-space:nowrap;overflow:hidden;text-overflow:ellipsis;flex:1;font-size:13.5px}
.badge{display:inline-flex;align-items:center;gap:3px;padding:2px 7px;border-radius:10px;font-size:11px;font-weight:700;background:rgba(51,144,236,.16);color:var(--acc)}
.badge.ok{background:rgba(16,185,129,.16);color:#10b981}
</style></head><body>
<div id="app">
 <aside id="side">
  <div class="hd">
   <button class="ib" id="menu" title="Menu">☰</button>
   <input id="search" placeholder="Search chats or @username…" autocomplete="off">
   <button class="ib" id="pwa-btn" title="Install as Mobile APK / PC Desktop App">📲</button>
   <button class="ib" id="dlmgr" title="Multi-Thread Downloads & Server Cache">⚡<span class="bd-dot" id="dlcnt" style="display:none">0</span></button>
  </div>
  <div id="pwa-banner" style="display:none;padding:7px 10px;background:linear-gradient(90deg,rgba(16,185,129,.16),rgba(36,129,204,.16));border-bottom:1px solid var(--bd);align-items:center;justify-content:space-between;gap:8px;font-size:12.5px"></div>
  <div id="accbar"></div>
  <div id="dlbar"></div>
  <div id="folders"></div>
  <div id="list"><div class="empty">⏳ Loading Telegram chats…</div></div>
  <button id="fab" title="New message / group / channel">✎</button>
  <div id="drawer"><div id="dr"></div></div>
 </aside>
 <main id="main">
  <div id="cbar" style="display:none">
   <button class="ib" id="back">←</button><div id="cav"></div><div class="tt" id="ctt"><b></b><span class="st"></span></div>
   <button class="ib" id="csched" title="Scheduled messages">⏰</button>
   <button class="ib" id="cdate" title="Jump to date">📅</button>
   <button class="ib" id="csr" title="Search in chat">🔍</button>
   <button class="ib" id="cinfo" title="Chat Info">ⓘ</button>
   <button class="ib" id="cmore" title="More actions">⋮</button>
  </div>
  <div id="tbar"></div>
  <div id="sel">
   <button class="ib" id="selx">✕</button><b id="selc" style="flex:1"></b>
   <button class="ib" id="selrng" title="Select range between first & last">↔</button>
   <button class="ib" id="selcp" title="Copy text">⧉</button>
   <button class="ib" id="seldl" title="Download files">⬇</button>
   <button class="ib" id="self" title="Forward selected">➦</button>
   <button class="ib" id="seld" title="Delete selected">🗑</button>
  </div>
  <div id="pin">
   <div class="bar"></div>
   <div style="flex:1;min-width:0"><b style="color:var(--acc);font-size:12.5px">📌 Pinned message</b><div class="mut" id="pint" style="white-space:nowrap;overflow:hidden;text-overflow:ellipsis;font-size:13px"></div></div>
   <button class="ib" id="pinall" title="View all pinned messages" style="width:32px;height:32px;font-size:14px">☰</button>
  </div>
  <div id="msgs"><div id="mi"><div class="empty">Select a chat to start messaging</div></div><button id="dn">⌄</button></div>
  <div id="ups"></div>
  <div id="comp" style="display:none"><div id="cin"><div id="bar2"><div style="flex:1;min-width:0"></div><button class="ib" id="bar2x">✕</button></div><div id="emo"></div>
   <div id="box">
    <div id="pillbox">
     <button class="ib" id="emob" title="Emoji">☺</button>
     <textarea id="txt" rows="1" placeholder="Message"></textarea>
     <button class="ib" id="md" title="Markdown formatting">𝐁</button>
     <button class="ib" id="att" title="Attach file, media, or poll">📎</button>
    </div>
    <button id="mic" title="Record Voice Message (Click to start/stop)">🎤</button>
    <button id="send" title="Send (Right-click for Silent / Schedule)">➤</button>
   </div>
   <input id="file" type="file" multiple hidden></div></div>
 </main>
 <aside id="info"></aside>
</div>
<div id="lb"><button class="x">✕</button><button class="x" id="lbd" title="Download" style="right:64px">⬇</button><span id="lbc" style="position:absolute;top:18px;left:16px;color:#fff;font-size:14px"></span><button class="nv" style="left:6px">‹</button><img alt=""><button class="nv" style="right:6px">›</button></div>
<div id="fv"><div class="vh"><b id="fvt"></b><span id="fvst" style="font-size:12px;color:#6ee7b7"></span><a class="ib" id="fvd" title="Fast Download to Browser">⬇</a><button class="ib" id="fvx" title="Close">✕</button></div><div class="vb" id="fvb"></div></div>
<div id="toast"></div><iframe name="tg_dl_frame" id="dlframe" style="display:none"></iframe>
<script>
'use strict';
const $=(s,r)=>(r||document).querySelector(s);
const h=(t,p,...k)=>{const e=document.createElement(t);if(p)for(const[a,v]of Object.entries(p)){if(v==null||v===false)continue;if(a==='on')for(const[ev,f]of Object.entries(v))e.addEventListener(ev,f);else if(a==='text')e.textContent=v;else if(a==='class')e.className=v;else if(a==='style')e.style.cssText=v;else e.setAttribute(a,v===true?'':v)}for(const c of k.flat(9)){if(c==null||c===false)continue;e.append(c.nodeType?c:document.createTextNode(String(c)))}return e};
const LS={get(k,d){try{const v=localStorage.getItem('tg_'+k);return v==null?d:JSON.parse(v)}catch(e){return d}},set(k,v){try{localStorage.setItem('tg_'+k,JSON.stringify(v))}catch(e){}}};
const Q=o=>new URLSearchParams(o).toString(),sleep=ms=>new Promise(r=>setTimeout(r,ms));
const S={acc:null,accs:[],chat:null,cd:null,topic:0,topics:[],dl:[],adl:[],folder:'all',archived:false,msgs:[],mcache:{},readOut:0,readIn:0,pinned:null,typing:{},seq:0,sel:new Set(),reply:null,edit:null,uploads:{},dls:{},bw:null,defApi:false,loadingOld:false,noMore:false,gsearch:[],q:'',info:false,drafts:{},unreadFrom:0,dlCfg:{threads:4,ttl:3600,auto_cache:true},
 set:Object.assign({theme:'auto',avatars:true,previews:true,notif:false,sound:true,md:false,enter:true,fs:15},LS.get('set',{}))};
const EMOJI='😀😁😂🤣😊😍😘😎🤔😢😭😡👍👎👏🙏💪🔥❤️💔🎉✨🌹🎂🍕☕🚀✅❌⚠️📌📎💯😴🤝👀🙈😅😉😇🥰😋🤗🤩😏😬🙄😤😱🤯🥳😜🫡🫶⚡🌟💎🎵🎬📷👑🛡'.match(/\p{Extended_Pictographic}\uFE0F?/gu)||[];
const REACT=['👍','❤️','🔥','🥰','👏','😁','🤔','😢','🎉','⚡','💯','👎'];
const sz=n=>n>=1073741824?(n/1073741824).toFixed(2)+' GB':n>=1048576?(n/1048576).toFixed(1)+' MB':Math.max(1,Math.round((n||0)/1024))+' KB';
const spd=n=>!n?'':n>=1048576?(n/1048576).toFixed(1)+' MB/s':Math.round(n/1024)+' KB/s';
const fmtTtl=s=>{s=Math.max(0,Math.round(s||0));if(s>=3600)return Math.floor(s/3600)+'h '+Math.floor((s%3600)/60)+'m';if(s>=60)return Math.floor(s/60)+'m '+(s%60)+'s';return s+'s'};
const mbs=n=>Math.round(n/1048576)+' MB';
const BIGF=1024*1024,dlKey=m=>S.acc+':'+S.chat+':'+m.id,pctOf=st=>st.size?Math.min(100,Math.round((st.done||0)*100/st.size)):0;
const dur=s=>{s=Math.round(s||0);return Math.floor(s/60)+':'+String(s%60).padStart(2,'0')};
const tm=t=>t?new Date(t*1e3).toLocaleTimeString([],{hour:'2-digit',minute:'2-digit'}):'';
const dayKey=t=>{const d=new Date(t*1e3);return d.getFullYear()+'-'+d.getMonth()+'-'+d.getDate()};
function dayLabel(t){const d=new Date(t*1e3),n=new Date();if(dayKey(t)===dayKey(n/1e3))return 'Today';const y=new Date(n-864e5);if(dayKey(t)===dayKey(y/1e3))return 'Yesterday';return d.toLocaleDateString([],{day:'numeric',month:'long',year:d.getFullYear()===n.getFullYear()?undefined:'numeric'})}
function listTime(t){if(!t)return '';const d=new Date(t*1e3),n=new Date();if(dayKey(t)===dayKey(n/1e3))return tm(t);if(n-d<6048e5)return d.toLocaleDateString([],{weekday:'short'});return d.toLocaleDateString([],{day:'2-digit',month:'2-digit',year:'2-digit'})}
const COLS=['#e17076','#faa774','#a695e7','#7bc862','#6ec9cb','#65aadd','#ee7aae'];
const colorOf=id=>COLS[Math.abs(Number(id)||0)%COLS.length];
function toast(m,ms){const t=$('#toast');t.textContent=m;t.style.display='block';clearTimeout(toast.t);toast.t=setTimeout(()=>t.style.display='none',ms||3200)}
async function api(path,body,opt){const o={credentials:'same-origin'};if(opt&&opt.keepalive)o.keepalive=true;if(opt&&opt.raw){o.method='POST';o.headers={'X-Requested-With':'app','Content-Type':'application/octet-stream'};o.body=opt.raw}
 else if(body!==undefined){o.method='POST';o.headers={'Content-Type':'application/json','X-Requested-With':'app'};o.body=JSON.stringify(body)}
 const r=await fetch(path,o);let d={};try{d=await r.json()}catch(e){}
 if(!r.ok){if(r.status===404&&!d.error)throw new Error('Session expired — get a new link from the bot');const e=new Error(d.error||('error '+r.status));e.status=r.status;e.data=d;throw e}return d}
const A=(o)=>Object.assign({acc:S.acc},o);
const fileUrl=(b,dl)=>'/app/api/file?'+Q(Object.assign({},b,S.dlTok?{dt:S.dlTok}:{},dl?{dl:1}:{}));
const cur=()=>S.accs.find(a=>a.id===S.acc)||{};
function saveSet(){LS.set('set',S.set);applyTheme()}
function applyTheme(){document.documentElement.style.setProperty('--fs',(S.set.fs||15)+'px');let t=S.set.theme;if(t==='auto')t=(window.matchMedia&&matchMedia('(prefers-color-scheme: dark)').matches)?'dark':'light';document.documentElement.dataset.theme=t}
/* ---------- avatar ---------- */
function avatar(id,name,size,online){const a=h('div',{class:'av',style:`background:${colorOf(id)};${size?`width:${size}px;height:${size}px;font-size:${Math.round(size*.38)}px`:''}`,text:(name||'?').trim().slice(0,1).toUpperCase()});
 if(S.set.avatars&&S.acc&&id){const i=h('img',{alt:'',loading:'lazy',src:'/app/api/avatar?'+Q({acc:S.acc,chat:id}),on:{error(){i.remove()}}});a.append(i)}
 if(online)a.append(h('i',{class:'dot'}));return a}
/* ---------- rich text ---------- */
const SAFE=/^(https?:\/\/|mailto:)/i;
function wrapEnt(e,node,seg){const T=e.t;let el;
 if(T==='bold')el=h('b');else if(T==='italic')el=h('i');else if(T==='underline')el=h('u');else if(T==='strike')el=h('s');else if(T==='code')el=h('code');else if(T==='pre')el=h('pre');
 else if(T==='blockquote')el=h('blockquote');else if(T==='spoiler'){el=h('span',{class:'sp-l',on:{click(){el.classList.add('rv')}}})}
 else if(T==='url'){const u=/^https?:\/\//i.test(seg)?seg:'http://'+seg;el=SAFE.test(u)?h('a',{href:u,target:'_blank',rel:'noopener noreferrer'}):h('span')}
 else if(T==='texturl'){el=e.u&&SAFE.test(e.u)?h('a',{href:e.u,target:'_blank',rel:'noopener noreferrer',title:e.u}):h('span')}
 else if(T==='email'){el=h('a',{href:'mailto:'+seg})}
 else if(T==='mention'){el=h('span',{class:'lk',style:'cursor:pointer',on:{click(){openByQuery(seg)}}})}
 else if(T==='hashtag'||T==='cashtag'){el=h('span',{class:'lk',style:'cursor:pointer',on:{click(){$('#search').value=seg;onSearch()}}})}
 else if(T==='botcommand'){el=h('span',{class:'lk',style:'cursor:pointer',on:{click(){const t=$('#txt');t.value=seg;t.focus()}}})}
 else el=h('span');el.append(node);return el}
function richText(text,ents){const f=document.createDocumentFragment();if(!text)return f;const es=(ents||[]).filter(e=>e.l>0&&e.o>=0&&e.o<text.length);
 if(!es.length){f.append(text);return f}
 const pts=new Set([0,text.length]);es.forEach(e=>{pts.add(e.o);pts.add(Math.min(text.length,e.o+e.l))});const P=[...pts].sort((a,b)=>a-b);
 for(let i=0;i<P.length-1;i++){const a=P[i],b=P[i+1],seg=text.slice(a,b);if(!seg)continue;let node=document.createTextNode(seg);
  for(const e of es.filter(e=>e.o<=a&&e.o+e.l>=b).sort((x,y)=>x.l-y.l))node=wrapEnt(e,node,seg);f.append(node)}return f}
function plainLinks(text){const f=document.createDocumentFragment();const re=/https?:\/\/[^\s<>"']+/g;let last=0,m;while((m=re.exec(text))){f.append(text.slice(last,m.index));f.append(h('a',{href:m[0],target:'_blank',rel:'noopener noreferrer',text:m[0]}));last=m.index+m[0].length}f.append(text.slice(last));return f}
/* ---------- menus / modals ---------- */
let MENU=null;function closeMenu(){if(MENU){MENU.remove();MENU=null}}
function showMenu(x,y,items,top){closeMenu();const m=h('div',{class:'menu'});if(top)m.append(top);
 for(const it of items){if(!it)continue;m.append(h('div',{class:'mi2'+(it.red?' red':''),on:{click(){closeMenu();it.fn()}}},h('span',{text:it.ic||''}),it.t))}
 document.body.append(m);const w=m.offsetWidth||220,hh=m.offsetHeight||200;m.style.left=Math.max(6,Math.min(x,innerWidth-w-6))+'px';m.style.top=Math.max(6,Math.min(y,innerHeight-hh-6))+'px';MENU=m}
document.addEventListener('click',e=>{if(MENU&&!MENU.contains(e.target))closeMenu()},true);
let _navDepth=0,_ignoringPop=false;
function pushNav(layer){
 _navDepth++;
 try{history.pushState({tgLayer:layer,depth:_navDepth},'','#'+layer)}catch(e){}
}
function hasActiveLayer(){
 return !!(document.querySelector('.ov')||$('#lb').style.display==='flex'||$('#fv').style.display==='flex'||$('#drawer').classList.contains('on')||$('#emo').classList.contains('on')||S.info||S.sel.size||document.body.classList.contains('chat')||S.archived||S.folder!=='all'||S.q);
}
function ensureNavGuard(){
 if(hasActiveLayer()&&(!history.state||!history.state.tgLayer)){
  _navDepth++;
  try{history.pushState({tgLayer:'guard',depth:_navDepth},'','#app')}catch(e){}
 }
}
function modal(title,body,actions){const ov=h('div',{class:'ov',on:{mousedown(e){if(e.target===ov)close()}}});
 pushNav('modal');
 const close=()=>{if(ov.parentNode){ov.remove();ensureNavGuard()}};const mf=h('div',{class:'mf'});for(const a of actions||[]){mf.append(h('button',{class:a.c||'',text:a.t,on:{click(){a.fn?a.fn(close):close()}}}))}
 ov.append(h('div',{class:'mdl'},title?h('h3',{text:title}):null,h('div',{class:'mb'},body),(actions&&actions.length)?mf:null));document.body.append(ov);return close}
function confirmBox(msg,okText,fn,red){return modal('',h('div',{text:msg}),[{t:'Cancel'},{t:okText||'OK',c:red?'d':'p',fn(c){c();fn()}}])}
/* ---------- quick account bar & chat list ---------- */
function drawAccBar(){
 const bar=$('#accbar');
 if(S.accs.length<=1){bar.replaceChildren();return}
 bar.replaceChildren(...S.accs.map(a=>h('button',{class:'apill'+(a.id===S.acc?' on':''),title:a.phone||a.name,on:{click(){switchAcc(a.id)}}},
  h('span',{class:'st-d'+(a.state==='online'?' ok':'')}),
  h('span',{text:(a.premium?'⭐ ':'')+(a.label||a.name||a.id)})
 )),h('button',{class:'apill',title:'Add Telegram Account',on:{click:addAccount}},'＋'));
}
const FOLDERS=[['all','All'],['unread','Unread'],['archived','🗄 Archived'],['owned','👑 Admin'],['user','Personal'],['group','Groups'],['channel','Channels'],['bot','Bots'],['saved','🔖 Saved']];
function drawFolders(){
 const f=$('#folders');
 f.replaceChildren(...FOLDERS.map(([k,n])=>{
  const c=k==='unread'?S.dl.filter(d=>d.unread&&!d.muted).length:(k==='archived'?(S.adl||[]).length:0);
  const isOn=(k==='archived'&&S.archived)||(!S.archived&&S.folder===k);
  return h('button',{class:isOn?'on':'',on:{click(){
   if(k==='saved'){const u=cur().uid;if(u)openById(u,0);return}
   if(k==='archived'){S.archived=true;S.folder='archived';pushNav('archived');drawFolders();drawList();return}
   S.archived=false;S.folder=k;drawFolders();drawList();
  }}},n,c?h('span',{class:'n',style:k==='archived'?'background:var(--bdgm)':'',text:c}):null);
 }));
}
function lastText(d){if(S.drafts[d.id]&&d.id!==S.chat)return h('span',{class:'mut'},h('span',{style:'color:#ef4444;font-weight:600',text:'Draft: '}),S.drafts[d.id].slice(0,60));const t=S.typing[d.id];if(t&&t.exp>Date.now())return h('span',{class:'typ',text:(t.who&&d.type!=='user'?t.who+' ':'')+(t.action==='recording'?'is recording…':'typing…')});return h('span',{class:'mut',text:(d.out&&d.last?'You: ':'')+d.last})}
function drawList(){
 const q=S.q.toLowerCase();
 const rows=[];
 if(S.archived){
  rows.push(h('div',{style:'display:flex;align-items:center;justify-content:space-between;padding:8px 12px;background:var(--hov);border-radius:10px;margin:2px 4px 6px'},
   h('b',{style:'color:var(--acc);font-size:13.5px',text:`🗄 Archived Chats (${(S.adl||[]).length})`}),
   h('button',{style:'color:var(--acc);font-weight:700;font-size:13px',text:'← Back to Main Chats',on:{click(){S.archived=false;S.folder='all';drawFolders();drawList()}}})
  ));
 }else if(!q&&S.folder==='all'&&(S.adl||[]).length){
  // Official Telegram-style Pinned "Archived Chats" folder row at the top of the main chat list
  const archUnread=S.adl.reduce((a,x)=>a+(x.unread||0),0);
  const previewNames=S.adl.slice(0,4).map(x=>x.name).join(', ');
  rows.push(h('div',{class:'row',style:'background:rgba(148,163,184,.1);border:1px dashed var(--bd);margin-bottom:4px',on:{click(){S.archived=true;S.folder='archived';pushNav('archived');drawFolders();drawList()}}},
   h('div',{class:'av',style:'background:linear-gradient(135deg,#64748b,#475569);font-size:22px',text:'🗄'}),
   h('div',{class:'rm'},
    h('div',{class:'r1'},h('b',{text:'Archived Chats'}),h('span',{class:'t',text:S.adl.length+' chat'+(S.adl.length===1?'':'s')})),
    h('div',{class:'r2'},h('span',{class:'mut',text:previewNames||'Tap to view archived chats'}),archUnread?h('span',{class:'bd m',text:archUnread>999?'999+':archUnread}):null)
   )
  ));
 }
 const srcList=S.archived?(S.adl||[]):(q?[...S.dl,...(S.adl||[])]:S.dl);
 let L=srcList.filter(d=>(!q||d.name.toLowerCase().includes(q)||(d.username&&d.username.toLowerCase().includes(q))));
 if(!S.archived&&!q){
  if(S.folder==='unread')L=L.filter(d=>d.unread);
  else if(S.folder==='bot')L=L.filter(d=>d.bot);
  else if(S.folder==='owned')L=L.filter(d=>d.owner||d.admin);
  else if(S.folder!=='all')L=L.filter(d=>d.type===S.folder&&!d.bot);
 }
 for(const d of L){
  const isArch=!!d.archived||S.archived;
  const ub=d.unread?h('span',{class:'bd'+(d.muted?' m':''),text:d.unread>999?'999+':d.unread}):(d.mentions?h('span',{class:'bd',text:'@'}):(d.pinned?h('span',{class:'mut',text:'📌'}):null));
  const unarchBtn=S.archived?h('button',{class:'ib',style:'width:28px;height:28px;font-size:14px',title:'Unarchive chat',text:'📤',on:{click(e){e.stopPropagation();chatAct('archive',d,{archive:false})}}}):null;
  rows.push(h('div',{class:'row'+(d.id===S.chat?' on':''),on:{click(){openChat(d)},contextmenu(e){e.preventDefault();chatMenu(e.clientX,e.clientY,d)}}},
   avatar(d.id,d.name,0,d.online),
   h('div',{class:'rm'},
    h('div',{class:'r1'},h('b',{text:(isArch&&!S.archived?'🗄 ':'')+(d.verified?'✔ ':'')+(d.premium?'⭐ ':'')+(d.forum?'💬 ':'')+(d.owner?'👑 ':(d.admin?'🛡 ':''))+d.name}),h('span',{class:'t',text:(d.muted?'🔇 ':'')+listTime(d.ts)})),
    h('div',{class:'r2'},lastText(d),h('div',{style:'display:flex;align-items:center;gap:4px'},ub,unarchBtn))
   )
  ));
 }
 if(S.q.length>=2){if(S.gres&&S.gres.dialog)rows.push(h('div',{class:'sec',text:'Global Match'}),dialogRow(S.gres.dialog));
  if(S.gsearch.length){rows.push(h('div',{class:'sec',text:'Messages'}));for(const m of S.gsearch)rows.push(h('div',{class:'row',on:{click(){openById(m.chat,m.id)}}},avatar(m.chat,m.chat_name),h('div',{class:'rm'},h('div',{class:'r1'},h('b',{text:m.chat_name||'Chat'}),h('span',{class:'t',text:listTime(m.ts)})),h('div',{class:'r2'},h('span',{class:'mut',text:m.text||'📎 Media'})))))}}
 if(!rows.length)rows.push(h('div',{class:'empty',text:S.archived?'No archived chats yet':(S.dl.length?'No chats found':'Loading…')}));$('#list').replaceChildren(...rows);
 const n=S.dl.reduce((a,d)=>a+(d.muted?0:d.unread),0);document.title='TG Hub';if(navigator.setAppBadge){try{if(n)navigator.setAppBadge(n).catch(()=>{});else navigator.clearAppBadge().catch(()=>{})}catch(e){}}}
function dialogRow(d){return h('div',{class:'row',on:{click(){openChat(d)}}},avatar(d.id,d.name),h('div',{class:'rm'},h('div',{class:'r1'},h('b',{text:d.name})),h('div',{class:'r2'},h('span',{class:'mut',text:d.username?'@'+d.username:d.type}))))}
async function loadDialogs(){
 if(!S.acc)return;
 if(!S.dl.length){
  const cachedDl=LS.get('dl_'+S.acc,null);
  if(cachedDl&&cachedDl.dl&&cachedDl.dl.length){S.dl=cachedDl.dl;S.adl=cachedDl.adl||[];drawFolders();drawList()}
 }
 try{
  const r=await api('/app/api/dialogs?'+Q({acc:S.acc}));
  S.dl=r.dialogs||[];S.adl=r.archived||[];
  LS.set('dl_'+S.acc,{dl:S.dl.slice(0,80),adl:S.adl.slice(0,40)});
  drawFolders();drawList();
 }catch(e){
  if(!S.dl.length){
   $('#list').replaceChildren(h('div',{class:'empty'},'⏳ '+e.message,h('br'),h('button',{style:'color:var(--acc);font-weight:700;margin-top:8px',text:'🔄 Retry',on:{click:loadDialogs}})));
   if(e.status===409||/online|सुरू/.test(e.message))setTimeout(loadDialogs,2500);
  }
 }
}
async function loadState(){
 const s=await api('/app/api/state');
 S.accs=s.accounts;S.bw=s.bw;S.defApi=s.default_api;S.chunk=s.chunk;
 if(s.dl_cfg)S.dlCfg=s.dl_cfg;
 if(s.dl_tok){S.dlTok=s.dl_tok;LS.set('pwa_dt',s.dl_tok)}
 if(s.downloads){
  for(const[k,st]of Object.entries(s.downloads)){
   S.dls[k]=st;
   if(st.state==='downloading')watchDl(k);
  }
  updateDlCounter();
 }
 if(!S.acc||!S.accs.find(a=>a.id===S.acc))S.acc=(LS.get('acc')&&S.accs.find(a=>a.id===LS.get('acc')))?LS.get('acc'):(S.accs[0]||{}).id||null;
 drawAccBar();
 return s;
}
function chatMenu(x,y,d){const isArch=!!d.archived||S.archived;showMenu(x,y,[
 {ic:d.pinned?'📍':'📌',t:d.pinned?'Unpin':'Pin',fn:()=>chatAct('pin',d,{pin:!d.pinned})},
 {ic:d.muted?'🔔':'🔇',t:d.muted?'Unmute':'Mute',fn:()=>chatAct('mute',d,{mute:!d.muted})},
 {ic:'✓',t:'Mark as read',fn:()=>api('/app/api/read',A({chat:d.id,max_id:0})).then(()=>{d.unread=0;d.mentions=0;drawList()})},
 {ic:isArch?'📤':'🗄',t:isArch?'Unarchive Chat':'Archive Chat',fn:()=>chatAct('archive',d,{archive:!isArch})},
 {ic:'🗑',t:d.type==='user'?'Delete chat':'Leave',red:true,fn:()=>confirmBox((d.type==='user'?'Delete this chat?':'Leave this chat?'),'Confirm',()=>chatAct('delete',d,{}),true)}
])}
async function chatAct(k,d,extra){try{await api('/app/api/chat/'+k,A(Object.assign({chat:d.id},extra)));if(k==='delete'&&S.chat===d.id){S.chat=null;showEmpty()}await loadDialogs()}catch(e){toast(e.message)}}
/* ---------- open chat & forum topics bar ---------- */
function showEmpty(){$('#cbar').style.display='none';$('#tbar').style.display='none';$('#comp').style.display='none';$('#pin').style.display='none';$('#mi').replaceChildren(h('div',{class:'empty',text:'Select a chat to start messaging'}));document.body.classList.remove('chat')}
function closeChatView(fromPop){
 document.querySelectorAll('#mi video,#mi audio').forEach(stopMediaEl);
 if(S.chat&&$('#txt'))S.drafts[S.chat]=$('#txt').value;
 if(S.info){S.info=false;document.body.classList.remove('info-on')}
 if(S.sel.size){S.sel.clear();selUpdate()}
 $('#emo').classList.remove('on');
 document.body.classList.remove('chat');
 if(innerWidth<=820){S.chat=null;S.cd=null;drawList()}
 if(!fromPop&&location.hash){try{history.replaceState({tgRoot:1},'','/app/')}catch(e){}}
 ensureNavGuard();
}
async function openByQuery(q){try{const r=await api('/app/api/resolve',A({q}));openChat(r.dialog)}catch(e){toast(e.message)}}
async function openById(chat,mid){let d=S.dl.find(x=>x.id===chat)||(S.adl||[]).find(x=>x.id===chat)||{id:chat,name:'Chat',type:'user'};await openChat(d,mid)}
let _chatSeq=0;
async function openChat(d,jump){
 document.querySelectorAll('#mi video,#mi audio').forEach(stopMediaEl);
 if(S.chat&&$('#txt'))S.drafts[S.chat]=$('#txt').value;
 const wasChatOpen=document.body.classList.contains('chat');
 S.chat=d.id;S.cd=d;S.topic=0;S.topics=[];S.noMore=false;S.sel.clear();S.reply=null;S.edit=null;S.q='';$('#search').value='';S.gsearch=[];S.gres=null;
 document.body.classList.add('chat');
 if(!wasChatOpen||!history.state||!history.state.tgLayer)pushNav('chat');
 document.body.classList.add('chat');$('#cbar').style.display='flex';$('#tbar').style.display='none';$('#comp').style.display='block';$('#txt').value=S.drafts[d.id]||'';autosize();barUpdate();selUpdate();
 const cav=$('#cav');cav.replaceChildren(avatar(d.id,d.name,40,d.online));cav.onclick=$('#ctt').onclick=()=>toggleInfo();$('#ctt b').textContent=(d.verified?'✔ ':'')+(d.forum?'💬 ':'')+d.name;setStatus(d.type==='user'?(d.online?'online':'loading…'):'');drawList();
 // INSTANT 0ms UI switch: immediately show cached messages for this chat OR clear old chat bubbles right away!
 const ck=S.acc+':'+d.id+':0',cached=S.mcache[ck];
 if(!jump&&cached&&cached.msgs&&cached.msgs.length){
  S.msgs=cached.msgs.slice();S.readOut=cached.readOut||0;S.readIn=cached.readIn||d.read_in||0;S.pinned=cached.pinned||null;S.unreadFrom=0;
  drawPinned();drawMsgs('bottom');
 }else{
  S.msgs=[];S.pinned=null;drawPinned();
  $('#mi').replaceChildren(h('div',{class:'empty',text:'⏳ Loading messages…'}));
 }
 if(d.forum){loadChatTopics(d.id)}
 if(S.info)drawInfo();
 reloadChatMsgs(jump);
 api('/app/api/chatinfo?'+Q({acc:S.acc,chat:d.id})).then(r=>{if(S.chat!==d.id)return;S.ci=r.info;setStatus(r.info.status||'');if(r.info.forum&&!S.topics.length)loadChatTopics(d.id);if(S.info)drawInfo()}).catch(()=>{});
}
async function loadChatTopics(chatId){
 try{
  const r=await api('/app/api/chat/topics?'+Q({acc:S.acc,chat:chatId}));
  if(S.chat!==chatId)return;
  S.topics=r.topics||[];
  drawTopicBar();
 }catch(e){$('#tbar').style.display='none'}
}
function drawTopicBar(){
 const tb=$('#tbar');
 if(!S.topics||!S.topics.length){tb.style.display='none';return}
 tb.style.display='flex';
 tb.replaceChildren(
  h('button',{class:!S.topic?'on':'',text:'All Messages',on:{click(){S.topic=0;drawTopicBar();reloadChatMsgs()}}}),
  ...S.topics.map(tp=>h('button',{class:S.topic===tp.id?'on':'',text:(tp.closed?'🔒 ':'# ')+tp.title+(tp.unread?' ('+tp.unread+')':''),on:{click(){S.topic=tp.id;drawTopicBar();reloadChatMsgs()}}})),
  (S.cd&&(S.cd.owner||S.cd.admin))?h('button',{text:'＋ Topic',on:{click(){const inp=h('input',{placeholder:'Topic title'});modal('New Forum Topic',inp,[{t:'Cancel'},{t:'Create',c:'p',async fn(c){if(!inp.value.trim())return;try{await api('/app/api/chat/topic/create',A({chat:S.chat,title:inp.value.trim()}));c();loadChatTopics(S.chat)}catch(e){toast(e.message)}}}]);inp.focus()}}}):null
 );
}
async function reloadChatMsgs(jump){
 const mySeq=++_chatSeq,chatId=S.chat,topId=S.topic||0,ck=S.acc+':'+chatId+':'+topId;
 if(topId&&!jump)$('#mi').replaceChildren(h('div',{class:'empty',text:'⏳ Loading topic messages…'}));
 try{
  if(jump){
   const r=await api('/app/api/around?'+Q({acc:S.acc,chat:chatId,id:jump}));
   if(mySeq!==_chatSeq||S.chat!==chatId)return;
   S.msgs=r.messages;
  }else{
   const r=await api('/app/api/messages?'+Q({acc:S.acc,chat:chatId,limit:40,topic:topId}));
   if(mySeq!==_chatSeq||S.chat!==chatId)return;
   S.msgs=r.messages;S.readOut=r.read_out||0;S.readIn=r.read_in||(S.cd&&S.cd.read_in)||0;S.pinned=r.pinned;
   const um=S.msgs.find(m=>!m.out&&m.id>S.readIn);S.unreadFrom=(S.cd&&S.cd.unread&&um)?um.id:0;
   S.mcache[ck]={msgs:S.msgs.slice(-40),readOut:S.readOut,readIn:S.readIn,pinned:S.pinned};
  }
 }catch(e){if(mySeq===_chatSeq)toast(e.message);return}
 drawPinned();drawMsgs(jump?'jump':'unread',jump);markRead();
}
function setStatus(t){const s=$('#cbar .st');s.textContent=t||'';s.classList.toggle('on',t==='online'||/typing|recording/.test(t||''))}
function drawPinned(){const p=$('#pin');if(!S.pinned){p.style.display='none';return}p.style.display='flex';$('#pint').textContent=S.pinned.text;p.onclick=(e)=>{if(e.target.id==='pinall')return;jumpTo(S.pinned.id)}}
$('#pinall').onclick=e=>{e.stopPropagation();openPinnedList()};
async function openPinnedList(){
 if(!S.chat)return;
 const body=h('div',{style:'max-height:55vh;overflow:auto'},h('div',{class:'empty',text:'Loading…'}));
 const close=modal('📌 All Pinned Messages',body,[{t:'Close'}]);
 try{
  const ms=(await api('/app/api/pinned?'+Q({acc:S.acc,chat:S.chat}))).messages;
  if(!ms.length){body.replaceChildren(h('div',{class:'empty',text:'No pinned messages'}));return}
  body.replaceChildren(...ms.map(m=>h('div',{class:'row',on:{click(){close();jumpTo(m.id)}}},
   h('div',{class:'rm'},h('div',{class:'r1'},h('b',{text:m.out?'You':m.from||'Message'}),h('span',{class:'t',text:dayLabel(m.ts)+' '+tm(m.ts)})),h('div',{class:'mut',text:m.text||'📎 Media'})),
   h('button',{class:'ib',title:'Unpin',text:'📍',on:{async click(ev){ev.stopPropagation();try{await api('/app/api/pin',A({chat:S.chat,id:m.id,unpin:true}));toast('Unpinned');close();openPinnedList()}catch(er){toast(er.message)}}}})
  )));
 }catch(e){body.replaceChildren(h('div',{class:'empty',text:e.message}))}
}
async function jumpTo(id){const el=document.querySelector(`[data-id="${id}"]`);if(el){flash(el);return}try{S.msgs=(await api('/app/api/around?'+Q({acc:S.acc,chat:S.chat,id}))).messages;S.noMore=false;drawMsgs('jump',id)}catch(e){toast(e.message)}}
function flash(el){if(el.scrollIntoView)el.scrollIntoView({block:'center'});el.classList.remove('hl');void el.offsetWidth;el.classList.add('hl')}
function markRead(){if(S.msgs.length&&S.cd&&(S.cd.unread||0)>=0){const mx=S.msgs[S.msgs.length-1].id;api('/app/api/read',A({chat:S.chat,max_id:mx})).catch(()=>{});const d=S.dl.find(x=>x.id===S.chat);if(d&&d.unread){d.unread=0;d.mentions=0;drawList()}}}

/* ---------- message rendering + auto server caching on view/download ---------- */

let _wakeLock=null;
async function holdWakeLock(on){
 if(!('wakeLock' in navigator))return;
 try{if(on&&!_wakeLock)_wakeLock=await navigator.wakeLock.request('screen');else if(!on&&_wakeLock){await _wakeLock.release();_wakeLock=null}}catch(e){}
}
function stopMediaEl(el){
 if(!el)return;
 try{el.pause();el.removeAttribute('src');el.load()}catch(e){}
 if('mediaSession' in navigator){try{navigator.mediaSession.setActionHandler('play',null);navigator.mediaSession.setActionHandler('pause',null)}catch(e){}}
 holdWakeLock(false);
}
function pauseAllMedia(exceptEl){
 document.querySelectorAll('video,audio').forEach(el=>{if(el!==exceptEl){try{el.pause()}catch(e){}}});
}
function bindMediaSession(el,m){
 el.addEventListener('play',()=>{
  pauseAllMedia(el);
  holdWakeLock(true);
  if('mediaSession' in navigator&&window.MediaMetadata){
   const x=m.media||{};
   navigator.mediaSession.metadata=new MediaMetadata({
    title:x.name||(m.text?m.text.slice(0,60):'Telegram Media'),
    artist:(S.cd&&S.cd.name)||m.from||'Telegram Hub',
    artwork:[{src:'/app/icon-192.png',sizes:'192x192',type:'image/png'}]
   });
   navigator.mediaSession.setActionHandler('play',()=>{if(document.body.contains(el))el.play();else stopMediaEl(el)});
   navigator.mediaSession.setActionHandler('pause',()=>el.pause());
  }
 });
 el.addEventListener('pause',()=>holdWakeLock(false));
 el.addEventListener('ended',()=>holdWakeLock(false));
}

function mediaNode(m){const x=m.media;if(!x)return null;const b={acc:S.acc,chat:S.chat,id:m.id},file=fileUrl(b,false),th='/app/api/thumb?'+Q(b);
 if(x.kind==='photo'){const w=h('div',{class:'ph',on:{click(e){if(S.sel.size||e.shiftKey)return;lightbox(m.id)}}});if(x.w&&x.h)w.style.aspectRatio=x.w+'/'+x.h;
  if(S.set.previews){const i=h('img',{alt:'',loading:'lazy',src:th,on:{error(){w.replaceChildren(h('div',{style:'padding:30px;text-align:center',text:'📷 Photo · '+sz(x.size)+' · tap'}))}}});w.append(i)}else w.append(h('div',{style:'padding:30px;text-align:center',text:'📷 Photo · '+sz(x.size)+' · tap to load'}));return w}
 if(x.kind==='sticker'){return x.mime==='image/webp'?h('img',{class:'stk',alt:x.emoji||'',loading:'lazy',src:th,on:{error(e){e.target.replaceWith(h('div',{class:'stk-e',text:x.emoji||'🏷'}))}}}):h('div',{class:'stk-e',text:x.emoji||'🏷'})}
 if(x.kind==='video'||x.kind==='gif'||x.kind==='videonote'){
  const k=dlKey(m),st=S.dls[k]||{state:'none'},w=h('div',{class:'vid'});
  if(x.w&&x.h)w.style.aspectRatio=x.w+'/'+x.h;
  if(x.kind==='videonote')w.style.cssText+=';border-radius:50%;width:220px;height:220px';
  const start=async(e)=>{
   if(e)e.stopPropagation();
   pauseAllMedia(null);
   const tgt=document.querySelector('.vid[data-dl="'+k+'"]')||w;
   tgt.classList.add('playing');
   tgt.dataset.activeVideo='1';
   if(x.kind!=='videonote')tgt.style.aspectRatio='auto';
   const v=h('video',{src:file,playsinline:true,preload:'auto',controls:x.kind!=='gif',autoplay:true});
   bindMediaSession(v,m);
   if(x.kind==='gif'){v.loop=true;v.muted=true}
   const codecWarn=h('div',{style:'display:none;background:#7c2d12;color:#ffedd5;padding:6px 8px;font-size:12px;justify-content:space-between;align-items:center;gap:6px'},
    h('span',{text:'⚠ या व्हिडिओचा Codec (HEVC/MKV) ब्राउझरमध्ये दिसत नाही (फक्त आवाज येतोय).'}),
    h('button',{style:'background:#ea580c;color:#fff;padding:3px 8px;border-radius:6px;font-weight:700;white-space:nowrap',text:'🎬 VLC / Theatre मध्ये उघडा',on:{click(ev){ev.stopPropagation();v.pause();shareLinkModal(m)}}})
   );
   v.addEventListener('loadeddata',()=>{if(v.videoWidth===0&&v.duration>0)codecWarn.style.display='flex'});
   const spdBtn=h('button',{text:'1x',title:'Playback speed',on:{click(ev){ev.stopPropagation();const rates=[1,1.25,1.5,1.75,2,2.5,0.5];const nx=rates[(rates.indexOf(v.playbackRate)+1)%rates.length];v.playbackRate=nx;spdBtn.textContent=nx+'x'}}});
   const thBtn=h('button',{text:'⛶',title:'Open in Big Theatre Player',on:{click(ev){ev.stopPropagation();stopMediaEl(v);openFile(m)}}});
   const snapBtn=h('button',{text:'📸',title:'Capture HD Screenshot of Video Frame',on:{click(ev){ev.stopPropagation();snapVideo(v,x.name||('video_'+m.id))}}});
   const pipBtn=h('button',{text:'⧉ PiP',title:'Picture in Picture',on:{click(ev){ev.stopPropagation();if(v.requestPictureInPicture)v.requestPictureInPicture().catch(()=>{})}}});
   const shBtn=h('button',{text:'🔗',title:'VLC / Public Share Link',on:{click(ev){ev.stopPropagation();shareLinkModal(m)}}});
   const dlBtn=h('button',{text:'⬇ Save',title:'Fast Download from Server',on:{click(ev){ev.stopPropagation();downloadMsg(m)}}});
   const bar=x.kind==='gif'?null:h('div',{class:'vbar'},h('span',{class:'dlp',text:dlBadgeText(S.dls[k]||{state:'downloading',size:x.size},x)}),h('div',{style:'display:flex;gap:4px'},spdBtn,thBtn,snapBtn,pipBtn,shBtn,dlBtn));
   tgt.replaceChildren(v,codecWarn,bar);tgt.onclick=null;
   const p=v.play&&v.play();if(p&&p.catch)p.catch(()=>{});
   // Start multi-threaded server caching after video element is mounted
   serverDownload(m,false).catch(()=>{});
  };
  if(S.set.previews)w.append(h('img',{alt:'',loading:'lazy',src:th,on:{error(e){e.target.remove()}}}));else w.append(h('div',{style:'height:140px'}));
  const stTxt=st.state==='cached'?'⚡ Cached · '+sz(x.size):(st.state==='downloading'?'⚡ '+pctOf(st)+'% · '+spd(st.speed):(x.kind==='gif'?'GIF':dur(x.dur)+' · '+sz(x.size)));
  w.append(h('div',{class:'play'},h('i',{text:'▶'})),h('span',{class:'chip',text:stTxt}),h('span',{class:'chip dlp',style:'top:auto;bottom:8px;display:'+(st.state==='downloading'||st.state==='cached'?'block':'none'),text:dlBadgeText(st,x)}));
  w.setAttribute('data-dl',k);w.onclick=start;return w;
 }
 if(x.kind==='voice'||x.kind==='audio'){
  return h('div',{},h('div',{class:'mut',style:'font-size:13px;display:flex;justify-content:space-between;gap:8px'},
   h('span',{text:(x.kind==='voice'?'🎤 Voice message ':'🎵 '+(x.name||'Audio')+' ')+dur(x.dur)+' · '+sz(x.size)}),
   h('button',{style:'color:var(--acc);font-weight:600;font-size:12px',text:'⬇',title:'Download audio',on:{click(e){e.stopPropagation();downloadMsg(m)}}})
  ),h('audio',{controls:true,preload:'none',src:file,on:{play(){serverDownload(m,false).catch(()=>{})}}}))
 }
 return fileCard(m,x,file)}

function dlBadgeText(st,x){
 if(!st||st.state==='none')return '⚡ Multi-thread ready';
 if(st.state==='downloading')return `⚡ ${st.threads||S.dlCfg.threads}T · ${pctOf(st)}%`+(st.speed?` · ${spd(st.speed)}`:'');
 if(st.state==='cached')return `✔ Server Cached`+(st.expires_in?` · ⏱ ${fmtTtl(st.expires_in)}`:'');
 if(st.state==='error')return `⚠ ${st.err||'failed'}`;
 return '';
}
function dlText(st,x){
 if(st.state==='downloading')return `⚡ Server (${st.threads||S.dlCfg.threads}T): ${pctOf(st)}% · ${sz(st.done||0)} / ${sz(st.size||x.size)}`+(st.speed?` · ${spd(st.speed)}`:'');
 if(st.state==='cached')return `${sz(x.size)} · ⚡ Ready on server`+(st.expires_in?` (clears in ${fmtTtl(st.expires_in)})`:'')+' — tap ⬇';
 if(st.state==='error')return `⚠ ${st.err||'failed'} — tap ⬇ to retry`;
 return `${sz(x.size)} · ⚡ Multi-thread fast download`;
}
function fileCard(m,x,file){
 const k=dlKey(m),st=S.dls[k]||{state:'none'};
 const icon=h('button',{class:'fi',title:st.state==='downloading'?'Cancel server download':'Fast Download to Browser',text:st.state==='cached'?'✔':(st.state==='downloading'?'✕':'⬇'),on:{click(e){e.stopPropagation();if(st.state==='downloading')cancelDl(m);else downloadMsg(m)}}});
 return h('div',{class:'file',title:'Click to view / open on server','data-dl':k,on:{click(){openFile(m)}}},
  icon,
  h('div',{style:'min-width:0;flex:1'},
   h('b',{text:x.name||'File'}),
   h('span',{class:'mut dlp',style:'font-size:12.5px;display:block',text:dlText(st,x)}),
   h('div',{class:'dlb',style:'height:4px;background:var(--bd);border-radius:2px;margin-top:4px;overflow:hidden;display:'+(st.state==='downloading'?'block':'none')},
    h('i',{style:'display:block;height:100%;background:var(--acc);transition:width .25s;width:'+pctOf(st)+'%'})
   )
  )
 );
}
function setDl(k,st){
 S.dls[k]=st;
 updateDlCounter();
 const[a,c]=k.split(':');if(a!==S.acc||String(c)!==String(S.chat))return;
 updDl(k);
}
function updateDlCounter(){
 const active=Object.entries(S.dls).filter(([_,x])=>x&&x.state==='downloading');
 const n=active.length;
 const badge=$('#dlcnt');
 if(badge){badge.style.display=n?'flex':'none';badge.textContent=n}
 const bar=$('#dlbar');
 if(!bar)return;
 if(!n){bar.replaceChildren();return}
 bar.replaceChildren(...active.slice(0,3).map(([k,st])=>{
  const[acc,chat,id]=k.split(':'),pct=pctOf(st);
  return h('div',{style:'display:flex;flex-direction:column;gap:3px'},
   h('div',{style:'display:flex;justify-content:space-between;align-items:center;gap:6px'},
    h('span',{style:'font-weight:600;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;flex:1;cursor:pointer',on:{click:openDlManager},text:`⚡ ${st.name||('File #'+id)} · ${pct}%`+(st.speed?` (${spd(st.speed)})`:'')}),
    h('button',{style:'color:#ef4444;font-weight:700;padding:0 4px',title:'Cancel download',text:'✕',on:{async click(e){e.stopPropagation();try{await api('/app/api/dl/cancel',{acc,chat:+chat,id:+id});setDl(k,{state:'none'})}catch(er){toast(er.message)}}}})
   ),
   h('div',{style:'height:3px;background:var(--bd);border-radius:2px;overflow:hidden'},h('i',{style:'display:block;height:100%;background:var(--acc);width:'+pct+'%'}))
  );
 }));
}
function updDl(k){
 const st=S.dls[k];if(!st)return;
 document.querySelectorAll('[data-dl="'+k+'"]').forEach(el=>{
  const p=el.querySelector('.dlp'),b=el.querySelector('.dlb i'),fi=el.querySelector('.fi'),pct=pctOf(st),vid=el.classList.contains('vid');
  if(p){
   p.textContent=vid?dlBadgeText(st,{size:st.size}):dlText(st,{size:st.size});
   if(vid)p.style.display='block';
  }
  if(fi){
   fi.textContent=st.state==='cached'?'✔':(st.state==='downloading'?'✕':'⬇');
   fi.title=st.state==='downloading'?'Cancel server download':'Fast Download to Browser';
  }
  if(b){b.parentNode.style.display=st.state==='downloading'?'block':'none';b.style.width=pct+'%'}
 });
 const fvst=$('#fvst');
 if(fvst&&$('#fv').dataset.dl===k)fvst.textContent=dlBadgeText(st,{size:st.size});
}
async function serverDownload(m,wait){
 const k=dlKey(m),cur0=S.dls[k];
 if(cur0&&cur0.state==='cached')return;
 const r=await api('/app/api/dl/start',A({chat:S.chat,id:m.id}),{keepalive:true});
 setDl(k,{state:r.state,done:r.done||0,size:r.size||(m.media&&m.media.size)||0,speed:r.speed||0,threads:r.threads||S.dlCfg.threads,expires_in:r.expires_in||S.dlCfg.ttl,name:(m.media&&m.media.name)||('File #'+m.id)});
 if(r.state==='downloading')watchDl(k);
 if(!wait)return;
 while(S.dls[k]&&S.dls[k].state==='downloading')await sleep(350);
 if(!S.dls[k]||S.dls[k].state!=='cached')throw new Error((S.dls[k]&&S.dls[k].err)||'Download failed');
}
const WATCH={};
async function watchDl(k){
 if(WATCH[k])return;WATCH[k]=1;
 const[acc,chat,id]=k.split(':');
 try{
  while(S.dls[k]&&S.dls[k].state==='downloading'){
   await sleep(1800);
   const r=await api('/app/api/dl/status?'+Q({acc,chat,ids:id}));
   const it=r.items[id];
   if(it&&S.dls[k])setDl(k,it.state==='none'?{state:'none'}:Object.assign({},it,{size:it.size||S.dls[k].size}));
  }
 }catch(e){}
 WATCH[k]=0;
}
async function cancelDl(m){try{await api('/app/api/dl/cancel',A({chat:S.chat,id:m.id}));setDl(dlKey(m),{state:'none'})}catch(e){toast(e.message)}}
let dlBusy=false;
async function refreshDlStatus(){
 if(dlBusy||!S.chat)return;
 const ids=S.msgs.filter(m=>m.media&&['file','video','audio','other'].includes(m.media.kind)&&!S.dls[dlKey(m)]).map(m=>m.id);
 if(!ids.length)return;dlBusy=true;
 try{
  const r=await api('/app/api/dl/status?'+Q({acc:S.acc,chat:S.chat,ids:ids.slice(0,60).join(',')}));
  let ch=false;
  for(const id of ids.slice(0,60)){
   const it=r.items[id]||{state:'none'},k=S.acc+':'+S.chat+':'+id;
   S.dls[k]=it;
   if(it.state==='cached'||it.state==='downloading')updDl(k);
   if(it.state==='downloading')watchDl(k);
  }
  updateDlCounter();
 }catch(e){}
 dlBusy=false;
}

function pollNode(m){const p=m.poll,voted=p.a.some(o=>o.me)||p.closed,tot=p.a.reduce((s,o)=>s+o.n,0)||1;const sel=new Set();
 const box=h('div',{class:'poll'},h('b',{class:'q',text:(p.quiz?'🎯 ':'📊 ')+p.q}),h('div',{class:'mut',style:'font-size:11.5px;margin-bottom:4px',text:(p.public?'Public Poll':'Anonymous Poll')+(p.quiz?' · Quiz':'')+(p.multi?' · Multiple answers':'')}));
 p.a.forEach(o=>{const row=h('div',{class:'po',on:{async click(){if(p.closed||voted)return;if(p.multi){sel.has(o.i)?sel.delete(o.i):sel.add(o.i);row.style.outline=sel.has(o.i)?'2px solid var(--acc)':'';return}await vote([o.i])}}},
  voted?h('i',{style:`width:${Math.round(o.n*100/tot)}%`}):null,h('span',{},h('span',{text:(o.me?(o.ok===false?'❌ ':'✔ '):(o.ok?'✅ ':''))+o.t}),voted?h('span',{text:Math.round(o.n*100/tot)+'% ('+o.n+')'}):null));box.append(row)});
 async function vote(ix){try{const r=await api('/app/api/vote',A({chat:S.chat,id:m.id,options:ix}));replaceMsg(r.message)}catch(e){toast(e.message)}}
 if(p.multi&&!voted&&!p.closed)box.append(h('button',{style:'color:var(--acc);font-weight:700;padding:4px 0',text:'Submit Vote',on:{click(){if(sel.size)vote([...sel])}}}));
 if(voted&&!p.closed&&!p.quiz)box.append(h('button',{class:'mut',style:'font-size:12px;margin-right:8px',text:'Retract vote',on:{click(){vote([])}}}));
 if(p.solution&&voted)box.append(h('div',{class:'tr-box',text:'💡 '+p.solution}));
 box.append(h('div',{class:'mut',style:'font-size:12px;margin-top:2px',text:(p.total||0)+' votes'+(p.closed?' · closed':'')}));return box}

function bodyNode(m){const n=[];
 if(m.fwd)n.push(h('div',{class:'fw',text:'↪ Forwarded from '+m.fwd}));if(m.via)n.push(h('div',{class:'mut',style:'font-size:12px',text:'via @'+m.via}));
 if(m.reply){const r=S.msgs.find(x=>x.id===m.reply);n.push(h('div',{class:'rp',on:{click(e){e.stopPropagation();jumpTo(m.reply)}}},h('b',{text:r?(r.out?'You':r.from||'Message'):'Reply'}),h('span',{text:r?(r.text||'📎 Media'):'Tap to jump to original message'})))}
 const mn=mediaNode(m);if(mn)n.push(mn);
 if(m.poll)n.push(pollNode(m));
 if(m.geo)n.push(h('div',{class:'card'},h('b',{text:'📍 '+(m.geo.title||'Location')}),h('div',{class:'ds'},h('a',{href:`https://www.openstreetmap.org/?mlat=${m.geo.lat}&mlon=${m.geo.lon}#map=16/${m.geo.lat}/${m.geo.lon}`,target:'_blank',rel:'noopener noreferrer',text:m.geo.lat.toFixed(5)+', '+m.geo.lon.toFixed(5)}))));
 if(m.contact)n.push(h('div',{class:'card'},h('b',{text:'👤 '+m.contact.name}),h('div',{class:'ds',text:m.contact.phone})));
 if(m.dice)n.push(h('div',{class:'stk-e',text:m.dice.e+' '+m.dice.v}));
 if(m.text)n.push(h('div',{class:'mt'},m.ents&&m.ents.length?richText(m.text,m.ents):plainLinks(m.text)));
 if(m._tr)n.push(h('div',{class:'tr-box'},h('b',{style:'color:var(--acc);font-size:11.5px;display:block',text:'🌐 Translated'}),m._tr));
 if(m.web){const w=m.web;n.push(h('div',{class:'card',style:'cursor:pointer',on:{click(){if(SAFE.test(w.url))window.open(w.url,'_blank','noopener')}}},h('b',{text:w.site||w.url}),h('div',{style:'font-weight:600',text:w.title}),h('div',{class:'ds mut',text:w.desc}),
  w.photo&&S.set.previews?h('img',{alt:'',loading:'lazy',style:'max-width:100%;border-radius:6px;margin-top:4px',src:'/app/api/thumb?'+Q({acc:S.acc,chat:S.chat,id:m.id}),on:{error(e){e.target.remove()}}}):null))}
 if(m.react)n.push(h('div',{class:'rx'},m.react.map(r=>h('button',{class:r.me?'me':'',text:r.e+' '+r.n,on:{click(e){e.stopPropagation();react(m,r.me?'':r.e)}}}))));
 if(m.btn)n.push(h('div',{class:'bt'},m.btn.map((row,i)=>h('div',{},row.map((b,j)=>h('button',{text:(b.u?'🔗 ':'')+b.t,on:{async click(e){e.stopPropagation();if(b.u){if(SAFE.test(b.u))window.open(b.u,'_blank','noopener');return}try{const r=await api('/app/api/click',A({chat:S.chat,id:m.id,i,j}));if(r.text)(r.alert?alert:toast)(r.text);if(r.url&&SAFE.test(r.url))window.open(r.url,'_blank','noopener')}catch(er){toast(er.message)}}}}))))));
 return n}

function ticks(m){if(!m.out)return null;return h('span',{class:'tk',text:m.id<=S.readOut?'✓✓':'✓'})}
function bubbleRow(m,first,last,showName,album){const out=m.out;const row=h('div',{class:'mr'+(out?' out':''),'data-id':m.id});
 if(!out&&S.cd&&S.cd.type!=='user'){row.append(last?avatar(m.from_id,m.from,34):h('div',{class:'sp'}))}
 const nobg=m.media&&(m.media.kind==='sticker'||m.media.kind==='videonote')&&!m.text;
 const b=h('div',{class:'m'+(last?' tl':'')+(nobg?' nobg':'')+(S.sel.has(m.id)?' sel':''),title:new Date(m.ts*1e3).toLocaleString()});
 // Hover quick-action bar
 const qa=h('div',{class:'qact'},
  h('button',{text:'❤️',title:'React ❤️',on:{click(e){e.stopPropagation();react(m,'❤️')}}}),
  h('button',{text:'👍',title:'React 👍',on:{click(e){e.stopPropagation();react(m,'👍')}}}),
  h('button',{text:'↩',title:'Reply',on:{click(e){e.stopPropagation();setReply(m)}}}),
  m.media&&m.media.kind!=='sticker'?h('button',{text:'⬇',title:'Fast Download',on:{click(e){e.stopPropagation();downloadMsg(m)}}}):null,
  h('button',{text:'⋮',title:'Menu',on:{click(e){e.stopPropagation();msgMenu(e.clientX,e.clientY,m)}}})
 );
 b.append(qa);
 if(showName&&!out&&S.cd&&S.cd.type!=='user')b.append(h('div',{class:'mn',style:'color:'+colorOf(m.from_id),text:m.from||'User',on:{click(e){e.stopPropagation();if(m.from_id)openById(m.from_id,0)}}}));
 if(album&&album.length>1){const g=h('div',{style:`display:grid;grid-template-columns:repeat(${album.length>3?2:Math.min(album.length,2)},1fr);gap:2px;margin:-2px -4px 3px`});album.forEach(a=>g.append(mediaNode(a)));b.append(g);const tx=album.find(a=>a.text);if(tx)b.append(...bodyNode(Object.assign({},tx,{media:null,reply:null,fwd:'',via:'',react:tx.react,btn:null})))}
 else b.append(...bodyNode(m));
 b.append(h('span',{class:'meta'},m._deleted?h('b',{style:'color:#ef4444',text:'👻 deleted'}):null,m.pinned?'📌':null,m.views?'👁 '+m.views:null,m.edited?'edited':null,tm(m.ts),ticks(m)));b.append(h('div',{style:'clear:both'}));
 b.addEventListener('contextmenu',e=>{e.preventDefault();msgMenu(e.clientX,e.clientY,m)});let lp;let tx0=0,ty0=0,swiped=false;
 b.addEventListener('touchstart',e=>{const t=e.touches[0];tx0=t.clientX;ty0=t.clientY;swiped=false;lp=setTimeout(()=>msgMenu(t.clientX,t.clientY,m),520)},{passive:true});
 b.addEventListener('touchmove',e=>{clearTimeout(lp);const t=e.touches[0],dx=t.clientX-tx0,dy=Math.abs(t.clientY-ty0);if(dy<28&&Math.abs(dx)>22){b.style.transform=`translateX(${Math.max(-55,Math.min(55,dx*0.5))}px)`;swiped=Math.abs(dx)>48}},{passive:true});
 b.addEventListener('touchend',()=>{clearTimeout(lp);b.style.transform='';if(swiped){if(navigator.vibrate)try{navigator.vibrate(15)}catch(e){}setReply(m);swiped=false}},{passive:true});
 b.addEventListener('click',e=>{if(S.sel.size||e.shiftKey)toggleSel(m.id);else if(e.detail===2)setReply(m)});row.append(b);return row}

function drawMsgs(mode,jumpId){const box=$('#msgs'),mi=$('#mi'),keep=box.scrollHeight-box.scrollTop;const kids=[];let prev=null;const L=S.msgs;
 for(let i=0;i<L.length;i++){const m=L[i];
  if(!prev||dayKey(prev.ts)!==dayKey(m.ts))kids.push(h('div',{class:'day',text:dayLabel(m.ts)}));
  if(S.unreadFrom&&m.id===S.unreadFrom)kids.push(h('div',{class:'unr',text:'Unread messages'}));
  if(m.service){kids.push(h('div',{class:'svc','data-id':m.id,text:(m.from?m.from+' ':'')+m.text}));prev=m;continue}
  let album=null,end=i;if(m.grp&&m.media&&(m.media.kind==='photo'||m.media.kind==='video')){album=[m];while(L[end+1]&&L[end+1].grp===m.grp){album.push(L[++end])}if(album.length<2)album=null}
  const nxt=L[end+1],sameP=prev&&!prev.service&&prev.from_id===m.from_id&&prev.out===m.out&&m.ts-prev.ts<300&&dayKey(prev.ts)===dayKey(m.ts);
  const last=!nxt||nxt.service||nxt.from_id!==m.from_id||nxt.out!==m.out||nxt.ts-m.ts>=300||dayKey(nxt.ts)!==dayKey(m.ts);
  kids.push(bubbleRow(m,!sameP,last,!sameP,album));prev=L[end];i=end}
 if(!kids.length)kids.push(h('div',{class:'empty',text:'No messages here yet'}));
 const activeVids=new Map();
 mi.querySelectorAll('.vid[data-active-video="1"]').forEach(vw=>{
  const dk=vw.getAttribute('data-dl'),v=vw.querySelector('video');
  if(dk&&v&&!v.ended)activeVids.set(dk,{node:vw,wasPlaying:!v.paused});
 });
 const activeAuds=new Map();
 mi.querySelectorAll('audio').forEach(au=>{
  const row=au.closest('[data-id]');
  if(row&&!au.paused&&!au.ended)activeAuds.set(row.getAttribute('data-id'),au);
 });
 mi.querySelectorAll('video,audio').forEach(el=>{
  const vw=el.closest('.vid[data-active-video="1"]');
  if(vw&&activeVids.has(vw.getAttribute('data-dl')))return;
  const row=el.closest('[data-id]');
  if(el.tagName==='AUDIO'&&row&&activeAuds.has(row.getAttribute('data-id')))return;
  stopMediaEl(el);
 });
 mi.replaceChildren(...kids);
 for(const[dk,info]of activeVids){
  try{
   const sl=mi.querySelector('.vid[data-dl="'+dk+'"]');
   if(sl){sl.replaceWith(info.node);if(info.wasPlaying){const v=info.node.querySelector('video');if(v&&v.paused){const p=v.play();if(p&&p.catch)p.catch(()=>{})}}}
   else stopMediaEl(info.node.querySelector('video'));
  }catch(er){}
 }
 for(const[id,au]of activeAuds){
  try{
   const row=mi.querySelector('[data-id="'+id+'"]'),sl=row&&row.querySelector('audio');
   if(sl){sl.replaceWith(au);if(au.paused){const p=au.play();if(p&&p.catch)p.catch(()=>{})}}
   else stopMediaEl(au);
  }catch(er){}
 }
 if(mode==='unread'){const u=mi.querySelector('.unr');if(u&&u.scrollIntoView)u.scrollIntoView({block:'start'});else box.scrollTop=box.scrollHeight}
 else if(mode==='bottom')box.scrollTop=box.scrollHeight;else if(mode==='jump'){const el=mi.querySelector(`[data-id="${jumpId}"]`);if(el)flash(el)}else box.scrollTop=box.scrollHeight-keep;onScroll();setTimeout(refreshDlStatus,0)}
function replaceMsg(m){const i=S.msgs.findIndex(x=>x.id===m.id);if(i>=0)S.msgs[i]=m;drawMsgs('keep')}
async function loadOlder(){if(S.loadingOld||S.noMore||!S.msgs.length)return;S.loadingOld=true;try{const r=await api('/app/api/messages?'+Q({acc:S.acc,chat:S.chat,before:S.msgs[0].id,limit:40,topic:S.topic||0}));if(!r.messages.length)S.noMore=true;else{S.msgs=r.messages.concat(S.msgs);drawMsgs('keep')}}catch(e){}S.loadingOld=false}
function onScroll(){const b=$('#msgs'),nearEnd=b.scrollHeight-b.scrollTop-b.clientHeight<120;$('#dn').style.display=nearEnd?'none':'block';if(b.scrollTop<90)loadOlder();if(nearEnd)S.newBelow=0;const bd=$('#dn .bd');if(bd)bd.remove();if(!nearEnd&&S.newBelow)$('#dn').append(h('span',{class:'bd',text:S.newBelow}))}
$('#msgs').addEventListener('scroll',onScroll);$('#dn').onclick=()=>{const b=$('#msgs');b.scrollTop=b.scrollHeight};

/* ---------- message actions ---------- */
async function react(m,emoji){try{const r=await api('/app/api/react',A({chat:S.chat,id:m.id,emoji}));if(r.message)replaceMsg(r.message)}catch(e){toast(e.message)}}
function fullReactPicker(m){
 const g=h('div',{style:'display:grid;grid-template-columns:repeat(auto-fill,minmax(38px,1fr));gap:4px'});
 const c=modal('Choose Reaction',g,[{t:'Close'}]);
 EMOJI.forEach(e=>g.append(h('button',{style:'font-size:24px;padding:4px;border-radius:6px',text:e,on:{click(){c();react(m,e)}}})));
}
async function translateMsg(m){
 const lang=prompt('Translate message to language code (en = English, mr = Marathi, hi = Hindi):','en');
 if(!lang)return;
 try{
  const r=await api('/app/api/translate',A({chat:S.chat,id:m.id,to:lang.trim()}));
  m._tr=r.text;drawMsgs('keep');
 }catch(e){toast(e.message)}
}
function copyMsgLink(m){
 const d=S.cd||{};
 if(d.username){copyText(`https://t.me/${d.username}/${m.id}`);return}
 const cid=String(S.chat).replace(/^-100/,'').replace(/^-/,'');
 copyText(`https://t.me/c/${cid}/${m.id}`);
}
function msgMenu(x,y,m){
 closeMenu();
 const top=h('div',{class:'rb'},
  ...REACT.map(e=>h('button',{text:e,on:{click(){closeMenu();react(m,e)}}})),
  h('button',{text:'＋',title:'More reactions',on:{click(){closeMenu();fullReactPicker(m)}}})
 );
 showMenu(x,y,[
  {ic:'↩',t:'Reply',fn:()=>setReply(m)},
  m.media&&m.media.kind!=='sticker'?{ic:'👁',t:'Open & Cache on Server',fn:()=>openFile(m)}:null,
  m.media&&m.media.kind!=='sticker'?{ic:'⬇',t:'Download to Device',fn:()=>downloadMsg(m)}:null,
  (m.media&&m.media.kind!=='sticker'&&navigator.share)?{ic:'📱',t:'Share to Phone / Apps (WhatsApp, Files…)',fn:()=>shareFileMobile(m)}:null,
  m.media&&m.media.kind!=='sticker'?{ic:'🔗',t:'Public Share / VLC Stream Link',fn:()=>shareLinkModal(m)}:null,
  m.media&&m.media.kind!=='sticker'?{ic:'📦',t:'Backup to Storage Group',fn:()=>backupToStorageModal(m)}:null,
  m.media&&m.media.kind!=='sticker'?{ic:'⚡',t:'Pre-Download on Server ('+S.dlCfg.threads+'T)',fn:()=>serverDownload(m,false).then(()=>toast('⚡ Server multi-thread download started'))}:null,
  {ic:'🔖',t:'Save to Saved Messages',fn:()=>saveMsg(m)},
  m.text?{ic:'⧉',t:'Copy text',fn:()=>copyText(m.text)}:null,
  m.text?{ic:'🌐',t:'Translate',fn:()=>translateMsg(m)}:null,
  (S.cd&&S.cd.type!=='user')?{ic:'🔗',t:'Copy message link',fn:()=>copyMsgLink(m)}:null,
  {ic:'➦',t:'Forward',fn:()=>forwardDlg([m.id])},
  {ic:m.pinned?'📍':'📌',t:m.pinned?'Unpin':'Pin',fn:()=>pinMsg(m)},
  (m.poll&&!m.poll.closed&&m.out)?{ic:'🛑',t:'Stop Poll',fn:()=>api('/app/api/poll/close',A({chat:S.chat,id:m.id})).then(r=>replaceMsg(r.message)).catch(e=>toast(e.message))}:null,
  (m.out&&m.text&&!m.service)?{ic:'✎',t:'Edit',fn:()=>startEdit(m)}:null,
  {ic:'☑',t:'Select',fn:()=>toggleSel(m.id)},
  {ic:'🗑',t:'Delete',red:true,fn:()=>deleteDlg([m.id])}
 ],top);
}
function copyText(t){if(navigator.clipboard&&navigator.clipboard.writeText)navigator.clipboard.writeText(t).then(()=>toast('Copied'),()=>toast('Copy failed'));else toast('Copy not available')}
async function pinMsg(m){
 if(m.pinned){try{await api('/app/api/pin',A({chat:S.chat,id:m.id,unpin:true}));m.pinned=false;S.pinned=null;drawPinned();drawMsgs('keep');toast('Unpinned')}catch(e){toast(e.message)};return}
 const nf=h('input',{type:'checkbox',checked:false});
 modal('Pin message?',h('label',{},nf,'Notify all members'),[{t:'Cancel'},{t:'Pin',c:'p',async fn(c){try{await api('/app/api/pin',A({chat:S.chat,id:m.id,unpin:false,notify:nf.checked}));m.pinned=true;S.pinned={id:m.id,text:m.text||'📎 Media'};drawPinned();drawMsgs('keep');toast('Pinned');c()}catch(e){toast(e.message)}}}]);
}
function deleteDlg(ids){const rv=h('input',{type:'checkbox',checked:true});modal('Delete '+(ids.length>1?ids.length+' messages':'message')+'?',h('label',{},rv,'Also delete for everyone'),[{t:'Cancel'},{t:'Delete',c:'d',async fn(c){try{await api('/app/api/delete',A({chat:S.chat,ids,revoke:rv.checked}));S.msgs=S.msgs.filter(m=>!ids.includes(m.id));S.sel.clear();selUpdate();drawMsgs('keep');c()}catch(e){toast(e.message)}}}])}
function forwardDlg(ids){
 const q=h('input',{placeholder:'Search target chats…'}),cmt=h('input',{placeholder:'Optional comment message…'}),list=h('div',{style:'max-height:42vh;overflow:auto'}),hide=h('input',{type:'checkbox'}),chosen=new Set();
 const draw=()=>list.replaceChildren(...[...S.dl,...(S.adl||[])].filter(d=>d.name.toLowerCase().includes(q.value.toLowerCase())).slice(0,80).map(d=>{
  const on=chosen.has(d.id);
  return h('div',{class:'row'+(on?' on':''),on:{click(){on?chosen.delete(d.id):chosen.add(d.id);draw()}}},avatar(d.id,d.name,36),h('b',{style:'flex:1',text:d.name}),h('span',{text:on?'✓':''}));
 }));
 q.oninput=draw;
 const close=modal('Forward '+ids.length+' message(s)',[q,cmt,list,h('label',{},hide,'Hide sender name (Drop author)')],[
  {t:'Cancel'},
  {t:'Forward',c:'p',async fn(c){
   if(!chosen.size){toast('Select at least 1 chat');return}
   try{
    await api('/app/api/forward',A({from_chat:S.chat,ids,to_chats:[...chosen],hide_author:hide.checked,comment:cmt.value}));
    toast('✅ Forwarded to '+chosen.size+' chat(s)');S.sel.clear();selUpdate();drawMsgs('keep');c();
   }catch(e){toast(e.message)}
  }}
 ]);
 draw();
}
function toggleSel(id){S.sel.has(id)?S.sel.delete(id):S.sel.add(id);selUpdate();drawMsgs('keep')}
function selUpdate(){$('#sel').style.display=S.sel.size?'flex':'none';$('#cbar').style.display=(S.sel.size||!S.chat)?'none':'flex';$('#selc').textContent=S.sel.size+' selected'}
$('#selx').onclick=()=>{S.sel.clear();selUpdate();drawMsgs('keep')};
$('#selrng').onclick=()=>{
 if(S.sel.size<2){toast('Select at least 2 messages first to select everything between them');return}
 const arr=[...S.sel],lo=Math.min(...arr),hi=Math.max(...arr);
 S.msgs.forEach(m=>{if(m.id>=lo&&m.id<=hi&&!m.service)S.sel.add(m.id)});
 selUpdate();drawMsgs('keep');
};
$('#self').onclick=()=>forwardDlg([...S.sel]);$('#seld').onclick=()=>deleteDlg([...S.sel]);

/* ---------- composer, polls, scheduled messages & voice recorder ---------- */
function autosize(){const t=$('#txt');t.style.height='auto';t.style.height=Math.min(180,t.scrollHeight)+'px'}
function barUpdate(){const b=$('#bar2'),c=b.firstElementChild;b.style.display=(S.reply||S.edit)?'flex':'none';const m=S.edit||S.reply;if(m){c.replaceChildren(h('b',{style:'color:var(--acc);font-size:13px;display:block',text:S.edit?'✎ Edit message':'↩ Reply to '+(m.out?'yourself':m.from||'message')}),h('span',{class:'mut',style:'display:block;white-space:nowrap;overflow:hidden;text-overflow:ellipsis',text:m.text||'📎 Media'}))}}
function setReply(m){S.reply=m;S.edit=null;barUpdate();$('#txt').focus()}
function startEdit(m){S.edit=m;S.reply=null;$('#txt').value=m.text;autosize();barUpdate();$('#txt').focus()}
function cancelCompose(){if(S.edit)$('#txt').value='';S.reply=null;S.edit=null;barUpdate();autosize()}
$('#bar2x').onclick=cancelCompose;
async function send(opt){opt=opt||{};const t=$('#txt').value;if(!t.trim()||!S.chat)return;const tx=t;$('#txt').value='';autosize();delete S.drafts[S.chat];
 try{if(S.edit){const r=await api('/app/api/edit',A({chat:S.chat,id:S.edit.id,text:tx,md:S.set.md}));replaceMsg(r.message);S.edit=null}
  else{const r=await api('/app/api/send',A({chat:S.chat,text:tx,reply_to:S.reply&&S.reply.id,topic_id:S.topic||undefined,md:S.set.md,silent:opt.silent,schedule:opt.schedule}));if(opt.schedule)toast('⏰ Message scheduled');else{if(!S.msgs.some(x=>x.id===r.message.id))S.msgs.push(r.message);drawMsgs('bottom')}}
  S.reply=null;barUpdate()}catch(e){toast(e.message);$('#txt').value=tx;autosize()}}
$('#send').onclick=()=>send();$('#send').oncontextmenu=e=>{e.preventDefault();sendMenu(e.clientX,e.clientY)};
let sendLP;$('#send').addEventListener('touchstart',e=>{const t=e.touches[0];sendLP=setTimeout(()=>sendMenu(t.clientX,t.clientY-120),600)},{passive:true});$('#send').addEventListener('touchend',()=>clearTimeout(sendLP),{passive:true});
function sendMenu(x,y){showMenu(x-160,y-110,[
 {ic:'🔕',t:'Send without sound',fn:()=>send({silent:true})},
 {ic:'⏰',t:'Schedule message',fn:()=>{const dt=h('input',{type:'datetime-local'});modal('Schedule Message',dt,[{t:'Cancel'},{t:'Schedule',c:'p',fn(c){const ts=Math.floor(new Date(dt.value).getTime()/1e3);if(!ts||ts<Date.now()/1e3+30){toast('Choose a future time');return}c();send({schedule:ts})}}])}},
 {ic:'📅',t:'View Scheduled Messages',fn:()=>openScheduled()}
])}
$('#csched').onclick=()=>openScheduled();
async function openScheduled(){
 if(!S.chat)return;
 const body=h('div',{style:'max-height:55vh;overflow:auto'},h('div',{class:'empty',text:'Loading…'}));
 const close=modal('⏰ Scheduled Messages',body,[{t:'Close'}]);
 try{
  const ms=(await api('/app/api/scheduled?'+Q({acc:S.acc,chat:S.chat}))).messages;
  if(!ms.length){body.replaceChildren(h('div',{class:'empty',text:'No scheduled messages in this chat'}));return}
  body.replaceChildren(...ms.map(m=>h('div',{class:'dl-card'},
   h('div',{class:'top'},h('b',{text:new Date(m.ts*1e3).toLocaleString()}),
    h('div',{style:'display:flex;gap:6px'},
     h('button',{style:'color:var(--acc);font-weight:600;font-size:12.5px',text:'Send Now',on:{async click(){try{await api('/app/api/scheduled/sendnow',A({chat:S.chat,ids:[m.id]}));toast('✅ Sent');close();reloadChatMsgs()}catch(e){toast(e.message)}}}}),
     h('button',{style:'color:#ef4444;font-weight:600;font-size:12.5px',text:'Delete',on:{async click(){try{await api('/app/api/scheduled/delete',A({chat:S.chat,ids:[m.id]}));toast('🗑 Deleted');close();openScheduled()}catch(e){toast(e.message)}}}})
    )
   ),
   h('div',{class:'mut',text:m.text||'📎 Media'})
  )));
 }catch(e){body.replaceChildren(h('div',{class:'empty',text:e.message}))}
}

/* Voice Recorder */
let REC=null;
$('#mic').onclick=async()=>{
 if(!S.chat)return;
 if(REC){REC.stop();return}
 if(!navigator.mediaDevices||!navigator.mediaDevices.getUserMedia){toast('Microphone not supported in this browser');return}
 try{
  const stream=await navigator.mediaDevices.getUserMedia({audio:true});
  const chunks=[];
  const mr=new MediaRecorder(stream);
  const t0=Date.now();
  $('#mic').classList.add('rec');
  toast('🎤 Recording voice note… Click 🎤 again to send');
  mr.ondataavailable=e=>{if(e.data&&e.data.size)chunks.push(e.data)};
  mr.onstop=()=>{
   $('#mic').classList.remove('rec');
   stream.getTracks().forEach(t=>t.stop());
   REC=null;
   if(!chunks.length||Date.now()-t0<600){toast('Voice recording canceled (too short)');return}
   const blob=new Blob(chunks,{type:mr.mimeType||'audio/ogg'});
   const ext=(mr.mimeType||'').includes('mp4')?'m4a':'ogg';
   const file=new File([blob],`voice_${Date.now()}.${ext}`,{type:blob.type});
   queueUpload(file,false,{is_voice:true});
  };
  REC=mr;mr.start();
 }catch(e){toast('Microphone access denied: '+e.message)}
};

let lastTyping=0;$('#txt').addEventListener('input',()=>{autosize();if(S.chat)S.drafts[S.chat]=$('#txt').value;if(Date.now()-lastTyping>4500&&$('#txt').value&&S.chat){lastTyping=Date.now();api('/app/api/typing',A({chat:S.chat})).catch(()=>{})}});
$('#txt').addEventListener('keydown',e=>{if(e.key==='Enter'&&!e.shiftKey&&S.set.enter&&innerWidth>820){e.preventDefault();send()}else if(e.key==='Escape')cancelCompose();else if(e.key==='ArrowUp'&&!$('#txt').value){const l=[...S.msgs].reverse().find(m=>m.out&&m.text&&!m.service);if(l){e.preventDefault();startEdit(l)}}});
$('#txt').addEventListener('paste',e=>{const fs=[...(e.clipboardData&&e.clipboardData.files||[])];if(fs.length){e.preventDefault();pickFiles(fs,false)}});
$('#md').onclick=()=>{S.set.md=!S.set.md;saveSet();$('#md').style.color=S.set.md?'var(--acc)':'';toast(S.set.md?'Markdown formatting ON (**bold** __italic__ `code`)':'Formatting OFF')};
const emo=$('#emo');EMOJI.forEach(e=>emo.append(h('button',{text:e,on:{click(){const t=$('#txt');t.setRangeText(e,t.selectionStart,t.selectionEnd,'end');t.focus();autosize()}}})));$('#emob').onclick=()=>emo.classList.toggle('on');
$('#att').onclick=e=>{
 const r=e.currentTarget.getBoundingClientRect();
 showMenu(r.left-120,r.top-145,[
  {ic:'🖼',t:'Photo or Video',fn:()=>{$('#file').dataset.f='0';$('#file').click()}},
  {ic:'📄',t:'File (Uncompressed)',fn:()=>{$('#file').dataset.f='1';$('#file').click()}},
  {ic:'📊',t:'Create Poll / Quiz',fn:()=>createPollModal()}
 ]);
};
$('#file').onchange=e=>{pickFiles([...e.target.files],e.target.dataset.f==='1');e.target.value=''};
const msgsEl=$('#msgs');['dragover','drop'].forEach(ev=>msgsEl.addEventListener(ev,e=>{e.preventDefault();if(ev==='drop'&&e.dataTransfer)pickFiles([...e.dataTransfer.files],false)}));
function pickFiles(fs,asFile){if(!S.chat)return;for(const f of fs)queueUpload(f,asFile)}

function createPollModal(){
 if(!S.chat)return;
 const q=h('input',{placeholder:'Ask a question…',maxlength:255});
 const optsBox=h('div',{style:'display:flex;flex-direction:column;gap:6px'});
 const addOpt=(ph)=>{const inp=h('input',{placeholder:ph||`Option ${optsBox.children.length+1}`,maxlength:100});optsBox.append(inp)};
 addOpt('Option 1');addOpt('Option 2');
 const addBtn=h('button',{style:'color:var(--acc);font-weight:600;text-align:left',text:'＋ Add option',on:{click(){if(optsBox.children.length<10)addOpt()}}});
 const pub=h('input',{type:'checkbox'}),multi=h('input',{type:'checkbox'}),quiz=h('input',{type:'checkbox'});
 const corr=h('input',{type:'number',min:1,max:10,value:1,placeholder:'Correct option # (1-10)',style:'display:none'}),sol=h('input',{placeholder:'Explanation for quiz answer (optional)',style:'display:none'});
 quiz.onchange=()=>{corr.style.display=sol.style.display=quiz.checked?'block':'none';if(quiz.checked)multi.checked=false};
 modal('📊 Create Poll / Quiz',[
  q,optsBox,addBtn,
  h('label',{},pub,'Public Voting (non-anonymous)'),
  h('label',{},multi,'Allow Multiple Answers'),
  h('label',{},quiz,'🎯 Quiz Mode (1 correct answer)'),
  corr,sol
 ],[
  {t:'Cancel'},
  {t:'Send Poll',c:'p',async fn(c){
   const options=[...optsBox.querySelectorAll('input')].map(i=>i.value.trim()).filter(Boolean);
   if(!q.value.trim()||options.length<2){toast('Enter a question and at least 2 options');return}
   try{
    const r=await api('/app/api/poll/create',A({chat:S.chat,question:q.value,options,public:pub.checked,multi:multi.checked,quiz:quiz.checked,correct:(parseInt(corr.value,10)||1)-1,solution:sol.value,reply_to:S.reply&&S.reply.id}));
    if(!S.msgs.some(x=>x.id===r.message.id))S.msgs.push(r.message);
    drawMsgs('bottom');c();
   }catch(e){toast(e.message)}
  }}
 ]);
 q.focus();
}

/* ---------- मोठ्या फाइलचा upload (8 MB तुकडे, resume सकट) ---------- */
function drawUploads(){const u=$('#ups');u.replaceChildren(...Object.values(S.uploads).filter(x=>x.chat===S.chat||x.state==='error').map(x=>{const pct=x.state==='receiving'?x.written/x.size*50:50+x.sent/x.size*50;
 return h('div',{class:'up'},h('div',{style:'display:flex;justify-content:space-between;gap:8px'},h('span',{text:'⬆ '+x.name+' · '+sz(x.size)+' · '+(x.state==='receiving'?'uploading to server':x.state==='sending'?'sending to Telegram':x.state)+(x.err?': '+x.err:'')}),
  (x.state==='receiving'||x.state==='sending')?h('button',{class:'mut',text:'✕',on:{click(){cancelUpload(x)}}}):h('button',{class:'mut',text:'✕',on:{click(){delete S.uploads[x.id];drawUploads()}}})),h('div',{class:'pb'},h('i',{style:'width:'+Math.min(100,pct)+'%'})))}))}
async function cancelUpload(x){x.state='canceled';try{await api('/app/api/up/cancel',{id:x.id})}catch(e){}delete S.uploads[x.id];drawUploads()}
const ab=b=>b.arrayBuffer?b.arrayBuffer():new Promise((res,rej)=>{const r=new FileReader();r.onload=()=>res(r.result);r.onerror=rej;r.readAsArrayBuffer(b)});
async function queueUpload(file,asFile,extra){extra=extra||{};const lim=cur().max_upload||4e9;if(file.size>lim){toast(`${file.name}: over Telegram's limit (${mbs(lim)})`);return}
 const cap=S.edit?'':'';let st;try{st=await api('/app/api/up/start',A({chat:S.chat,size:file.size,name:file.name,caption:cap,reply_to:S.reply&&S.reply.id,topic_id:S.topic||undefined,as_file:asFile,is_voice:!!extra.is_voice,is_round:!!extra.is_round,md:S.set.md}))}catch(e){toast(e.message);return}
 S.reply=null;barUpdate();const U={id:st.id,name:file.name,size:file.size,written:0,sent:0,state:'receiving',chat:S.chat,err:''};S.uploads[U.id]=U;drawUploads();
 try{let off=0;while(off<file.size){if(U.state==='canceled')return;const end=Math.min(off+st.chunk,file.size),buf=await ab(file.slice(off,end));let tries=0;
   for(;;){try{const r=await api('/app/api/up/chunk?'+Q({id:U.id,off}),undefined,{raw:buf});U.written=r.written;off=r.written;break}catch(e){if(e.status===409&&e.data&&e.data.written!=null){off=e.data.written;U.written=off;break}if(++tries>4)throw e;await sleep(1500*tries)}}
   drawUploads()}
  await api('/app/api/up/finish',{id:U.id});U.state='sending';drawUploads();watchUpload(U)}catch(e){U.state='error';U.err=e.message;drawUploads()}}
async function watchUpload(U){while(S.uploads[U.id]&&U.state==='sending'){await sleep(3000);try{const r=await api('/app/api/up/status?'+Q({id:U.id}));if(r.state==='done'){finishUpload(U,r.message)}else if(r.state==='error'||r.state==='canceled'){U.state=r.state;U.err=r.err;drawUploads()}}catch(e){}}}
function finishUpload(U,msg){if(U.state==='done')return;U.state='done';if(msg&&U.chat===S.chat&&!S.msgs.some(x=>x.id===msg.id)){S.msgs.push(msg);drawMsgs('bottom')}setTimeout(()=>{delete S.uploads[U.id];drawUploads()},1500);drawUploads()}

/* ---------- in-app file viewer + fast server-to-browser download ---------- */
function fileKind(x){const m=(x.mime||'').toLowerCase(),n=(x.name||'').toLowerCase();
 if(x.kind==='photo'||m.startsWith('image/'))return 'image';
 if(x.kind==='video'||x.kind==='gif'||x.kind==='videonote'||m.startsWith('video/')||/\.(mp4|webm|mkv|mov|m4v)$/.test(n))return 'video';
 if(x.kind==='voice'||x.kind==='audio'||m.startsWith('audio/')||/\.(mp3|m4a|ogg|wav|flac|aac)$/.test(n))return 'audio';
 if(m==='application/pdf'||n.endsWith('.pdf'))return 'pdf';
 if(m.startsWith('text/')||m==='application/json'||/\.(txt|md|json|csv|log|py|js|ts|html?|css|xml|ya?ml|ini|conf|sh|sql|c|cpp|h|java|go|rs|php|rb|toml)$/.test(n))return 'text';
 return 'other'}
async function probeFile(url){try{const r=await fetch(url,{headers:{Range:'bytes=0-0'},credentials:'same-origin'});if(r.ok)return '';let e='';try{e=(await r.json()).error}catch(_){}return e||('error '+r.status)}catch(e){return 'network error'}}

async function downloadMsg(m){
 const x=m.media;if(!x)return;
 const u=fileUrl({acc:S.acc,chat:S.chat,id:m.id},true);
 // 1) Start server multi-threaded download with keepalive:true so closing/refreshing the app never stops it!
 serverDownload(m,false).catch(()=>{});
 // 2) On installed mobile WebAPK (standalone mode), direct top-level attachment link hands off straight to Android System DownloadManager
 //    without navigating away because Content-Disposition: attachment is set! On desktop, hidden iframe prevents unload.
 const isStandalone=window.matchMedia('(display-mode: standalone)').matches||window.navigator.standalone||innerWidth<=820;
 const a=h('a',{href:u,download:x.name||('file_'+m.id),target:isStandalone?'_self':'tg_dl_frame',rel:'noopener',style:'display:none'});
 document.body.append(a);a.click();setTimeout(()=>a.remove(),1000);
 if(navigator.vibrate)try{navigator.vibrate(20)}catch(e){}
 toast('⬇ Downloading to device… (Check notification bar / Downloads)');
}
async function shareFileMobile(m){
 const x=m.media;if(!x)return;
 if(!navigator.share){downloadMsg(m);return}
 const u=fileUrl({acc:S.acc,chat:S.chat,id:m.id},true);
 if(x.size<=45*1048576&&navigator.canShare){
  toast('📱 Preparing file for native mobile share…',5000);
  try{
   const r=await fetch(u,{credentials:'same-origin'});
   const blob=await r.blob();
   const file=new File([blob],x.name||('file_'+m.id),{type:x.mime||blob.type||'application/octet-stream'});
   if(navigator.canShare({files:[file]})){await navigator.share({title:x.name||'Telegram File',files:[file]});return}
  }catch(e){if(e.name==='AbortError')return}
 }
 // For huge files (>45 MB), generate a direct share link and open native Android Share sheet
 try{
  const sr=await api('/app/api/share/create',A({chat:S.chat,id:m.id,hours:24}));
  await navigator.share({title:x.name||'Telegram File',text:x.name||'Telegram File',url:location.origin+sr.path+'?dl=1'});
 }catch(e){if(e.name!=='AbortError')downloadMsg(m)}
}
function closeFv(fromPop){const fv=$('#fv');if(fv.style.display==='none')return;fv.querySelectorAll('video,audio').forEach(stopMediaEl);fv.style.display='none';fv.dataset.dl='';$('#fvb').replaceChildren();$('#fvst').textContent='';ensureNavGuard()}
window.addEventListener('popstate',()=>{
 if(_ignoringPop){_ignoringPop=false;return}
 if(MENU){closeMenu();ensureNavGuard();return}
 const ovs=document.querySelectorAll('.ov');if(ovs.length){ovs[ovs.length-1].remove();ensureNavGuard();return}
 if($('#lb').style.display==='flex'){$('#lb').style.display='none';$('#lb img').removeAttribute('src');LB=null;ensureNavGuard();return}
 if($('#fv').style.display==='flex'){closeFv(true);return}
 if($('#emo').classList.contains('on')){$('#emo').classList.remove('on');ensureNavGuard();return}
 if($('#drawer').classList.contains('on')){closeDrawer();ensureNavGuard();return}
 if(S.info){toggleInfo();ensureNavGuard();return}
 if(S.sel.size){S.sel.clear();selUpdate();drawMsgs('keep');ensureNavGuard();return}
 if(document.body.classList.contains('chat')){closeChatView(true);return}
 if(S.archived||S.folder!=='all'||S.q){S.archived=false;S.folder='all';S.q='';$('#search').value='';S.gsearch=[];S.gres=null;drawFolders();drawList();ensureNavGuard();return}
});
$('#fvx').onclick=()=>closeFv();
async function openFile(m){
 const x=m.media;if(!x)return;
 pauseAllMedia(null);
 $('#fvb').querySelectorAll('video,audio').forEach(stopMediaEl);
 const k=dlKey(m),url=fileUrl({acc:S.acc,chat:S.chat,id:m.id},false),kind=fileKind(x),body=$('#fvb');
 // Immediately start caching the viewed file on the server with multi-threading!
 serverDownload(m,false).catch(()=>{});
 $('#fv').dataset.dl=k;
 $('#fvt').textContent=x.name||(kind==='image'?'Photo':kind==='video'?'Video':'File');
 $('#fvst').textContent=dlBadgeText(S.dls[k]||{state:'downloading',size:x.size},x);
 const d=$('#fvd');d.href='#';d.onclick=e=>{e.preventDefault();downloadMsg(m)};
 body.replaceChildren(h('div',{class:'mut',text:'⚡ Loading via multi-thread server cache…'}));$('#fv').style.display='flex';pushNav('viewer');
 const fail=async()=>{const er=await probeFile(url);body.replaceChildren(h('div',{style:'text-align:center;max-width:420px'},h('div',{style:'font-size:44px',text:'⚠'}),h('div',{text:er||"Can't preview this format directly"}),h('div',{class:'mut',style:'margin-top:8px',text:'The file is being cached on the server — click ⬇ Download to save it directly.'}),h('button',{style:'margin-top:12px;background:var(--acc);color:#fff;border-radius:8px;padding:10px 18px;font-weight:600',text:'⬇ Fast Download',on:{click(){downloadMsg(m)}}})))};
 if(kind==='image')body.replaceChildren(h('img',{alt:'',src:url,on:{error:fail}}));
 else if(kind==='video'){
  const v=h('video',{src:url,controls:true,autoplay:true,playsinline:true,preload:'auto',on:{error:fail}});bindMediaSession(v,m);
  const warn=h('div',{style:'display:none;background:#7c2d12;color:#ffedd5;padding:8px 12px;border-radius:8px;font-size:13px;gap:10px;align-items:center'},
   h('span',{text:'⚠ या व्हिडिओचा Codec (HEVC/MKV) ब्राउझरमध्ये सपोर्टेड नाही (फक्त आवाज येतोय).'}),
   h('button',{style:'background:#ea580c;color:#fff;padding:5px 12px;border-radius:6px;font-weight:700',text:'🎬 VLC मध्ये स्ट्रीम करा',on:{click(){v.pause();shareLinkModal(m)}}})
  );
  v.addEventListener('loadeddata',()=>{if(v.videoWidth===0&&v.duration>0)warn.style.display='flex'});
  body.replaceChildren(v,warn);
  const p=v.play&&v.play();if(p&&p.catch)p.catch(()=>{});
 }
 else if(kind==='audio')body.replaceChildren(h('audio',{src:url,controls:true,autoplay:true,style:'width:min(92vw,520px)',on:{error:fail}}));
 else if(kind==='pdf'){const er=await probeFile(url);if(er)return fail();body.replaceChildren(h('iframe',{src:url}))}
 else if(kind==='text'){try{const r=await fetch(url,{headers:{Range:'bytes=0-1048575'},credentials:'same-origin'});if(!r.ok)return fail();const t=await r.text();body.replaceChildren(h('pre',{text:t+(x.size>1048576?'\n\n… (first 1 MB shown — use ⬇ Download for the full file)':'')}))}catch(e){fail()}}
 else body.replaceChildren(h('div',{style:'text-align:center'},h('div',{style:'font-size:56px',text:'📄'}),h('b',{text:x.name||'File'}),h('div',{class:'mut',style:'margin:6px 0 14px',text:sz(x.size)+' · Caching on server with '+S.dlCfg.threads+' threads'}),h('button',{class:'p',style:'background:var(--acc);color:#fff;border-radius:8px;padding:10px 18px;font-weight:600',text:'⬇ Fast Download to Browser',on:{click(){downloadMsg(m)}}})));
}

/* ---------- ⚡ Multi-Thread Downloads & Server Cache Manager ---------- */
$('#dlmgr').onclick=()=>openDlManager();
async function openDlManager(){
 const thSel=h('select',{style:'width:auto'},[[1,'1 Thread (Ultra Safe)'],[2,'2 Threads (Safe)'],[4,'4 Threads (Recommended ⚡)'],[6,'6 Threads (Fast)'],[8,'8 Threads (Max)']].map(([n,t])=>h('option',{value:n,text:t})));
 thSel.value=String(Math.min(8,S.dlCfg.threads||4));
 const ttlMin0=Math.round((S.dlCfg.ttl||3600)/60);
 const ttlSel=h('select',{style:'width:auto'},[[15,'15 mins'],[30,'30 mins'],[60,'1 hour'],[120,'2 hours'],[360,'6 hours'],[720,'12 hours'],[1440,'24 hours']].map(([v,t])=>h('option',{value:v,text:t})));
 ttlSel.value=String([15,30,60,120,360,720,1440].includes(ttlMin0)?ttlMin0:60);
 const autoChk=h('input',{type:'checkbox',checked:S.dlCfg.auto_cache!==false});
 const usageTxt=h('div',{class:'mut',style:'font-size:12px',text:'Loading cache usage…'});
 const saveCfg=async()=>{
  try{
   const res=await api('/app/api/cache/settings',{threads:+thSel.value,ttl_minutes:+ttlSel.value,auto_cache:autoChk.checked});
   S.dlCfg=res.cfg;toast('⚡ Settings updated');refreshModal();
  }catch(e){toast(e.message)}
 };
 thSel.onchange=ttlSel.onchange=autoChk.onchange=saveCfg;
 const cfgCard=h('div',{class:'dl-card'},
  h('div',{style:'display:flex;align-items:center;gap:6px;font-size:12px;color:#10b981;font-weight:700'},'🛡 Account Anti-Ban Protection Active (Smart FloodWait Pacing + Safe Worker Cap)'),
  h('div',{class:'sw'},h('span',{text:'⚡ Parallel Download Threads'}),thSel),
  h('div',{class:'sw'},h('span',{text:'⏱ Fixed Auto-Clear Server Time'}),ttlSel),
  h('label',{style:'font-size:13px'},autoChk,'Auto-download to server when viewing video / file'),
  usageTxt
 );
 const listBox=h('div',{style:'display:flex;flex-direction:column;gap:8px'},h('div',{class:'empty',text:'Loading…'}));
 const box=h('div',{style:'display:flex;flex-direction:column;gap:10px'},cfgCard,listBox);
 let timer=null;
 const close=modal('⚡ Multi-Thread Downloads & Server Cache',box,[{t:'Close',fn(c){clearInterval(timer);c()}}]);
 async function refreshModal(){
  try{
   const r=await api('/app/api/dl/list');
   S.dlCfg=r.cfg||S.dlCfg;
   usageTxt.textContent=`Disk cache used: ${sz(r.used)} (${r.files} cached file(s)). Viewed & downloaded files are cached on the server and automatically deleted after the fixed time.`;
   const kids=[];
   if(r.active&&r.active.length){
    kids.push(h('b',{style:'font-size:13px;color:var(--acc)',text:`Active Server Downloads (${r.active.length})`}));
    for(const a of r.active){
     const pct=pctOf(a);
     kids.push(h('div',{class:'dl-card'},
      h('div',{class:'top'},h('b',{text:a.name}),h('span',{class:'badge',text:`${a.threads}T · ${spd(a.speed)}`}),
       h('button',{style:'color:#ef4444;font-weight:700',text:'✕',on:{async click(){await api('/app/api/dl/cancel',{acc:a.acc,chat:a.chat,id:a.id});refreshModal()}}})
      ),
      h('div',{class:'mut',style:'font-size:12px',text:`${pct}% · ${sz(a.done)} / ${sz(a.size)}`}),
      h('div',{style:'height:5px;background:var(--bd);border-radius:3px;overflow:hidden'},h('i',{style:'display:block;height:100%;background:var(--acc);width:'+pct+'%'}))
     ));
    }
   }
   kids.push(h('div',{style:'display:flex;justify-content:space-between;align-items:center'},
    h('b',{style:'font-size:13px',text:`Cached on Server (${(r.cached||[]).length})`}),
    r.cached&&r.cached.length?h('button',{style:'color:#ef4444;font-size:12.5px;font-weight:600',text:'🗑 Clear All Now',on:{async click(){await api('/app/api/cache/clear',{});S.dls={};drawMsgs('keep');toast('Server cache cleared');refreshModal()}}}):null
   ));
   if(!r.cached||!r.cached.length){
    kids.push(h('div',{class:'mut',style:'text-align:center;padding:12px;font-size:13px',text:'No cached files on server right now.'}));
   }else{
    for(const c of r.cached){
     const dlUrl=c.acc&&c.chat&&c.id?fileUrl({acc:c.acc,chat:c.chat,id:c.id},true):null;
     kids.push(h('div',{class:'dl-card'},
      h('div',{class:'top'},
       h('b',{text:c.name}),
       h('span',{class:'badge ok',text:'⏱ '+fmtTtl(c.expires_in)})
      ),
      h('div',{style:'display:flex;justify-content:space-between;align-items:center;font-size:12.5px'},
       h('span',{class:'mut',text:sz(c.size)}),
       h('div',{style:'display:flex;gap:10px'},
        dlUrl?h('a',{href:dlUrl,download:c.name,style:'color:var(--acc);font-weight:700;text-decoration:none',text:'⬇ Save to Browser'}):null,
        h('button',{style:'color:#ef4444;font-weight:600',text:'🗑 Delete',on:{async click(){await api('/app/api/cache/delete',{key:c.key});refreshModal()}}})
       )
      )
     ));
    }
   }
   listBox.replaceChildren(...kids);
  }catch(e){listBox.replaceChildren(h('div',{class:'err',text:e.message}))}
 }
 refreshModal();
 timer=setInterval(()=>{if(!document.body.contains(box)){clearInterval(timer);return}refreshModal()},2200);
}


/* ---------- 🚀 Premium & Innovative Features (Share/VLC Link, Ghost Vault, Auto-Reply, Cross-Account Search, Analytics, Video Snap) ---------- */
function snapVideo(v,name){
 try{
  if(!v.videoWidth||!v.videoHeight){toast('Wait for video frame to load first');return}
  const c=document.createElement('canvas');c.width=v.videoWidth;c.height=v.videoHeight;
  c.getContext('2d').drawImage(v,0,0,c.width,c.height);
  c.toBlob(blob=>{
   if(!blob)return;
   const u=URL.createObjectURL(blob);
   const a=h('a',{href:u,download:`${name.replace(/\.[^.]+$/,'')}_frame_${Math.round(v.currentTime)}s.png`,style:'display:none'});
   document.body.append(a);a.click();a.remove();
   setTimeout(()=>URL.revokeObjectURL(u),5000);
   toast('📸 Video frame screenshot saved!');
  },'image/png');
 }catch(e){toast('Screenshot failed: '+e.message)}
}

function shareLinkModal(m){
 const hrs=h('select',{style:'width:auto'},[[1,'1 Hour'],[6,'6 Hours'],[24,'24 Hours (1 Day)'],[72,'3 Days'],[168,'7 Days']].map(([v,t])=>h('option',{value:v,text:t})));
 hrs.value='24';
 const resBox=h('div',{style:'display:flex;flex-direction:column;gap:8px'});
 async function gen(){
  resBox.replaceChildren(h('div',{class:'mut',text:'Generating direct stream/download link…'}));
  try{
   const r=await api('/app/api/share/create',A({chat:S.chat,id:m.id,hours:+hrs.value}));
   const fullUrl=location.origin+r.path;
   const dlUrl=fullUrl+'?dl=1';
   resBox.replaceChildren(
    h('div',{class:'dl-card'},
     h('b',{style:'font-size:12.5px;color:var(--acc)',text:'🎬 Direct Stream Link (VLC / MX Player / PotPlayer / Browser):'}),
     h('input',{value:fullUrl,readonly:true,on:{click(e){e.target.select();copyText(fullUrl)}}}),
     h('div',{style:'display:flex;gap:8px'},
      h('button',{style:'background:var(--acc);color:#fff;padding:6px 12px;border-radius:8px;font-weight:600;font-size:12.5px',text:'⧉ Copy Stream Link',on:{click(){copyText(fullUrl)}}}),
      h('button',{style:'background:var(--hov);color:var(--acc);padding:6px 12px;border-radius:8px;font-weight:600;font-size:12.5px',text:'⬇ Copy IDM / Direct Download Link',on:{click(){copyText(dlUrl)}}})
     )
    ),
    h('div',{class:'mut',style:'font-size:12px',text:'💡 Paste this link in VLC (Media → Open Network Stream) or MX Player to watch at full multi-thread server speed without logging in!'})
   );
  }catch(e){resBox.replaceChildren(h('div',{class:'err',text:e.message}))}
 }
 hrs.onchange=gen;
 modal('🔗 Public Share & VLC Stream Link',[h('div',{class:'sw'},h('span',{text:'Link Expiry Time'}),hrs),resBox],[{t:'Close'}]);
 gen();
}

async function backupToStorageModal(m){
 const body=h('div',{style:'display:flex;flex-direction:column;gap:8px'},h('div',{class:'mut',text:'Loading Backup Storage groups…'}));
 const close=modal('📦 Backup File to Storage Group',body,[{t:'Cancel'}]);
 try{
  const r=await api('/app/api/storage/groups');
  const gs=r.groups||[];
  const targets=[...gs.map(g=>({id:g.id,name:'📦 '+g.title})),...S.dl.filter(d=>d.type!=='user'&&(d.owner||d.admin)).map(d=>({id:d.id,name:'🛡 '+d.name}))];
  if(!targets.length){body.replaceChildren(h('div',{class:'empty',text:'No Storage Groups or Admin Groups found.'}));return}
  body.replaceChildren(
   h('div',{class:'mut',style:'font-size:13px',text:'Choose a Backup Group / Channel to copy this file into instantly (0 bandwidth used):'}),
   ...targets.slice(0,30).map(t=>h('div',{class:'row',on:{async click(){
    try{
     await api('/app/api/forward',A({from_chat:S.chat,ids:[m.id],to_chat:t.id,hide_author:true}));
     toast('📦 Backed up to '+t.name);close();
    }catch(e){toast(e.message)}
   }}},avatar(t.id,t.name,36),h('b',{text:t.name})))
  );
 }catch(e){body.replaceChildren(h('div',{class:'err',text:e.message}))}
}

async function openGhostAndAutoReply(){
 const box=h('div',{style:'display:flex;flex-direction:column;gap:10px'},h('div',{class:'empty',text:'Loading…'}));
 modal('👻 Ghost Anti-Delete Vault & 🤖 Auto-Reply',box,[{t:'Close'}]);
 async function load(){
  try{
   const r=await api('/app/api/ghost/list?'+Q({acc:S.acc}));
   const gChk=h('input',{type:'checkbox',checked:r.anti_delete!==false});
   const arChk=h('input',{type:'checkbox',checked:!!(r.auto_reply&&r.auto_reply.enabled)});
   const ctChk=h('input',{type:'checkbox',checked:!!(r.auto_reply&&r.auto_reply.contacts_only)});
   const arTxt=h('textarea',{rows:2,placeholder:'Auto-reply message when someone DMs you (e.g. "I am currently studying, will reply later!")'});
   arTxt.value=(r.auto_reply&&r.auto_reply.text)||'';
   const cdSel=h('select',{style:'width:auto'},[[300,'Every 5m'],[1800,'Every 30m'],[3600,'Every 1h'],[21600,'Every 6h']].map(([v,t])=>h('option',{value:v,text:t})));
   cdSel.value=String((r.auto_reply&&r.auto_reply.cooldown)||3600);
   const saveBtn=h('button',{style:'background:var(--acc);color:#fff;padding:7px 14px;border-radius:8px;font-weight:600;align-self:flex-end',text:'💾 Save Settings',on:{async click(){
    try{
     await api('/app/api/ghost/settings',A({anti_delete:gChk.checked,auto_reply_enabled:arChk.checked,auto_reply_text:arTxt.value,cooldown:+cdSel.value,contacts_only:ctChk.checked}));
     toast('✅ Ghost & Auto-Reply settings saved');
    }catch(e){toast(e.message)}
   }}});
   const vList=(r.deleted||[]);
   box.replaceChildren(
    h('div',{class:'dl-card'},
     h('label',{style:'font-weight:700'},gChk,'👻 Anti-Delete Mode (Keep deleted messages visible in chat & vault)'),
     h('hr',{style:'border:0;border-top:1px solid var(--bd);width:100%'}),
     h('label',{style:'font-weight:700'},arChk,'🤖 Smart Auto-Reply in Private Chats (Away Mode)'),
     h('div',{class:'mut',style:'font-size:11.5px;color:#10b981'},'🛡 Anti-Ban Shield: Simulates 2.2s typing & caps at 4 replies/min.'),
     arTxt,
     h('label',{style:'font-size:12.5px'},ctChk,'🛡 Reply to Saved Contacts only (Recommended to avoid spam traps)'),
     h('div',{class:'sw'},h('span',{class:'mut',style:'font-size:12.5px',text:'Reply Cooldown per User'}),cdSel),
     saveBtn
    ),
    h('div',{style:'display:flex;justify-content:space-between;align-items:center'},
     h('b',{style:'font-size:13.5px',text:`👻 Recently Deleted Messages (${vList.length})`}),
     vList.length?h('button',{style:'color:#ef4444;font-size:12.5px;font-weight:600',text:'🗑 Clear Vault',on:{async click(){await api('/app/api/ghost/settings',A({clear_vault:true}));load()}}}):null
    ),
    ...(vList.length?vList.map(m=>h('div',{class:'dl-card',style:'cursor:pointer',on:{click(){document.querySelector('.ov').remove();openById(m.chat,m.id)}}},
     h('div',{class:'top'},h('b',{style:'color:#ef4444',text:'👻 '+(m.from||'User')}),h('span',{class:'mut',style:'font-size:11.5px',text:dayLabel(m.deleted_at||m.ts)+' '+tm(m.deleted_at||m.ts)})),
     h('div',{text:m.text||(m.media?'📎 '+(m.media.name||m.media.kind):'Message')})
    )):[h('div',{class:'mut',style:'text-align:center;padding:14px;font-size:13px',text:'No deleted messages captured yet. When someone deletes a message, it will appear here!'})])
   );
  }catch(e){box.replaceChildren(h('div',{class:'err',text:e.message}))}
 }
 load();
}

function openCrossSearch(){
 const q=h('input',{placeholder:'Search messages or files across ALL accounts…'}),res=h('div',{style:'max-height:52vh;overflow:auto;display:flex;flex-direction:column;gap:6px'});
 const goBtn=h('button',{style:'background:var(--acc);color:#fff;padding:8px 14px;border-radius:8px;font-weight:600',text:'Search All',on:{click:doSearch}});
 async function doSearch(){
  if(q.value.trim().length<2){toast('Enter at least 2 characters');return}
  res.replaceChildren(h('div',{class:'empty',text:'🌐 Searching across all connected Telegram accounts…'}));
  try{
   const ms=(await api('/app/api/cross_search?'+Q({q:q.value.trim()}))).messages||[];
   if(!ms.length){res.replaceChildren(h('div',{class:'empty',text:'No matches across accounts'}));return}
   res.replaceChildren(...ms.map(m=>h('div',{class:'dl-card',style:'cursor:pointer',on:{async click(){
    close();
    if(m.acc&&m.acc!==S.acc)await switchAcc(m.acc);
    openById(m.chat,m.id);
   }}},
    h('div',{class:'top'},h('b',{text:m.chat_name||'Chat'}),h('span',{class:'badge',text:m.acc_name||m.acc})),
    h('div',{style:'font-size:13px',text:m.text||(m.media?'📎 '+(m.media.name||m.media.kind):'')}),
    h('div',{class:'mut',style:'font-size:11.5px',text:(m.from?m.from+' · ':'')+dayLabel(m.ts)+' '+tm(m.ts)+(m.media&&m.media.size?' · '+sz(m.media.size):'')})
   )));
  }catch(e){res.replaceChildren(h('div',{class:'err',text:e.message}))}
 }
 q.addEventListener('keydown',e=>{if(e.key==='Enter')doSearch()});
 const close=modal('🌐 Cross-Account Global Search',[h('div',{style:'display:flex;gap:8px'},q,goBtn),res],[{t:'Close'}]);
 q.focus();
}

async function drawAnalytics(b){
 b.replaceChildren(h('div',{class:'empty',text:'📊 Analyzing recent chat activity…'}));
 try{
  const r=await api('/app/api/analytics?'+Q({acc:S.acc,chat:S.chat}));
  const ks=Object.entries(r.kinds||{}).sort((a,b)=>b[1]-a[1]);
  b.replaceChildren(
   h('div',{class:'kv'},
    h('div',{class:'dl-card'},
     h('b',{style:'color:var(--acc)',text:`📊 Chat Overview (Last ${r.sampled} msgs)`}),
     h('div',{style:'font-size:13px',text:`Total Media Size: ${sz(r.total_media_bytes||0)}`}),
     r.peak_hour!=null?h('div',{class:'mut',style:'font-size:12.5px',text:`Most Active Hour: ${r.peak_hour}:00 – ${r.peak_hour+1}:00`}):null
    )
   ),
   h('div',{class:'kv'},
    h('b',{style:'display:block;margin-bottom:6px',text:'📁 Content Breakdown'}),
    ...ks.map(([k,n])=>h('div',{class:'sw',style:'font-size:13px'},h('span',{text:k.toUpperCase()}),h('b',{text:n+' msgs'})))
   ),
   h('div',{class:'kv'},
    h('b',{style:'display:block;margin-bottom:6px',text:'🏆 Top Active Senders'}),
    ...(r.top_senders||[]).map((s,i)=>h('div',{class:'sw',style:'font-size:13px'},h('span',{text:`${i+1}. ${s.name}`}),h('span',{class:'badge',text:s.count+' msgs'})))
   )
  );
 }catch(e){b.replaceChildren(h('div',{class:'empty',text:e.message}))}
}

/* ---------- lightbox ---------- */
let LB=null;
function lightbox(id){const L=S.msgs.filter(m=>m.media&&m.media.kind==='photo');let i=L.findIndex(m=>m.id===id);if(i<0)return;const lb=$('#lb'),im=lb.querySelector('img');
 const show=()=>{const m=L[i];im.src=fileUrl({acc:S.acc,chat:S.chat,id:m.id},false);$('#lbc').textContent=(i+1)+' / '+L.length};
 const step=d=>{const n=i+d;if(n>=0&&n<L.length){i=n;show()}};LB={step};lb.style.display='flex';pushNav('photo');show();
 const close=()=>{lb.style.display='none';im.removeAttribute('src');LB=null;ensureNavGuard()};
 lb.querySelector('.x').onclick=close;lb.onclick=e=>{if(e.target===lb)close()};$('#lbd').onclick=()=>downloadMsg(L[i]);
 const nv=lb.querySelectorAll('.nv');nv[0].onclick=()=>step(-1);nv[1].onclick=()=>step(1)}

/* ---------- info panel & official user/group management ---------- */
function toggleInfo(){S.info=!S.info;document.body.classList.toggle('info-on',S.info);if(S.info){pushNav('info');drawInfo()}else ensureNavGuard()}
$('#cinfo').onclick=toggleInfo;
function drawInfo(){
 const p=$('#info'),d=S.cd,ci=S.ci&&S.ci.id===S.chat?S.ci:null;if(!d){p.replaceChildren();return}
 const kv=(k,v)=>v?h('div',{class:'kv',style:'cursor:pointer',title:'Click to copy',on:{click(){copyText(v)}}},h('div',{text:v}),h('small',{text:k})):null;
 const userActs=(d.type==='user'&&!d.self)?h('div',{class:'kv',style:'display:flex;flex-wrap:wrap;gap:8px'},
  h('button',{style:'background:var(--hov);padding:6px 12px;border-radius:8px;font-weight:600;color:var(--acc)',text:ci&&ci.contact?'🗑 Remove Contact':'➕ Add Contact',on:{async click(){
   if(ci&&ci.contact){
    try{await api('/app/api/contact/delete',A({user:d.id}));ci.contact=false;toast('Contact removed');drawInfo()}catch(e){toast(e.message)}
   }else{
    const fn=h('input',{value:d.name,placeholder:'First name'}),ln=h('input',{placeholder:'Last name'}),ph=h('input',{value:(ci&&ci.phone)||'',placeholder:'Phone (optional)'});
    modal('Add Contact',[fn,ln,ph],[{t:'Cancel'},{t:'Save',c:'p',async fn(c){try{await api('/app/api/contact/add',A({user:d.id,first:fn.value,last:ln.value,phone:ph.value}));if(ci)ci.contact=true;toast('✅ Contact added');c();drawInfo()}catch(e){toast(e.message)}}}]);
   }
  }}}),
  h('button',{style:'background:var(--hov);padding:6px 12px;border-radius:8px;font-weight:600;color:'+(ci&&ci.blocked?'#10b981':'#ef4444'),text:ci&&ci.blocked?'✅ Unblock User':'🚫 Block User',on:{async click(){
   try{const r=await api('/app/api/user/block',A({user:d.id,block:!(ci&&ci.blocked)}));if(ci)ci.blocked=r.blocked;toast(r.blocked?'🚫 User blocked':'✅ User unblocked');drawInfo()}catch(e){toast(e.message)}
  }}})
 ):null;

 const tabs=[['media','Media'],['files','Files'],['links','Links'],['voice','Voice'],['music','Music'],['gifs','GIFs'],['analytics','📊 Stats']];
 if(d.type==='user'&&!d.self)tabs.push(['common','Common']);
 if(d.type!=='user')tabs.push(['members','Members']);
 if(d.type!=='user'&&(d.owner||d.admin))tabs.push(['manage','⚙ Manage']);

 p.replaceChildren(
  h('div',{class:'hd'},h('button',{class:'ib',text:'✕',on:{click:toggleInfo}}),h('b',{text:'Info'})),
  h('div',{class:'big'},avatar(d.id,d.name,106),h('b',{style:'font-size:19px;margin-top:8px;text-align:center',text:(d.verified?'✔ ':'')+(d.premium?'⭐ ':'')+d.name}),h('span',{class:'mut',text:(ci&&ci.status)||''})),
  kv('Bio / Description',ci&&ci.about),
  kv('Username',ci&&ci.username?'@'+ci.username:''),
  kv('Phone',ci&&ci.phone?'+'+ci.phone.replace(/^\+/,''):''),
  kv('Telegram ID',String(d.id)),
  userActs,
  h('div',{class:'kv'},h('div',{class:'sw'},h('span',{text:'Notifications'}),h('button',{class:'mut',text:d.muted?'🔇 Muted':'🔔 On',on:{async click(){await chatAct('mute',d,{mute:!d.muted});d.muted=!d.muted;drawInfo()}}}))),
  h('div',{class:'tabs',id:'itabs'},tabs.map(([k,n],idx)=>h('button',{class:idx===0?'on':'',text:n,on:{click(e){document.querySelectorAll('#itabs button').forEach(b=>b.classList.remove('on'));e.target.classList.add('on');loadShared(k)}}}))),
  h('div',{id:'ibody'})
 );
 loadShared('media');
}
async function loadShared(kind){const b=$('#ibody');b.replaceChildren(h('div',{class:'empty',text:'Loading…'}));try{
 if(kind==='manage'){drawManage(b);return}
 if(kind==='analytics'){drawAnalytics(b);return}
 if(kind==='common'){
  const cs=(await api('/app/api/user/common?'+Q({acc:S.acc,user:S.chat}))).chats;
  if(!cs.length){b.replaceChildren(h('div',{class:'empty',text:'No groups in common'}));return}
  b.replaceChildren(...cs.map(c=>dialogRow(c)));return;
 }
 if(kind==='members'){const r=(await api('/app/api/members?'+Q({acc:S.acc,chat:S.chat}))).members;b.replaceChildren(...r.map(u=>h('div',{class:'row',on:{click(){openByQuery(u.username?'@'+u.username:String(u.id))}}},avatar(u.id,u.name,40),h('div',{class:'rm'},h('b',{text:(u.bot?'🤖 ':'')+u.name}),h('div',{class:'mut',style:'font-size:13px',text:u.status||(u.username?'@'+u.username:'')})))));if(!r.length)b.replaceChildren(h('div',{class:'empty',text:'Not available'}));return}
 const ms=(await api('/app/api/shared?'+Q({acc:S.acc,chat:S.chat,kind}))).messages;if(!ms.length){b.replaceChildren(h('div',{class:'empty',text:'Nothing here'}));return}
 if(kind==='media'||kind==='gifs'){b.replaceChildren(h('div',{class:'grid'},ms.map(m=>h('div',{on:{click(){openById(S.chat,m.id)}}},S.set.previews?h('img',{alt:'',loading:'lazy',src:'/app/api/thumb?'+Q({acc:S.acc,chat:S.chat,id:m.id}),on:{error(e){e.target.remove()}}}):null))))}
 else b.replaceChildren(...ms.map(m=>h('div',{class:'kv',style:'cursor:pointer',on:{click(){openById(S.chat,m.id)}}},kind==='links'?h('div',{},m.web?h('b',{text:m.web.title||m.web.url}):null,h('div',{class:'mut',style:'font-size:13px',text:m.text.slice(0,120)})):h('div',{text:(m.media&&m.media.name)||(m.media&&m.media.kind)||'File'}),h('small',{text:tm(m.ts)+' · '+(m.media?sz(m.media.size):'')}))))}catch(e){b.replaceChildren(h('div',{class:'empty',text:e.message}))}}

/* ---------- group/channel management (owner/admin only) ---------- */
function drawManage(b){const d=S.cd,ci=S.ci&&S.ci.id===S.chat?S.ci:null;
 const t=h('input',{value:d.name,maxlength:128,placeholder:'Title'}),a=h('textarea',{rows:3,maxlength:255,placeholder:'Description'});a.value=(ci&&ci.about)||'';
 const saveBtn=h('button',{style:'color:var(--acc);font-weight:600;padding:4px 0',text:'💾 Save title / description',on:{async click(){if(!t.value.trim())return;try{await api('/app/api/chat/edit',A({chat:S.chat,title:t.value,about:a.value}));d.name=t.value;toast('✅ Saved');drawList();const nm=document.querySelector('#info .big b');if(nm)nm.textContent=t.value;const ct=document.querySelector('#ctt b');if(ct)ct.textContent=t.value}catch(e){toast(e.message)}}}});
 const phInp=h('input',{type:'file',accept:'image/*',hidden:true});
 phInp.onchange=async()=>{const f=phInp.files[0];if(!f)return;try{const buf=await ab(f);await api('/app/api/chat/photo?'+Q({acc:S.acc,chat:S.chat}),undefined,{raw:buf});toast('✅ Chat photo updated');drawList();drawInfo()}catch(e){toast(e.message)}};
 const phBtn=h('button',{style:'color:var(--acc);font-weight:600;padding:4px 0;display:block',text:'📷 Change Group / Channel Photo',on:{click(){phInp.click()}}});

 // Slow mode
 const slowSel=h('select',{},[[0,'Off'],[10,'10s'],[30,'30s'],[60,'1m'],[300,'5m'],[900,'15m'],[3600,'1h']].map(([v,l])=>h('option',{value:v,text:l})));
 slowSel.value=String((ci&&ci.slowmode)||0);
 slowSel.onchange=async()=>{try{await api('/app/api/chat/slowmode',A({chat:S.chat,seconds:+slowSel.value}));toast('🐢 Slow mode updated')}catch(e){toast(e.message)}};

 // Invite links
 const invBox=h('div',{});
 async function loadInvites(){
  try{
   const r=(await api('/app/api/chat/invites?'+Q({acc:S.acc,chat:S.chat}))).invites||[];
   invBox.replaceChildren(
    h('button',{style:'color:var(--acc);font-weight:600;margin-bottom:6px',text:'＋ Create Invite Link',on:{async click(){try{const res=await api('/app/api/chat/invite/create',A({chat:S.chat}));copyText(res.link);loadInvites()}catch(e){toast(e.message)}}}}),
    ...r.map(iv=>h('div',{class:'dl-card',style:'margin-bottom:4px;cursor:pointer',title:'Click to copy link',on:{click(){copyText(iv.link)}}},
     h('b',{style:'font-size:12.5px;color:var(--acc)',text:iv.link}),
     h('span',{class:'mut',style:'font-size:11.5px',text:`Used: ${iv.usage}`+(iv.limit?` / ${iv.limit}`:'')+(iv.permanent?' · Permanent':'')})
    ))
   );
  }catch(e){invBox.replaceChildren(h('div',{class:'mut',style:'font-size:12px',text:e.message}))}
 }
 loadInvites();

 const addIn=h('input',{placeholder:'@username / phone — add member or bot'}),memList=h('div',{});
 const addBtn=h('button',{style:'color:var(--acc);font-weight:600',text:'➕ Add',on:{async click(){if(!addIn.value.trim())return;try{const r=await api('/app/api/chat/member/add',A({chat:S.chat,q:addIn.value}));toast('✅ Added: '+r.name);addIn.value='';loadMembersMgr()}catch(e){toast(e.message)}}}});
 async function memberAct(k,u,promote){try{if(k==='admin')await api('/app/api/chat/member/admin',A({chat:S.chat,uid:u.id,promote}));else await api('/app/api/chat/member/kick',A({chat:S.chat,uid:u.id}));toast('✅ Updated');loadMembersMgr()}catch(e){toast(e.message)}}
 function memberMenu(e,u){e.stopPropagation();const r=e.currentTarget.getBoundingClientRect();showMenu(r.left-160,r.bottom,[
  {t:'🛡 Promote to admin',fn:()=>memberAct('admin',u,true)},{t:'Demote (remove admin)',fn:()=>memberAct('admin',u,false)},
  {t:'🚫 Remove from group',red:true,fn:()=>confirmBox('Remove '+u.name+' from this group?','Remove',()=>memberAct('kick',u),true)}])}
 async function loadMembersMgr(){memList.replaceChildren(h('div',{class:'empty',text:'Loading…'}));try{
  const r=(await api('/app/api/members?'+Q({acc:S.acc,chat:S.chat}))).members;
  memList.replaceChildren(...r.map(u=>h('div',{class:'row'},avatar(u.id,u.name,36),
   h('div',{class:'rm'},h('b',{text:(u.bot?'🤖 ':'')+u.name}),h('div',{class:'mut',style:'font-size:12px',text:u.username?'@'+u.username:''})),
   h('button',{class:'ib',text:'⋮',on:{click(e){memberMenu(e,u)}}}))));
  if(!r.length)memList.replaceChildren(h('div',{class:'empty',text:'No members'}))}catch(e){memList.replaceChildren(h('div',{class:'empty',text:e.message}))}}
 loadMembersMgr();
 const forumToggle=h('button',{class:'mut',text:'…'}),topicsBox=h('div',{});
 async function refreshTopics(){try{
  const r=(await api('/app/api/chat/topics?'+Q({acc:S.acc,chat:S.chat}))).topics;
  S.topics=r;drawTopicBar();
  forumToggle.textContent='🟢 Topics Enabled — Click to Disable';
  forumToggle.onclick=async()=>{try{await api('/app/api/chat/forum/toggle',A({chat:S.chat,enabled:false}));S.topics=[];drawTopicBar();toast('Topics disabled');refreshTopics()}catch(e){toast(e.message)}};
  const newT=h('input',{placeholder:'New topic title'});
  const newBtn=h('button',{style:'color:var(--acc);font-weight:600',text:'+ Create',on:{async click(){if(!newT.value.trim())return;try{await api('/app/api/chat/topic/create',A({chat:S.chat,title:newT.value}));newT.value='';toast('✅ Topic created');refreshTopics()}catch(e){toast(e.message)}}}});
  topicsBox.replaceChildren(h('div',{style:'display:flex;gap:8px;margin-bottom:8px'},newT,newBtn),
   ...r.map(tp=>h('div',{class:'kv',style:'display:flex;justify-content:space-between;align-items:center'},
    h('span',{text:(tp.closed?'🔒 ':'# ')+tp.title}),
    h('div',{style:'display:flex;gap:6px'},
     h('button',{class:'ib',text:tp.closed?'🔓':'🔒',title:tp.closed?'Reopen':'Close',on:{async click(){try{await api('/app/api/chat/topic/edit',A({chat:S.chat,topic_id:tp.id,closed:!tp.closed}));refreshTopics()}catch(e){toast(e.message)}}}}),
     h('button',{class:'ib',style:'color:#ef4444',text:'🗑',on:{click(){confirmBox('Delete topic "'+tp.title+'"?','Delete',async()=>{try{await api('/app/api/chat/topic/delete',A({chat:S.chat,topic_id:tp.id}));toast('🗑 Deleted');refreshTopics()}catch(e){toast(e.message)}},true)}}})
    ))));
 }catch(e){
  forumToggle.textContent='⚪ Enable Forum Topics';
  forumToggle.onclick=async()=>{try{await api('/app/api/chat/forum/toggle',A({chat:S.chat,enabled:true}));toast('✅ Topics enabled');refreshTopics()}catch(e2){toast(e2.message)}};
  topicsBox.replaceChildren(h('div',{class:'mut',style:'font-size:13px',text:e.message}))}}
 refreshTopics();
 b.replaceChildren(
  h('div',{class:'kv'},h('b',{text:'Title, Description & Photo'}),t,a,saveBtn,phBtn,phInp),
  d.type==='group'?h('div',{class:'kv'},h('div',{class:'sw'},h('b',{text:'🐢 Slow Mode'}),slowSel)):null,
  h('div',{class:'kv'},h('b',{text:'🔗 Invite Links'}),invBox),
  h('div',{class:'kv'},h('b',{text:'💬 Forum Topics'}),h('div',{style:'margin-bottom:8px'},forumToggle),topicsBox),
  h('div',{class:'kv'},h('b',{text:'👥 Members'}),h('div',{style:'display:flex;gap:8px;margin-bottom:8px'},addIn,addBtn),memList)
 )}

/* ---------- extras: save, jump to date, mark all read, shortcuts, multi-select actions ---------- */
async function saveMsg(m){const u=cur().uid;if(!u){toast('No account');return}try{await api('/app/api/forward',A({from_chat:S.chat,ids:[m.id],to_chat:u,hide_author:false}));toast('🔖 Saved to Saved Messages')}catch(e){toast(e.message)}}
async function markAll(){const L=[...S.dl,...(S.adl||[])].filter(d=>d.unread||d.mentions).slice(0,25);if(!L.length){toast('Nothing unread');return}toast('Safely marking '+L.length+' chats as read…',6000);let n=0;
 for(const d of L){try{await api('/app/api/read',A({chat:d.id,max_id:0}));d.unread=0;d.mentions=0;n++;drawList();await sleep(350)}catch(e){}}toast('✓ '+n+' chat(s) marked as read')}
$('#cdate').onclick=()=>{if(!S.chat)return;const dt=h('input',{type:'date'});dt.max=new Date().toISOString().slice(0,10);
 modal('Jump to date',[h('div',{class:'mut',text:'Shows the last message of that day (or the closest one before it).'}),dt],[{t:'Cancel'},{t:'Go',c:'p',async fn(c){if(!dt.value)return;const ts=Math.floor(new Date(dt.value+'T23:59:59').getTime()/1e3);
  try{const r=await api('/app/api/bydate?'+Q({acc:S.acc,chat:S.chat,ts}));c();if(!r.id){toast('No messages on or before that date');return}jumpTo(r.id)}catch(e){toast(e.message)}}}])};
$('#selcp').onclick=()=>{const t=S.msgs.filter(m=>S.sel.has(m.id)&&m.text).map(m=>m.text).join('\n\n');if(t)copyText(t);else toast('No text in the selection')};
$('#seldl').onclick=async()=>{const ms=S.msgs.filter(m=>S.sel.has(m.id)&&m.media&&m.media.kind!=='sticker');if(!ms.length){toast('No files in the selection');return}for(const m of ms){await downloadMsg(m);await sleep(700)}};
function stepChat(d){const i=S.dl.findIndex(x=>x.id===S.chat),n=S.dl[i+d];if(n)openChat(n)}
document.addEventListener('keydown',e=>{
 if(LB&&$('#lb').style.display!=='none'){if(e.key==='ArrowLeft')LB.step(-1);else if(e.key==='ArrowRight')LB.step(1)}
 if((e.ctrlKey||e.metaKey)&&e.key.toLowerCase()==='k'){e.preventDefault();$('#search').focus();$('#search').select()}
 else if(e.altKey&&(e.key==='ArrowUp'||e.key==='ArrowDown')){e.preventDefault();stepChat(e.key==='ArrowDown'?1:-1)}});

/* ---------- chat header menu / search ---------- */
$('#cmore').onclick=e=>{const d=S.cd;if(!d)return;const r=e.currentTarget.getBoundingClientRect();chatMenu(r.left-150,r.bottom,d)};
$('#back').onclick=()=>closeChatView(false);
$('#csr').onclick=()=>{const q=h('input',{placeholder:'Search in this chat…'}),res=h('div',{style:'max-height:50vh;overflow:auto'});let t;q.oninput=()=>{clearTimeout(t);t=setTimeout(async()=>{if(q.value.length<2)return;try{const r=(await api('/app/api/search?'+Q({acc:S.acc,q:q.value,chat:S.chat}))).messages;res.replaceChildren(...(r.length?r.map(m=>h('div',{class:'row',on:{click(){close();jumpTo(m.id)}}},h('div',{class:'rm'},h('div',{class:'r1'},h('b',{text:m.out?'You':m.from||''}),h('span',{class:'t',text:dayLabel(m.ts)+' '+tm(m.ts)})),h('div',{class:'mut',text:m.text||'📎 Media'})))):[h('div',{class:'empty',text:'No results'})]))}catch(e){res.replaceChildren(h('div',{class:'empty',text:e.message}))}},350)};const close=modal('🔍 Search in Chat',[q,res],[{t:'Close'}]);q.focus()};
let st2;function onSearch(){S.q=$('#search').value.trim();S.gsearch=[];S.gres=null;drawList();clearTimeout(st2);if(S.q.length<2)return;st2=setTimeout(async()=>{try{if(/^(@|https?:\/\/t\.me\/|\+\d{8,})/.test(S.q)){S.gres=await api('/app/api/resolve',A({q:S.q}))}else S.gsearch=(await api('/app/api/search?'+Q({acc:S.acc,q:S.q}))).messages}catch(e){S.gsearch=[]}drawList()},400)}
$('#search').oninput=onSearch;

/* ---------- drawer / modals ---------- */
const stBtn=(t,fn)=>h('button',{text:t,on:{click(){closeDrawer();fn()}}});
function askPw(cb){const p=h('input',{type:'password',placeholder:'Two-step verification password',autocomplete:'off'});modal('Cloud password',[h('div',{class:'mut',text:'This account has a cloud password:'}),p],[{t:'Cancel'},{t:'Continue',c:'p',fn(c){const v=p.value;c();cb(v)}}]);p.focus()}
async function loadStorage(){try{const r=await api('/app/api/storage/state'),j=JSON.stringify(r);if(j!==S.stj){S.stj=j;S.st=r;if($('#drawer').classList.contains('on'))openDrawer(true)}}catch(e){}}
function linkStorage(aid){const go=()=>doLink(aid);if(S.st&&S.st.ready)confirmBox('Storage scanning account will change and every group will be scanned again. Continue?','Continue',go);else go()}
async function doLink(aid,lid,pw){toast('Linking account to Storage…',9000);try{const r=await api('/app/api/storage/link',{acc:aid,link_id:lid,password:pw});if(r.need_password){askPw(p=>doLink(aid,r.link_id,p));return}toast(r.already?'Already used for Storage':'✅ Storage scanning account set: '+(r.me||''),5000);S.stj='';await loadState();loadStorage()}catch(e){toast(e.message,7000)}}
async function importStorage(lid,pw){toast('Adding the Storage account here…',9000);try{const r=pw?await api('/app/api/login/password',{login_id:lid,password:pw}):await api('/app/api/storage/import',{label:'Storage',login_id:lid});if(r.next==='password'){askPw(p=>importStorage(r.login_id||lid,p));return}await loadState();S.stj='';loadStorage();toast('✅ Account added: '+(r.name||''),5000)}catch(e){toast(e.message,7000)}}
function openDrawer(noRefresh){const dr=$('#dr'),c=cur(),st=S.st||{};dr.replaceChildren(
  h('div',{style:'padding:18px;background:var(--hov)'},avatar(c.uid,c.name||'?',64),h('b',{style:'display:block;margin-top:8px;font-size:16px',text:(c.premium?'⭐ ':'')+(c.label||c.name||'No account')}),h('span',{class:'mut',text:(c.phone||'')+(c.username?' · @'+c.username:'')})),
  ...S.accs.map(a=>h('div',{class:'ac'+(a.id===S.acc?' on':''),on:{click(){switchAcc(a.id);closeDrawer()}}},avatar(a.uid,a.name,34),h('div',{},h('b',{text:(a.premium?'⭐ ':'')+(a.label||a.name)}),h('div',{class:'mut',style:'font-size:12px',text:a.state+(a.storage?' · 📦 Storage scanning':'')})))),
  st.available?h('div',{class:'stb'},h('b',{text:'📦 Storage scanning account'}),h('div',{class:'mut',style:'font-size:13px;margin:2px 0 4px',text:st.ready?st.me:(st.error||'Not logged in — files uploaded by other bots can not be read')}),
   st.ready&&!st.linked?stBtn('＋ Show this account here (no new login)',()=>importStorage()):null,
   c.id&&!c.storage?stBtn('Use "'+(c.label||c.name)+'" for Storage scanning',()=>linkStorage(c.id)):null):null,
  h('div',{class:'it',on:{click(){closeDrawer();addAccount()}}},'＋','Add Telegram Account'),
  h('hr',{style:'border:0;border-top:1px solid var(--bd);width:100%'}),
  h('div',{class:'it',on:{click(){closeDrawer();installPwaModal()}}},'📲','Install App (Mobile APK / PC)'),
  h('div',{class:'it',on:{click(){closeDrawer();openDlManager()}}},'⚡','Downloads & Server Cache'),
  h('div',{class:'it',on:{click(){closeDrawer();openGhostAndAutoReply()}}},'👻','Ghost Vault & Auto-Reply'),
  h('div',{class:'it',on:{click(){closeDrawer();openCrossSearch()}}},'🌐','Cross-Account Search'),
  h('div',{class:'it',on:{click(){closeDrawer();if(c.uid)openById(c.uid,0)}}},'🔖','Saved Messages'),
  h('div',{class:'it',on:{click(){closeDrawer();newChat()}}},'👤','Contacts / New Chat / Group'),
  h('div',{class:'it',on:{click(){closeDrawer();S.archived=!S.archived;S.folder=S.archived?'archived':'all';drawFolders();drawList()}}},'🗄',S.archived?'Back to Main Chats':`Archived Chats (${(S.adl||[]).length})`),
  h('div',{class:'it',on:{click(){closeDrawer();markAll()}}},'✓','Mark All as Read'),
  h('div',{class:'it',on:{click(){closeDrawer();sessions()}}},'🛡','Active Devices / Sessions'),
  h('div',{class:'it',on:{click(){closeDrawer();profile()}}},'✎','Edit Profile & Photo'),
  h('div',{class:'it',on:{click(){closeDrawer();settings()}}},'⚙','Settings'),
  h('div',{class:'it',on:{click(){S.set.theme=document.documentElement.dataset.theme==='dark'?'light':'dark';saveSet()}}},'🌙','Toggle Night Mode'),
  h('div',{class:'mut',style:'padding:12px 18px;font-size:12px',text:S.bw?`📊 ${(S.bw.used/1048576).toFixed(0)} MB used`+(S.bw.limit?` / ${(S.bw.limit/1073741824).toFixed(1)} GB`:' (unlimited)')+` (${S.bw.month})`:''})
 );
 if(!$('#drawer').classList.contains('on'))pushNav('drawer');
 $('#drawer').classList.add('on');if(noRefresh!==true)loadStorage()}
function closeDrawer(){$('#drawer').classList.remove('on');ensureNavGuard()}$('#menu').onclick=openDrawer;$('#drawer').onclick=e=>{if(e.target.id==='drawer')closeDrawer()};
async function switchAcc(id){if(id===S.acc)return;S.acc=id;LS.set('acc',id);S.chat=null;S.cd=null;S.dl=[];S.adl=[];S.archived=false;S.folder='all';drawAccBar();showEmpty();drawList();await loadDialogs()}
function newChat(){const f=h('input',{placeholder:'@username, phone (+91…) or t.me/+invite link'}),q=h('input',{placeholder:'Search saved contacts…'}),list=h('div',{style:'max-height:36vh;overflow:auto'});let C=[];
 const draw=()=>list.replaceChildren(...C.filter(c=>c.name.toLowerCase().includes(q.value.toLowerCase())||(c.username&&c.username.toLowerCase().includes(q.value.toLowerCase()))).map(c=>h('div',{class:'row',on:{click(){close();openById(c.id,0)}}},avatar(c.id,c.name,38,c.online),h('div',{class:'rm'},h('b',{text:c.name}),h('div',{class:'mut',style:'font-size:13px',text:(c.username?'@'+c.username:'')+(c.phone?' · +'+c.phone:'')})))));
 const close=modal('New Message / Group / Channel',[f,h('div',{style:'display:flex;gap:8px'},h('button',{style:'color:var(--acc);font-weight:600',text:'Open Chat',on:{async click(){try{const r=await api('/app/api/resolve',A({q:f.value}));close();openChat(r.dialog)}catch(e){toast(e.message)}}}}),h('button',{style:'color:var(--acc);font-weight:600',text:'Join Group/Channel',on:{async click(){try{const r=await api('/app/api/join',A({q:f.value}));close();await loadDialogs();openChat(r.dialog)}catch(e){toast(e.message)}}}})),
  h('div',{style:'display:flex;gap:8px'},h('button',{style:'color:var(--acc);font-weight:600',text:'👥 New Group',on:{click(){close();newGroupOrChannel('group')}}}),h('button',{style:'color:var(--acc);font-weight:600',text:'📢 New Channel',on:{click(){close();newGroupOrChannel('channel')}}})),
  q,list],[{t:'Close'}]);
 q.oninput=draw;api('/app/api/contacts?'+Q({acc:S.acc})).then(r=>{C=r.contacts;draw()}).catch(e=>list.replaceChildren(h('div',{class:'empty',text:e.message})))}
function newGroupOrChannel(kind){const t=h('input',{placeholder:kind==='group'?'Group name':'Channel name',maxlength:128}),a=h('textarea',{rows:3,maxlength:255,placeholder:'Description (optional)'}),err=h('div',{class:'err'});
 modal(kind==='group'?'👥 New Group':'📢 New Channel',[t,a,err],[{t:'Cancel'},{t:'Create',c:'p',async fn(c){const title=t.value.trim();if(!title){err.textContent='नाव टाक';return}try{const r=await api('/app/api/chat/create',A({title,about:a.value,kind}));c();await loadDialogs();openChat(r.dialog)}catch(e){err.textContent=e.message}}}]);t.focus()}
$('#fab').onclick=newChat;

function settings(){const sw=(k,t)=>{const i=h('input',{type:'checkbox'});i.checked=!!S.set[k];i.onchange=async()=>{S.set[k]=i.checked;if(k==='notif'&&i.checked&&window.Notification&&Notification.permission!=='granted'){const p=await Notification.requestPermission();if(p!=='granted'){S.set.notif=false;i.checked=false;toast('Notifications blocked by the browser')}}saveSet();if(k==='avatars'||k==='previews'){drawList();drawMsgs('keep')}};return h('div',{class:'sw'},h('span',{text:t}),i)};
 const th=h('select',{style:'width:auto'},['auto','light','dark'].map(v=>h('option',{value:v,text:v[0].toUpperCase()+v.slice(1)})));th.value=S.set.theme;th.onchange=()=>{S.set.theme=th.value;saveSet()};
 const fsSel=h('select',{style:'width:auto'},[[13,'Small'],[15,'Normal'],[17,'Large'],[19,'Extra large']].map(([v,t])=>h('option',{value:v,text:t})));fsSel.value=String(S.set.fs||15);fsSel.onchange=()=>{S.set.fs=+fsSel.value;saveSet()};
 const dlBtn=h('button',{style:'color:var(--acc);font-weight:700;text-align:left;padding:6px 0',text:'⚡ Open Multi-Thread Download & Server Cache Settings',on:{click(){document.querySelector('.ov').remove();openDlManager()}}});
 modal('⚙ Settings',[
  h('div',{class:'sw'},h('span',{text:'Theme'}),th),
  h('div',{class:'sw'},h('span',{text:'Text size'}),fsSel),
  dlBtn,
  sw('avatars','Show profile photos'),
  sw('previews','Auto-load photo/video previews'),
  sw('notif','Desktop notifications'),
  sw('sound','Notification sound'),
  sw('enter','Enter sends message'),
  sw('md','Markdown formatting by default'),
  h('div',{class:'mut',style:'font-size:12.5px',text:'Shortcuts: Ctrl+K search · Alt+↑/↓ switch chat · Esc close · ←/→ in photo viewer · Double-click message to reply · Shift+click to select'})
 ],[{t:'Close'}])}

async function sessions(){const body=h('div',{},h('div',{class:'empty',text:'Loading…'}));const close=modal('🛡 Active Sessions / Devices',body,[{t:'Close'}]);try{const r=(await api('/app/api/sessions?'+Q({acc:S.acc}))).sessions;body.replaceChildren(...r.map(s=>h('div',{class:'dl-card'},h('b',{text:(s.current?'🟢 This server — ':'')+(s.app||s.device)}),h('div',{class:'mut',style:'font-size:13px',text:`${s.device} · ${s.platform} · ${s.ip} · ${s.country}`}),h('div',{class:'mut',style:'font-size:12px',text:'Last active '+new Date(s.active*1e3).toLocaleString()}),
  s.current?null:h('button',{style:'color:#ef4444;font-weight:600;text-align:left',text:'Terminate Session',on:{click(){confirmBox('Terminate this session?','Terminate',async()=>{try{await api('/app/api/sessions/terminate',A({hash:s.hash}));close();sessions()}catch(e){toast(e.message)}},true)}}}))))}catch(e){body.replaceChildren(h('div',{class:'err',text:e.message}))}}

async function profile(){
 const c=cur();let about='',uname=c.username||'';
 try{const inf=(await api('/app/api/chatinfo?'+Q({acc:S.acc,chat:c.uid}))).info;about=inf.about||'';uname=inf.username||uname}catch(e){}
 const parts=(c.name||'').split(' ');
 const f=h('input',{value:parts[0]||'',placeholder:'First name'}),l=h('input',{value:parts.slice(1).join(' '),placeholder:'Last name'}),u=h('input',{value:uname,placeholder:'Username (without @)'}),a=h('textarea',{rows:3,maxlength:70,placeholder:'Bio (max 70 chars)'});a.value=about;
 const phInp=h('input',{type:'file',accept:'image/*',hidden:true});
 phInp.onchange=async()=>{const file=phInp.files[0];if(!file)return;try{const buf=await ab(file);await api('/app/api/me/photo?'+Q({acc:S.acc}),undefined,{raw:buf});toast('✅ Profile photo updated')}catch(e){toast(e.message)}};
 const phBtn=h('button',{style:'color:var(--acc);font-weight:600;text-align:left',text:'📷 Upload New Profile Photo',on:{click(){phInp.click()}}});
 modal('✎ Edit Profile',[phBtn,phInp,f,l,u,a],[{t:'Cancel'},{t:'Save',c:'p',async fn(close){try{await api('/app/api/me',A({first:f.value,last:l.value,about:a.value,username:u.value!==uname?u.value:undefined}));close();await loadState();toast('✅ Profile saved')}catch(e){toast(e.message)}}}]);
}

/* ---------- account जोडा (Phone OTP + QR Login) ---------- */
function addAccount(){
 const body=h('div',{style:'display:flex;flex-direction:column;gap:8px'}),err=h('div',{class:'err'});
 let qrTimer=null;
 const close=modal('＋ Add Telegram Account',[body,err],[{t:'Cancel',fn(c){clearInterval(qrTimer);c()}}]);
 let LID=null;const say=t=>err.textContent=t||'';
 const lab=h('input',{placeholder:'Label (e.g. Personal / Work)'}),ph=h('input',{type:'tel',placeholder:'+919876543210'}),d1=h('input',{type:'radio',name:'api',value:'default'}),d2=h('input',{type:'radio',name:'api',value:'custom'}),aid=h('input',{placeholder:'api_id',style:'display:none'}),ah=h('input',{placeholder:'api_hash',style:'display:none'});
 d1.disabled=!S.defApi;(S.defApi?d1:d2).checked=true;const upd=()=>{const c=d2.checked;aid.style.display=ah.style.display=c?'block':'none'};d1.onchange=d2.onchange=upd;upd();
 const go=h('button',{class:'p',style:'background:var(--acc);color:#fff;border-radius:8px;padding:9px;font-weight:600',text:'📱 Send Login Code (Phone)',on:{async click(){say('Sending code…');try{const r=await api('/app/api/login/start',{label:lab.value,phone:ph.value,api:d2.checked?'custom':'default',api_id:aid.value,api_hash:ah.value});LID=r.login_id;say();step2()}catch(e){say(e.message)}}}});
 const qrBtn=h('button',{style:'background:var(--hov);color:var(--acc);border-radius:8px;padding:9px;font-weight:600',text:'📷 Login via Telegram QR / Link',on:{async click(){
  say('Generating QR login token…');
  try{
   const r=await api('/app/api/login/qr/start',{label:lab.value,api:d2.checked?'custom':'default',api_id:aid.value,api_hash:ah.value});
   LID=r.login_id;say();
   body.replaceChildren(
    h('div',{class:'mut',text:'Open this login link on a device where Telegram is logged in, or copy the tg:// login URL:'}),
    h('a',{href:r.url,style:'color:var(--acc);font-weight:700;word-break:break-all;padding:8px;background:var(--hov);border-radius:8px',text:r.url}),
    h('button',{style:'color:var(--acc);font-weight:600',text:'⧉ Copy tg:// login URL',on:{click(){copyText(r.url)}}}),
    h('div',{class:'mut',text:'⏳ Waiting for approval in Telegram app…'})
   );
   qrTimer=setInterval(async()=>{
    try{
     const chk=await api('/app/api/login/qr/check',{login_id:LID});
     if(chk.next==='password'){clearInterval(qrTimer);step3()}
     else if(chk.next==='done'){clearInterval(qrTimer);done(chk)}
    }catch(e){clearInterval(qrTimer);say(e.message)}
   },2000);
  }catch(e){say(e.message)}
 }}});
 body.append(lab,ph,h('label',{},d1,' Default API (server)'),h('label',{},d2,' Custom api_id / api_hash'),aid,ah,go,qrBtn);
 function step2(){const c=h('input',{placeholder:'5-digit Login Code',inputmode:'numeric',autocomplete:'off'}),b=h('button',{style:'background:var(--acc);color:#fff;border-radius:8px;padding:9px;font-weight:600',text:'Verify Code',on:{async click(){say('Verifying…');try{const r=await api('/app/api/login/code',{login_id:LID,code:c.value});if(r.next==='password')step3();else done(r)}catch(e){say(e.message)}}}});body.replaceChildren(h('div',{class:'mut',text:'Enter the code Telegram sent to your app/SMS:'}),c,b);c.focus()}
 function step3(){say();const p=h('input',{type:'password',placeholder:'Two-step verification password',autocomplete:'off'}),b=h('button',{style:'background:var(--acc);color:#fff;border-radius:8px;padding:9px;font-weight:600',text:'Unlock & Sign In',on:{async click(){say('Checking password…');try{done(await api('/app/api/login/password',{login_id:LID,password:p.value}))}catch(e){say(e.message)}}}});body.replaceChildren(h('div',{class:'mut',text:'Enter 2FA Cloud Password:'}),p,b);p.focus()}
 async function done(r){clearInterval(qrTimer);close();S.acc=r.account;LS.set('acc',r.account);await loadState();await loadDialogs();toast('✅ Account added: '+(r.name||''))}
}

/* ---------- live events / notifications ---------- */
function beep(){if(!S.set.sound)return;try{const C=window.AudioContext||window.webkitAudioContext;if(!C)return;const c=new C(),o=c.createOscillator(),g=c.createGain();o.frequency.value=880;g.gain.value=.05;o.connect(g);g.connect(c.destination);o.start();setTimeout(()=>{o.stop();c.close()},120)}catch(e){}}
function notify(e){
 const d=S.dl.find(x=>x.id===e.chat)||(S.adl||[]).find(x=>x.id===e.chat);
 if(d&&d.muted)return;
 if(document.hidden||e.chat!==S.chat){
  beep();
  if(S.set.notif&&window.Notification&&Notification.permission==='granted'){
   const title=(d&&d.name)||e.msg.from||'Telegram',opts={body:(e.msg.text||'📎 Media').slice(0,120),icon:'/app/icon-192.png',badge:'/app/icon-192.png',tag:'chat_'+e.chat,renotify:true};
   if(navigator.serviceWorker&&navigator.serviceWorker.ready){
    navigator.serviceWorker.ready.then(reg=>reg.showNotification(title,opts)).catch(()=>{try{new Notification(title,opts)}catch(_){}});
   }else{try{new Notification(title,opts)}catch(_){}}
  }
 }
}
let dt=null,lastDlgSync=0;
const soon=(needDialogs)=>{clearTimeout(dt);dt=setTimeout(()=>{const now=Date.now();if(needDialogs&&now-lastDlgSync>25000){lastDlgSync=now;loadDialogs()}},1500)};
function handleEvent(e){
 if(e.type==='dl'){
  const k=e.acc+':'+e.chat+':'+e.mid,st={state:e.state,done:e.done,contig:e.contig||0,size:e.total,speed:e.speed||0,threads:e.threads||S.dlCfg.threads,expires_in:e.expires_in||S.dlCfg.ttl,err:e.err||''};
  if(e.acc===S.acc&&String(e.chat)===String(S.chat))setDl(k,st);else{S.dls[k]=st;updateDlCounter()}
  return;
 }
 if(e.type==='up'){const U=S.uploads[e.uid];if(U){if(e.state==='sending'){U.state='sending';U.sent=e.sent}else if(e.state==='done')finishUpload(U,e.msg);else{U.state=e.state;U.err=e.err||''}drawUploads()}return}
 if(e.acc!==S.acc)return;
 if(e.type==='typing'){if(e.action){S.typing[e.chat]={action:e.action,who:e.who,exp:Date.now()+6500};setTimeout(drawList,6600)}else delete S.typing[e.chat];if(e.chat===S.chat){const d=S.cd;setStatus(e.action?(d&&d.type!=='user'&&e.who?e.who+' is ':'')+(e.action==='recording'?'recording…':'typing…'):(S.ci&&S.ci.status)||'')}drawList();return}
 if(e.type==='read'){if(e.chat===S.chat){S.readOut=Math.max(S.readOut,e.max_id||0);drawMsgs('keep')}return}
 const d=S.dl.find(x=>x.id===e.chat)||(S.adl||[]).find(x=>x.id===e.chat);
 if(e.type==='new'&&e.msg){if(d&&!e.msg.out){d.last=e.msg.text||'📎 Media';d.ts=e.msg.ts;if(e.chat!==S.chat||document.hidden)d.unread=(d.unread||0)+1}if(!e.msg.out)notify(e);drawList()}
 if(e.chat===S.chat){
  if(S.topic&&e.msg&&e.msg.topic&&e.msg.topic!==S.topic)return;
  const box=$('#msgs'),atEnd=box.scrollHeight-box.scrollTop-box.clientHeight<140;
  if(e.type==='new'&&e.msg){const ck0=S.acc+':'+e.chat+':0';if(S.mcache[ck0]&&!S.mcache[ck0].msgs.some(x=>x.id===e.msg.id))S.mcache[ck0].msgs.push(e.msg)}if(e.type==='new'&&e.msg&&!S.msgs.some(x=>x.id===e.msg.id)){S.msgs.push(e.msg);if(!e.msg.out&&!atEnd)S.newBelow=(S.newBelow||0)+1;drawMsgs(atEnd||e.msg.out?'bottom':'keep');if(atEnd&&!document.hidden)markRead()}
  else if(e.type==='edit'&&e.msg)replaceMsg(e.msg);else if(e.type==='del'){if(e.ghost){S.msgs.forEach(x=>{if((e.ids||[]).includes(x.id)&&!x.out)x._deleted=true});S.msgs=S.msgs.filter(x=>!((e.ids||[]).includes(x.id)&&x.out))}else{S.msgs=S.msgs.filter(x=>!(e.ids||[]).includes(x.id))}drawMsgs('keep')}
 }
 soon(!d&&e.type==='new')}
async function poll(){for(;;){try{const r=await api('/app/api/events?'+Q({since:S.seq,wait:20}));S.seq=r.seq;for(const e of r.events)handleEvent(e)}catch(e){await sleep(3500)}}}
document.addEventListener('keydown',e=>{if(e.key==='Escape'){closeMenu();closeFv();$('#lb').style.display='none';const ov=document.querySelector('.ov');if(ov)ov.remove();if(S.sel.size){S.sel.clear();selUpdate();drawMsgs('keep')}}});
document.addEventListener('visibilitychange',()=>{if(!document.hidden&&S.chat)markRead()});

/* ---------- 📲 Installable Mobile APK (WebAPK) & PC Desktop App (PWA) ---------- */
let deferredPrompt=null,pwaModalRefresh=null;
function isStandaloneApp(){
 return window.matchMedia('(display-mode: standalone)').matches||window.matchMedia('(display-mode: minimal-ui)').matches||!!window.navigator.standalone;
}
function drawPwaBanner(){
 const bn=$('#pwa-banner'),b=$('#pwa-btn');
 if(b)b.style.color=deferredPrompt?'#10b981':(isStandaloneApp()?'var(--acc)':'');
 if(!bn)return;
 if(isStandaloneApp()||LS.get('hide_pwa_bn',false)||!deferredPrompt){bn.style.display='none';return}
 bn.style.display='flex';
 bn.replaceChildren(
  h('span',{style:'font-weight:600;color:var(--fg)',text:'📲 Install TG Hub as a Real Mobile App (WebAPK)'}),
  h('div',{style:'display:flex;gap:6px;align-items:center'},
   h('button',{style:'background:#10b981;color:#fff;padding:4px 10px;border-radius:8px;font-weight:700;font-size:12px',text:'Install App',on:{async click(){
    if(!deferredPrompt){installPwaModal();return}
    deferredPrompt.prompt();
    const c=await deferredPrompt.userChoice;
    if(c&&c.outcome==='accepted'){deferredPrompt=null;drawPwaBanner();toast('✅ Installing real WebAPK app — check Home Screen in 5-10s!')}
   }}}),
   h('button',{class:'mut',style:'font-size:14px;padding:0 4px',text:'✕',on:{click(){LS.set('hide_pwa_bn',true);bn.style.display='none'}}})
  )
 );
}
if('serviceWorker' in navigator){
 navigator.serviceWorker.register('/app/sw.js',{scope:'/app/'}).then(()=>{
  if(pwaModalRefresh)pwaModalRefresh();
 }).catch(()=>{});
 navigator.serviceWorker.addEventListener('controllerchange',()=>{if(pwaModalRefresh)pwaModalRefresh()});
}
window.addEventListener('beforeinstallprompt',e=>{
 e.preventDefault();
 deferredPrompt=e;
 drawPwaBanner();
 if(pwaModalRefresh)pwaModalRefresh();
});
window.addEventListener('appinstalled',()=>{
 deferredPrompt=null;
 drawPwaBanner();
 if(pwaModalRefresh)pwaModalRefresh();
 toast('✅ Real Native App (WebAPK) installed on your device!');
});
$('#pwa-btn').onclick=()=>installPwaModal();
async function runPwaDiagnostics(diagBox){
 diagBox.replaceChildren(h('div',{class:'mut',style:'font-size:12.5px',text:'🔍 Running live PWA & WebAPK diagnostics…'}));
 const lines=[];
 const add=(ok,title,detail)=>lines.push(h('div',{style:'font-size:12px;padding:4px 0;border-bottom:1px solid var(--bd)'},h('b',{style:'color:'+(ok===true?'#10b981':(ok===false?'#ef4444':'#f59e0b')),text:(ok===true?'✔ ':ok===false?'✖ ':'⚠ ')+title+': '}),h('span',{text:detail})));
 // 1. URL & HTTPS check
 const isHttps=location.protocol==='https:'||location.hostname==='localhost'||location.hostname==='127.0.0.1';
 add(isHttps,'HTTPS / Secure Context',`${location.protocol}//${location.host}${location.pathname}`);
 const inScope=location.pathname.startsWith('/app');
 add(inScope,'URL Path in Scope',location.pathname+(location.pathname==='/app'?' (Tip: /app/ is preferred)':''));
 // 2. Service Worker check
 if('serviceWorker' in navigator){
  try{
   const reg=await navigator.serviceWorker.getRegistration('/app/');
   const ctrl=!!navigator.serviceWorker.controller;
   add(!!reg&&ctrl,'Service Worker',reg?(`Registered (scope: ${reg.scope}, state: ${(reg.active&&reg.active.state)||'installing'}, controller: ${ctrl?'YES':'NO — tap Reload button above'})`):'NOT registered');
  }catch(e){add(false,'Service Worker Error',e.message)}
 }else add(false,'Service Worker','Not supported by this browser');
 // 3. Manifest fetch & JSON parse check
 try{
  const mr=await fetch('/app/manifest.json?v=5',{cache:'no-store'});
  const mj=await mr.json();
  add(mr.ok&&!!mj.name,'Manifest (/app/manifest.json)',`HTTP ${mr.status} · name="${mj.name}" · short_name="${mj.short_name}" · start_url="${mj.start_url}" · icons=${(mj.icons||[]).length}`);
 }catch(e){add(false,'Manifest Fetch Failed',e.message)}
 // 4. Icon 192 & 512 bitmap decode check
 for(const sz0 of [192,512]){
  try{
   const dim=await new Promise((res,rej)=>{const im=new Image();im.onload=()=>res(`${im.naturalWidth}x${im.naturalHeight}`);im.onerror=()=>rej(new Error('Image decode failed'));im.src=`/app/icon-${sz0}.png?t=${Date.now()}`});
   add(dim===`${sz0}x${sz0}`,`Icon ${sz0}x${sz0} PNG`,dim+' valid PNG bitmap');
  }catch(e){add(false,`Icon ${sz0}x${sz0} PNG`,e.message)}
 }
 // 5. Check document.title & SVG icon conflict
 const svgIcon=document.querySelector('link[rel="icon"][type="image/svg+xml"]');
 add(!svgIcon,'Icon Tag Conflict Check',svgIcon?'WARNING: SVG icon tag found in <head> (causes WebAPK "Installing..." to fail!)':'Clean (PNG icons only)');
 add(!/\(\d+\)/.test(document.title),'App Title Check',`document.title = "${document.title}"`);
 // 6. beforeinstallprompt & Server-side WebAPK request log
 try{
  const dg=await api('/app/api/pwa/diag');
  const logs=dg.logs||[];
  const webapkHits=logs.filter(l=>l.is_webapk_server||l.path.includes('maskable')||l.path.includes('manifest.json'));
  const isReadyOrInstalled=isStandaloneApp()||!!deferredPrompt||webapkHits.length>0;
  add(isReadyOrInstalled,'Install Prompt / App Status',isStandaloneApp()?'Running as Standalone Native App (WebAPK)':(deferredPrompt?'READY (1-Click Native WebAPK prompt active)':(webapkHits.length?'Manifest & Maskable APK Icons fetched by Chrome Installer (App Installed / Ready!)':'Waiting for Chrome install check')));
  add(webapkHits.length>0,'WebAPK Installer & Manifest Hits',webapkHits.length?`Verified (${webapkHits.length} installer/manifest fetch hits recorded)`:`Waiting (${logs.length} hits)`);
  if(logs.length){
   lines.push(h('div',{style:'margin-top:6px;font-size:11px;font-family:monospace;background:var(--bg);padding:6px;border-radius:6px;max-height:110px;overflow:auto'},
    h('b',{style:'display:block;margin-bottom:3px;color:var(--acc)',text:'Recent Server PWA Requests:'}),
    ...logs.slice(0,10).map(l=>h('div',{text:`[${tm(l.ts)}] ${l.method} ${l.path} · ${(l.is_webapk_server||l.path.includes('maskable'))?'🤖 WebAPK Installer':'📱 Browser'}`}))
   ));
  }
 }catch(e){}
 diagBox.replaceChildren(...lines);
}
async function installPwaModal(){
 const box=h('div',{style:'display:flex;flex-direction:column;gap:10px'});
 const diagBox=h('div',{class:'dl-card'});
 const renderBox=()=>{
  const standalone=isStandaloneApp();
  const swCtrl=!!(navigator.serviceWorker&&navigator.serviceWorker.controller);
  const natBtn=deferredPrompt?h('button',{style:'background:#10b981;color:#fff;padding:11px 14px;border-radius:10px;font-weight:700;width:100%;font-size:14.5px;box-shadow:0 4px 14px rgba(16,185,129,.35)',text:'📲 Install Real App on Phone / PC Now (1-Click WebAPK)',on:{async click(){
   if(!deferredPrompt)return;
   deferredPrompt.prompt();
   const choice=await deferredPrompt.userChoice;
   if(choice&&choice.outcome==='accepted'){
    deferredPrompt=null;drawPwaBanner();renderBox();
    toast('⏳ Android WebAPK तयार करत आहे (5-15 सेकंद थांबा)…',8000);
    setTimeout(()=>{runPwaDiagnostics(diagBox)},6000);
   }
  }}}):null;
  const reloadSwBtn=(!standalone&&!swCtrl&&'serviceWorker' in navigator)?h('button',{style:'background:#f59e0b;color:#fff;padding:10px 14px;border-radius:10px;font-weight:700;width:100%',text:'🔄 Activate App Installer (1-Tap Reload)',on:{click(){location.replace('/app/')}}}):null;
  const fsBtn=h('button',{style:'background:var(--acc);color:#fff;padding:10px 14px;border-radius:10px;font-weight:700;width:100%',text:document.fullscreenElement?'🗗 Exit Fullscreen App Mode':'📱 Open Fullscreen App Mode Now',on:{click(){
   if(document.fullscreenElement){document.exitFullscreen().catch(()=>{})}
   else if(document.documentElement.requestFullscreen){document.documentElement.requestFullscreen().catch(e=>toast(e.message))}
   const ov=document.querySelector('.ov');if(ov)ov.remove();
  }}});
  box.replaceChildren(
   h('div',{class:'dl-card',style:'border:1px solid '+(standalone||deferredPrompt?'#10b981':'var(--bd)')},
    h('b',{style:'color:'+(standalone||deferredPrompt?'#10b981':'var(--acc)')+';font-size:13.5px',text:standalone?'✅ हे App तुमच्या फोनवर Standalone Native App म्हणून चालू आहे!':(deferredPrompt?'✅ 1-Click Native WebAPK Install तयार आहे! (वरचे हिरवे बटण दाबा)':(swCtrl?'ℹ️ खालील Live Diagnostic रिपोर्ट तपासा':'⏳ App Installer तयार होत आहे — खालील पिवळे Activate बटण दाबा'))}),
    h('div',{class:'mut',style:'font-size:12px'},`Status: HTTPS=${location.protocol==='https:'||location.hostname==='localhost'?'✔':'✖'} · ServiceWorker=${swCtrl?'✔ Active':'⏳ Ready on reload'} · Title="${document.title}"`)
   ),
   natBtn,
   reloadSwBtn,
   fsBtn,
   h('div',{style:'display:flex;justify-content:space-between;align-items:center'},
    h('b',{style:'font-size:13px;color:var(--acc)',text:'🔍 Live Install Issue & WebAPK Debugger'}),
    h('button',{style:'color:var(--acc);font-size:12px;font-weight:700',text:'🔄 Re-Test Now',on:{click(){runPwaDiagnostics(diagBox)}}})
   ),
   diagBox,
   h('div',{class:'dl-card'},
    h('b',{style:'color:#f59e0b;font-size:13px',text:'🛠 Chrome मध्ये स्वतःचा छुपे WebAPK Error कसा बघायचा?'}),
    h('div',{style:'font-size:12px;line-height:1.45',text:'Chrome च्या नवीन टॅबमध्ये खालील URL कॉपी करून पेस्ट करा — तिथे Chrome ने App का इन्स्टॉल केले नाही याचा अचूक Error दिसेल:'}),
    h('div',{style:'display:flex;gap:6px;margin-top:4px'},
     h('button',{style:'background:var(--bg);border:1px solid var(--bd);padding:6px 10px;border-radius:8px;font-size:12px;font-weight:600;flex:1',text:'⧉ Copy chrome://webapks',on:{click(){copyText('chrome://webapks')}}}),
     h('button',{style:'background:var(--bg);border:1px solid var(--bd);padding:6px 10px;border-radius:8px;font-size:12px;font-weight:600;flex:1',text:'⧉ Copy chrome://web-app-internals',on:{click(){copyText('chrome://web-app-internals')}}})
    )
   ),
   h('div',{class:'dl-card'},
    h('b',{style:'color:var(--acc);font-size:13px',text:'📱 "Installing..." अडकून नंतर गायब का होते? (3 कारणे व उपाय)'}),
    h('div',{style:'font-size:12px;line-height:1.5;white-space:pre-line',text:'1) जर आधी "Installing Telegram (422)..." दिसत असेल, तर त्याचा अर्थ तुमचा सर्व्हर अजून जुनाच कोड चालवत आहे (422 हा तुमच्या Unread मेसेजेसचा आकडा होता, जो जुन्या कोडमध्ये <title> मध्ये येत होता आणि SVG आयकॉनमुळे WebAPK फेल होत होते). हा नवीन कोड सर्व्हरवर Deploy करा!\n2) Redmi / Xiaomi / Vivo / Oppo / Realme फोनवर: Settings → Apps → Manage Apps → Chrome → Other Permissions → "Home screen shortcuts" आणि "Install unknown apps" दोन्ही Allow करा.\n3) Google Play Store मध्ये लॉगिन असावे आणि Play Protect चालू असावे (Android वर Chrome बॅकग्राउंडला Google Play मार्फतच WebAPK साइन आणि इन्स्टॉल करते).'}
    )
   )
  );
  runPwaDiagnostics(diagBox);
 };
 pwaModalRefresh=renderBox;
 renderBox();
 modal('📲 App Installer & Live Error Debugger',box,[{t:'Close',fn(c){pwaModalRefresh=null;c()}}]);
}

/* ---------- start ---------- */
(async()=>{applyTheme();if(window.matchMedia)try{matchMedia('(prefers-color-scheme: dark)').addEventListener('change',applyTheme)}catch(e){}
 $('#md').style.color=S.set.md?'var(--acc)':'';
 try{const s=await loadState();S.seq=s.seq||0;drawFolders();if(!S.accs.length){$('#list').replaceChildren(h('div',{class:'empty'},'No Telegram account connected yet.',h('br'),h('button',{style:'color:var(--acc);font-weight:700;margin-top:10px',text:'＋ Add Telegram Account',on:{click:addAccount}})))}else await loadDialogs();loadStorage()}
 catch(e){document.body.replaceChildren(h('div',{class:'empty',text:'✖ '+e.message}));return}
 poll();setInterval(()=>loadState().catch(()=>{}),60000);setInterval(()=>{if(Object.keys(S.typing).length)drawList()},3000)})();
</script></body></html>'''
