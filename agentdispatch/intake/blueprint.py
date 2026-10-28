"""the BLUEPRINT: everything the architect works out before any job exists - what is wanted and why, whether it is feasible, requirements with priorities, assumptions, open questions, risks, what to
study first, the design (components, interfaces, hooks, tools that must exist), and a plan of jobs with dependencies, acceptance checks and a trace from every requirement to the jobs
that deliver it. `normalize` makes any model output safe to store; `validate` is the deterministic readiness gate: a plan with an uncovered requirement, a job nobody could verify, a missing tool
nobody is building, an unanswered question without a default or a dependency loop is NOT ready, however good it reads."""
import re, shlex

from ..agency.jobs import CAPS, KINDS, NEEDS
from ..nightshift import command_ok

PRIORITIES = ("must", "should", "could")
MAX_JOBS = 16
BINARY_EXAMPLE = (".wav", ".mp3", ".png", ".jpg", ".jpeg", ".pdf", ".zip", ".stl", ".step", ".fcstd", ".mp4", ".bin", ".onnx", ".pt")
MAX_REQ = 60


def _s(v, n=400):
    return str(v).strip()[:n] if isinstance(v, (str, int, float)) and not isinstance(v, bool) else ""


def _list(v, n=40):
    return v if isinstance(v, list) else []


def _strs(v, n=40, w=300):
    return [_s(x, w) for x in _list(v)[:n] if _s(x, w)]


def key_of(text, taken):
    k = re.sub(r"[^a-z0-9]+", "-", (text or "job").lower()).strip("-")[:24] or "job"
    base, i = k, 2
    while k in taken:
        k, i = "%s-%d" % (base, i), i + 1
    return k


def specs_of(v):
    """what each fixture file must verify, written in the outline pass; the file text itself is written later, one job at a time"""
    out = []
    for e in (v if isinstance(v, list) else [])[:8]:
        if isinstance(e, dict) and e.get("path"):
            p = str(e["path"]).replace("\\", "/").lstrip("/")
            if p and ".." not in p.split("/") and not re.match(r"^[A-Za-z]:", p):
                out.append(dict(path=p[:200], asserts=_s(e.get("asserts") or e.get("purpose"), 600)))
    return out


def fixtures_of(v):
    """files the architect writes into the project BEFORE the job runs (the acceptance script, tests, seed data): agents may never edit a test or the check of their own job, so whoever approves the plan
    approves these. Relative paths only, no escapes, bounded."""
    out = {}
    for path, text in (v.items() if isinstance(v, dict) else []):
        p = str(path).replace("\\", "/").lstrip("/")
        if not p or ".." in p.split("/") or re.match(r"^[A-Za-z]:", p) or not isinstance(text, str) or len(text) > 40000 or len(out) >= 8:
            continue
        out[p[:200]] = text
    return out


