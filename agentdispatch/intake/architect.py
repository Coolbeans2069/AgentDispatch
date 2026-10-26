"""the ARCHITECT: a strong model (Claude, Codex, Gemini or Grok through their own CLIs; the local model only as a last resort) that interviews the person, studies whatever must be understood first
(reverse-engineering existing software with the REA tools), works out the design including the hooks and tools that do not exist yet, writes a plan of jobs with acceptance checks, and has a DIFFERENT
model attack it before anything is built. The expensive thinking happens once, here, with every local tool free to use; the deterministic gate in blueprint.validate decides whether the result is ready.
Each pass runs in a background thread; the page watches the session."""
import json, os, re, threading, time

from ..agency.adapters import detect_limit, parse_reset
from ..agency.realadapters import RealAdapter
from ..jsonx import extract_json
from ..textfit import fit as _fit, tail as _tail
from ..agency.jobs import CAPS
from ..store import BoardError
from . import blueprint as BP

PREFERENCE = ("claude-code", "codex-cli", "gemini-cli", "grok-cli")                 # the strong models, best thinkers first
MAX_ROUNDS = 6
PHASES = ("interview", "study", "design", "plan", "review", "revise", "ready", "needs_you", "running", "waiting_review", "done")
MAX_REVISE = 4
SHORT_CORE = ("You are the lead systems architect of an automated engineering agency. A person describes what they want; your work decides whether it succeeds. Be thorough, concrete and honest: "
        "state what is not feasible instead of pretending, never invent facts or file paths, keep every number and unit exactly, think about what would make this fail and put a hook, a test, a monitor or a "
        "tool in the plan that prevents it, and think beyond what the person asked: what do they actually need so that they do not fail? Reply with ONE JSON object and nothing else (no fences, no prose outside it).")
SECTIONS = ('"title", "summary", "feasibility": {"verdict": "feasible|partly|not_feasible|unknown", "why", "alternatives": []}, "goals": [], "non_goals": [], "requirements": [{"id": "R1", "text", '
            '"priority": "must|should|could", "source": "user|assumed|derived", "acceptance": "how it is verified"}], "assumptions": [{"text", "risk"}], "open_questions": [{"id": "Q1", "question", "why", '
            '"default": "what you will assume if they do not answer", "answered": ""}], "risks": [{"risk", "mitigation"}], "reference_systems": [{"name", "why", "how_to_study"}]')


def clip(text, n):
    return _fit(text or "", n, "section", head=0.5)                                        # head + tail with a marker that says how much was left out


