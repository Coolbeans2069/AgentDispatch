"""the conductor: the small local model only logs by default, one invalid op refuses the whole answer, bad rounds back off, the board hears only about applied swaps"""
import json, os, sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tests"))
from test_routing import mk, done
from agentdispatch.agency.registry import DEFAULTS
from agentdispatch.agency.conductor import Conductor

FETCH = lambda u: dict(models=[dict(name="qwen3-coder:30b", size=19e9)]) if "tags" in u else dict(models=[])
GOOD = dict(op="set_local", kind="code", model="qwen3-coder:30b", why="coder")
BAD = dict(op="set_local", kind="code", model="made-up:1b", why="invented")


def setup(cfg):
    r, av = mk()
    app = r["app"]
    app.availability = av
    app.registry.agents["ollama-agent"] = dict(DEFAULTS["ollama-agent"], id="ollama-agent")
    if cfg is not None:
        json.dump(cfg, open(os.path.join(app.data, "conductor.json"), "w"))
    return r, av, app


def test_small_model_logs_only_by_default():
    r, av, app = setup(None)
    before = av.router.local_model("code")
    c = Conductor(app, lambda p: json.dumps(dict(swaps=[GOOD])), fetch=FETCH)
    assert c.mode() == "log" and Conductor(app, None).cfg()["interval_s"] >= 1800
    out = c.step(force=True)
    assert out["action"] == "logged" and out["would_apply"] == 1, out
    assert av.router.local_model("code") == before
    assert not os.path.exists(os.path.join(app.data, "routing_overrides.json"))
    done(r)


def test_one_invalid_op_refuses_all_and_backs_off():
    r, av, app = setup(dict(apply="auto"))
    c = Conductor(app, lambda p: json.dumps(dict(swaps=[GOOD, BAD])), fetch=FETCH)
    base = c.interval()
    out = c.step(force=True)
    assert out["action"] == "refused" and out["accepted"] == 0 and out["rejected"] == 1, out
    assert not os.path.exists(os.path.join(app.data, "routing_overrides.json"))
    assert c.interval() == 2 * base
    c.bad = 50
    assert c.interval() == 6 * 3600
    c.ask = lambda p: json.dumps(dict(swaps=[GOOD]))
    out = c.step(force=True)
    assert out["action"] == "decided" and out["accepted"] == 1 and c.bad == 0, out
    assert av.router.local_model("code") == "qwen3-coder:30b"
    log = open(os.path.join(app.data, "conductor_log.jsonl")).read()
    assert '"refused": true' in log
    done(r)


def test_errors_back_off():
    r, av, app = setup(dict(apply="auto"))
    def boom(p):
        raise RuntimeError("model down")
    c = Conductor(app, boom, fetch=FETCH)
    assert c.step(force=True)["action"] == "error" and c.bad == 1 and c.interval() == 3600
    done(r)


if __name__ == "__main__":
    for k, f in list(globals().items()):
        if k.startswith("test_"):
            f()
            print("ok", k)
