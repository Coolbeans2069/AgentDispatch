"""the agent registry: who can do what, how to run them unattended, what they cost. Built-in defaults; <data>/agents.json overrides them (validated; a bad file is refused, not half-used)."""
import copy, json, os, re

from .jobs import NEEDS

COST = ("free", "local", "cheap", "plan", "premium")
COST_RANK = {c: i for i, c in enumerate(COST)}
TIER_OF = {"free": "ollama", "local": "ollama", "cheap": "mid", "plan": "mid", "premium": "strong"}     # the broker's escalation ladder (orchestrator.tier_for)
TIER_RANK = {"ollama": 0, "mid": 1, "strong": 2}
KINDS = ("cli", "app", "local")
REGIONS = ("frontal", "parietal", "temporal", "occipital", "cerebellum", "brainstem", "broca", "auditory", "limbic")
DEFAULT_COLORS = ("#e8793a", "#4da3ff", "#3ddc84", "#b57bff", "#ff4d9d", "#ffd34d", "#19f0c8", "#ff9a1a", "#e6edf3")
SANDBOX = ("copy", "container", "none")

# Verified 2026-10-16 (Stage 1): claude -p --tools "" (text only), gemini --skip-trust -p ... -o text, codex exec -s read-only (reached its usage-limit message, flags accepted),
# grok --tools "" --disable-web-search -p "<prompt>" (the prompt as an ARGUMENT: through stdin Grok ignores it and starts exploring the disk). Every CLI runs with NO tools:
# edits come back as a JSON patch the agency applies to a throw-away copy.
# `brain` is optional display metadata for the /brain page: {region, color, label}; without it the region follows the agent's strengths. Adding an agent = one entry here (or in data/agents.json).
DEFAULTS = {
    "ollama-local": dict(kind="local", name="Local Ollama", strengths=["fast", "write", "code"], cost="local", concurrency=1, daily_cap=200, sandbox="container", enabled=True,
                         run=dict(via="router", model="auto-local", timeout_s=14400), er_id=None, limits_source="none", binary=None, brain=dict(region="cerebellum", color="#b57bff")),
    "ollama-agent": dict(kind="local", name="Local agent (Claude Code on Ollama)", strengths=["code", "write", "fast"], cost="local", concurrency=1, daily_cap=100, sandbox="container", enabled=True,
                         run=dict(via="agentic", timeout_s=14400), er_id=None, limits_source="none", binary="docker", brain=dict(region="cerebellum", color="#c28bff"),
                         note="a local model working like Claude Code in its own Docker container: it edits and tests the job's workspace copy itself; the gate, check and review are unchanged"),
    "local-tools": dict(kind="cli", name="Local tools", strengths=["tool"], cost="free", concurrency=2, daily_cap=10000, sandbox="none", enabled=True, run=dict(via="tools", timeout_s=1800), er_id=None,
                        limits_source="none", binary=None, brain=dict(region="parietal", color="#5dff9a"),
                        note="this computer itself: runs a step's tool call (a calculation, a Lean check, a test, git) with no model and no tokens"),
    "gemini-cli": dict(kind="cli", name="Gemini CLI", strengths=["research", "long-context", "review", "plan", "write"], cost="cheap", concurrency=1, daily_cap=40, sandbox="copy", enabled=True,
                       run=dict(argv=["gemini", "--skip-trust", "-p", "Follow the instructions you were sent on stdin.", "-o", "text"], stdin_prompt=True, timeout_s=1200, model_flag=["-m", "{model}"],
                                models=["gemini-3.8-flash", "gemini-3-flash-preview", "gemini-3.5-flash-lite", "gemini-3.1-flash-lite"]), er_id="gemini",
                       limits_source="learned", binary="gemini", brain=dict(region="temporal", color="#4da3ff")),
    "openrouter": dict(kind="cli", name="OpenRouter free", strengths=["code", "write", "research", "plan", "review", "reasoning", "vision", "fast", "long-context"], cost="free", concurrency=1, daily_cap=50, sandbox="copy", enabled=True,
                       run=dict(via="router", model="openrouter/free", timeout_s=1200,
                                models=["openrouter/free", "nvidia/nemotron-3-super-120b-a12b:free", "google/gemma-4-31b-it:free", "cohere/north-mini-code:free",
                                        "nvidia/nemotron-3.5-lightning:free", "thinkingmachines/inkling:free", "thinkingmachines/inkling-small:free",
                                        "google/gemma-4-26b-a4b-it:free", "nvidia/nemotron-3-nano-omni-30b-a3b-reasoning:free",
                                        "poolside/laguna-s-2.1:free", "poolside/laguna-xs-2.1:free", "xingchen-agi/xing4.0-29b-a4b:free",
                                        "nvidia/nemotron-3.5-super-vl:free", "nvidia/nemotron-3-ultra-550b-a55b:free",
                                        "nvidia/nemotron-3.5-content-safety:free", "liquid/lfm-2.5-2.6b:free", "dots-studio/dots-3-note-preview:free"]),
                       er_id="openrouter", limits_source="engineroom:openrouter", binary=None, brain=dict(region="temporal", color="#00d4aa"),
                       note="OpenRouter free-tier models through the broker router (OPENROUTER_API_KEY); no separate CLI"),
    "codex-cli": dict(kind="cli", name="Codex CLI", strengths=["code", "review", "plan"], cost="plan", concurrency=1, daily_cap=20, sandbox="copy", enabled=True,
                      run=dict(argv=["codex", "exec", "-s", "read-only", "--skip-git-repo-check", "--ephemeral", "-C", "{cwd}", "-"], stdin_prompt=True, timeout_s=1800), er_id="codex",
                      limits_source="engineroom:codex", binary="codex", brain=dict(region="parietal", color="#3ddc84")),
    "claude-code": dict(kind="cli", name="Claude Code", strengths=["code", "review", "plan", "long-context", "vision", "write", "research"], cost="premium", concurrency=1, daily_cap=15,
                        sandbox="copy", enabled=True, run=dict(argv=["claude", "-p", "--tools", "", "--no-session-persistence", "--disable-slash-commands"], stdin_prompt=True, timeout_s=1800),
                        er_id="claude", limits_source="learned", binary="claude", brain=dict(region="frontal", color="#e8793a")),
    "grok-cli": dict(kind="cli", name="Grok CLI", strengths=["research", "write", "review"], cost="plan", concurrency=1, daily_cap=10, sandbox="copy", enabled=True,
                     run=dict(argv=["grok", "--tools", "", "--disable-web-search", "--prompt-file", "{prompt_file}"], stdin_prompt=False, timeout_s=1200), er_id="grokcli",
                     limits_source="engineroom:grokcli", binary="grok", brain=dict(region="limbic", color="#e6edf3")),
    "comfy-image": dict(kind="app", name="Image gen (ComfyUI)", strengths=["image"], cost="local", concurrency=1, daily_cap=0, sandbox="none", enabled=False, run=dict(via="pull"), er_id=None,
                        limits_source="none", binary=None, brain=dict(region="occipital", color="#ff6bb5"), service=dict(kind="comfyui", role="image"),
                        note="the ComfyUI windows: shown in the brain while they run; the agency does not drive them yet"),
    "comfy-video": dict(kind="app", name="Video gen (Wan)", strengths=["video"], cost="local", concurrency=1, daily_cap=0, sandbox="none", enabled=False, run=dict(via="pull"), er_id=None,
                        limits_source="none", binary=None, brain=dict(region="occipital", color="#d14dff"), service=dict(kind="comfyui", role="video"), note="as above"),
    "comfy-speech": dict(kind="app", name="Speech (Fish Audio S2)", strengths=["speech"], cost="local", concurrency=1, daily_cap=0, sandbox="none", enabled=False, run=dict(via="pull"), er_id=None,
                         limits_source="none", binary=None, brain=dict(region="broca", color="#ffd34d"), service=dict(kind="comfyui", role="speech"), note="as above"),
    "comfy-music": dict(kind="app", name="Music (ACE-Step)", strengths=["audio"], cost="local", concurrency=1, daily_cap=0, sandbox="none", enabled=False, run=dict(via="pull"), er_id=None,
                        limits_source="none", binary=None, brain=dict(region="auditory", color="#ff9a1a"), service=dict(kind="comfyui", role="audio"), note="as above"),
    "pull-agent": dict(kind="app", name="Pull agent", strengths=["research", "write", "code", "review", "plan"], cost="plan", concurrency=1, daily_cap=30, sandbox="none", enabled=True,
                     run=dict(via="pull", lease_min=30), er_id="pullagent", limits_source="learned", binary=None, brain=dict(region="limbic", color="#b9c4cf"),
                     note="any external agent or open session that asks the agency for work (agency_next / agency_submit); the agency cannot launch it"),
}
REQUIRED = ("kind", "strengths", "cost", "concurrency", "daily_cap", "sandbox", "run")


