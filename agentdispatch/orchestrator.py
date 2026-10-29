"""the board's orchestrator: triages the pool of open issues (once a day, or early when a similar job shows up), suggests who should take each one, links duplicates, and decides
which tier of model is next on the escalation ladder (local Ollama at night -> mid cloud -> strong). It only WRITES NOTES AND SUGGESTIONS to the board; it never changes code or runs tools.
The language model behind it is injected: Gemini free tier first (budget-checked through the engine room), local Ollama as the fallback."""
import json, os, re, time, urllib.parse, urllib.request
from .paths import OLLAMA_URL as _OLLAMA

from .similarity import similarity

TIERS = ["ollama", "mid", "strong"]
CATEGORIES = ["bug", "knowledge", "tool-gap", "environment", "needs-human", "other"]
NIGHT = (0, 6)                       # local hours when the Ollama night shift may work
TRIAGE_EVERY_S = 24 * 3600
SIMILAR_JOB = 0.6

SYSTEM = ("You are the triage lead of a shared issue board used by several AI models. Reply with ONE JSON object and nothing else. "
          "Be brief and practical. Only use the issue ids you are given.")


class LLMUnavailable(Exception):
    pass


def in_night_window(now=None, window=NIGHT):
    h = time.localtime(now or time.time()).tm_hour
    return window[0] <= h < window[1]


def tier_for(trail, max_local_fails=2, max_mid_fails=2):
    """the escalation ladder from what the attempt ledger says: how many failures each tier has already had"""
    fails = {t: 0 for t in TIERS}
    for a in trail.get("dead_ends", []):
        actor = (a.get("actor") or "").lower()
        t = "ollama" if ("ollama" in actor or "night" in actor or "local" in actor) else "strong" if any(k in actor for k in ("opus", "fable", "strong", "claude-opus")) else "mid"
        fails[t] += 1
    if fails["ollama"] < max_local_fails and not any(fails[t] for t in ("mid", "strong")):
        return "ollama"
    if fails["mid"] < max_mid_fails and not fails["strong"]:
        return "mid"
    return "strong"


DUP_HINT = 0.60
DUP_AUTO = 0.72              # at this text similarity two issues are linked as duplicates even if the model missed it


def triage_prompt(items, hints=None):
    from .textfit import fit as _ofit                                             # each issue's text whole up to 1500 characters, else a visible marker (was a silent 300-character cut)
    lines = []
    for it in items:
        h = (hints or {}).get(it["id"])
        lines.append("- id %s | %s | tags %s | tried %d, failed %d | %s%s" % (it["id"], it["title"], ",".join(it["tags"]) or "-", it["attempts"], it["fails"], _ofit((it["body"] or "").replace("\n", " "), 1500, "issue text"),
                                                                               (" | text-similar to %s (%.2f)" % h) if h else ""))
    return ("Issues:\n%s\n\nFor EACH issue return JSON: {\"issues\":[{\"id\":\"P-000001\",\"category\":one of %s,\"difficulty\":1-5,\"tier\":one of %s,"
            "\"duplicate_of\":null or another listed id,\"next_step\":\"one short sentence\",\"needs_tool\":null or \"short description of a missing tool\"}]}" % ("\n".join(lines), "|".join(CATEGORIES), "|".join(TIERS)))


def parse_triage(text, ids):
    """validate the model's reply; returns {id: record}. Unknown ids are ignored; bad enum values are corrected, never trusted."""
    try:
        obj = json.loads(text)
    except Exception:
        m = re.search(r"\{.*\}", text or "", re.S)
        try:
            obj = json.loads(m.group(0)) if m else {}
        except Exception:
            obj = {}
    out = {}
    for r in (obj.get("issues") if isinstance(obj, dict) else None) or []:
        if not isinstance(r, dict) or r.get("id") not in ids:
            continue
        dup = r.get("duplicate_of")
        out[r["id"]] = dict(category=r.get("category") if r.get("category") in CATEGORIES else "other",
                            difficulty=min(5, max(1, int(r["difficulty"]))) if isinstance(r.get("difficulty"), (int, float)) else 3,
                            tier=r.get("tier") if r.get("tier") in TIERS else "mid",
                            duplicate_of=dup if dup in ids and dup != r["id"] else None,
                            next_step=str(r.get("next_step") or "")[:200], needs_tool=(str(r["needs_tool"])[:160] if r.get("needs_tool") else None))
    return out


