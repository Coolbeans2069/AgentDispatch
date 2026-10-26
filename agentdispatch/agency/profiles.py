"""what each agent can really do and how to prompt it (agent_profiles.json next to this file; <data>/agent_profiles.json overrides fields), merged at prompt time with what is true NOW:
available or not (and why), cooling down, and how often it has really finished each kind of work (route_stats). The planner and the architect read this instead of guessing."""
import copy, json, os
from ..textfit import fit

HERE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "agent_profiles.json")


def load(data_dir=None):
    try:
        with open(HERE, encoding="utf-8") as f:
            base = json.load(f)
    except (OSError, ValueError):
        base = {"agents": {}}
    base.setdefault("agents", {})
    if data_dir:
        p = os.path.join(data_dir, "agent_profiles.json")
        try:
            with open(p, encoding="utf-8") as f:
                over = json.load(f)
            for aid, d in (over.get("agents") or {}).items():
                if isinstance(d, dict):
                    base["agents"].setdefault(aid, {}).update(d)
        except (OSError, ValueError, AttributeError):
            pass
    return base


def route_stats(jobs):
    """-> {agent: {kind: (ok, n)}}"""
    out = {}
    try:
        for r in jobs.db.execute("SELECT agent, kind, ok, n FROM route_stats").fetchall():
            out.setdefault(r["agent"], {})[r["kind"]] = (r["ok"], r["n"])
    except Exception:
        pass
    return out


def live(reg, av=None, jobs=None, data_dir=None):
    """-> [profile dicts with id, strengths, cost, available, why, cooling_until, success] for every enabled agent"""
    prof = load(data_dir)["agents"]
    stats = route_stats(jobs) if jobs is not None else {}
    out = []
    for aid, a in reg.agents.items():
        if not a.get("enabled", True):
            continue
        p = copy.deepcopy(prof.get(aid) or {"note": "no profile written: capabilities unknown"})
        p.update(id=aid, strengths=list(a.get("strengths") or []), cost=a.get("cost"), via=(a.get("run") or {}).get("via") or "cli")
        if av is not None:
            try:
                ok, why = (av.check_pull(a) if p["via"] == "pull" else av.check(a))
            except Exception as e:
                ok, why = False, "availability check failed: %s" % e
            p["available"], p["why"] = bool(ok), why
        if jobs is not None:
            try:
                p["cooling_until"] = jobs.cooldown_until(aid) or None
            except Exception:
                p["cooling_until"] = None
        p["success"] = {k: "%d/%d" % v for k, v in (stats.get(aid) or {}).items()}
        out.append(p)
    return out


def _yn(v):
    return "yes" if v is True else "no" if v is False else str(v)


def text(reg, av=None, jobs=None, data_dir=None, limit=20000, only=None):
    """a compact AGENTS block for a prompt"""
    lines = ["AGENTS (what each can really do right now; 'unknown' means not verified - do not rely on it):"]
    for p in live(reg, av, jobs, data_dir):
        if only and p["id"] not in only:
            continue
        state = ("available" if p.get("available") else "NOT available: %s" % p.get("why")) if "available" in p else "availability unknown"
        bits = ["%s [%s, %s; %s]" % (p["id"], p.get("cost"), "/".join(p["strengths"][:6]), state)]
        if "can_run_code" in p:
            bits.append("runs code: %s; reads files: %s; web: %s" % (_yn(p.get("can_run_code")), _yn(p.get("can_read_files")), _yn(p.get("can_read_web"))))
        if p.get("never"):
            bits.append("never for: " + ", ".join(p["never"]))
        if p.get("prompt_tips"):
            bits.append("prompting: " + p["prompt_tips"])
        if p.get("success"):
            bits.append("finished: " + ", ".join("%s %s" % kv for kv in p["success"].items()))
        if p.get("note"):
            bits.append(p["note"])
        lines.append("- " + " | ".join(bits))
    return fit("\n".join(lines), limit, "agents list")


KIND_LABEL = {"step": "THIS STEP", "plan": "THIS PLAN", "review": "THIS REVIEW"}


def guide(agent, kind, data_dir=None):
    """-> the agent's own prompting notes for a step / plan / review prompt (agent_profiles.json agents.<id>.prompt), or '' when it has none.
    {"how_run": how the prompt reaches it and what it can and cannot reach, "length": how much to say, "step"/"plan"/"review": per-kind advice}"""
    if not agent or not agent.get("id"):
        return ""
    try:
        pr = (load(data_dir)["agents"].get(agent["id"]) or {}).get("prompt") or {}
    except Exception:
        return ""
    if not isinstance(pr, dict):
        return ""
    lines = []
    if pr.get("how_run"):
        lines.append("HOW YOU ARE RUN (%s): %s" % (agent["id"], pr["how_run"]))
    if pr.get(kind):
        lines.append("FOR %s: %s" % (KIND_LABEL.get(kind, kind.upper()), pr[kind]))
    if pr.get("length"):
        lines.append("LENGTH: " + pr["length"])
    return "\n".join(lines)
