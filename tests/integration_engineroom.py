"""integration between the two components: agentdispatch asks the engine room for Gemini budgets and reports its Ollama calls through engineroom's local API.
Starts the engine room (engineroom/ in this repo) on a spare port with a scratch data folder. Not part of run_all.py because it opens a local port; run it directly."""
import json, os, subprocess, sys, tempfile, time, urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if not os.path.isfile(os.path.join(ROOT, "engineroom", "app.py")):
    print("SKIP: engineroom is not in this checkout")
    sys.exit(0)
PORT = 8897
scratch = tempfile.mkdtemp(prefix="er_int_")
env = dict(os.environ, ENGINEROOM_PORT=str(PORT), ENGINEROOM_DATA=scratch, AD_LIMITS_NO_LIVE="1", ENGINEROOM_LIMITS_FILE=os.path.join(scratch, "limits.json"), ENGINEROOM_URL="http://127.0.0.1:%d" % PORT)
p = subprocess.Popen([sys.executable, "-m", "engineroom", "--no-window"], cwd=ROOT, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
base = "http://127.0.0.1:%d" % PORT


def get(path):
    with urllib.request.urlopen(base + path, timeout=10) as r:
        return json.loads(r.read())


try:
    for _ in range(60):
        try:
            get("/budget?model=gemini-3.5-flash-lite")
            break
        except Exception:
            time.sleep(0.5)
    b = get("/budget?model=gemini-3.5-flash-lite")
    assert b["ok_to_send"] is True and b["day_budget"] == 250 and 0 < b["rpd_left"] <= 250, b   # half of the 500 free requests a day (the engine room keeps its day count in its own folder, so a real count may already be in it)
    # the broker's orchestrator asks for the budget and reports through the same API (no key in this test, so it stops before the network call)
    sys.path.insert(0, ROOT)
    os.environ.pop("GEMINI_API_KEY", None); os.environ.pop("GOOGLE_API_KEY", None)
    from agentdispatch.orchestrator import gemini_llm, LLMUnavailable
    try:
        gemini_llm(engine_room=base)("s", "u")
        raise AssertionError("should not call Gemini without a key")
    except LLMUnavailable:
        pass
    # a broker-side Ollama call reported to the engine room shows on its OLLAMA dial and in its call log
    req = urllib.request.Request(base + "/llm", data=json.dumps(dict(backend="ollama", who="agentdispatch", model="local-qwen3-4b", prompt_tokens=120, gen_tokens=300, ms=6000)).encode(), method="POST")
    urllib.request.urlopen(req, timeout=10).read()
    time.sleep(2.5)
    o = get("/now")["ollama"]
    assert o["calls_today"] == 1 and o["gen_today"] == 300 and 49 <= o["now_tok_s"] <= 51, o
    rows = open(os.path.join(scratch, "metrics", "ollama_calls.csv"), encoding="utf-8").read().splitlines()
    assert "agentdispatch" in rows[1] and "local-qwen3-4b" in rows[1]
    print("INTEGRATION OK: budget, usage reporting and the Ollama log work across the two components")
finally:
    p.kill()