class Orchestrator:
    def __init__(self, board, llm, fallback=None, actor="orchestrator", now=time.time):
        self.board, self.llm, self.fallback, self.actor, self.now = board, llm, fallback, actor, now

    def _ask(self, prompt):
        for model in (self.llm, self.fallback):
            if model is None:
                continue
            try:
                return model(SYSTEM, prompt)
            except LLMUnavailable:
                continue
        raise LLMUnavailable("no language model available for triage")

    def _last_triage(self, post_id):
        return self.board.last_event(post_id, "triage")

    def pending(self, only_ids=None, every_s=TRIAGE_EVERY_S):
        """open or escalated issues that were never triaged, or not since a new attempt/reply, or not within the interval"""
        out = []
        for st in ("open", "escalated"):
            for p in self.board.read(state=st, limit=100)["posts"]:
                if p["kind"] not in ("issue", "question") or (only_ids and p["id"] not in only_ids):
                    continue
                last = self._last_triage(p["id"])
                if last is None or (not only_ids and self.now() - last["ts"] >= every_s):
                    out.append(p["id"])
        return out

    def _similar_hints(self, chunk):
        """for each issue: the OLDER open issue it most resembles (by text), if the resemblance is strong"""
        pool = {}
        for st in ("open", "escalated"):
            for p in self.board.read(state=st, limit=100)["posts"]:
                if p["kind"] in ("issue", "question"):
                    pool[p["id"]] = p["title"] + " " + (self.board.trail(p["id"])["issue"].get("body") or "")
        out = {}
        for pid in chunk:
            best = max(((similarity(pool[pid], t), o) for o, t in pool.items() if o != pid and o < pid), default=(0.0, None))
            if best[1] and best[0] >= DUP_HINT:
                out[pid] = (best[1], best[0])
        return out

    def sweep(self, only_ids=None, batch=8):
        ids = self.pending(only_ids)
        done = {}
        for i in range(0, len(ids), batch):
            chunk = ids[i:i + batch]
            items = [dict(self.board.trail(x)["issue"], tags=self.board.trail(x)["issue"]["tags"]) for x in chunk]
            hints = self._similar_hints(chunk)
            recs = parse_triage(self._ask(triage_prompt(items, hints)), set(chunk))
            for pid in chunk:
                r = recs.get(pid)
                if r is None:                                              # the model skipped one: the next sweep tries again; nothing is invented
                    continue
                if r["duplicate_of"] is None and pid in hints and hints[pid][1] >= DUP_AUTO:
                    r["duplicate_of"] = hints[pid][0]                      # the text is clearly the same problem: link it whatever the model said
                self.apply(pid, r)
                done[pid] = r
        return dict(ok=True, triaged=len(done), skipped=len(ids) - len(done), results=done)

    def apply(self, pid, r):
        t = self.board.trail(pid)
        tier = r["tier"] if r["duplicate_of"] is None else "ollama"
        ladder = tier_for(t)                                              # what the ledger says wins over the model's guess when more failures have happened
        if TIERS.index(ladder) > TIERS.index(tier):
            tier = ladder
        note = "triage: %s, difficulty %d, next tier: %s. %s" % (r["category"], r["difficulty"], tier, r["next_step"])
        if r["duplicate_of"]:
            note += " Duplicate of %s: follow that thread." % r["duplicate_of"]
        if r["needs_tool"]:
            note += " Missing tool: %s." % r["needs_tool"]
        self.board.post(self.actor, "status", "triage of %s" % pid, note, ["triage", r["category"], tier], parent=pid)
        self.board.suggest(self.actor, pid, {"ollama": "local-ollama-night", "mid": "mid-cloud", "strong": "strong-cloud"}[tier], r["next_step"])
        self.board.note_event(pid, "triage", self.actor, dict(r, tier=tier))

    def on_job(self, description):
        """called by the router when a job comes through: if it resembles an open issue, triage that issue now instead of waiting for the daily sweep"""
        hits = []
        for st in ("open", "escalated"):
            for p in self.board.read(state=st, limit=100)["posts"]:
                if similarity(description, p["title"] + " " + (self.board.trail(p["id"])["issue"].get("body") or "")) >= SIMILAR_JOB:
                    hits.append(p["id"])
        return dict(similar=hits, sweep=self.sweep(only_ids=set(hits)) if hits else None)