def normalize(bp, old=None):
    """-> a clean blueprint. Sections the input lacks keep the old value; nothing unknown is kept."""
    old = old or {}
    bp = bp if isinstance(bp, dict) else {}
    out = dict(old)

    def has(k):
        return k in bp and bp[k] not in (None, "")
    for k, n in (("title", 120), ("summary", 2500), ("verification", 1500)):
        if has(k):
            out[k] = _s(bp[k], n)
    if isinstance(bp.get("feasibility"), dict):
        f = bp["feasibility"]
        v = _s(f.get("verdict"), 20).lower()
        out["feasibility"] = dict(verdict=v if v in ("feasible", "partly", "not_feasible", "unknown") else "unknown", why=_s(f.get("why"), 800), alternatives=_strs(f.get("alternatives"), 6))
    for k in ("goals", "non_goals"):
        if isinstance(bp.get(k), list):
            out[k] = _strs(bp[k], 20)
    if isinstance(bp.get("requirements"), list):
        reqs, ids = [], set()
        for i, r in enumerate(bp["requirements"][:MAX_REQ], 1):
            r = r if isinstance(r, dict) else dict(text=r)
            t = _s(r.get("text"), 4000)
            if not t:
                continue
            rid = _s(r.get("id"), 8)
            rid = rid if re.match(r"^R\d+$", rid) and rid not in ids else "R%d" % i
            while rid in ids:
                rid += "x"
            ids.add(rid)
            pr = _s(r.get("priority"), 8).lower()
            reqs.append(dict(id=rid, text=t, priority=pr if pr in PRIORITIES else "must", source=_s(r.get("source"), 10) or "user", acceptance=_s(r.get("acceptance"), 4000)))
        out["requirements"] = reqs
    if isinstance(bp.get("assumptions"), list):
        out["assumptions"] = [dict(text=_s(a.get("text") if isinstance(a, dict) else a, 300), risk=_s(a.get("risk") if isinstance(a, dict) else "", 200)) for a in bp["assumptions"][:30] if _s(a.get("text") if isinstance(a, dict) else a, 300)]
    if isinstance(bp.get("open_questions"), list):
        qs = []
        for i, q in enumerate(bp["open_questions"][:20], 1):
            q = q if isinstance(q, dict) else dict(question=q)
            if _s(q.get("question"), 300):
                qid = _s(q.get("id"), 8)
                qs.append(dict(id=qid if re.match(r"^Q\d+$", qid) else "Q%d" % i, question=_s(q.get("question"), 300), why=_s(q.get("why"), 200), default=_s(q.get("default"), 200), answered=_s(q.get("answered"), 400)))
        out["open_questions"] = qs
    if isinstance(bp.get("risks"), list):
        out["risks"] = [dict(risk=_s(r.get("risk") if isinstance(r, dict) else r, 300), mitigation=_s(r.get("mitigation") if isinstance(r, dict) else "", 300)) for r in bp["risks"][:20] if _s(r.get("risk") if isinstance(r, dict) else r, 300)]
    if isinstance(bp.get("reference_systems"), list):
        out["reference_systems"] = [dict(name=_s(r.get("name"), 120), why=_s(r.get("why"), 300), how_to_study=_s(r.get("how_to_study"), 300)) for r in bp["reference_systems"][:10] if isinstance(r, dict) and _s(r.get("name"), 120)]
    if isinstance(bp.get("findings"), list):
        out["findings"] = [dict(about=_s(r.get("about"), 120), text=_s(r.get("text"), 1200)) for r in bp["findings"][:30] if isinstance(r, dict) and _s(r.get("text"), 1200)]
    if isinstance(bp.get("design"), dict):
        d = bp["design"]
        comps = []
        for c in _list(d.get("components"))[:30]:
            if isinstance(c, dict) and _s(c.get("name"), 80):
                comps.append(dict(name=_s(c["name"], 80), responsibility=_s(c.get("responsibility"), 300), interfaces=_strs(c.get("interfaces"), 8, 160), tech=_s(c.get("tech"), 120)))
        tools = []
        for t in _list(d.get("tools_needed"))[:12]:
            if isinstance(t, dict) and re.match(r"^[a-z0-9_-]{2,40}$", _s(t.get("name"), 40).lower()):
                ex = [dict(args=_strs(e.get("args"), 6, 80), expect=_s(e.get("expect"), 120)) for e in _list(t.get("examples"))[:5] if isinstance(e, dict) and _s(e.get("expect"), 120)]
                tools.append(dict(name=_s(t["name"], 40).lower(), description=_s(t.get("description"), 500), examples=ex, reuse=_s(t.get("reuse"), 120)))
        out["design"] = dict(architecture=_s(d.get("architecture"), 2000), components=comps, data_flow=_s(d.get("data_flow"), 1000),
                             hooks=[dict(name=_s(h.get("name"), 80), when=_s(h.get("when"), 200), does=_s(h.get("does"), 300)) for h in _list(d.get("hooks"))[:20] if isinstance(h, dict) and _s(h.get("name"), 80)], tools_needed=tools)
    if isinstance(bp.get("plan"), dict) and isinstance(bp["plan"].get("jobs"), list):
        jobs, keys = [], set()
        for j in bp["plan"]["jobs"][:MAX_JOBS]:
            if not isinstance(j, dict) or not _s(j.get("title"), 120) or not _s(j.get("goal"), CAPS["goal"]):
                continue
            k = _s(j.get("key"), 30).lower()
            k = k if re.match(r"^[a-z0-9-]{1,30}$", k) and k not in keys else key_of(j["title"], keys)
            keys.add(k)
            steps = []
            for s in _list(j.get("steps"))[:12]:
                s = s if isinstance(s, dict) else dict(objective=s)
                if not _s(s.get("objective"), CAPS["objective"]):
                    continue
                st = dict(objective=_s(s["objective"], CAPS["objective"]), needs=[n for n in _strs(s.get("needs"), 6, 20) if n in NEEDS] or ["research"])
                if isinstance(s.get("tool"), dict) and isinstance(s["tool"].get("tool"), str):
                    st["tool"] = {k2: v for k2, v in s["tool"].items() if k2 in ("tool", "args", "code", "file", "expect", "timeout", "project", "cwd")}
                    st["needs"] = ["tool"]
                if _s(s.get("hint"), 200):
                    st["hint"] = _s(s["hint"], 200)
                steps.append(st)
            kind = _s(j.get("kind"), 12)
            jobs.append(dict(key=k, title=_s(j["title"], 120), goal=_s(j["goal"], CAPS["goal"]), kind=kind if kind in KINDS else "general", target=_s(j.get("target"), 300), check=_s(j.get("check"), CAPS["check"]), rubric=_s(j.get("rubric"), CAPS["rubric"]),
                             covers=[r for r in _strs(j.get("covers"), 30, 8) if re.match(r"^R\d+", r)], depends_on=[d for d in _strs(j.get("depends_on"), 10, 30)], template=_s(j.get("template"), 40), phase=_s(j.get("phase"), 60),
                             steps=steps, fixtures=fixtures_of(j.get("fixtures")), fixtures_spec=specs_of(j.get("fixtures_spec"))))
        before = {j.get("key"): j for j in ((old.get("plan") or {}).get("jobs") or [])}
        for j in jobs:                                                                    # a revised plan that leaves out what was already written must not lose it
            prev = before.get(j["key"])
            if prev and not j["fixtures"] and prev.get("fixtures"):
                j["fixtures"] = prev["fixtures"]
            if prev and not j["fixtures_spec"] and prev.get("fixtures_spec"):
                j["fixtures_spec"] = prev["fixtures_spec"]
        out["plan"] = dict(jobs=jobs)
    if isinstance(bp.get("effort"), dict):
        out["effort"] = dict(notes=_s(bp["effort"].get("notes"), 500), local_tool_runs=int(bp["effort"].get("local_tool_runs") or 0) if str(bp["effort"].get("local_tool_runs", "0")).isdigit() else 0,
                             subscription_runs=int(bp["effort"].get("subscription_runs") or 0) if str(bp["effort"].get("subscription_runs", "0")).isdigit() else 0)
    return out