class RegistryError(ValueError):
    pass


def validate(agents):
    problems = []
    if not isinstance(agents, dict) or not agents:
        return ["agents must be a non-empty object"]
    for aid, a in agents.items():
        if not isinstance(a, dict):
            problems.append("%s: must be an object" % aid)
            continue
        for k in REQUIRED:
            if k not in a:
                problems.append("%s: missing %s" % (aid, k))
        if a.get("kind") not in KINDS:
            problems.append("%s: kind must be one of %s" % (aid, ", ".join(KINDS)))
        if a.get("cost") not in COST:
            problems.append("%s: cost must be one of %s" % (aid, ", ".join(COST)))
        if a.get("sandbox") not in SANDBOX:
            problems.append("%s: sandbox must be one of %s" % (aid, ", ".join(SANDBOX)))
        bad = [s for s in (a.get("strengths") or []) if s not in NEEDS]
        if bad or not a.get("strengths"):
            problems.append("%s: strengths must be a non-empty list from %s" % (aid, ", ".join(NEEDS)))
        for k in ("concurrency", "daily_cap"):
            if not isinstance(a.get(k), int) or isinstance(a.get(k), bool) or a.get(k) < 0 or a.get(k) > 10000:
                problems.append("%s: %s must be an integer 0-10000" % (aid, k))
        br = a.get("brain")
        if br is not None and (not isinstance(br, dict) or (br.get("region") and br["region"] not in REGIONS) or (br.get("color") and not re.match(r"^#[0-9a-fA-F]{6}$", str(br["color"])))):
            problems.append("%s: brain must be {region: one of %s, color: #rrggbb}" % (aid, ", ".join(REGIONS)))
        run = a.get("run")
        if not isinstance(run, dict) or not (run.get("argv") or run.get("via")):
            problems.append("%s: run needs argv (a command list) or via" % aid)
        elif run.get("argv") and not (isinstance(run["argv"], list) and all(isinstance(x, str) for x in run["argv"])):
            problems.append("%s: run.argv must be a list of strings" % aid)
        if isinstance(run, dict) and run.get("models") is not None and not (isinstance(run["models"], list) and all(isinstance(x, str) and re.match(r"^[A-Za-z0-9._:/-]{2,120}$", x) for x in run["models"])):
            problems.append("%s: run.models must be a list of model names, best first" % aid)
    return problems