class Architect:
    def __init__(self, app, intake):
        self.app, self.intake = app, intake
        self.threads = {}

    # ------------------------------------------------------------ the system prompt (a file the owner can override in <data>/prompts)
    def core(self):
        path = os.path.join(self.app.data, "prompts", "architect_system.md")
        if not os.path.isfile(path):
            path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "prompts", "architect_system.md")
        try:
            text = open(path, encoding="utf-8").read()
        except OSError:
            text = SHORT_CORE
        cfg = self.app.usage.autonomy.load()
        return text + ("\n\n## Context of this run\nAutonomous projects folder: %s (new projects go in a sub-folder of it). Strong-model calls this plan may use: %d. Autonomous mode: %s. Reply with ONE JSON object, "
                       "no fences." % (cfg["projects_root"], cfg["strong_calls_per_session"], "ON: a ready plan is created and run without asking" if cfg["enabled"] else "off: the person approves the plan"))

    # ------------------------------------------------------------ who thinks
    def adapter(self):
        from ..agency import policy
        from ..intake.chat import chat_model

        def local(prompt):
            r = self.app.router.chat(dict(model=chat_model(self.app), messages=[dict(role="user", content=prompt)], max_tokens=16000, temperature=0.2, response_format=dict(type="json_object")), "architect")
            return r["choices"][0]["message"]["content"]
        return RealAdapter(policy.load(self.app.data)["work_dir"], local, toolbox=self.app.localtools, book=self.app.availability.book)

    def candidates(self, exclude=()):
        reg, av = self.app.registry, self.app.availability
        out = []
        for aid in PREFERENCE:
            a = reg.agents.get(aid)
            if a and a.get("enabled", True) and aid not in exclude and (a.get("run") or {}).get("argv"):
                ok, why = av.check(a)
                if ok:
                    out.append(a)
        return out

    def usable(self, agent, sid):
        ok, why = self.app.usage.allow_strong(agent["id"], sid)
        if not ok:
            self.log(sid, "", "Leaving %s alone: %s." % (agent.get("name", agent["id"]), why))
        return ok

    def capabilities(self):
        box, reg = self.app.localtools, self.app.registry
        tools = ", ".join("%s (%s)" % (t["id"], t["desc"][:50]) for t in box.reg.listing() if t["available"] and t["kind"] not in ("lib", "runtime"))
        from ..agency import profiles as PROF
        agents = PROF.text(reg, getattr(self.app, "availability", None), getattr(self.app, "jobs", None), getattr(self.app, "data", None))
        cat = self.app.catalog
        models = ", ".join("%s: %s" % (f, n) for f, n in cat.counts().items() if n)
        rea = ""
        ix = getattr(self.app, "index", None)
        if ix and ix.indexes:
            rea = " REVERSE ENGINEERING (REA, Ghidra based; use rea_search then rea_describe then rea_call): backends %s." % ", ".join(ix.indexes)
        return ("THIS COMPUTER CAN DO, FREE: tools - %s. %s\nModels on disk by function - %s.%s Tools that do NOT exist can be built by an agent (put them in design.tools_needed)."
                % (tools, agents, models, rea))

    def hints(self, text):
        try:
            s = self.app.campaign.playbooks.suggest(text)
        except Exception:
            return ""
        out = []
        for h in s.get("similar_successes", [])[:2]:
            out.append("a similar job worked before (%s): steps %s" % (h["title"], " -> ".join(x["objective"][:50] for x in h["steps"][:4])))
        for w in s.get("warnings", [])[:3]:
            out.append("a similar job failed: " + w["reason"][:600])
        return ("LESSONS FROM FINISHED WORK: " + " | ".join(out)) if out else ""

    # ------------------------------------------------------------ tools the thinker may call mid-thought
    TOOLS_TEXT = ('LOCAL TOOLS (free): to use them reply ONLY with {"tool_calls": [{"tool": "calc", "code": "<python that prints>"}, {"tool": "lean", "code": "<lean>"}, {"tool": "git", "args": ["log", "-3"]}, '
                  '{"tool": "rea_search", "query": "decompile function"}, {"tool": "rea_describe", "names": ["<tool name>"]}, {"tool": "rea_call", "name": "<tool name>", "arguments": {}}, '
                  '{"tool": "playbook_search", "query": "similar past work"}, {"tool": "local_llm", "prompt": "draft / summarise / extract this (free local model: check its answer)"}, {"tool": "freecad", "args": ["<script.py in a project folder>"]}, {"tool": "tool_list"}]} (at most 4 per round, at most %d rounds); you are sent the outputs, then give your final JSON.' % MAX_ROUNDS)

    def run_tool(self, c, sid):
        t = c.get("tool", "")
        try:
            if t in ("rea_search", "rea_describe", "rea_call"):
                ix = getattr(self.app, "index", None)
                if not ix or not ix.indexes:
                    return dict(ok=False, error="the reverse-engineering tool index is not available")
                s = dict(actor="architect")
                if t == "rea_search":
                    return ix.tool_search(dict(query=str(c.get("query", ""))[:200], backend="rea", limit=8), s)
                if t == "rea_describe":
                    return ix.tool_describe(dict(names=[str(x) for x in (c.get("names") or [])][:4]), s)
                return ix.tool_call(dict(name=str(c.get("name", "")), arguments=c.get("arguments") or {}, timeout_s=int(c.get("timeout_s", 300))), s)
            if t == "local_llm":
                r = self.app.localtools.run("local_llm", code=str(c.get("prompt") or c.get("code") or "")[:8000], caller="architect", job=sid)
                self.app.usage.record("local", "local_llm", 0, len(str(c.get("prompt") or "")), len(r.get("stdout") or ""), sid)
                return dict(ok=r.get("ok"), text=clip(r.get("stdout") or "", 12000), problems=r.get("problems"))
            if t == "playbook_search":
                return self.app.api.playbooks(str(c.get("query", "")), "", "", "", 5)
            if t == "tool_list":
                return dict(ok=True, tools=[dict(id=x["id"], ready=x["available"], desc=x["desc"][:70]) for x in self.app.localtools.reg.listing()])
            r = self.app.localtools.run(t, args=c.get("args"), code=c.get("code"), file=c.get("file"), timeout=c.get("timeout"), caller="architect", job=sid, project=c.get("project"))
            return dict(ok=r.get("ok"), output=_tail(r.get("stdout") or "", 8000), errors=_tail(r.get("stderr") or "", 3000) if not r.get("ok") else "", problems=r.get("problems"))
        except Exception as e:
            return dict(ok=False, error="%s: %s" % (e.__class__.__name__, str(e)[:160]))

    # ------------------------------------------------------------ asking: strong model first, the next if it is out of quota, local last
    def log(self, sid, phase, text, **kv):
        def fn(s):
            s["thinking"] = (s.get("thinking", []) + [dict(t=time.time(), phase=phase, text=text[:600], **kv)])[-120:]
            if phase in PHASES:                                                               # repairs and stall notes are events, not the plan's phase
                s["phase"] = phase
        self.intake.update(sid, fn)

    STRONG_ONLY = ("design", "plan", "fixtures", "revise", "repair")                        # a weak local model must not write or rewrite these: it produced an empty plan once

    def ask(self, sid, prompt, key, phase, exclude=(), rounds=0, expect_list=False):
        """-> (dict | None, who). The strong models in order; a limit message puts that model on cooldown and moves to the next. `rounds` > 0 lets the thinker call tools between answers."""
        ad = self.adapter()
        pool = self.candidates(exclude)
        tried = []
        def fits(a):
            lim = (a.get("run") or {}).get("max_prompt_chars")
            if lim and len(prompt) > lim:
                self.log(sid, "", "Skipping %s: this prompt (%d characters) is longer than it can take (%d)." % (a.get("name", a["id"]), len(prompt), lim))
                return False
            return True
        for agent in [a for a in pool if self.usable(a, sid) and fits(a)] + ([] if phase in self.STRONG_ONLY else [None]):
            who = agent["id"] if agent else "local model"
            if agent is None:
                self.log(sid, phase, "No strong model is available right now (%s): using the local model, which thinks less deeply." % ("; ".join(tried) or "none are enabled"), who=who)
            ag = agent or dict(id="ollama-local", run=dict(via="router"), cost="local", name="local model")
            text, err, calls = self._converse(ad, ag, prompt, sid, rounds, phase, key)
            if err and detect_limit(err + " " + (text or "")) and agent:
                self.app.jobs.cooldown_set(agent["id"], err[:200], until=parse_reset(err + " " + (text or ""), time.time()))
                tried.append("%s hit its limit" % agent["id"])
                continue
            if err:
                tried.append("%s: %s" % (ag["id"], err[:80]))
                continue
            d = extract_json(text, key)
            if d is None:
                text2, err2, _ = self._converse(ad, ag, prompt + "\n\nYour last reply was not the required JSON object. Reply with ONLY the JSON object.", sid, 0, phase, key)
                d = extract_json(text2 or "", key) if not err2 else None
            if d is not None:
                return d, who
            tried.append("%s gave no usable JSON" % ag["id"])
        return None, "nobody"

    def _ask1(self, ad, agent, prompt, sid, phase=None):
        """one model call, with its usage written down (strong or local). The phase says how hard the call is, so the router gives an interview a cheap rung and the plan or a repair a strong one"""
        t0 = time.time()
        router = getattr(getattr(ad, "book", None), "router", None)
        if router is not None and hasattr(ad, "hint"):
            levels = (router.cfg.get("architect") or {})
            ad.hint = dict(difficulty=levels.get(phase or "default", levels.get("default", 0.75)), kind="step", attempts=0)
        try:
            text, err = ad._ask(agent, prompt, None)
        finally:
            if hasattr(ad, "hint"):
                ad.hint = None
        self.app.usage.record("local" if agent["id"] == "ollama-local" else "strong", agent["id"], time.time() - t0, len(prompt), len(text or ""), sid, ok=not err, note=(err or "")[:100])
        return text, err

    def _converse(self, ad, agent, prompt, sid, rounds, phase, key):
        text, err = self._ask1(ad, agent, prompt, sid, phase)
        n = 0
        while not err and rounds and n < rounds:
            calls = ad._tool_calls(text)
            if not calls:
                break
            n += 1
            outs = []
            for c in calls:
                self.log(sid, phase, "%s runs %s %s" % (agent.get("name", agent["id"]), c.get("tool"), (c.get("query") or c.get("name") or " ".join(map(str, c.get("args") or []))[:50] or "")[:60]), tool=c.get("tool"))
                outs.append(dict(tool=c.get("tool"), result=self.run_tool(c, sid)))
            follow = "%s\n\nYOUR TOOL CALLS:\n%s\n\nTOOL RESULTS (computed on this computer):\n%s\n\nContinue: give your final JSON object%s." % (
                prompt, clip(text, 1500), clip(json.dumps(outs, ensure_ascii=False, default=str), 7000), "" if n < rounds else " now (no more tool rounds)")
            text, err = self._ask1(ad, agent, follow, sid, phase)
        return text, err, n

    # ------------------------------------------------------------ prompts
    def context(self, s, extra=""):
        msgs = "\n".join("%s: %s" % ("PERSON" if m["role"] == "user" else "ARCHITECT", clip(m["text"], 8000)) for m in s["messages"][-14:] if m["role"] in ("user", "assistant"))
        files = "\n".join("- %s (%s): %s" % (f["name"], f["kind"], clip(f["analysis"].get("summary") or "not looked at", 3000)) for f in s["files"]) or "(none)"
        bp = json.dumps(s.get("blueprint") or {}, ensure_ascii=False)
        return "CONVERSATION:\n%s\n\nATTACHMENTS (looked at by models on this computer):\n%s\n\nBLUEPRINT SO FAR:\n%s\n\n%s\n%s\n%s" % (msgs, files, clip(bp, 9000), self.capabilities(), self.hints(msgs[-600:]), extra)

    def p_interview(self, s):
        return "\n".join([self.core(), "PASS=interview", "Talk to the person like a demanding senior engineer and product analyst. Extract every requirement, number and constraint they gave; record assumptions you must make; ask at most THREE "
                          "questions, only those whose answer would change the design, each with the default you will assume; mark questions the person has now answered (answered: their answer); say plainly if part of "
                          "it looks infeasible and offer alternatives; list any existing software that should be studied (reverse engineered) before designing.", self.context(s),
                          'Reply: {"reply": "<2-4 friendly sentences for the person: what you understood, what you assume, your questions>", "blueprint": {%s}} - include only the sections you change; sections you '
                          "include replace the old ones, so keep what is still true." % SECTIONS])

    def p_study(self, s):
        return "\n".join([self.core(), "PASS=study", "Before designing, understand what must be understood. For every item in reference_systems and every attached file that is software, firmware or a data format: study it with the "
                          "REA reverse-engineering tools (rea_search, rea_describe, rea_call) and the local tools, and record concrete findings (formats, protocols, algorithms, constants, behaviours, "
                          "limits). Decompile or inspect what you must; never guess what a tool could tell you. If nothing needs studying say so.", self.TOOLS_TEXT, self.context(s),
                          'Reply: {"findings": [{"about": "<what>", "text": "<what you learned, concrete>"}], "blueprint": {"reference_systems": [...], "assumptions": [...], "risks": [...]}}'])

    def p_design(self, s):
        return "\n".join([self.core(), "PASS=design", "Design the solution. Think out of the box: what must exist for the person NOT to fail? List components with responsibilities and interfaces, the data flow, the HOOKS "
                          "(events, checks, monitors, fallbacks) that keep it on track, and the TOOLS that do not exist yet but should (small local programs with examples: they will be built and tested before the "
                          "rest). Reuse local tools and models that already exist. Be honest in feasibility.", self.TOOLS_TEXT, self.context(s),
                          'Reply: {"design": {"architecture": "...", "components": [{"name", "responsibility", "interfaces": [], "tech"}], "data_flow": "...", "hooks": [{"name", "when", "does"}], '
                          '"tools_needed": [{"name": "kebab-name", "description": "inputs, output", "examples": [{"args": [], "expect": "text the tool prints"}], "reuse": "existing tool considered"}]}, '
                          '"feasibility": {...}, "risks": [...]}'])

    def p_plan(self, s):
        return "\n".join([self.core(), "PASS=plan", "Turn the design into a PLAN OF JOBS an automated agency can execute and verify. Rules: (1) every job small enough for one agent pass; (2) every job has an acceptance check "
                          "(`python path/to/script.py` or `python -m pytest ...`) OR a rubric a reviewer can score - code jobs need a check; (3) agents may never edit tests or the check of their own job, so YOU write the "
                          "acceptance scripts and tests, and they are installed before the job runs: in THIS reply give only `fixtures_spec` for each job - a short list of {path, asserts} saying which file(s) will check the job "
                          "and exactly which behaviours, adverse cases and exact values they must assert (no file text here: each file is written in its own pass, so keep this reply SHORT: goals under 600 characters, "
                          "at most 14 jobs); (4) every requirement id appears in some job's `covers`; "
                          "(5) `depends_on` lists job keys that must finish first (build tools first, then the parts, then integration, then end-to-end verification); (6) prefer deterministic steps that call a local "
                          "tool (step.tool = {\"tool\": \"calc|lean|git|...\", \"args\" or \"code\"}) over asking a model; (7) kind is code for jobs that change files in a project folder (give `target`, a folder path under the "
                          "person's project folder; invent none that the person did not give - use the one in the conversation, or ask), general for analysis/research/design/documents, production for long builds.",
                          self.context(s), 'Reply: {"plan": {"jobs": [{"key": "kebab-key", "title", "goal": "complete detailed instructions for the agent", "kind", "target", "check", "rubric", "covers": ["R1"], '
                          '"depends_on": ["other-key"], "phase": "1 Foundations", "steps": [{"objective", "needs": ["code"], "tool": {...}}], "fixtures_spec": [{"path": "tests/check_x.py", "asserts": "what it must verify"}]}]}, '
                          '"verification": "how the whole thing is verified end to end", "effort": {"local_tool_runs": 0, "subscription_runs": 0, "notes": ""}, "requirements": [... add derived requirements if missing]}'])

    def p_fixture(self, s, job):
        return "\n".join([self.core(), "PASS=fixtures", "Write the COMPLETE acceptance script(s) and tests for ONE job of the plan, exactly as specified. Rules: standard library only (unittest or plain asserts), deterministic, "
                          "fast, offline; they must FAIL on a project that lacks the work (never pass on an empty project: import the module and call it); test behaviour with exact values and adverse cases, not wording; "
                          "the first check file listed must exit 0 only when the whole job is done; agents will never be allowed to edit these files. If the job needs an external program (ffmpeg, a GPU, the network) "
                          "that the sandbox does not have, test the logic with a fake and leave the real run to a later job.",
                          "THE JOB: " + json.dumps(dict(key=job["key"], title=job["title"], goal=job["goal"], target=job.get("target"), check=job.get("check"), depends_on=job.get("depends_on"),
                                                        fixtures_spec=job.get("fixtures_spec")), ensure_ascii=False),
                          "DESIGN (components and interfaces the code must follow): " + clip(json.dumps((s["blueprint"] or {}).get("design") or {}, ensure_ascii=False), 7000),
                          "OTHER JOBS (so names and interfaces agree): " + "; ".join("%s: %s" % (j["key"], j["title"]) for j in (s["blueprint"].get("plan") or {}).get("jobs", [])),
                          'Reply: {"fixtures": {"<path from fixtures_spec>": "<complete file text>"}}'])

    def write_fixtures(self, sid, workers=4):
        """the plan names what each job's check must verify; the check files themselves are written here, one call per job, several at a time (each call is small, so none is cut off or takes half an hour)"""
        import concurrent.futures as cf
        s = self.intake.get(sid)
        jobs = [j for j in (s["blueprint"].get("plan") or {}).get("jobs", []) if j.get("fixtures_spec") and not j.get("fixtures")]
        if not jobs:
            return 0
        self.log(sid, "plan", "Writing the acceptance scripts and tests for %d job(s), %d at a time..." % (len(jobs), workers))

        def one(j):
            want = [e["path"] for e in j["fixtures_spec"]]
            have, who = {}, "nobody"
            for attempt in (1, 2, 3):
                missing = [p_ for p_ in want if p_ not in have]
                if not missing:
                    break
                prompt = self.p_fixture(self.intake.get(sid), j)
                if have:                                                                    # a check that reads a data file or helper that was never written can never pass: ask again for exactly the missing files
                    prompt += "\n\nALREADY WRITTEN (do not repeat): %s\nSTILL MISSING - write exactly these: %s" % (", ".join(have), ", ".join(missing))
                d, who = self.ask(sid, prompt, "fixtures", "fixtures")
                fx = BP.fixtures_of((d or {}).get("fixtures")) if isinstance(d, dict) else {}
                for k_, v_ in fx.items():
                    have.setdefault(k_, v_)
            if [p_ for p_ in want if p_ not in have]:
                self.log(sid, "plan", "Job %s: still missing %s after 3 tries." % (j["key"], ", ".join(p_ for p_ in want if p_ not in have)))
            return j["key"], have, who
        done = 0
        with cf.ThreadPoolExecutor(max_workers=workers) as ex:
            for key, fx, who in ex.map(one, jobs):
                if not fx:
                    self.log(sid, "plan", "No usable tests came back for job %s." % key)
                    continue

                def put(se, key=key, fx=fx):
                    for jj in (se["blueprint"].get("plan") or {}).get("jobs", []):
                        if jj["key"] == key:
                            jj["fixtures"] = fx
                self.intake.update(sid, put)
                done += 1
        self.log(sid, "plan", "Wrote the tests for %d of %d job(s)." % (done, len(jobs)))
        return done

    def p_critique(self, s, validation):
        fx = "\n".join("--- %s :: %s ---\n%s" % (j["key"], p, clip(t, 16000)) for j in (s["blueprint"].get("plan") or {}).get("jobs", []) for p, t in j.get("fixtures", {}).items())
        fx = clip(fx, 80000)
        return "\n".join([self.core(), "PASS=critique", "You are the adversarial reviewer. A different model made this blueprint. Attack it: uncovered or vague requirements, acceptance checks that would pass without the thing "
                          "working, hidden assumptions, missing hooks or tools, infeasible claims, wrong job order, security or safety problems, steps a deterministic tool should do instead of a model, wasted subscription "
                          "usage (work a local model or tool could do). READ THE FIXTURES BELOW LINE BY LINE: would each test fail on an empty project? does it test behaviour or just wording? is it deterministic, standard-library "
                          "only and fast? are adverse cases included? Be specific and cite job keys and requirement ids. ALSO CHECK, for every job, that its goal names exactly the same file paths, JSON field names, value types and formats as its fixtures (the protected check is the authority and the agent cannot change it): when the goal says one path or shape and the check asserts another, the job can NEVER pass - report it as a blocker with the exact mismatch. Do not rewrite the plan.", "FIXTURES (the acceptance scripts and tests):\n" + (fx or "(none)"),
                          "DETERMINISTIC CHECK RESULT: " + clip(json.dumps(validation, ensure_ascii=False), 20000), self.context(s),
                          'Reply: {"verdict": "ready|revise", "issues": [{"severity": "blocker|major|minor", "area": "...", "issue": "...", "fix": "..."}]}'])

    def p_revise(self, s, issues, validation):
        return "\n".join([self.core(), "PASS=revise", "Fix the blueprint: resolve every blocker the checker found and every blocker / major issue the reviewer raised (or say in a risk why not). Return the sections you change; they replace "
                          "the old ones, so keep what is right. Be BRIEF: return ONLY what changes. For the plan give just the jobs you change or add (full job objects, same key to replace one) under plan.jobs "
                          "and the keys of jobs to delete under remove_jobs; jobs you do not mention stay exactly as they are. If a change alters what a job's check must verify, give its new fixtures_spec (its test files are "
                          "rewritten afterwards); never repeat unchanged jobs, design sections or requirements.", "CHECKER BLOCKERS: " + clip(json.dumps(validation.get("blockers", []), ensure_ascii=False), 20000), "REVIEWER ISSUES: " + clip(json.dumps(issues, ensure_ascii=False), 40000),
                          self.context(s), 'Reply: {"blueprint": {"plan": {"jobs": [only the changed or new jobs]}, "remove_jobs": ["key"], "requirements": [only changed or added], "design": {only if it changes}, "verification": "only if it changes"}}'])

    def fixture_gate(self, bp):
        """-> (blockers, warnings): RED BEFORE GREEN. Every dependency-free code job's check, with the architect's fixtures installed in a copy of the project folder, must FAIL there: a check that already
        passes before any work is done proves nothing. Runs in the same Docker sandbox as every acceptance check."""
        import shutil, tempfile
        from ..agency.runner import CheckRunner
        runner = CheckRunner(self.app.data)
        blockers, warnings = [], []
        for j in (bp.get("plan") or {}).get("jobs", []):
            if not (j["check"] and j["fixtures"] and j["kind"] == "code" and not j["depends_on"]):
                continue
            tmp = tempfile.mkdtemp(prefix="redgreen_")
            try:
                if j["target"] and os.path.isdir(j["target"]):
                    shutil.copytree(j["target"], os.path.join(tmp, "p"), ignore=shutil.ignore_patterns(".git", "__pycache__", "node_modules"))
                else:
                    os.makedirs(os.path.join(tmp, "p"))
                for rel, text in j["fixtures"].items():
                    fp = os.path.join(tmp, "p", rel)
                    os.makedirs(os.path.dirname(fp), exist_ok=True)
                    with open(fp, "w", encoding="utf-8", newline="\n") as f:
                        f.write(text)
                try:
                    ok, out = runner.run(os.path.join(tmp, "p"), j["check"])
                except ValueError as e:
                    blockers.append("job %s: %s" % (j["key"], e))
                    continue
                if ok is True:
                    blockers.append("job %s: its check PASSES before any work is done (output: %s): a vacuous test proves nothing. Make it exercise the behaviour the job must create." % (j["key"], (out or "").strip()[-100:]))
                elif ok is None:
                    warnings.append("job %s: the red-before-green check could not run (%s)" % (j["key"], (out or "")[:80]))
            finally:
                shutil.rmtree(tmp, ignore_errors=True)
        return blockers, warnings

    def assess(self, bp):
        v = BP.validate(bp, self.app)
        b, w = self.fixture_gate(bp)
        if self.app.usage.autonomy.load()["enabled"]:                                         # nobody is watching: a test nobody could run is not a test
            b += [x.replace("could not run", "COULD NOT RUN (autonomous mode will not approve unverified tests)") for x in w if "could not run" in x]
            w = [x for x in w if "could not run" not in x]
        v["blockers"] += b
        v["warnings"] += w
        v["ok"] = not v["blockers"]
        return v

    # ------------------------------------------------------------ passes
    def apply(self, sid, d, who, phase):
        """fold a model's blueprint patch (and findings) into the session; the form follows"""
        patch = dict(d.get("blueprint") or {}) if isinstance(d, dict) else {}
        for k in ("findings", "design", "plan", "feasibility", "risks", "verification", "effort", "requirements"):
            if k in d and k not in patch:
                patch[k] = d[k]
        if phase == "revise" and isinstance(patch.get("plan"), dict):                      # a revision names only what changed: fold those jobs into the plan by key
            cur = (self.intake.get(sid).get("blueprint") or {}).get("plan") or {}
            old = {j["key"]: j for j in cur.get("jobs", [])}
            drop = set(map(str, (d.get("remove_jobs") or patch.get("remove_jobs") or [])))
            merged = [j for j in cur.get("jobs", []) if j["key"] not in drop]
            idx = {j["key"]: i for i, j in enumerate(merged)}
            for nj in patch["plan"].get("jobs", []):
                if not isinstance(nj, dict):
                    continue
                k = str(nj.get("key", ""))
                prev = old.get(k)
                if prev:                                                                    # a changed job keeps whatever the revision did not mention (its check, target, steps, tests ...)
                    spec_changed = bool(nj.get("fixtures_spec")) and nj["fixtures_spec"] != prev.get("fixtures_spec") and not nj.get("fixtures")
                    nj = dict(prev, **{kk: vv for kk, vv in nj.items() if vv not in (None, "", [], {})})
                    if spec_changed:
                        nj["fixtures"] = {}                                                 # what the check must verify changed: its test files are rewritten
                if k in idx:
                    merged[idx[k]] = nj
                else:
                    merged.append(nj)
            patch["plan"] = dict(cur, jobs=merged)
        have = len(((self.intake.get(sid).get("blueprint") or {}).get("plan") or {}).get("jobs") or [])
        got = len(((patch.get("plan") or {}).get("jobs")) or []) if isinstance(patch.get("plan"), dict) else None
        if got is not None and have >= 3 and got < have / 2.0:                              # a rewrite that throws away most of a good plan is a failed answer, not a revision
            self.log(sid, phase, "Ignored the %s: it came back with %d job(s) where the plan has %d." % (phase, got, have))
            patch.pop("plan", None)

        def fn(s):
            bp = BP.normalize(patch, s.get("blueprint") or {})
            s["blueprint"] = bp
            from . import chat
            s["form"] = BP.form_from_blueprint(bp, s["form"])
        self.intake.update(sid, fn)

    def interview(self, sid, text):
        s = self.intake.get(sid)
        self.log(sid, "interview", "Reading what you said...")
        d, who = self.ask(sid, self.p_interview(s), "reply", "interview")
        if d is None:
            self.intake.say(sid, "assistant", "No model answered just now. Your words are kept; press Send again in a minute, or edit the form directly.")
            self.log(sid, "interview", "Nobody could answer.")
            return
        self.apply(sid, d, who, "interview")
        self.intake.say(sid, "assistant", str(d.get("reply", "Noted."))[:2000], by=who)
        self.log(sid, "interview", "Answered by %s." % who)

    def think_through(self, sid, only_review=False):
        """study -> design -> plan -> (checker + a different model's attack -> revise)x2 -> ready or a list of what is still missing"""
        s = self.intake.get(sid)
        asked = set()
        strong_critic = False
        for phase, prompt_fn, rounds in (("study", self.p_study, MAX_ROUNDS), ("design", self.p_design, 3), ("plan", self.p_plan, 0)):
            if only_review:
                break
            s = self.intake.get(sid)
            if phase == "study" and not s["blueprint"].get("reference_systems") and not any(f["kind"] in ("text", "other") for f in s["files"]):
                self.log(sid, "study", "Nothing needs reverse engineering or studying first.")
                continue
            self.log(sid, phase, {"study": "Studying what has to be understood first (reverse engineering, references)...", "design": "Designing: components, hooks, the tools that must exist...", "plan": "Writing the plan of jobs, with acceptance checks and tests..."}[phase])
            d, who = self.ask(sid, prompt_fn(s), "findings" if phase == "study" else "design" if phase == "design" else "plan", phase, rounds=rounds)
            if d is None:
                self.log(sid, phase, "No model produced a usable %s. Try again in a minute." % phase)
                self.intake.update(sid, lambda se: se.update(phase="needs_you"))
                return
            asked.add(who)
            self.apply(sid, d, who, phase)
            if phase == "plan":
                self.write_fixtures(sid)
            self.log(sid, phase, "%s done by %s." % (phase.capitalize(), who))
        for rnd in range(MAX_REVISE + 1):
            s = self.intake.get(sid)
            v = self.assess(s["blueprint"])
            self.log(sid, "review", "Checker: %d blocker(s), %d warning(s). A different model now attacks the plan..." % (len(v["blockers"]), len(v["warnings"])))
            crit, cwho = self.ask(sid, self.p_critique(s, v), "issues", "review", exclude=tuple(asked))
            issues = (crit or {}).get("issues") or []
            serious = [i for i in issues if isinstance(i, dict) and i.get("severity") in ("blocker", "major")]
            strong_critic = strong_critic or (crit is not None and cwho not in ("local model", "nobody"))
            self.intake.update(sid, lambda se: se.update(critique=dict(by=cwho, verdict=(crit or {}).get("verdict"), issues=issues[:20], round=rnd, strong=strong_critic)))
            self.log(sid, "review", "%s found %d serious issue(s)." % (cwho, len(serious)) if crit else "No second model was available to review it: the checker alone decides.")
            if (not v["blockers"] and not serious) or rnd == MAX_REVISE:
                break
            self.log(sid, "revise", "Fixing the blockers and issues (round %d)..." % (rnd + 1))
            d, who = self.ask(sid, self.p_revise(s, serious, v), "blueprint", "revise")
            if d is None:
                self.log(sid, "revise", "No strong model could revise it just now.")
                break
            self.apply(sid, d, who, "revise")
            self.write_fixtures(sid)                                                       # any job whose checks changed or that is new gets its test files written
        s = self.intake.get(sid)
        v = self.assess(s["blueprint"])
        extra, seen_blockers = 0, None
        while v["blockers"] and extra < 3 and v["blockers"] != seen_blockers:                 # stop as soon as a round changes nothing: the same blockers again means this model cannot fix them                                                 # mechanical blockers (a dependency on a job that no longer exists, a missing test file) are fixed without another attack round
            extra += 1
            seen_blockers = list(v["blockers"])
            self.log(sid, "revise", "The checker still has %d blocker(s): fixing just those (extra round %d)..." % (len(v["blockers"]), extra))
            d, who = self.ask(sid, self.p_revise(s, [], v), "blueprint", "revise")
            if d is None:
                break
            self.apply(sid, d, who, "revise")
            self.write_fixtures(sid)
            s = self.intake.get(sid)
            v = self.assess(s["blueprint"])
        self.intake.update(sid, lambda se: se.update(phase="ready" if v["ok"] else "needs_you", readiness=v))
        self.log(sid, "ready" if v["ok"] else "needs_you", "The plan is ready for your approval." if v["ok"] else "Not ready: %s" % "; ".join(v["blockers"][:3]))
        if v["ok"] and s.get("autonomous") and self.app.usage.autonomy.load()["enabled"] and not strong_critic:
            tries = int(s.get("review_tries", 0)) + 1                                              # nobody is watching: a plan nobody strong has attacked is not approved, it waits
            if tries <= 8:
                self.intake.update(sid, lambda se: se.update(phase="waiting_review", review_tries=tries, review_at=time.time() + 1800))
                self.log(sid, "waiting_review", "The plan passed the checker but no strong second model was available to attack it (limits / cooling). Waiting 30 minutes, then trying again (%d of 8)." % tries)
                return
        if v["ok"] and s.get("autonomous") and self.app.usage.autonomy.load()["enabled"]:
            try:
                made = self.submit_plan(sid, "architect (autonomous)")
                self.intake.update(sid, lambda se: se.update(phase="running"))
                self.log(sid, "running", "Autonomous: the plan passed every gate. %d job(s) created and queued; the agency runs them and the supervisor repairs failures." % len(made))
                self.intake.say(sid, "assistant", "The plan passed every gate (the checker, a second model, red-before-green tests). %d job(s) are queued and running. I will supervise and repair within my budget." % len(made))
                return
            except BoardError as e:
                self.log(sid, "needs_you", "Autonomous approval stopped: %s" % str(e)[:200])
                v = dict(v, ok=False, blockers=v["blockers"] + [str(e)[:200]])
        self.intake.say(sid, "assistant", ("The blueprint is ready: %d job(s), every requirement traced and every job verifiable. Read it on the right and press Create." % len(s["blueprint"]["plan"]["jobs"])) if v["ok"] else
                        "I could not make the plan ready yet. What is missing:\n- " + "\n- ".join(v["blockers"][:6]) + "\nAnswer the open questions or tell me more, then press Think it through again.")

    # ------------------------------------------------------------ running a pass in the background
    def start(self, sid, action, text=""):
        def work():
            try:
                self.intake.update(sid, lambda s: s.update(work=dict(state="running", action=action, started=time.time())))
                if action == "interview":
                    self.interview(sid, text)
                elif action == "review":
                    self.think_through(sid, only_review=True)
                elif action == "auto":                                                             # the whole chain: understand, study, design, plan, attack, (create)
                    self.interview(sid, text)
                    self.think_through(sid)
                else:
                    self.think_through(sid)
            except Exception as e:
                self.log(sid, "", "Stopped by an error: %s: %s" % (e.__class__.__name__, str(e)[:200]))
            finally:
                self.intake.update(sid, lambda s: s.update(work=dict(state="idle", action=action, finished=time.time())))
        s = self.intake.get(sid)
        if (s.get("work") or {}).get("state") == "running" and time.time() - (s["work"].get("started") or 0) < 3600:
            raise BoardError("busy", "I am still thinking about the last thing: wait for it to finish")
        t = threading.Thread(target=work, daemon=True)
        self.threads[sid] = t
        self.intake.update(sid, lambda se: se.update(work=dict(state="running", action=action, started=time.time())))
        t.start()
        return t

    def p_repair(self, s, job, failures):
        return "\n".join([self.core(), "PASS=repair", "A job of the plan failed or is stuck. Read why, decide whether the GOAL, the TESTS or the PLAN was wrong, and return ONE replacement job (smaller and clearer if it was too big; "
                          "with corrected or added fixtures if the tests were wrong; a different approach if the same one failed twice). If the failure shows the requirement is infeasible, say so in `note` and set "
                          "`give_up` true. Keep the same covers and depends_on unless they were the problem.", "FAILED JOB: " + json.dumps(dict(key=job["key"], title=job["title"], state=job["state"], why=job["why"], goal=job["goal"], steps=job["steps"]), ensure_ascii=False),
                          "FAILURE POINTS (from its workflow record): " + clip(json.dumps(failures, ensure_ascii=False), 30000), self.context(s),
                          'Reply: {"note": "what was wrong", "give_up": false, "job": {"key", "title", "goal", "kind", "target", "check", "rubric", "covers": [], "depends_on": [], "steps": [...], "fixtures": {}}} - or, if the job was '
                          'too big or two things at once, "jobs": [ ...2 to 4 smaller jobs in order, each with its own goal and fixtures... ] instead of "job"'])

    def repair(self, sid, key, history=None, rewrite=False):
        """-> the replacement job (the first of them), or None. A LADDER, cheapest first: (1) the same job again at a stronger model with the failure written into its goal (no architect call);
        (2) the architect rewrites the job: clearer goal, corrected tests, or a SPLIT into smaller jobs; (3) one more architect attempt with everything learned; then it is the person's. Budgeted
        per plan (at least the configured number, more for bigger plans), bounded per job, logged."""
        s = self.intake.get(sid)
        cfg = self.app.usage.autonomy.load()
        reps = s.get("repairs") or {}
        made = s.get("made") or {}
        cap = max(cfg["max_repairs_per_plan"], len(made) // 2)
        step = reps.get(key, 0)
        jid = made.get(key)
        if sum(reps.values()) >= cap or step >= 3 or not jid:
            return None
        j = self.app.jobs.get(jid)
        spec = next((x for x in s["blueprint"]["plan"]["jobs"] if x["key"] == key), None)
        if not spec:
            return None
        failures = (self.app.campaign.governor.workflow(j, j["state"]) or {}).get("failure_points", [])
        if history:                                                                                # the watchdog hands over every failed copy: the re-plan must see all of them, not only the last
            failures = list(failures) + [dict(reason="earlier copy %s %s: %s" % (h.get("id"), h.get("state"), str(h.get("why") or "")[:2000])) for h in history[:8]]
        why = j.get("why") or ""
        if rewrite and step == 0:
            step = 1                                                                               # copies of it already failed again and again: another identical retry is pointless, go straight to a rewrite
        self.intake.update(sid, lambda se: se.update(repairs=dict(se.get("repairs") or {}, **{key: step + 1})))
        if step == 0:
            # free-zone first retry: OpenRouter Nemotron / other :free models (or local), NOT Claude premium — stops smashing session limits
            self.log(sid, "repair", "Job %s (%s) is %s: retrying once in the FREE zone (OpenRouter Nemotron / local), not Claude..." % (jid, key, j["state"]))
            # the failure note is the point of this retry: the goal gives way, never the note (it was cut off at 3500 characters)
            note = ("\n\nA PREVIOUS ATTEMPT FAILED. Do not repeat it. Why: %s. Failure points: %s"
                    "\nUse a stronger FREE OpenRouter model (Nemotron super/ultra/nano-reasoning, Gemma 31B, Inkling) or a capable local coder. Stay off paid Claude for this retry."
                    % (why, "; ".join(f.get("reason", "")[:2000] for f in failures[:6])))
            return self._replace(sid, key, [dict(spec, goal=(spec["goal"][:max(1000, CAPS["goal"] - len(note))] + note)[:CAPS["goal"]])], jid, "free-zone stronger model",
                                 min_tier="", priority=2, prefer_agent="openrouter", prefer_cost="free", free_zone=True)
        self.log(sid, "repair", "Job %s (%s) failed again: asking the architect to rewrite or split it (%d of %d used)..." % (jid, key, sum(reps.values()) + 1, cap))
        d, who = self.ask(sid, self.p_repair(s, dict(spec, state=j["state"], why=why), failures), "note", "repair")
        if not d or d.get("give_up"):
            self.log(sid, "repair", "No repair for %s: %s" % (key, str((d or {}).get("note", "no answer"))[:200]))
            return None
        raw = d.get("jobs") if isinstance(d.get("jobs"), list) and len(d["jobs"]) >= 1 else [d.get("job")] if isinstance(d.get("job"), dict) else []
        specs = []
        for i, x in enumerate(raw[:4]):
            nj = BP.normalize(dict(plan=dict(jobs=[dict(x, key="%s-r%d" % (key, i + 1))])), {}).get("plan", {}).get("jobs", [])
            if nj:
                specs.append(nj[0])
        if not specs:
            return None
        # rewrite/split still prefers free zone unless nothing free/local can take the needs
        return self._replace(sid, key, specs, jid, who, priority=2, prefer_agent="openrouter", prefer_cost="free", free_zone=True)

    def _replace(self, sid, key, specs, old_jid, who, min_tier="", priority=2, prefer_agent="", prefer_cost="", free_zone=False):
        """create the replacement job(s) (a chain when it is a split), install their fixtures, move everything that waited for the old job to the last replacement, cancel the old one"""
        s = self.intake.get(sid)
        made = s.get("made") or {}
        old = self.app.jobs.get(old_jid)
        orig = next((x for x in s["blueprint"]["plan"]["jobs"] if x["key"] == key), {})
        deps = [made[k] for k in orig.get("depends_on", []) if k in made]
        prev, first = None, None
        for i, nj in enumerate(specs):
            tgt = nj.get("target") or orig.get("target", "")
            for rel, text in (nj.get("fixtures") or {}).items():
                if tgt:
                    p = os.path.join(tgt, rel)
                    os.makedirs(os.path.dirname(p), exist_ok=True)
                    with open(p, "w", encoding="utf-8", newline="\n") as f:
                        f.write(text)
            acc = {a: nj.get(a) for a in ("check", "rubric") if nj.get(a)} or {a: orig.get(a) for a in ("check", "rubric") if orig.get(a)}
            meta = dict(depends_on_jobs=([prev] if prev else deps))
            if min_tier:
                meta["min_tier"] = min_tier
            if prefer_agent:
                meta["prefer_agent"] = prefer_agent
            if prefer_cost:
                meta["prefer_cost"] = prefer_cost
            if free_zone:
                meta["free_zone"] = True
            if not meta["depends_on_jobs"]:
                meta.pop("depends_on_jobs")
            tag = "free-zone retry" if free_zone else "stronger model"
            plain_retry = bool(min_tier) or (free_zone and nj.get("title") in (old["title"], orig.get("title"), None, ""))        # the same job again; a rewrite or a split piece keeps the title the architect gave it
            base_title = re.sub(r"(\s*\(retry, [^)]*\))+$", "", old["title"])                                        # never pile up "(retry, ...)" suffixes
            title = ("%s (retry, %s)" % (base_title, tag))[:120] if plain_retry or not nj.get("title") else nj["title"]
            job = self.app.jobs.create("architect (repair)", title, nj["goal"], nj.get("kind") or orig.get("kind", "general"), tgt, acc, None, priority, meta or None)
            steps = nj.get("steps") or orig.get("steps") or []
            if steps:
                job = self.app.jobs.add_steps(job["id"], steps, "architect (repair)")
            prev, first = job["id"], first or job
        if old["state"] not in ("merged", "delivered", "failed", "cancelled"):
            self.app.jobs.cancel(old_jid, "architect", "replaced by %s" % first["id"])
        n = self.app.jobs.replace_dependency(old_jid, prev)
        self.intake.update(sid, lambda se: se.update(made=dict(se.get("made") or {}, **{key: prev}), job=(se.get("job") or "") + "," + prev))
        self.log(sid, "repair", "Replacement %s for %s (%s)%s." % (", ".join(["job " + first["id"]] + ([prev] if prev != first["id"] else [])), key, who, ("; %d waiting job(s) now wait for it" % n) if n else ""))
        return first

    # ------------------------------------------------------------ the approved plan becomes jobs
    def submit_plan(self, sid, actor, allow_missing_folders=True):
        """-> [jobs]. Checks readiness, installs the plan's fixtures (acceptance scripts) into the project folders, queues the tool builds, then creates the jobs in dependency order."""
        s = self.intake.get(sid)
        bp = s["blueprint"]
        v = self.assess(bp)
        if not v["ok"]:
            raise BoardError("not_ready", "the plan is not ready: " + "; ".join(v["blockers"][:4]))
        if s["status"] != "open":
            raise BoardError("closed", "this intake already became jobs")
        jobs = bp["plan"]["jobs"]
        by = {j["key"]: j for j in jobs}
        tg = sorted({j["target"] for j in jobs if j["target"] and (j["check"] or j["kind"] == "code")})
        bad = [t for t in tg if self.app.jobs.target_ok and not self.app.jobs.target_ok(t)]
        if bad:
            raise BoardError("bad_target", "the target folder(s) %s are not one of the allowed project folders: agents may only work on copies of folders you allow (Allow it on the Agency page, or python -m agentdispatch nightshift setup --root FOLDER)" % ", ".join(bad))
        for t in tg:
            if allow_missing_folders:
                os.makedirs(t, exist_ok=True)
        for j in jobs:                                                                            # fixtures: the acceptance scripts the architect wrote, put in place before anything runs
            for rel, text in j["fixtures"].items():
                p = os.path.join(j["target"], rel)
                if os.path.exists(p) and open(p, encoding="utf-8", errors="replace").read() != text:
                    raise BoardError("fixture_exists", "%s already exists in %s with different content: the plan will not overwrite it" % (rel, j["target"]))
                os.makedirs(os.path.dirname(p), exist_ok=True)
                with open(p, "w", encoding="utf-8", newline="\n") as f:
                    f.write(text)
        from ..campaign import engineer
        cfg = self.app.usage.autonomy.load()
        build_dir = os.path.join(cfg["projects_root"], "_tools") if cfg["enabled"] and s.get("autonomous") else None              # inside the safe tree: accepted tool builds are applied by themselves
        tool_jobs, by_tool = [], {}
        for t in (bp.get("design") or {}).get("tools_needed", []):
            r = engineer.need_tool(self.app.localtools, self.app.jobs, self.app.campaign.governor, t["name"], t["description"] or t["name"], t["examples"], actor, build_dir=build_dir)
            if r.get("action") == "build":
                tool_jobs.append(r["job"])
                by_tool[t["name"]] = r["job"]
            elif not r.get("ok"):
                raise BoardError(r.get("error", "tool"), "the tool %s that the plan needs could not be queued: %s" % (t["name"], r.get("message", "")))
        order, _ = BP.topo(jobs)
        made = {}
        for k in order:
            j = by[k]
            acc = {a: j[a] for a in ("check", "rubric") if j[a]}
            text = (j["goal"] + " " + " ".join(st["objective"] + str(st.get("tool", "")) for st in j["steps"])).lower()
            uses = [jid for name, jid in by_tool.items() if name in text]                          # only a job that actually uses a tool waits for it
            meta = dict(depends_on_jobs=[made[d] for d in j["depends_on"] if d in made] + uses, template=j["template"] if j["template"] else "", gate=j["phase"][:60]) if (j["depends_on"] or uses or j["phase"]) else None
            if meta and not meta.get("template"):
                meta.pop("template")
            job = self.app.jobs.create(actor, j["title"], j["goal"], j["kind"], j["target"], acc, None, 1, meta)
            if j["steps"]:
                job = self.app.jobs.add_steps(job["id"], j["steps"], actor)
            made[k] = job["id"]
        self.intake.update(sid, lambda se: se.update(status="submitted", job=",".join(made.values()), made=made))
        return [self.app.jobs.get(i) for i in tool_jobs + list(made.values())]
