"""the agency as MCP tools (full profile): submit a job, plan it, look at it, cancel it. Running agents is the dispatcher's job (a separate, explicit process), never a side effect of these calls."""
from ..mcp_server import Tool
from ..store import BoardError
from .jobs import CAPS, KINDS, NEEDS

SUBMIT_MAX = 8_000_000                                                     # a pull agent's whole answer (complete files): it was capped at 60000 characters

S = lambda n=None, **k: dict(type="string", **({"maxLength": n} if n else {}), **k)
OBJ = lambda props, req=(): dict(type="object", properties=props, required=list(req))


def build(jobs, registry, availability, dispatcher=None):
    def actor(s):
        return (s or {}).get("actor") or "unknown"

    def wrap(fn):
        def h(a, s):
            try:
                return fn(a, s)
            except BoardError as e:
                return e.as_dict()
        return h

    def submit(a, s):
        j = jobs.create(actor(s), a["title"], a["goal"], a.get("kind", "code"), a.get("target", ""), a.get("acceptance"), a.get("budget"), a.get("priority", 1), a.get("meta"), dedupe=not a.get("allow_duplicate"))
        if a.get("steps"):
            j = jobs.add_steps(j["id"], a["steps"], actor(s))
        return dict(ok=True, job=slim(j), note="" if a.get("steps") else "no steps given: it waits in 'queued' until a planner (Stage 1) or job_add_steps plans it")

    def add_steps(a, s):
        return dict(ok=True, job=slim(jobs.add_steps(a["job_id"], a["steps"], actor(s))))

    def status(a, s):
        return dict(ok=True, job=jobs.get(a["job_id"]))

    def listing(a, s):
        return dict(ok=True, counts=jobs.counts(), jobs=[slim(j) for j in jobs.list(a.get("state"), a.get("limit", 20))])

    def cancel(a, s):
        return dict(ok=True, job=slim(jobs.cancel(a["job_id"], actor(s), a.get("why", "cancelled"))))

    def agents(a, s):
        out = []
        for ag in registry.agents.values():
            ok, why = availability.check(ag)
            out.append(dict(id=ag["id"], name=ag.get("name", ag["id"]), cost=ag["cost"], strengths=ag["strengths"], available=ok, why=why))
        return dict(ok=True, agents=out)

    def who(a, s):
        """the agent id the caller may act as: the one it names, if its token / client name matches it (an agent cannot take work in another agent's name)"""
        from .brain import match_agent
        named = a.get("agent") or match_agent(registry, actor(s))
        if not named or named not in registry.agents:
            raise BoardError("unknown_agent", "say which agent you are (agent: one of %s)" % ", ".join(registry.agents))
        if match_agent(registry, actor(s)) != named and actor(s) != "owner":
            raise BoardError("not_you", "this connection is %r: it cannot act as %r" % (actor(s), named))
        return named

    def nxt(a, s):
        return dispatcher().offer(who(a, s))

    def submit_(a, s):
        return dispatcher().submit(who(a, s), a["task_id"], a["text"])

    steps_schema = dict(type="array", items=OBJ(dict(objective=S(CAPS["objective"]), needs=dict(type="array", items=S(enum=list(NEEDS))), tool=dict(type="object"), hint=S(200), why=S(CAPS["step_why"]), prefer=S(40)), ["objective"]))
    return [
        Tool("job_submit", "Give the agency a job. kind=code needs acceptance.check (a command that must exit 0 in a sandbox) or acceptance.rubric (what a reviewer must see). Optional steps; otherwise it waits to be planned.",
             OBJ(dict(title=S(120), goal=S(CAPS["goal"]), kind=S(enum=list(KINDS)), target=S(300), acceptance=OBJ(dict(check=S(CAPS["check"]), rubric=S(CAPS["rubric"]))), steps=steps_schema,
                      priority=dict(type="integer", minimum=0, maximum=3), meta=dict(type="object"), allow_duplicate=dict(type="boolean"), budget=OBJ(dict(max_steps=dict(type="integer"), max_attempts_per_step=dict(type="integer"), wall_min=dict(type="integer"), max_runs=dict(type="integer")))),
                 ["title", "goal"]), wrap(submit), ("full",)),
        Tool("job_add_steps", "Plan a queued job: a list of steps, each an objective and the strengths it needs (code, review, long-context, vision, fast, plan, research, write).",
             OBJ(dict(job_id=S(20), steps=steps_schema), ["job_id", "steps"]), wrap(add_steps), ("full",)),
        Tool("job_status", "One job with its steps, state and why.", OBJ(dict(job_id=S(20)), ["job_id"]), wrap(status), ("full",), read_only=True),
        Tool("job_list", "Jobs by state (queued, planning, running, reviewing, ready, blocked, merged, delivered, failed, cancelled) with counts.",
             OBJ(dict(state=S(20), limit=dict(type="integer", minimum=1, maximum=100))), wrap(listing), ("full",), read_only=True),
        Tool("job_cancel", "Cancel a job and skip its unfinished steps.", OBJ(dict(job_id=S(20), why=S(200)), ["job_id"]), wrap(cancel), ("full",)),
        Tool("agency_next", "For an agent that asks for work (a desktop app, an open session): get the next task you are eligible for - a review, a plan or a step - with its full prompt and the exact answer format; or none. "
             "You hold a task for about 30 minutes: answer it with agency_submit.", OBJ(dict(agent=S(40))), wrap(nxt), ("full",)),
        Tool("agency_submit", "Hand back your answer to the task from agency_next (the JSON object it asked for, as text). The agency reads it, applies it to a throw-away copy, runs the checks and the review gate.",
             OBJ(dict(task_id=S(20), text=S(SUBMIT_MAX), agent=S(40)), ["task_id", "text"]), wrap(submit_), ("full",)),
        Tool("agency_agents", "Which agents the agency could use right now and why not, for those that cannot be used.", OBJ({}), wrap(agents), ("full",), read_only=True),
    ]


def slim(j):
    return {k: j[k] for k in ("id", "title", "kind", "target", "state", "shown", "why", "priority", "post_id") if k in j} | dict(steps=j.get("steps") if isinstance(j.get("steps"), dict) else
            [dict(n=s["n"], state=s["state"], agent=s["agent"], objective=s["objective"][:80]) for s in (j.get("steps") or [])])
