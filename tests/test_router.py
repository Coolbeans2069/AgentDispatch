import http.client, json, os, shutil, sys, tempfile, threading, time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from agentdispatch.app import App
from agentdispatch.router import Router, RouteError, BOARD_NOTE
from agentdispatch.web import Server, make_handler, new_token

CALLS = dict(ollama=[], gemini=[], engine=[])
STATE = dict(ollama_fail=False, gemini_status=200, budget_ok=True, reply="Hello from the local model.", loaded=[])


class Fake(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _j(self, obj, code=200):
        b = json.dumps(obj).encode()
        self.send_response(code); self.send_header("Content-Type", "application/json"); self.send_header("Content-Length", str(len(b))); self.end_headers(); self.wfile.write(b)

    def do_GET(self):
        if self.path == "/api/tags":
            return self._j(dict(models=[dict(name="local-qwen3-4b:latest", size=2_000_000_000), dict(name="local-gemma3-27b:latest", size=16_000_000_000), dict(name="qwen2.5vl:7b", size=5_000_000_000)]))
        if self.path == "/api/ps":
            return self._j(dict(models=[dict(name=n) for n in STATE["loaded"]]))
        if self.path.startswith("/budget"):
            return self._j(dict(ok_to_send=STATE["budget_ok"]))
        self._j({}, 404)

    def _lines(self, lines, ctype):
        self.send_response(200); self.send_header("Content-Type", ctype); self.send_header("Connection", "close"); self.end_headers()
        for l in lines:
            self.wfile.write(l.encode()); self.wfile.flush()
        self.close_connection = True

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
        if self.path == "/api/show":
            if STATE.get("show_fail"):
                return self._j({}, 500)
            return self._j(dict(capabilities=["completion", "tools"] if "qwen3" in body["model"] else ["completion"]))
        if self.path == "/api/chat":
            CALLS["ollama"].append(body)
            if STATE["ollama_fail"]:
                return self._j(dict(error="boom"), 500)
            last = (body["messages"][-1].get("content") or "")
            if body.get("tools") and ("weather" in last.lower() or "two cities" in last.lower()) and body["messages"][-1]["role"] == "user":
                call = dict(function=dict(name="get_weather", arguments=dict(city="Paris")))
                if body.get("stream"):
                    if "two cities" in last:                                                  # two calls, each in its own stream chunk, as Ollama sends them
                        c2 = dict(function=dict(name="get_weather", arguments=dict(city="Sydney")))
                        return self._lines([json.dumps(dict(message=dict(role="assistant", content="", tool_calls=[call]), done=False)) + "\n",
                                            json.dumps(dict(message=dict(role="assistant", content="", tool_calls=[c2]), done=False)) + "\n",
                                            json.dumps(dict(message=dict(role="assistant", content=""), done=True, done_reason="stop", prompt_eval_count=9, eval_count=8, eval_duration=500_000_000)) + "\n"], "application/x-ndjson")
                    return self._lines([json.dumps(dict(message=dict(role="assistant", content="", tool_calls=[call]), done=False)) + "\n",
                                        json.dumps(dict(message=dict(role="assistant", content=""), done=True, done_reason="stop", prompt_eval_count=9, eval_count=4, eval_duration=500_000_000)) + "\n"], "application/x-ndjson")
                return self._j(dict(message=dict(role="assistant", content="", tool_calls=[call]), done=True, done_reason="stop", prompt_eval_count=9, eval_count=4, eval_duration=500_000_000))
            if body.get("stream"):
                parts = [STATE["reply"][i:i + 12] for i in range(0, len(STATE["reply"]), 12)]
                lines = [json.dumps(dict(message=dict(role="assistant", content=p_), done=False)) + "\n" for p_ in parts]
                lines.append(json.dumps(dict(message=dict(role="assistant", content=""), done=True, done_reason="stop", prompt_eval_count=12, eval_count=34, eval_duration=1_000_000_000)) + "\n")
                return self._lines(lines, "application/x-ndjson")
            return self._j(dict(message=dict(role="assistant", content=STATE["reply"]), done=True, done_reason="stop", prompt_eval_count=12, eval_count=34, eval_duration=1_000_000_000))
        if self.path == "/chat/completions":
            CALLS["gemini"].append(body)
            if STATE["gemini_status"] != 200:
                return self._j(dict(error="limit"), STATE["gemini_status"])
            if body.get("stream"):
                ev = lambda d: "data: " + json.dumps(d) + "\n\n"
                return self._lines([ev(dict(choices=[dict(index=0, delta=dict(role="assistant", content="Hello "), finish_reason=None)])),
                                    ev(dict(choices=[dict(index=0, delta=dict(content="from the cloud stream."), finish_reason=None)])),
                                    ev(dict(choices=[dict(index=0, delta={}, finish_reason="stop")], usage=dict(prompt_tokens=5, completion_tokens=6, total_tokens=11))), "data: [DONE]\n\n"], "text/event-stream")
            if body.get("tools") and "weather" in (body["messages"][-1].get("content") or "").lower() and body["messages"][-1]["role"] == "user":
                return self._j(dict(id="g2", object="chat.completion", created=1, model=body["model"], choices=[dict(index=0, message=dict(role="assistant", content=None,
                               tool_calls=[dict(id="call_g", type="function", function=dict(name="get_weather", arguments='{"city": "Paris"}'))]), finish_reason="tool_calls")], usage=dict(prompt_tokens=5, completion_tokens=6, total_tokens=11)))
            return self._j(dict(id="g1", object="chat.completion", created=1, model=body["model"], choices=[dict(index=0, message=dict(role="assistant", content="Hello from the cloud model."), finish_reason="stop")],
                                usage=dict(prompt_tokens=5, completion_tokens=6, total_tokens=11)))
        if self.path == "/llm":
            CALLS["engine"].append(body)
            return self._j(dict(ok=True))
        self._j({}, 404)


def start_fake():
    srv = ThreadingHTTPServer(("127.0.0.1", 0), Fake)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, "http://127.0.0.1:%d" % srv.server_address[1]


def reset():
    for k in CALLS:
        CALLS[k].clear()
    STATE.update(ollama_fail=False, gemini_status=200, budget_ok=True, reply="Hello from the local model.", loaded=["local-qwen3-4b:latest"])


def mk():
    fake, base = start_fake()
    d = tempfile.mkdtemp(prefix="router_")
    app = App(d, with_index=False)
    os.environ["GEMINI_API_KEY"] = "test-key-not-real"
    app.router = Router(app, dict(ollama_url=base, engine_room=base, gemini_url=base, check_vram=False))
    reset()
    return app, fake, d


def msg(text="Say hello"):
    return dict(messages=[dict(role="user", content=text)])


def test_auto_goes_local_first_and_reports():
    app, fake, d = mk()
    r = app.router.chat(dict(model="auto", **msg()), "claude-code")
    assert r["choices"][0]["message"]["content"] == "Hello from the local model." and r["usage"]["total_tokens"] == 46
    assert r["agentdispatch"]["route"][0]["backend"] == "ollama" and not CALLS["gemini"]
    sent = CALLS["ollama"][0]
    assert sent["model"] == "local-qwen3-4b:latest" and sent["think"] is False and sent["stream"] is False
    assert sent["messages"][0]["role"] == "system" and BOARD_NOTE[:20] in sent["messages"][0]["content"]       # the board note was injected
    assert CALLS["engine"][0]["backend"] == "ollama" and CALLS["engine"][0]["gen_tokens"] == 34 and CALLS["engine"][0]["ms"] == 1000
    log = [json.loads(l) for l in open(os.path.join(d, "router.jsonl"))]
    assert log[0]["actor"] == "claude-code" and log[0]["ok"] and log[0]["tokens"] == 46
    fake.shutdown(); app.board.close(); shutil.rmtree(d, ignore_errors=True)


def test_ladder_falls_to_cloud_then_respects_budget():
    app, fake, d = mk()
    STATE["ollama_fail"] = True
    r = app.router.chat(dict(model="auto", **msg()), "x")
    assert [t["backend"] for t in r["agentdispatch"]["route"]] == ["ollama", "gemini"] and r["choices"][0]["message"]["content"] == "Hello from the cloud model."
    assert CALLS["engine"][-1]["backend"] == "gemini" and CALLS["engine"][-1]["tokens"] == 11
    # the engine room says the free budget is spent: the cloud step is skipped, the bigger local model is tried (and also fails here) -> 502
    STATE["budget_ok"] = False
    try:
        app.router.chat(dict(model="auto", **msg()), "x")
        raise AssertionError("expected RouteError")
    except RouteError as e:
        assert e.status == 502 and [t["backend"] for t in e.tried] == ["ollama", "gemini", "ollama"] and "budget" in e.tried[1]["error"]
    # a 429 from Google is not retried: it simply moves on
    STATE.update(budget_ok=True, gemini_status=429, ollama_fail=False)
    r = app.router.chat(dict(model="auto-cloud", **msg()), "x")
    assert [t["backend"] for t in r["agentdispatch"]["route"]] == ["gemini", "ollama"] and len(CALLS["gemini"]) == 2
    fake.shutdown(); app.board.close(); shutil.rmtree(d, ignore_errors=True)


def test_explicit_models_images_json_and_long_prompts():
    app, fake, d = mk()
    r = app.router.chat(dict(model="local-gemma3-27b", **msg()), "x")
    assert r["agentdispatch"]["route"][0]["model"] == "local-gemma3-27b:latest" and CALLS["ollama"][-1]["messages"][0]["role"] == "user"      # no board note on explicit models
    r = app.router.chat(dict(model="gemini-3.8-flash", **msg()), "x")
    assert CALLS["gemini"][-1]["model"] == "gemini-3.8-flash"
    try:
        app.router.chat(dict(model="no-such-model", **msg()), "x")
        raise AssertionError("expected 404")
    except RouteError as e:
        assert e.status == 404
    img = dict(model="auto", messages=[dict(role="user", content=[dict(type="text", text="what is this"), dict(type="image_url", image_url=dict(url="data:image/png;base64,QUJD"))])])
    app.router.chat(img, "x")
    last = CALLS["ollama"][-1]
    assert last["model"] == "qwen2.5vl:7b" and last["messages"][-1]["images"] == ["QUJD"] and last["messages"][-1]["content"] == "what is this"
    app.router.chat(dict(model="auto", response_format=dict(type="json_object"), max_tokens=50, temperature=0.2, **msg()), "x")
    assert CALLS["ollama"][-1]["format"] == "json" and CALLS["ollama"][-1]["options"]["num_predict"] == 50 and CALLS["ollama"][-1]["options"]["temperature"] == 0.2
    n = len(CALLS["ollama"])
    r = app.router.chat(dict(model="auto", **msg("word " * 5000)), "x")                      # a long prompt goes to the big free cloud context first
    assert r["agentdispatch"]["route"][0]["backend"] == "gemini" and len(CALLS["ollama"]) == n
    fake.shutdown(); app.board.close(); shutil.rmtree(d, ignore_errors=True)


def test_board_line_is_posted_and_stripped_and_failures_are_reported():
    app, fake, d = mk()
    STATE["reply"] = "I could not finish.\nBOARD: Cannot parse the Ghidra output format"
    r = app.router.chat(dict(model="auto", **msg()), "pull-agent")
    assert "BOARD:" not in r["choices"][0]["message"]["content"] and r["choices"][0]["message"]["content"] == "I could not finish."
    pid = r["agentdispatch"]["board_posts"][0]["id"]
    t = app.board.trail(pid)["issue"]
    assert t["title"] == "Cannot parse the Ghidra output format" and t["author"] == "pull-agent via router" and "router" in t["tags"]
    r2 = app.router.chat(dict(model="auto", **msg()), "codex")                                 # the same problem again is linked, not posted twice
    assert r2["agentdispatch"]["board_posts"][0] == dict(id=pid, duplicate=True)
    # every backend down: 502, and the outage itself lands on the board once
    STATE.update(ollama_fail=True, gemini_status=500)
    for _ in range(2):
        try:
            app.router.chat(dict(model="auto", **msg()), "x")
        except RouteError as e:
            assert e.status == 502
    outage = [p for p in app.board.read()["posts"] if "outage" in p["tags"]]
    assert len(outage) == 1
    fake.shutdown(); app.board.close(); shutil.rmtree(d, ignore_errors=True)


def test_http_endpoints_auth_and_streaming():
    app, fake, d = mk()
    srv = Server(("127.0.0.1", 0), make_handler(app))
    port = srv.server_address[1]
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    tok = new_token(d, "openai-client")

    def call(method, path, body=None, token=tok):
        c = http.client.HTTPConnection("127.0.0.1", port, timeout=30)
        h = {"Content-Type": "application/json"}
        if token:
            h["Authorization"] = "Bearer " + token
        c.request(method, path, json.dumps(body) if body is not None else None, h)
        r = c.getresponse(); data = r.read(); c.close()
        return r.status, data
    assert call("GET", "/v1/models", token=None)[0] == 401 and call("POST", "/v1/chat/completions", dict(model="auto"), token=None)[0] == 401
    st, data = call("GET", "/v1/models")
    ids = [m["id"] for m in json.loads(data)["data"]]
    assert st == 200 and "auto" in ids and "local-qwen3-4b:latest" in ids
    st, data = call("POST", "/v1/chat/completions", dict(model="auto", **msg()))
    body = json.loads(data)
    assert st == 200 and body["object"] == "chat.completion" and body["choices"][0]["message"]["content"] == "Hello from the local model."
    assert call("POST", "/v1/chat/completions", dict(model="auto", messages=[]))[0] == 400
    assert call("POST", "/v1/chat/completions", dict(model="zzz", **msg()))[0] == 404
    st, data = call("POST", "/v1/chat/completions", dict(model="auto", stream=True, **msg()))
    events = [l[6:] for l in data.decode().splitlines() if l.startswith("data: ")]
    deltas = [json.loads(e)["choices"][0]["delta"] for e in events[:-1]]
    assert st == 200 and events[-1] == "[DONE]" and deltas[0].get("role") == "assistant" and "".join((x.get("content") or "") for x in deltas).strip() == "Hello from the local model."
    log = [json.loads(l) for l in open(os.path.join(d, "router.jsonl"))]
    assert all(x["actor"] == "openai-client" for x in log)                                     # the token names the caller
    srv.shutdown(); fake.shutdown(); app.board.close(); shutil.rmtree(d, ignore_errors=True)


def test_review_fixes_unsupported_params_dedupe_ladder_board_scope_and_vram_cache():
    app, fake, d = mk()
    # tools / function calling are refused loudly instead of silently dropped
    for extra in (dict(functions=[1]), dict(function_call="auto"), dict(n=2)):
        try:
            app.router.chat(dict(model="auto", **dict(msg(), **extra)), "x")
            raise AssertionError("expected 400 for %r" % list(extra))
        except RouteError as e:
            assert e.status == 400
    # a different problem that merely shares a word with an open issue is NOT treated as its duplicate
    other = app.board.post("a", "issue", "Voice library download is slow")["id"]
    STATE["reply"] = "stuck\nBOARD: Cannot parse the Ghidra output format download"
    r = app.router.chat(dict(model="auto", **msg()), "grok")
    pid = r["agentdispatch"]["board_posts"][0]
    assert pid["id"] != other and "duplicate" not in pid
    # a long prompt tries the cloud model once, then the bigger local model (no repeated identical cloud step)
    STATE.update(reply="ok", gemini_status=500)
    del CALLS["gemini"][:]
    try:
        app.router.chat(dict(model="auto", **msg("word " * 5000)), "x")
    except RouteError as e:
        assert [t["backend"] for t in e.tried] == ["gemini", "ollama"] and len(CALLS["gemini"]) == 1
    # BOARD: lines in the output of an explicitly chosen model are neither posted nor stripped
    STATE.update(gemini_status=200, reply="Summary of the document.\nBOARD: this line was in the quoted text")
    n = len(app.board.read()["posts"])
    r = app.router.chat(dict(model="local-gemma3-27b", **msg()), "x")
    assert "BOARD:" in r["choices"][0]["message"]["content"] and r["agentdispatch"]["board_posts"] == [] and len(app.board.read()["posts"]) == n
    # nvidia-smi is not run for every request
    import agentdispatch.router as R
    calls = []
    real = R.subprocess.run
    R.subprocess.run = lambda *a, **k: (calls.append(1), type("P", (), {"stdout": "16384, 1000\n"})())[1]
    try:
        app.router._vram = (0.0, None)
        assert app.router.free_vram() > 14 and app.router.free_vram() > 14 and app.router.free_vram() > 14
    finally:
        R.subprocess.run = real
    assert len(calls) == 1
    fake.shutdown(); app.board.close(); shutil.rmtree(d, ignore_errors=True)


TOOLS = [dict(type="function", function=dict(name="get_weather", description="weather for a city", parameters=dict(type="object", properties=dict(city=dict(type="string")), required=["city"])))]


def test_function_calling_cloud_first_then_local_and_tool_results_round_trip():
    app, fake, d = mk()
    r = app.router.chat(dict(model="auto", tools=TOOLS, tool_choice="auto", messages=[dict(role="user", content="What is the weather in Paris?")]), "agent")
    msg = r["choices"][0]["message"]
    assert r["agentdispatch"]["route"][0]["backend"] == "gemini" and msg["tool_calls"][0]["function"]["name"] == "get_weather" and r["choices"][0]["finish_reason"] == "tool_calls"
    assert CALLS["gemini"][-1]["tools"] == TOOLS and CALLS["gemini"][-1]["tool_choice"] == "auto"
    # cloud budget gone: the local model that advertises tools takes it; the OpenAI shape comes back (arguments as a JSON string, ids, finish_reason)
    STATE["budget_ok"] = False
    r = app.router.chat(dict(model="auto", tools=TOOLS, messages=[dict(role="user", content="What is the weather in Paris?")]), "agent")
    msg = r["choices"][0]["message"]
    assert r["agentdispatch"]["route"][-1]["backend"] == "ollama" and CALLS["ollama"][-1]["model"] == "local-qwen3-4b:latest" and CALLS["ollama"][-1]["tools"] == TOOLS
    tc = msg["tool_calls"][0]
    assert msg["content"] is None and tc["type"] == "function" and tc["id"].startswith("call_") and json.loads(tc["function"]["arguments"]) == {"city": "Paris"} and r["choices"][0]["finish_reason"] == "tool_calls"
    # the follow-up turn carries the assistant's tool call and the tool result: both are translated for Ollama
    follow = dict(model="auto", tools=TOOLS, messages=[dict(role="user", content="What is the weather in Paris?"), dict(role="assistant", content=None, tool_calls=[tc]),
                                                         dict(role="tool", tool_call_id=tc["id"], content="31 C and sunny")])
    r = app.router.chat(follow, "agent")
    sent = CALLS["ollama"][-1]["messages"]
    assert sent[-2]["role"] == "assistant" and sent[-2]["tool_calls"][0]["function"] == dict(name="get_weather", arguments=dict(city="Paris"))
    assert sent[-1] == dict(role="tool", content="31 C and sunny", tool_name="get_weather") and r["choices"][0]["message"]["content"] == "Hello from the local model."
    # an explicit local model without the tools capability is refused clearly; auto-local with no capable model too
    try:
        app.router.chat(dict(model="local-gemma3-27b", tools=TOOLS, messages=[dict(role="user", content="hi")]), "agent")
        raise AssertionError("gemma3 has no tools capability")
    except RouteError as e:
        assert e.status == 400 and "does not support tools" in str(e)
    fake.shutdown(); app.board.close(); shutil.rmtree(d, ignore_errors=True)


def test_board_filter_state_machine():
    from agentdispatch.router import BoardFilter

    def run(parts):
        f = BoardFilter()
        out = "".join(f.feed(p) for p in parts) + f.flush()
        return out, f.board
    assert run(["Hello world.\nBOARD: stuck on X\nBye"]) == ("Hello world.\nBye", ["stuck on X"])
    assert run(["Hel", "lo\nBO", "ARD: sp", "lit title\nmore"]) == ("Hello\nmore", ["split title"])                 # a tag split across chunks
    assert run(["a BOARD: not at the start of a line"]) == ("a BOARD: not at the start of a line", [])
    assert run(["BOARDING pass is ready"]) == ("BOARDING pass is ready", [])                                            # a word that merely starts with BOARD
    assert run(["  board: indented and lower case"]) == ("", ["indented and lower case"])
    assert run(["x\nBOARD:"]) == ("x\n", [])
    assert run(["Boa"]) == ("Boa", []) and run(["B\nok"]) == ("B\nok", [])
    assert BoardFilter().feed("A long line without any newline that must flow through at once").startswith("A long line")


def sse(app_resp_lines):
    return [json.loads(l[6:]) for l in app_resp_lines if l.startswith("data: ") and l.strip() != "data: [DONE]"]


def test_real_streaming_ollama_and_gemini_filter_tools_and_fallback():
    app, fake, d = mk()
    # Ollama: pieces are forwarded as they arrive; the BOARD: line is held back, posted, and never streamed
    STATE["reply"] = "The first part of the answer is here.\nBOARD: Cannot reach the render queue"
    gen = app.router.chat_stream(dict(model="auto", stream=True, **msg()), "streamer")
    lines = list("".join(gen).split("\n\n"))
    chunks = sse([l for l in lines if l])
    text = "".join((c["choices"][0]["delta"].get("content") or "") for c in chunks)
    assert chunks[0]["choices"][0]["delta"]["role"] == "assistant" and text.strip() == "The first part of the answer is here." and "BOARD" not in text
    assert chunks[-1]["choices"][0]["finish_reason"] == "stop" and chunks[-1]["usage"]["total_tokens"] == 46 and "data: [DONE]" in lines
    assert sum(1 for c in chunks if c["choices"][0]["delta"].get("content")) >= 3                      # really several pieces, not one block
    posted = app.board.read()["posts"]
    assert [p_["title"] for p_ in posted] == ["Cannot reach the render queue"] and posted[0]["author"] == "streamer via router"
    assert CALLS["engine"][-1]["backend"] == "ollama" and CALLS["engine"][-1]["gen_tokens"] == 34
    # a streamed tool call
    STATE["budget_ok"] = False
    gen = app.router.chat_stream(dict(model="auto", stream=True, tools=TOOLS, messages=[dict(role="user", content="weather in Paris?")]), "streamer")
    chunks = sse([l for l in "".join(gen).split("\n\n") if l])
    calls = [c["choices"][0]["delta"]["tool_calls"][0] for c in chunks if c["choices"][0]["delta"].get("tool_calls")]
    assert calls[0]["function"]["name"] == "get_weather" and json.loads(calls[0]["function"]["arguments"]) == {"city": "Paris"} and chunks[-1]["choices"][0]["finish_reason"] == "tool_calls"
    # Gemini streaming is forwarded; Ollama failing to OPEN falls through to the cloud before any output
    STATE.update(budget_ok=True, ollama_fail=True)
    gen = app.router.chat_stream(dict(model="auto", stream=True, **msg()), "streamer")
    chunks = sse([l for l in "".join(gen).split("\n\n") if l])
    assert "".join((c["choices"][0]["delta"].get("content") or "") for c in chunks).strip() == "Hello from the cloud stream." and CALLS["gemini"][-1]["stream"] is True
    assert CALLS["gemini"][-1]["stream_options"] == dict(include_usage=True) and CALLS["engine"][-1] == dict(backend="gemini", who="agentdispatch-router", model="gemini-3.5-flash-lite", tokens=11)
    # everything down: RouteError before any output
    STATE.update(gemini_status=500)
    try:
        app.router.chat_stream(dict(model="auto", stream=True, **msg()), "streamer")
        raise AssertionError("expected RouteError")
    except RouteError as e:
        assert e.status == 502
    fake.shutdown(); app.board.close(); shutil.rmtree(d, ignore_errors=True)


def test_second_review_stream_indexes_tools_cache_busy_slot_and_log():
    app, fake, d = mk()
    # two tool calls streamed in separate chunks get indexes 0 and 1 (clients merge deltas by index)
    STATE["budget_ok"] = False
    gen = app.router.chat_stream(dict(model="auto", stream=True, tools=TOOLS, messages=[dict(role="user", content="two cities please: weather in Paris and Sydney")]), "s")
    chunks = sse([l for l in "".join(gen).split("\n\n") if l])
    idx = [tc["index"] for c in chunks for tc in (c["choices"][0]["delta"].get("tool_calls") or [])]
    names = [json.loads(tc["function"]["arguments"])["city"] for c in chunks for tc in (c["choices"][0]["delta"].get("tool_calls") or [])]
    assert idx == [0, 1] and names == ["Paris", "Sydney"]
    # a failed /api/show is not remembered: the model is judged again next time
    app.router._tools_cap.clear()
    STATE["show_fail"] = True
    assert app.router.supports_tools("local-qwen3-4b:latest") is False and "local-qwen3-4b:latest" not in app.router._tools_cap
    STATE["show_fail"] = False
    assert app.router.supports_tools("local-qwen3-4b:latest") is True
    # the local slot is held by someone else: the request moves on to the cloud instead of waiting
    STATE["budget_ok"] = True
    app.router.cfg["local_wait_s"] = 0.2
    app.router.sem.acquire()
    try:
        t0 = time.time()
        r = app.router.chat(dict(model="auto-local", **msg()), "x") if False else app.router.chat(dict(model="auto", **msg()), "x")
        assert time.time() - t0 < 5 and [t["backend"] for t in r["agentdispatch"]["route"]] == ["ollama", "gemini"] and "busy" in r["agentdispatch"]["route"][0]["error"]
    finally:
        app.router.sem.release()
    # a streamed BOARD: post is recorded in the router log
    STATE["reply"] = "answer\nBOARD: Something is blocked for the stream"
    "".join(app.router.chat_stream(dict(model="auto", stream=True, **msg()), "logger"))
    log = [json.loads(l) for l in open(os.path.join(d, "router.jsonl"))]
    assert log[-1]["board_posts"][0]["id"].startswith("P-")
    fake.shutdown(); app.board.close(); shutil.rmtree(d, ignore_errors=True)


if __name__ == "__main__":
    for n, f in sorted(globals().items()):
        if n.startswith("test_") and callable(f):
            f(); print("ok", n)
    print("ALL ROUTER TESTS PASSED")
