"""live: the router against the real local Ollama and (if a key is set) the real Gemini free tier, through the real engine room: plain chat, true streaming, and function calling
(skipped if Ollama is not running). Uses a handful of tiny requests."""
import http.client, json, os, shutil, sys, tempfile, threading, urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
try:
    urllib.request.urlopen("http://127.0.0.1:11434/api/tags", timeout=3).read()
except Exception:
    print("SKIP: no Ollama"); sys.exit(0)
from agentdispatch.app import App
from agentdispatch.web import Server, make_handler, new_token
d = tempfile.mkdtemp(prefix="liver_")
TOOLS = [dict(type="function", function=dict(name="get_weather", description="Get the current weather for a city", parameters=dict(type="object", properties=dict(city=dict(type="string")), required=["city"])))]
try:
    app = App(d, with_index=False)
    app.router.cfg["check_vram"] = False
    srv = Server(("127.0.0.1", 0), make_handler(app))
    port = srv.server_address[1]
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    tok = new_token(d, "live-test")

    def post(body):
        c = http.client.HTTPConnection("127.0.0.1", port, timeout=240)
        c.request("POST", "/v1/chat/completions", json.dumps(body), {"Authorization": "Bearer " + tok, "Content-Type": "application/json"})
        r = c.getresponse()
        return r.status, r.read().decode()

    st, raw = post(dict(model="qwen3:1.7b", messages=[dict(role="user", content="Reply with exactly: pong")], max_tokens=40))
    body = json.loads(raw)
    assert st == 200, raw
    print("plain chat OK:", repr(body["choices"][0]["message"]["content"][:40]), body["usage"]["total_tokens"], "tokens via", body["agentdispatch"]["route"][0]["model"])
    st, raw = post(dict(model="qwen3:1.7b", stream=True, messages=[dict(role="user", content="Count from 1 to 8 separated by spaces.")], max_tokens=60))
    events = [l[6:] for l in raw.splitlines() if l.startswith("data: ")]
    pieces = [json.loads(e)["choices"][0]["delta"].get("content") for e in events[:-1]]
    pieces = [p for p in pieces if p]
    assert st == 200 and events[-1] == "[DONE]" and len(pieces) >= 3, (st, raw[:300])
    print("streaming OK: %d pieces, text %r" % (len(pieces), "".join(pieces)[:50]))
    st, raw = post(dict(model="qwen3:1.7b", tools=TOOLS, messages=[dict(role="user", content="What is the weather in Paris right now? Use the tool.")], max_tokens=200))
    msg = json.loads(raw)["choices"][0]["message"]
    assert st == 200 and msg.get("tool_calls"), raw[:400]
    print("local function calling OK:", msg["tool_calls"][0]["function"])
    if os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY"):
        st, raw = post(dict(model="auto", tools=TOOLS, messages=[dict(role="user", content="What is the weather in Paris right now? Use the tool.")]))
        r = json.loads(raw)
        tc = (r["choices"][0]["message"].get("tool_calls") or [None])[0]
        assert st == 200 and tc and tc["function"]["name"] == "get_weather", raw[:400]
        print("cloud function calling OK via", r["agentdispatch"]["route"][-1]["model"], tc["function"]["arguments"])
        st, raw = post(dict(model="auto-cloud", stream=True, messages=[dict(role="user", content="Say hello in five words.")]))
        events = [l[6:] for l in raw.splitlines() if l.startswith("data: ")]
        assert st == 200 and events[-1] == "[DONE]" and any(json.loads(e)["choices"][0]["delta"].get("content") for e in events[:-1]), raw[:300]
        print("cloud streaming OK")
    print("LIVE ROUTER OK")
    srv.shutdown(); app.board.close()
finally:
    shutil.rmtree(d, ignore_errors=True)