class Registry:
    def __init__(self, data_dir=None, agents=None):
        self.agents = copy.deepcopy(DEFAULTS)
        path = os.path.join(data_dir, "agents.json") if data_dir else None
        if agents is None and path and os.path.isfile(path):
            try:
                agents = json.load(open(path, encoding="utf-8"))
            except ValueError as e:
                raise RegistryError("agents.json is not valid JSON: %s" % e)
        if agents is not None:
            problems = validate(agents)
            if problems:
                raise RegistryError("agents.json refused: " + "; ".join(problems[:6]))
            self.agents = copy.deepcopy(agents)
        ov = os.path.join(data_dir, "agent_overrides.json") if data_dir else None          # partial per-agent tuning (concurrency, daily_cap, enabled) without replacing the whole registry
        if agents is None and ov and os.path.isfile(ov):
            try:
                for aid, patch in json.load(open(ov, encoding="utf-8")).items():
                    if aid in self.agents and isinstance(patch, dict):
                        for k in ("concurrency", "daily_cap", "enabled"):
                            if k in patch and (isinstance(patch[k], bool) if k == "enabled" else isinstance(patch[k], int) and not isinstance(patch[k], bool) and 0 <= patch[k] <= 10000):
                                self.agents[aid][k] = patch[k]
            except (ValueError, OSError, AttributeError):
                pass
        for aid, a in self.agents.items():
            a["id"] = aid
            a.setdefault("enabled", True)
            a.setdefault("er_id", None)
            a.setdefault("binary", None)
            a.setdefault("limits_source", "learned")

    def get(self, aid):
        return self.agents[aid]

    def tier(self, aid):
        return TIER_OF[self.agents[aid]["cost"]]

    def capable(self, needs, push=True):
        """enabled agents that have every strength the step needs, cheapest first; push=True leaves out pull-only agents (apps that take work from the board themselves)"""
        need = set(needs or [])
        out = [a for a in self.agents.values() if a["enabled"] and need <= set(a["strengths"]) and not (push and (a["run"].get("via") == "pull"))]
        return sorted(out, key=lambda a: (COST_RANK[a["cost"]], a["id"]))

    def brain(self, aid):
        """where an agent sits in the brain picture and its colour: its own `brain` entry, else by modality (video / image / vision -> occipital, speech -> broca, audio -> auditory,
        local -> cerebellum, code -> parietal, plan / review -> frontal, research / long-context / write -> temporal), else the brainstem; a colour not given comes from a palette in registry order"""
        a = self.agents[aid]
        b = dict(a.get("brain") or {})
        st = set(a["strengths"])
        if not b.get("region"):
            b["region"] = ("occipital" if st & {"video", "image", "vision"} and not st & {"code", "plan"} else "broca" if "speech" in st else "auditory" if "audio" in st else
                           "cerebellum" if a["kind"] == "local" else "parietal" if "code" in st else "frontal" if st & {"plan", "review"} else
                           "temporal" if st & {"research", "long-context", "write"} else "brainstem")
        if not b.get("color"):
            b["color"] = DEFAULT_COLORS[list(self.agents).index(aid) % len(DEFAULT_COLORS)]
        b["label"] = b.get("label") or a.get("name", aid)
        return b

    def reviewers(self, exclude=()):
        return [a for a in self.capable(["review"]) if a["id"] not in set(exclude)]