# ------------------------------------------------------------ language models behind the orchestrator
def ollama_llm(model, url=_OLLAMA, timeout=300):
    def run(system, user):
        from .textfit import ollama_ctx
        body = dict(model=model, stream=False, think=False, format="json", keep_alive="5m", options=dict(temperature=0.1, num_predict=2048, num_ctx=ollama_ctx(len(system) + len(user), 2048, ceiling=65536)),
                    messages=[dict(role="system", content=system), dict(role="user", content="/no_think\n" + user)])
        try:
            with urllib.request.urlopen(urllib.request.Request(url + "/api/chat", json.dumps(body).encode(), {"Content-Type": "application/json"}), timeout=timeout) as r:
                resp = json.loads(r.read())
        except Exception as e:
            raise LLMUnavailable("ollama: %s" % e)
        return re.sub(r"(?s)<think>.*?</think>", "", (resp.get("message") or {}).get("content") or "").strip()
    return run


def gemini_llm(model="gemini-3.5-flash-lite", engine_room="http://127.0.0.1:8898", timeout=60):
    """free-tier Gemini through its OpenAI-compatible endpoint; asks the engine room's /budget first and reports every call, so the shared quota stays honest"""
    def run(system, user):
        key = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
        if not key:
            raise LLMUnavailable("no Gemini key in the environment")
        try:
            with urllib.request.urlopen("%s/budget?model=%s&backend=gemini" % (engine_room, model), timeout=3) as r:
                b = json.loads(r.read())
            if not b.get("ok_to_send"):
                raise LLMUnavailable("engine-room budget says wait: %s" % (b.get("note") or b.get("wait_s")))
        except LLMUnavailable:
            raise
        except Exception:
            pass                                                     # the engine room is not running: carry on, the free-tier limits still apply server-side
        body = dict(model=model, temperature=0.1, response_format=dict(type="json_object"), messages=[dict(role="system", content=system), dict(role="user", content=user)])
        req = urllib.request.Request("https://generativelanguage.googleapis.com/v1beta/openai/chat/completions", json.dumps(body).encode(),
                                     {"Content-Type": "application/json", "Authorization": "Bearer " + key})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                resp = json.loads(r.read())
        except Exception as e:
            raise LLMUnavailable("gemini: %s" % str(e)[:120])            # a 429 is NOT retried here: the caller falls back to Ollama
        try:
            u = resp.get("usage") or {}
            urllib.request.urlopen(urllib.request.Request(engine_room + "/llm", json.dumps(dict(backend="gemini", who="agentdispatch-orchestrator", model=model, tokens=u.get("total_tokens", 0))).encode(),
                                                            {"Content-Type": "application/json"}), timeout=2).read()
        except Exception:
            pass
        return resp["choices"][0]["message"]["content"]
    return run


def openrouter_llm(model=None, data_dir=None, engine_room="http://127.0.0.1:8898", timeout=90):
    """OpenRouter free models through the OpenAI-compatible API; budget-checked and reported like Gemini"""
    from . import openrouter as OR
    model = model or OR.DEFAULT_CLOUD

    def run(system, user):
        key = OR.api_key(data_dir)
        if not key:
            raise LLMUnavailable("no OpenRouter key (OPENROUTER_API_KEY)")
        try:
            q = urllib.parse.quote(model, safe="")
            with urllib.request.urlopen("%s/budget?model=%s&backend=openrouter" % (engine_room, q), timeout=3) as r:
                b = json.loads(r.read())
            if not b.get("ok_to_send"):
                raise LLMUnavailable("engine-room OpenRouter budget says wait: %s" % (b.get("note") or b.get("wait_s")))
        except LLMUnavailable:
            raise
        except Exception:
            pass
        body = dict(model=model, temperature=0.1, response_format=dict(type="json_object"), messages=[dict(role="system", content=system), dict(role="user", content=user)])
        req = urllib.request.Request(OR.API_URL + "/chat/completions", json.dumps(body).encode(), OR.headers(key))
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                resp = json.loads(r.read())
        except Exception as e:
            raise LLMUnavailable("openrouter: %s" % str(e)[:120])
        try:
            u = resp.get("usage") or {}
            urllib.request.urlopen(urllib.request.Request(engine_room + "/llm", json.dumps(dict(backend="openrouter", who="agentdispatch-orchestrator", model=model, tokens=u.get("total_tokens", 0))).encode(),
                                                            {"Content-Type": "application/json"}), timeout=2).read()
        except Exception:
            pass
        return resp["choices"][0]["message"]["content"]
    return run
