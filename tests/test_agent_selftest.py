"""the local agent's start-up self-test: a model that is missing, or whose pinned-window name is not valid / not listed, is marked invalid before any job is handed to it."""
import os, sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from agentdispatch.agency.agentic import AgentEnv


def env_with(tags, made=None):
    made = made if made is not None else []

    def fetch(url, body=None, timeout=60):
        if url.endswith("/api/create"):
            made.append(body["model"])
        if url.endswith("/api/tags"):
            return dict(models=[dict(name=n) for n in list(tags) + made])
        return {}
    return AgentEnv(None, None, None, cfg=lambda: dict(default=32768), fetch=fetch), made


def test_selftest_reports_each_model():
    env, made = env_with(["qwen3-coder:30b", "nemotron-cascade-2:latest", "qwen3-coder-ctx32k:30b"])
    res = {r["model"]: r for r in env.selftest(["qwen3-coder:30b", "nemotron-cascade-2:latest", "missing:7b"], which=lambda b: "/usr/bin/docker")}
    assert res["qwen3-coder:30b"]["ok"] and res["qwen3-coder:30b"]["run_as"] == "qwen3-coder-ctx32k:30b"
    assert res["nemotron-cascade-2:latest"]["ok"] and res["nemotron-cascade-2:latest"]["run_as"] == "nemotron-cascade-2:latest", "not created unless asked: the plain model runs"
    assert not res["missing:7b"]["ok"] and "not installed" in res["missing:7b"]["why"]
    assert not made
    res = env.selftest(["nemotron-cascade-2:latest"], which=lambda b: "/usr/bin/docker", create=True)
    assert res[0]["ok"] and res[0]["run_as"] == "nemotron-cascade-2-ctx32k:latest" and made == ["nemotron-cascade-2-ctx32k:latest"]


def test_selftest_without_docker_or_ollama_fails_every_model():
    env, _ = env_with(["qwen3-coder:30b"])
    assert not env.selftest(["qwen3-coder:30b"], which=lambda b: None)[0]["ok"]

    def down(url, body=None, timeout=60):
        raise OSError("refused")
    env.fetch = down
    r = env.selftest(["qwen3-coder:30b"], which=lambda b: "/usr/bin/docker")[0]
    assert not r["ok"] and "not answering" in r["why"]


def test_the_startup_hook_marks_bad_models_and_cools_the_agent_when_none_pass():
    from agentdispatch.__main__ import _agent_selftest
    invalid, cooled = [], []

    class Book:
        def invalid(self, agent, model, text=""):
            invalid.append((agent["id"], model, text))

    class Router:
        cfg = {}

        def ladder(self, agent):
            return [dict(model="a:1"), dict(model="b:2")]

    class Av:
        book, router = Book(), Router()

    class Jobs:
        def cooldown_set(self, agent, why, base_min=20, **k):
            cooled.append((agent, why))

    class Reg:
        agents = {"ollama-agent": dict(id="ollama-agent", enabled=True)}

    class App:
        availability, jobs, registry = Av(), Jobs(), Reg()

    class Env:
        def selftest(self, models, create=False):
            return [dict(model=m, ok=False, run_as=m, why="model %s is not installed in Ollama" % m) for m in models]
    res = _agent_selftest(App(), Env(), wait=True)
    assert len(res) == 2 and [m for _, m, _ in invalid] == ["a:1", "b:2"] and cooled and cooled[0][0] == "ollama-agent"


if __name__ == "__main__":
    for k, f in list(globals().items()):
        if k.startswith("test_"):
            f()
            print("ok", k)
