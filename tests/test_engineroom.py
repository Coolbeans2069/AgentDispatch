"""Engine Room: plan limits, the Ollama monitor, agent auto-detection and the agency feed (no network, no GPU needed)"""
import json, os, sys, tempfile, time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
tmp = tempfile.mkdtemp(prefix="er_")
os.environ["ENGINEROOM_DATA"] = tmp
os.environ["ENGINEROOM_NO_LIVE_LIMITS"] = "1"
os.environ["ENGINEROOM_LIMITS_FILE"] = os.path.join(tmp, "limits.json")
os.environ["ENGINEROOM_PULL_AGENT_PATTERNS"] = "pull-agent-app"
os.environ["ENGINEROOM_PORT"] = "8899"
from engineroom import limits as L
from engineroom import ollamamon as O


def test_limits_view_reset_stale_and_unknown_cycle():
    now = time.time()
    L.FILE = os.path.join(tmp, "limits.json")
    L._cache.update(mtime=0, data={})
    L.set_limit("claude", [dict(label="5h", pct=40, resets_at=now + 3600), dict(label="week", pct=70, resets_at=now + 86400 * 3)])
    L.set_limit("pullagent", [dict(label="week", pct=34, resets_at=now + 86400 * 6, span_min=0)])          # cycle length unknown
    L.set_limit("grokcli", [dict(label="5h", pct=100, resets_at=None, span_min=0)])                          # reset unknown too
    snap = L.snapshot(now)
    c = snap["claude"]["windows"]
    assert c[0]["pct"] == 40 and 0.79 < c[0]["elapsed"] < 0.81 and c[0]["resets_in_min"] == 60
    assert snap["pullagent"]["windows"][0]["elapsed"] is None and snap["pullagent"]["windows"][0]["resets_in_min"] > 8000
    gc = snap["grokcli"]["windows"][0]
    assert gc["pct"] == 100 and gc["elapsed"] is None and gc["reset_unknown"] is True
    later = L.snapshot(now + 7200)                                                                          # the 5-hour window has reset: 0 %, a new window
    assert later["claude"]["windows"][0]["pct"] == 0 and later["claude"]["windows"][0]["reset"] is True
    assert later["pullagent"]["stale"] is False and L.snapshot(now + 3 * 86400)["pullagent"]["stale"] is True   # hand-typed weekly figure: good for a day
    assert L.snapshot(now + 3600)["claude"]["stale"] is True                                                # a pushed figure goes grey after 45 minutes
    assert L.set_limit("x", [dict(label="5h", pct=250, resets_at=now + 5)])["windows"][0]["pct"] == 100.0   # clamped


def test_ollama_log_parsing_duty_and_calls():
    log = os.path.join(tmp, "server.log")
    now = time.time()
    t = lambda s: time.strftime("%Y/%m/%d - %H:%M:%S", time.localtime(now - s))
    open(log, "w", encoding="utf-8").write("\n".join([
        '[GIN] %s | 200 |   12.5s |       127.0.0.1 | POST     "/api/chat"' % t(10),
        '[GIN] %s | 200 |  177.6ms |       127.0.0.1 | GET      "/api/tags"' % t(8),
        '[GIN] %s | 200 |  1m2.5s |       127.0.0.1 | POST     "/api/generate"' % t(5),
        '[GIN] %s | 200 |    3.1s |       127.0.0.1 | POST     "/v1/chat/completions"' % t(200),
        "unrelated line"]) + "\n")
    O.LOG = log
    O.OLLAMA = "http://127.0.0.1:9"                                 # nothing there: /api/ps is simply empty
    O._st.update(off=None, reqs=[], calls_t=0, calls=[])
    snap = O.snapshot()
    assert snap["req_hour"] == 3                                   # the GET and the junk are ignored
    assert snap["req_min"] == 2 and 0.9 <= snap["duty"] <= 1.0     # 12.5 s + 62.5 s of work inside the last minute
    O.record_call("test", "qwen3:1.7b", 20, 100, 1000)
    O._st["calls_t"] = 0
    s2 = O.snapshot()
    assert s2["gen_today"] == 100 and s2["calls_today"] == 1 and s2["now_tok_s"] == 100.0 and s2["max_tok_s"] >= 100.0
    assert os.path.isfile(os.path.join(tmp, "metrics", "ollama_calls.csv"))


