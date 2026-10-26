import http.server, json, os, sys, threading, time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import agentdispatch.toolindex.llm as L
from agentdispatch.orchestrator import LLMUnavailable

def hinter(**kw):
    kw.setdefault("sleep", lambda s: None)
    return L.GemmaHinter(rpm=10 ** 6, tpm=10 ** 9, **kw)                         # pacing is tested separately with a fake clock


SEEN = []
SCRIPT = {}                                   # model -> list of (status, body) to return in turn


class H(http.server.BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _j(self, code, obj):
        b = json.dumps(obj).encode()
        self.send_response(code); self.send_header("Content-Type", "application/json"); self.send_header("Content-Length", str(len(b))); self.end_headers(); self.wfile.write(b)

    def do_GET(self):
        self._j(200, dict(ok_to_send=not SCRIPT.get("budget_off")))

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
        if self.path == "/llm":
            SEEN.append(("llm", body)); return self._j(200, dict(ok=True))
        model = self.path.split("/models/")[1].split(":")[0]
        SEEN.append((model, body, self.headers.get("x-goog-api-key"), self.headers.get("Authorization")))
        q = SCRIPT.get(model) or []
        status, out = q.pop(0) if q else (200, dict(candidates=[dict(content=dict(parts=[dict(text="ok")]))], usageMetadata=dict(totalTokenCount=100)))
        self._j(status, out)


def ok(text, tokens=100, thought=None):
    parts = ([dict(text=thought, thought=True)] if thought else []) + [dict(text=text)]
    return 200, dict(candidates=[dict(content=dict(parts=parts))], usageMetadata=dict(totalTokenCount=tokens))


def setup():
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = "http://127.0.0.1:%d" % srv.server_address[1]
    L.ENGINE_ROOM = base
    L.GEMINI_NATIVE = base + "/v1beta/models/%s:generateContent"
    os.environ["GEMINI_API_KEY"] = "fake-key"
    del SEEN[:]
    SCRIPT.clear()
    return srv


def test_request_shape_thought_parts_and_usage_report():
    srv = setup()
    SCRIPT["gemma-4-26b-a4b-it"] = [ok('{"tools": []}', 321, thought="private reasoning")]
    h = hinter()
    out = h.ask("You are strict.", "Group these tools.")
    assert out == '{"tools": []}'                                                    # the thought part is not returned
    model, body, xkey, auth = next(x for x in SEEN if x[0] != "llm")
    assert model == "gemma-4-26b-a4b-it" and xkey == "fake-key" and auth is None     # key in x-goog-api-key only (a Bearer header makes the native API answer 401)
    assert "systemInstruction" not in body and body["contents"][0]["parts"][0]["text"].startswith("You are strict.\n\nGroup these tools.")      # no system role: folded into the user message
    assert body["generationConfig"]["responseMimeType"] == "application/json"
    rep = [x[1] for x in SEEN if x[0] == "llm"][0]
    assert rep["backend"] == "gemini" and rep["model"] == "gemma-4-26b-a4b-it" and rep["tokens"] == 321 and rep["who"] == "agentdispatch-index"
    srv.shutdown()


def test_retries_then_next_model_then_gives_up():
    srv = setup()
    sleeps = []
    SCRIPT["gemma-4-26b-a4b-it"] = [(500, {}), (503, {}), ok("answer after two errors")]
    assert hinter(sleep=sleeps.append).ask("", "u") == "answer after two errors" and sleeps[:2] == [2, 6]
    rep = hinter().ask("", "u")                                                        # the answer remembers which model wrote it
    assert getattr(rep, "model", None) == "gemma-4-26b-a4b-it"
    SCRIPT["gemma-4-26b-a4b-it"] = [(500, {})] * 5                                    # the first model keeps failing: the second one is used
    SCRIPT["gemma-4-31b-it"] = [ok("from the 31B")]
    assert hinter().ask("", "u") == "from the 31B"
    SCRIPT["gemma-4-26b-a4b-it"] = [(500, {})] * 5
    SCRIPT["gemma-4-31b-it"] = [(500, {})] * 5
    try:
        hinter().ask("", "u")
        raise AssertionError("expected LLMUnavailable")
    except LLMUnavailable as e:
        assert "HTTP 500" in str(e)
    SCRIPT["gemma-4-26b-a4b-it"] = [(400, {})]                                        # a 400 is not retried, the next model is tried
    SCRIPT["gemma-4-31b-it"] = [ok("second")]
    n = len(SEEN)
    assert hinter().ask("", "u") == "second" and len([x for x in SEEN[n:] if x[0] == "gemma-4-26b-a4b-it"]) == 1
    srv.shutdown()


def test_budget_off_pacing_and_missing_key():
    srv = setup()
    SCRIPT["budget_off"] = True
    try:
        hinter().ask("", "u")
        raise AssertionError("budget is off")
    except LLMUnavailable as e:
        assert "budget" in str(e)
    assert not [x for x in SEEN if x[0] != "llm"]                                     # no request was sent while the engine room said wait
    SCRIPT.clear()
    clock = [1000.0]
    waits = []

    def sleep(s):
        waits.append(s); clock[0] += s
    h = L.GemmaHinter(rpm=3, tpm=100000, sleep=sleep, now=lambda: clock[0])
    for _ in range(5):
        h.ask("", "u")
    assert len(waits) >= 1 and max(waits) > 1                                          # the 4th request within a minute had to wait (3 requests per minute here)
    del os.environ["GEMINI_API_KEY"]
    os.environ.pop("GOOGLE_API_KEY", None)
    try:
        L.GemmaHinter().ask("", "u")
        raise AssertionError("no key")
    except LLMUnavailable as e:
        assert "key" in str(e)
    srv.shutdown()


def test_failed_attempts_give_their_pacing_reservation_back():
    srv = setup()
    clock = [1000.0]
    sleeps = []

    def sleep(s):
        sleeps.append(s); clock[0] += s
    SCRIPT["gemma-4-26b-a4b-it"] = [(500, {}), (500, {}), ok("fine", 900)]
    h = L.GemmaHinter(rpm=100, tpm=7000, sleep=sleep, now=lambda: clock[0])             # room for ONE estimated request (about 5,800 tokens) per minute
    assert h.ask("", "u") == "fine"
    assert sleeps == [2, 6]                                                              # only the error backoffs: no pacing wait for reservations the failed requests never used
    assert [e[1] for e in h.window] == [900]                                             # and the good one is booked at its real size, not the estimate
    srv.shutdown()


if __name__ == "__main__":
    for n, f in sorted(globals().items()):
        if n.startswith("test_") and callable(f):
            f(); print("ok", n)
    print("ALL GEMMA HINTER TESTS PASSED")
