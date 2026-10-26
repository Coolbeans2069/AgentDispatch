"""integration between the two components: agentdispatch reports its Ollama calls and asks for Gemini budgets through engineroom's local API.
Starts the engine room on a spare port with a scratch data folder (needs ../engineroom next to this repo)."""
import json, os, subprocess, sys, tempfile, time, urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ER = os.path.join(os.path.dirname(ROOT), "engineroom")
if not os.path.isfile(os.path.join(ER, "engineroom_app.py")):
    print("SKIP: engineroom is not next to this repo")
    sys.exit(0)
PORT = 8897
scratch = tempfile.mkdtemp(prefix="er_int_")
env = dict(os.environ, ENGINEROOM_PORT=str(PORT), MEDIASTUDIO_STUDIO_DIR=scratch, AD_LIMITS_NO_LIVE="1", MEDIASTUDIO_LIMITS_FILE=os.path.join(scratch, "limits.json"), ENGINEROOM_URL="http://127.0.0.1:%d" % PORT)
p = subprocess.Popen([sys.executable, os.path.join(ER, "engineroom_app.py"), "--no-window"], cwd=ER, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
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
    # the tool-index builder reports its Ollama calls to the engine room
    from agentdispatch.toolindex import build
    build.ENGINE_ROOM = base
    build.log_to_engine_room("local-qwen3-4b", dict(prompt_eval_count=120, eval_count=300, eval_duration=6_000_000_000))
    time.sleep(2.5)
    o = get("/now")["ollama"]
    assert o["calls_today"] == 1 and o["gen_today"] == 300 and 49 <= o["now_tok_s"] <= 51, o
    rows = open(os.path.join(scratch, "metrics", "ollama_calls.csv"), encoding="utf-8").read().splitlines()
    assert "agentdispatch-index" in rows[1] and "local-qwen3-4b" in rows[1]
    print("INTEGRATION OK: budget, usage reporting and the Ollama log work across the two components")
finally:
    p.kill()