def test_agent_clis_are_found_without_registering():
    from engineroom import app as E

    class P:
        def __init__(self, pid, name, cmd): self.info = dict(pid=pid, name=name, cmdline=cmd)

    class FakePsutil:
        @staticmethod
        def process_iter(_):
            return [P(1, "grok.exe", ["/opt/agents/grok.exe"]), P(2, "node", ["node", "/usr/lib/node_modules/@google/gemini-cli/dist/index.js"]),
                    P(3, "python", ["python", "-m", "engineroom", "--watch", "grok.exe"]), P(4, "pull-agent-app", ["pull-agent-app"]), P(5, "chrome", ["chrome"])]

    real = E.psutil
    E.psutil = FakePsutil
    E._auto_cache.update(t=0.0, pids=set())
    try:
        assert E.auto_registered() == {1, 2, 4}                                               # the Grok CLI, the Gemini CLI (node) and the pull agent; not the engine room itself, not chrome
        ag = {a["id"]: a["n"] for a in E.agents_snapshot()}
        assert ag == dict(claude=0, codex=0, gemini=1, grokcli=1, pullagent=1)                # what the 'connected' strip shows
    finally:
        E.psutil = real
        E._auto_cache.update(t=0.0, pids=set())


def test_agency_snapshot_and_agents_feed():
    import http.server, threading
    from engineroom import app as E

    class H(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            b = json.dumps(dict(ok=True, counts=dict(running=1), needs_you=[], agents=[], killed=False, auto_merge=False)).encode()
            self.send_response(200); self.send_header("Content-Type", "application/json"); self.end_headers(); self.wfile.write(b)

        def log_message(self, *a): pass
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    E.BROKER = "http://127.0.0.1:%d" % srv.server_address[1]
    E._agency_cache.update(t=0.0, data=None)
    assert E.agency_snapshot()["counts"] == {"running": 1}
    srv.shutdown(); srv.server_close()
    E.BROKER = "http://127.0.0.1:9"                                                         # nothing there: None, never an exception
    E._agency_cache.update(t=0.0, data=None)
    assert E.agency_snapshot() is None
    E._auto_cache.update(t=time.time(), pids=set(), counts=dict(claude=2, codex=0, gemini=0, grokcli=1, pullagent=0), cpu=dict(claude=12.5, codex=0.0, gemini=0.0, grokcli=0.5, pullagent=0.0))
    E.STATE["snap"] = dict(cpu=10, ram=20, gpus=[dict(util=5), dict(util=40)])
    feed = {a["id"]: a for a in E.agents_feed()}
    assert feed["claude"]["running"] and feed["claude"]["n"] == 2 and feed["claude"]["busy"] and not feed["grokcli"]["busy"] and feed["claude"]["cpu"] == 12.5 and not feed["codex"]["running"] and feed["grokcli"]["load"]["gpu_max"] == 40
    assert set(feed) == {"claude", "codex", "gemini", "grokcli", "pullagent"}


def test_config_comes_from_env_and_budget_math():
    from engineroom import app as E
    assert E.WORK == tmp and E.PORT == 8899 and E.PIDFILE.startswith(tmp)
    assert L.FILE.startswith(tmp)
    b0 = E.budget("gemini-3.5-flash-lite")
    assert b0["ok_to_send"] and b0["day_budget"] == 250
    for _ in range(12):
        E.llm_note("gemini", "gemini-3.5-flash-lite", 100)
    b1 = E.budget("gemini-3.5-flash-lite")
    assert b1["rpm_left"] == 0 and not b1["ok_to_send"]                                     # 80% of the 15 RPM cap is 12
    assert os.path.isfile(os.path.join(tmp, "gemini_usage.json"))
    o = E.budget("openrouter/free", "openrouter")
    assert o["backend"] == "openrouter" and o["day_budget"] == 25


if __name__ == "__main__":
    test_limits_view_reset_stale_and_unknown_cycle()
    print("ok limits")
    test_ollama_log_parsing_duty_and_calls()
    print("ok ollamamon")
    test_agent_clis_are_found_without_registering()
    print("ok auto-registration")
    test_agency_snapshot_and_agents_feed()
    print("ok agency feed")
    test_config_comes_from_env_and_budget_math()
    print("ok config and budget")
    print("ALL ENGINE-ROOM TESTS PASSED")
