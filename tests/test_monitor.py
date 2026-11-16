import http.server, json, os, sys, tempfile, threading, time, unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
os.environ["MONITOR_ALLOW_PRIVATE"] = "1"          # fake servers live on 127.0.0.1
import monitor  # noqa: E402

HERMES = {"memory": {"used_mb": 200.0, "total_mb": 512.0, "percent": 39.1, "oom_kills": 2},
          "cpu": {"percent": 12.5, "limit_cores": 0.1, "cores_used": 0.0125},
          "disk": {"used_mb": 1500.0, "total_mb": 15360.0, "percent": 9.8},
          "uptime_seconds": 7300, "platform": "Koyeb", "startup": {"phase": "waiting"},
          "processes": [{"pid": "1", "command": "picoclaw", "rss_mb": 80.0, "cpu": 3.0},
                        {"pid": "2", "command": "python3 keep_alive.py", "rss_mb": 25.0, "cpu": 9.0}],
          "processes_cpu": [{"pid": "2", "command": "python3 keep_alive.py", "rss_mb": 25.0, "cpu": 9.0}],
          "alerts": ["OOM kill detected (+2 since this page server started)"]}
TS = {"time": "10:00:00", "uptime": 500, "cpus": 0.1, "c_cpu": 91.0, "c_mem": 400 * 1048576, "c_mem_limit": 512 * 1048576,
      "disk": 1024 * 1048576, "disk_total": 15360 * 1048576, "disk_at": 1.0, "src": "cgroup-v2", "ts": {"state": "Running"},
      "nproc": 9, "guard": True, "kills": [],
      "top_cpu": [{"pid": 5, "name": "node", "cpu": 80.0, "rss": 90 * 1048576, "cmd": "node x"}],
      "top_mem": [{"pid": 5, "name": "node", "cpu": 80.0, "rss": 90 * 1048576, "cmd": "node x"}], "alerts": ["CPU high: 91% of 0.1 vCPU"]}

HITS = {}


class Fake(http.server.BaseHTTPRequestHandler):
    routes = {}

    def log_message(self, *a):
        pass

    def do_GET(self):
        HITS[self.server.tag] = HITS.get(self.server.tag, 0) + 1
        r = self.server.routes.get(self.path)
        if r is None:
            body, ctype = b"ok", "text/plain"                          # like hermes-ts / hermes for unknown paths
        else:
            if callable(r):
                r = r()
            body, ctype = (json.dumps(r).encode(), "application/json") if not isinstance(r, str) else (r.encode(), "text/html")
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def fake(tag, routes):
    s = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Fake)
    s.daemon_threads = True
    s.tag, s.routes = tag, routes
    threading.Thread(target=s.serve_forever, daemon=True).start()
    return s, f"http://127.0.0.1:{s.server_address[1]}"


def call(path, method="GET"):
    out = {}
    def sr(status, headers):
        out["status"], out["headers"] = status, dict(headers)
    body = b"".join(monitor.application({"PATH_INFO": path, "REQUEST_METHOD": method}, sr))
    return out["status"], out["headers"], body


class MonitorTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        monitor.STORE_FILE = os.path.join(self.tmp, "servers.json")
        monitor._saved.update(loaded=False, list=[])
        monitor._state.clear()
        monitor._db[0] = None
        os.environ.pop("MONITOR_SERVERS", None)
        monitor._env_cache.update(raw=None, list=[])
        HITS.clear()
        self.fakes = []

    def tearDown(self):
        for s in self.fakes:
            s.shutdown()
        monitor._last_view[0] = 0.0

    def mk(self, tag, routes):
        s, url = fake(tag, routes)
        self.fakes.append(s)
        return url

    # ---- normalising
    def test_normalize_hermes_and_terminal_formats(self):
        h = monitor.normalize(HERMES)
        self.assertEqual((h["cpu"]["percent"], h["ram"]["total_mb"], h["disk"]["used_mb"], h["uptime_s"]), (12.5, 512.0, 1500.0, 7300))
        self.assertEqual(h["top_cpu"][0]["name"], "python3 keep_alive.py")
        self.assertEqual(h["top_mem"][0]["name"], "picoclaw")                       # sorted by RAM
        self.assertIn("Koyeb", h["chips"]); self.assertIn("restoring state…", h["chips"]); self.assertIn("OOM kills: 2", h["chips"])
        t = monitor.normalize(TS)
        self.assertEqual((t["cpu"]["percent"], t["ram"]["used_mb"], t["ram"]["percent"], t["disk"]["measured"]), (91.0, 400.0, 78.1, True))
        self.assertIn("Tailscale: Running", t["chips"]); self.assertIn("OOM guard: active", t["chips"])
        with self.assertRaises(ValueError):
            monitor.normalize({"hello": 1})

    def test_alerts_are_derived_when_the_server_sends_none(self):
        d = dict(HERMES); d.pop("alerts"); d["memory"] = dict(d["memory"], percent=90.0)
        self.assertTrue(any(a.startswith("RAM high") for a in monitor.normalize(d)["alerts"]))

    # ---- polling
    def test_poll_detects_both_formats_and_ignores_the_wrong_path(self):
        u_h = self.mk("h", {"/api/stats": HERMES, "/": "<html>Hermes Agent is Alive</html>"})
        u_t = self.mk("t", {"/api": TS})
        for name, url, kind in (("Hermes", u_h, "hermes"), ("Terminal", u_t, "ts")):
            monitor._poll_one({"name": name, "url": url})
            st = monitor._state[name]
            self.assertEqual((st["kind"], st["fail"]), (kind, 0), (name, st))
            self.assertEqual(monitor._status(st, time.time()), "up")
        before = dict(HITS)
        monitor._poll_one({"name": "Hermes", "url": u_h})                          # kind known now: ONE request only
        self.assertEqual(HITS["h"] - before["h"], 1)

    def test_warming_dead_and_garbage_servers(self):
        calls = []
        def ts():
            calls.append(1)
            return {"warming": True} if len(calls) == 1 else TS
        u_w = self.mk("w", {"/api": ts})
        u_g = self.mk("g", {"/api/stats": {"nothing": "useful"}, "/api": "<html>hi</html>"})
        monitor._poll_one({"name": "W", "url": u_w})
        self.assertEqual(monitor._status(monitor._state["W"], time.time()), "warming")
        monitor._poll_one({"name": "W", "url": u_w})
        self.assertEqual(monitor._status(monitor._state["W"], time.time()), "up")
        monitor._poll_one({"name": "G", "url": u_g}); self.assertEqual(monitor._status(monitor._state["G"], time.time()), "checking")
        monitor._poll_one({"name": "G", "url": u_g}); self.assertEqual(monitor._status(monitor._state["G"], time.time()), "down")
        self.assertIn("unknown stats format", monitor._state["G"]["err"])
        monitor._poll_one({"name": "D", "url": "http://127.0.0.1:1"})
        monitor._poll_one({"name": "D", "url": "http://127.0.0.1:1"})
        self.assertEqual(monitor._status(monitor._state["D"], time.time()), "down")

    def test_server_that_stops_answering_becomes_stale_then_down_but_keeps_last_numbers(self):
        u = self.mk("s", {"/api": TS})
        monitor._poll_one({"name": "S", "url": u})
        st = monitor._state["S"]
        self.assertEqual(monitor._status(st, st["ok_at"] + 25), "stale")
        st["fail"] = 2
        self.assertEqual(monitor._status(st, time.time()), "down")
        self.assertEqual(st["data"]["cpu"]["percent"], 91.0)

    def test_overview_contains_this_server_and_hides_urls(self):
        u = self.mk("o", {"/api": TS})
        os.environ["MONITOR_SERVERS"] = f"Terminal={u}"
        monitor._poll_one(monitor.servers()[0])
        d = monitor.overview()
        names = [c["name"] for c in d["servers"]]
        self.assertEqual(names, [monitor.SELF_NAME, "Terminal"])
        self.assertTrue(d["servers"][0]["self"] and d["servers"][0]["status"] == "up")
        self.assertEqual(d["servers"][1]["status"], "up")
        self.assertEqual(d["summary"]["total"], 2)
        self.assertNotIn("127.0.0.1", json.dumps(d))                                # URLs never leave the server
        self.assertGreaterEqual(d["summary"]["alerts"], 1)                          # the CPU alert of the terminal

    def test_background_poller_only_runs_while_the_page_is_open(self):
        u = self.mk("p", {"/api": TS})
        os.environ["MONITOR_SERVERS"] = f"P={u}"
        monitor.POLL_EVERY = 0.3
        monitor.start()
        time.sleep(1.5)
        self.assertEqual(HITS.get("p", 0), 0)                                       # nobody watching: zero requests
        monitor.touch()
        time.sleep(2.0)
        self.assertGreaterEqual(HITS.get("p", 0), 3)                                # watching: ~every 0.3 s
        n = HITS["p"]
        monitor._last_view[0] = time.time() - monitor.ACTIVE - 1                   # visitor left
        time.sleep(1.5)
        n2 = HITS["p"]
        time.sleep(1.5)
        self.assertEqual(HITS["p"], n2)                                             # idle again
        monitor.POLL_EVERY = 5.0

    # ---- server list
    def test_env_formats(self):
        os.environ["MONITOR_SERVERS"] = "A=https://a.example.com, B|https://b.example.com/api/stats\nhttps://c.example.com;bad=ftp://x;=https://q"
        got = {s["name"]: s["url"] for s in monitor.servers()}
        self.assertEqual(got, {"A": "https://a.example.com", "B": "https://b.example.com/api/stats", "c": "https://c.example.com"})

    def test_add_delete_persist_and_validate(self):
        ok, n = monitor.add_server("My Term", "https://t.example.com/")
        self.assertTrue(ok, n)
        self.assertEqual(json.load(open(monitor.STORE_FILE)), [{"name": "My Term", "url": "https://t.example.com"}])
        monitor._saved.update(loaded=False, list=[])                                # simulate a restart
        self.assertEqual([s["name"] for s in monitor.servers()], ["My Term"])
        self.assertFalse(monitor.add_server("x" * 40, "https://a.example.com")[0])  # name too long
        self.assertFalse(monitor.add_server("ok", "ftp://a.example.com")[0])
        self.assertFalse(monitor.add_server("ok", "https://user:pw@a.example.com")[0])
        self.assertFalse(monitor.add_server(monitor.SELF_NAME, "https://a.example.com")[0])
        self.assertTrue(monitor.del_server("my term"))
        self.assertFalse(monitor.del_server("my term"))
        self.assertEqual(monitor.servers(), [])

    def test_private_and_metadata_addresses_are_refused_in_production_mode(self):
        os.environ["MONITOR_ALLOW_PRIVATE"] = "0"
        try:
            for bad in ("http://127.0.0.1:8080", "http://localhost", "http://169.254.169.254/latest", "http://[::1]/"):
                self.assertFalse(monitor.check_url(bad)[0], bad)
            self.assertTrue(monitor.check_url("https://example.com")[0])
        finally:
            os.environ["MONITOR_ALLOW_PRIVATE"] = "1"

    def test_env_server_name_cannot_be_overridden_by_bot(self):
        os.environ["MONITOR_SERVERS"] = "A=https://a.example.com"
        ok, msg = monitor.add_server("a", "https://other.example.com")
        self.assertFalse(ok); self.assertIn("env", msg)

    def test_redirects_are_not_followed(self):
        class R(http.server.BaseHTTPRequestHandler):
            def log_message(self, *a): pass
            def do_GET(self):
                self.send_response(302); self.send_header("Location", "http://169.254.169.254/"); self.end_headers()
        s = http.server.ThreadingHTTPServer(("127.0.0.1", 0), R); s.daemon_threads = True
        threading.Thread(target=s.serve_forever, daemon=True).start(); self.fakes.append(s)
        monitor._poll_one({"name": "R", "url": f"http://127.0.0.1:{s.server_address[1]}"})
        self.assertEqual(monitor._state["R"]["fail"], 1)
        self.assertIn("302", monitor._state["R"]["err"])

    # ---- WSGI
    def test_routes(self):
        self.assertTrue(monitor.handles("/memory") and monitor.handles("/memory/api") and not monitor.handles("/memories"))
        st, h, body = call("/memory")
        self.assertEqual(st, "200 OK"); self.assertIn("text/html", h["Content-Type"]); self.assertIn(b"Server monitor", body)
        self.assertIn("no-store", h["Cache-Control"])
        st, h, body = call("/memory/api")
        d = json.loads(body); self.assertEqual(h["Content-Type"], "application/json"); self.assertEqual(d["servers"][0]["name"], monitor.SELF_NAME)
        self.assertEqual(call("/memory/nope")[0], "404 Not Found")
        self.assertTrue(call("/memory", "POST")[0].startswith("405"))
        self.assertEqual(call("/memory", "HEAD")[2], b"")


if __name__ == "__main__":
    unittest.main()
