"""live: one real Gemini triage call through the orchestrator, budget-checked and reported via the engine room (skipped without a key or engine room). Uses ONE free-tier request."""
import os, sys, tempfile, shutil, urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
if not (os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")):
    print("SKIP: no Gemini key"); sys.exit(0)
try:
    urllib.request.urlopen("http://127.0.0.1:8898/budget?model=gemini-3.5-flash-lite", timeout=3).read()
except Exception:
    print("SKIP: engine room not running"); sys.exit(0)
from agentdispatch.store import Board
from agentdispatch.orchestrator import Orchestrator, gemini_llm
d = tempfile.mkdtemp(prefix="gem_")
try:
    b = Board(d)
    i = b.post("grok", "issue", "poll times out while a model loads", "history request times out after 30 s")["id"]
    r = Orchestrator(b, gemini_llm()).sweep()
    assert r["triaged"] == 1 and r["results"][i]["tier"] in ("ollama", "mid", "strong"), r
    print("LIVE GEMINI OK:", r["results"][i]["category"], r["results"][i]["tier"])
    b.close()
finally:
    shutil.rmtree(d, ignore_errors=True)