def topo(jobs):
    """-> (ordered keys, loop keys): dependencies first"""
    keys = {j["key"] for j in jobs}
    deps = {j["key"]: [d for d in j["depends_on"] if d in keys and d != j["key"]] for j in jobs}
    done, order, temp = set(), [], set()
    loop = []

    def visit(k):
        if k in done:
            return
        if k in temp:
            loop.append(k)
            return
        temp.add(k)
        for d in deps[k]:
            visit(d)
        temp.discard(k)
        done.add(k)
        order.append(k)
    for j in jobs:
        visit(j["key"])
    return order, sorted(set(loop))


def validate(bp, app=None):
    """-> dict(ok, blockers, warnings, coverage, order). Blockers stop submission; warnings are shown."""
    blockers, warnings = [], []
    reqs = bp.get("requirements") or []
    jobs = (bp.get("plan") or {}).get("jobs") or []
    if not (bp.get("title") and bp.get("summary")):
        blockers.append("the blueprint has no title and summary yet")
    if not reqs:
        blockers.append("no requirements were written down")
    if not jobs:
        blockers.append("there is no plan: no jobs")
    f = bp.get("feasibility") or {}
    if f.get("verdict") == "not_feasible":
        blockers.append("the architect judged this NOT feasible as asked: %s (alternatives: %s)" % (f.get("why", ""), "; ".join(f.get("alternatives", [])) or "none given"))
    open_q = [q for q in bp.get("open_questions", []) if not q.get("answered") and not q.get("default")]
    if open_q:
        blockers.append("unanswered question(s) with no default: " + "; ".join("%s %s" % (q["id"], q["question"][:80]) for q in open_q[:4]))
    keys = {j["key"] for j in jobs}
    coverage = {r["id"]: [j["key"] for j in jobs if r["id"] in j["covers"]] for r in reqs}
    for r in reqs:
        if not coverage[r["id"]] and r["priority"] != "could":
            blockers.append("requirement %s (%s) is not delivered by any job" % (r["id"], r["text"][:60]))
    for j in jobs:
        bad = [d for d in j["depends_on"] if d not in keys]
        if bad:
            blockers.append("job %s depends on unknown job(s) %s" % (j["key"], ", ".join(bad)))
        if not (j["check"] or j["rubric"]):
            blockers.append("job %s has no way to be verified: give it an acceptance check or a rubric" % j["key"])
        if j["check"]:
            try:
                ok = command_ok(shlex.split(j["check"]))
            except ValueError:
                ok = False
            if not ok:
                blockers.append("job %s: the check %r is not an allowed command (python <script.py> or python -m pytest ...)" % (j["key"], j["check"][:60]))
        if j["check"]:
            try:
                scripts = [a for a in shlex.split(j["check"])[1:] if a.endswith(".py")]
            except ValueError:
                scripts = []
            have = {p.lower() for p in j.get("fixtures", {})}
            import os
            for sc in scripts:
                if sc.replace("\\", "/").lower() not in have and not (j["target"] and os.path.isfile(os.path.join(j["target"], sc))):
                    blockers.append("job %s: the check script %s does not exist yet and the plan does not provide it (put it in the job's fixtures)" % (j["key"], sc))
        if j["kind"] == "code" and not j["target"]:
            blockers.append("code job %s has no project folder" % j["key"])
        if j["target"] and app is not None and j["check"] and app.jobs.target_ok and not app.jobs.target_ok(j["target"]):
            warnings.append("job %s: the folder %s is not allowed yet (you will be asked to allow it when the jobs are created)" % (j["key"], j["target"]))
        if not j["steps"]:
            warnings.append("job %s has no steps: an agent will plan it" % j["key"])
    order, loop = topo(jobs)
    if loop:
        blockers.append("the plan has a dependency loop through: " + ", ".join(loop))
    for t in (bp.get("design") or {}).get("tools_needed", []):
        for ex in t.get("examples", []):                                                        # a tool's examples are its acceptance test: they run in the sandbox, on relative paths, with no host files
            bad = [a for a in ex.get("args", []) if re.match(r"^([A-Za-z]:|/)", a) or a.lower().endswith(BINARY_EXAMPLE)]
            if bad:
                blockers.append("tool %s: its example uses %s, which cannot exist in the sandbox: use relative paths and text data (or a --fake mode)" % (t["name"], bad[0][:50]))
                break
        if app is not None:
            from ..campaign import engineer
            hit = engineer.find_existing(app.localtools, t["description"] or t["name"])
            if hit and hit["available"]:
                warnings.append("tool %s: %s already does this: reuse it" % (t["name"], hit["name"]))
            elif not any(j.get("template") == "build-a-tool" and t["name"] in j["key"] + j["title"].lower() for j in jobs):
                warnings.append("tool %s is needed: a build job will be added to the plan" % t["name"])
    if not (bp.get("verification") or "").strip():
        warnings.append("no overall verification strategy was written")
    return dict(ok=not blockers, blockers=blockers, warnings=warnings, coverage=coverage, order=order)


def form_from_blueprint(bp, form):
    """the plain job form that mirrors the blueprint (what the page shows next to it)"""
    f = dict(form)
    if bp.get("title"):
        f["title"] = bp["title"][:120]
    goal = bp.get("summary", "")
    if bp.get("goals"):
        goal += "\n\nGoals:\n" + "\n".join("- " + g for g in bp["goals"])
    if goal:
        f["goal"] = goal[:CAPS["goal"]]
    if bp.get("requirements"):
        f["requirements"] = ["%s %s" % (r["id"], r["text"]) for r in bp["requirements"]][:40]
    jobs = (bp.get("plan") or {}).get("jobs") or []
    if jobs:
        f["kind"] = jobs[0]["kind"] if len(jobs) == 1 else "general"
    return f
