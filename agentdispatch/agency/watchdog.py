"""Stuck-job watchdog: finds helix/agency jobs that look alive but are not moving, then either
blocks them honestly, retries a failed dependency (and remaps waiters), or asks a local Ollama
model which unlocked path to try next. Safe by default — never auto-accepts merges."""
from __future__ import annotations

import json, os, re, time, urllib.request
from ..paths import OLLAMA_URL as _OLLAMA

from ..textfit import fit as _wfit

MAX_FAILED_COPIES = 4

DEFAULTS = dict(
    enabled=True,
    interval_s=90,
    stale_running_s=1800,          # running with no open task this long => stuck
    model="local-qwen3-4b:latest",
    ollama=_OLLAMA,
    use_llm=True,
    max_actions_per_tick=2,
    auto_block=True,
    auto_retry_failed_dep=True,
    prefer_agents=["ollama-local", "openrouter", "gemini-cli", "codex-cli", "grok-cli", "claude-code"],
)

SYSTEM = """You are the AgentDispatch stuck-job watchdog on this machine.
You only output ONE JSON object, no markdown. Fields:
  action: one of block | reroute | reclaim | wait
  job_id: the stuck job id (required)
  agent: preferred agent id to try next (only for reroute/reclaim)
  why: short plain reason (one line)
Rules:
- Failed dependencies and failed jobs are handled by fixed rules, never by you.
- If a step is marked running but has no open task (zombie), use reclaim.
- If agents are busy/limited but another capable path exists, use reroute with agent.
- Use block when the job must stop pretending to run until a human or dep is fixed.
- Use wait only when something is legitimately in progress.
- Do NOT block when step attempts remain — prefer reclaim or reroute.
Never invent job ids. Never choose accept/merge."""


def _load_cfg(data_dir):
    path = os.path.join(data_dir, "watchdog.json")
    cfg = dict(DEFAULTS)
    if os.path.exists(path):
        try:
            with open(path, encoding="utf-8") as f:
                cfg.update(json.load(f) or {})
        except Exception:
            pass
    return cfg


def _log(data_dir, row):
    path = os.path.join(data_dir, "watchdog_log.jsonl")
    row = dict(row)
    row.setdefault("ts", time.time())
    try:
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
    except Exception:
        pass
    return row


def _tail_log(data_dir, n=40):
    path = os.path.join(data_dir, "watchdog_log.jsonl")
    if not os.path.exists(path):
        return []
    try:
        lines = open(path, encoding="utf-8").read().splitlines()[-n:]
        out = []
        for ln in lines:
            try:
                out.append(json.loads(ln))
            except Exception:
                pass
        return out
    except Exception:
        return []


def _any_open_task(app, job_id):
    """Any open, unexpired task for this job (any kind / step_n).

    jobs.open_task(job, kind) defaults step_n=0, so step-1+ work was invisible
    to the watchdog and looked like a zombie even while Claude held a lease.
    """
    try:
        rows = app.jobs.db.execute(
            "SELECT * FROM tasks WHERE job_id=? AND state='open' AND expires>? ORDER BY issued DESC LIMIT 5",
            (job_id, app.jobs.now()),
        ).fetchall()
        return [dict(r) for r in rows]
    except Exception:
        return []


def _base_title(title):
    t = re.sub(r"\s*\(retry[^)]*\)", "", title or "")
    return re.sub(r"\s*\((?:r(?:e(?:t(?:r(?:y[^)]*)?)?)?)?)?$", "", t).strip()                 # a long title is cut off mid-tag ("... (ret"): drop the stub too


def _copies(app, dep_job, limit=None):
    """the failed / cancelled copies of a job: same target, and the same title once the "(retry ...)" tags are dropped. Compared exactly in Python (a LIKE prefix on 60
    characters is case-blind, treats % and _ as wildcards and counted "<title> v2" as a copy); a retry title is cut to 95 characters + " (retry)", so when one base is that
    long (>= 88 once a cut-off stub is dropped) a base that starts with it is a copy too."""
    base = _base_title(dep_job["title"])
    rows = app.jobs.db.execute("SELECT id, state, title, why FROM jobs WHERE state IN ('failed','cancelled') AND target=? AND substr(title,1,?)=? ORDER BY id DESC",
                               (dep_job.get("target") or "", min(len(base), 60), base[:60])).fetchall()
    out = []
    for r in rows:
        b = _base_title(r["title"])
        if b == base or (min(len(b), len(base)) >= 88 and (b.startswith(base) or base.startswith(b))):
            out.append(r)
            if limit and len(out) >= limit:
                break
    return out


def failed_copies(app, dep_job):
    """how many copies of this job (same target, same title once the "(retry ...)" tags are dropped) already failed or were cancelled"""
    return len(_copies(app, dep_job))


def failure_history(app, dep_job, limit=8):
    """the failed copies of a job, newest first, with why each one stopped: what a re-plan needs to know so it does not repeat them"""
    return [dict(id=r["id"], state=r["state"], why=_wfit(r["why"] or "", 2000, "reason")) for r in _copies(app, dep_job, int(limit))]      # a re-plan reads these (was a silent 300-character cut)


def _session_of(app, job):
    """-> (session id, plan key) of the autonomous plan this job (or an earlier copy of it) belongs to, or (None, None)"""
    try:
        from ..intake.web_api import intake_of
        it = intake_of(app)
    except Exception:
        return None, None
    base, target = _base_title(job["title"]), job.get("target") or ""
    by_title = (None, None)
    for name in sorted(os.listdir(it.dir)) if os.path.isdir(it.dir) else []:
        if not name.startswith("S-"):
            continue
        try:
            s = it.get(name)
        except Exception:
            continue
        for key, jid in (s.get("made") or {}).items():
            if jid == job["id"]:
                return s["id"], key
            if by_title[0] is None:
                try:
                    mj = app.jobs.get(jid)
                except Exception:
                    continue
                if _base_title(mj["title"]) == base and (mj.get("target") or "") == target:
                    by_title = (s["id"], key)
    return by_title


def escalate(app, jid, dep, why, history=None, actor="watchdog"):
    """say it to the owner ONCE (a board issue + the job's why) and remember it in the job's meta, so later ticks leave the job alone instead of re-deciding it"""
    with app.jobs.lock:                                                                # read and write the meta under one lock: a concurrent change (depends, a re-plan) is not lost
        m = dict(app.jobs.meta(jid) or {})
        prev = m.get("watchdog_escalated") or {}
        if prev.get("dep") == dep:
            return dict(ok=True, action="escalate", job_id=jid, dep_id=dep, already=True, why="already escalated to the owner")
        m["watchdog_escalated"] = dict(dep=dep, ts=time.time(), why=(why or "")[:300])
        app.jobs.db.execute("INSERT OR REPLACE INTO job_meta(job_id,data) VALUES(?,?)", (jid, json.dumps(m)))
    text = "needs you: %s" % (why or "")[:260]
    try:
        app.jobs.note(jid, text)
    except Exception:
        pass
    try:
        body = "%s\nStuck job: %s. Dependency: %s.\nFailed copies (newest first):\n%s\nThe watchdog will not touch this job again until the dependency changes." % (
            why, jid, dep, "\n".join("- %s %s: %s" % (h["id"], h["state"], h["why"][:200]) for h in (history or [])[:8]) or "-")
        app.board.post("agency", "issue", ("[needs you] %s waits on %s" % (jid, dep))[:140], body[:1500], tags=["watchdog", "needs_you"], severity=2)
    except Exception:
        pass
    return dict(ok=True, action="escalate", job_id=jid, dep_id=dep, why=why)


def replan(app, jid, dep, why, actor="watchdog"):
    """a dependency whose copies keep failing goes to the architect's re-plan path (rewrite / split with the whole failure history), never another identical copy; with no plan or no repair budget left it is the owner's"""
    old = app.jobs.get(dep)
    history = failure_history(app, old)
    sid, key = _session_of(app, old)
    if sid:
        try:
            from ..intake.web_api import architect_of
            arch = architect_of(app)
            first = arch.repair(sid, key, history=history, rewrite=True)
        except Exception as e:
            first, why = None, why + "; the re-plan failed: %s: %s" % (e.__class__.__name__, str(e)[:120])
        if first:
            last = (arch.intake.get(sid).get("made") or {}).get(key) or first["id"]
            n = app.jobs.replace_dependency(dep, last)
            unblocked = _unblock_waiters(app, dep, last, actor)
            return dict(ok=True, action="replan", old=dep, new=first["id"], last=last, session=sid, key=key, remapped=n, unblocked=unblocked, why=why)
        why = why + "; the architect gave no replacement (repair budget used up or it gave up)"
    else:
        why = why + "; it is not part of an autonomous plan, so there is nothing to re-plan automatically"
    return escalate(app, jid, dep, why, history, actor)


def _unblock_waiters(app, dep, new_id, actor):
    unblocked = []
    for w in app.jobs.list(state="blocked", limit=50):
        wdeps = (app.jobs.meta(w["id"]) or {}).get("depends_on_jobs") or []
        if new_id in wdeps or dep in wdeps:
            try:
                app.jobs.unblock(w["id"], actor, "watchdog: %s now waits for %s" % (dep, new_id))
                unblocked.append(w["id"])
            except Exception:
                pass
    return unblocked


def find_stuck(app, cfg=None):
    """-> list of stuck dicts: job, age_s, why, failed_deps, open_task

    A job waiting on a live dep is healthy. A job between step attempts (todo
    steps still under budget, recent step activity) is also healthy — only park
    true zombies: ready, no open task, no recent step touch, past stale_s.
    Also catch running-step zombies: step.state=running but no matching open task.
    """
    cfg = cfg or _load_cfg(app.data)
    now = time.time()
    stale_s = float(cfg.get("stale_running_s") or 1800)
    hooks = getattr(getattr(app, "campaign", None), "governor", None)
    out = []
    seen = set()
    # running/planning: idle / zombie detection
    # blocked: often parked on a failed dep — those MUST trigger retry_dep
    # (previously only running/planning were scanned, so a failed helix sat silent)
    candidates = (
        app.jobs.list(state="running", limit=80)
        + app.jobs.list(state="planning", limit=20)
        + app.jobs.list(state="blocked", limit=40)
    )
    for j in candidates:
        jid = j["id"]
        if jid in seen:
            continue
        seen.add(jid)
        j = app.jobs.get(jid)
        open_rows = _any_open_task(app, j["id"])
        open_t = bool(open_rows)
        open_steps = {int(r.get("step_n") or 0) for r in open_rows}
        steps = j.get("steps") or []
        step_touch = max([float(s.get("updated") or 0) for s in steps] + [0])
        job_touch = float(j.get("updated") or j.get("created") or now)
        # open-task issue time counts as activity (agent still holding the lease)
        task_touch = max([float(r.get("issued") or 0) for r in open_rows] + [0])
        last_touch = max(step_touch, job_touch, task_touch)
        age = now - last_touch
        failed_deps, wait_why, ready_ok = [], "", True
        meta = j.get("meta") or app.jobs.meta(j["id"]) or {}
        dep_ids = meta.get("depends_on_jobs") or []
        if hooks:
            ready_ok, wait_why = hooks.ready(j)
            if not ready_ok:
                for dj in dep_ids:
                    try:
                        st = app.jobs.get(dj)["state"]
                    except Exception:
                        st = "missing"
                    if st in ("failed", "cancelled") or st == "missing":
                        failed_deps.append(dj)
        else:
            for dj in dep_ids:
                try:
                    st = app.jobs.get(dj)["state"]
                except Exception:
                    st = "missing"
                if st in ("failed", "cancelled") or st == "missing":
                    failed_deps.append(dj)
                    wait_why = wait_why or ("dependency %s is %s" % (dj, st))
        esc = (meta.get("watchdog_escalated") or {}).get("dep")
        if esc and esc in failed_deps:
            continue                                                                   # already said to the owner once: leave it until the dependency changes
        # waiting on a live queued/running dep is healthy — do not park those
        todo = [s for s in steps if s.get("state") == "todo"]
        running_steps = [s for s in steps if s.get("state") == "running"]
        budget = (j.get("budget") or {}).get("max_attempts_per_step") or 3
        retries_left = any(int(s.get("attempts") or 0) < int(budget) for s in (todo + running_steps))
        held_failed = bool(failed_deps)
        # running step with no open lease = abandoned mid-flight (real zombie)
        zombie_running = [
            s for s in running_steps
            if int(s.get("n") or 0) not in open_steps and (now - float(s.get("updated") or 0)) >= min(300, stale_s / 2)
        ]
        # between-attempt: todo with retries left and touched recently enough that
        # the dispatcher may still claim — never treat as idle_long
        between_attempts = bool(todo) and retries_left and age < stale_s * 2 and not zombie_running
        idle_long = (
            j["state"] in ("running", "planning")
            and ready_ok and (not open_t) and (not running_steps)
            and age >= stale_s and (todo or not steps)
            and not between_attempts
        )
        if zombie_running:
            kind = "zombie_running"
        elif held_failed:
            kind = "failed_dep"
        elif idle_long:
            kind = "stale"
        else:
            continue
        out.append(dict(
            job=j, age_s=round(age, 1), why=wait_why or j.get("why") or "",
            failed_deps=failed_deps, open_task=open_t,
            kind=kind,
            between_attempts=between_attempts, retries_left=retries_left,
            zombie_steps=[int(s.get("n") or 0) for s in zombie_running],
        ))
    return out


def _ask_local(cfg, prompt):
    """the watchdog's local model: a window sized to the prompt (Ollama's default silently drops the FRONT of a long prompt, where the instructions are), JSON mode, no thinking"""
    from ..textfit import fit, ollama_ctx, max_prompt_chars, CTX_MAX
    from ..jsonx import extract_json
    reply = int(cfg.get("num_predict") or 1024)
    ceiling = int(cfg.get("num_ctx_max") or 32768)
    prompt = fit(prompt, max_prompt_chars(ceiling, reply) - len(SYSTEM), "watchdog prompt")
    body = json.dumps(dict(
        model=cfg.get("model") or DEFAULTS["model"],
        stream=False,
        think=False,
        format="json",
        keep_alive="30m",
        options=dict(num_ctx=ollama_ctx(len(prompt) + len(SYSTEM), reply, ceiling=ceiling), num_predict=reply, temperature=0.1),
        messages=[
            dict(role="system", content=SYSTEM),
            dict(role="user", content=prompt),
        ],
    )).encode("utf-8")
    req = urllib.request.Request(
        (cfg.get("ollama") or DEFAULTS["ollama"]).rstrip("/") + "/api/chat",
        data=body, headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=90) as r:
        data = json.loads(r.read().decode("utf-8", "replace"))
    text = ((data.get("message") or {}).get("content") or data.get("response") or "").strip()
    obj = extract_json(text, None)
    if not isinstance(obj, dict):
        raise RuntimeError("watchdog model returned no JSON%s" % (" (the reply was cut off by the output limit)" if data.get("done_reason") == "length" else ""))
    return obj


def decide(app, stuck, cfg=None):
    """Rule first, optional local LLM refine. -> action dict"""
    cfg = cfg or _load_cfg(app.data)
    j = stuck["job"]
    prefer = cfg.get("prefer_agents") or DEFAULTS["prefer_agents"]
    # deterministic defaults
    capped = None
    if stuck["failed_deps"]:
        try:
            dj = app.jobs.get(stuck["failed_deps"][0])
            n = failed_copies(app, dj)
            if n >= MAX_FAILED_COPIES:
                capped = n
        except Exception:
            pass
    if stuck["failed_deps"] and capped:
        decision = dict(action="replan", job_id=j["id"], dep_id=stuck["failed_deps"][0], copies=capped,
                        why="%d copies of dependency %s already failed: re-plan it instead of copying it again" % (capped, stuck["failed_deps"][0]))
    elif stuck["failed_deps"] and cfg.get("auto_retry_failed_dep", True):
        decision = dict(action="retry_dep", job_id=j["id"], dep_id=stuck["failed_deps"][0],
                        why="dependency %s failed; retry it so waiters can move" % stuck["failed_deps"][0])
    elif stuck["kind"] == "zombie_running":
        # step says running but lease is gone — reset to todo and hand to another agent
        decision = dict(action="reclaim", job_id=j["id"],
                        agent=(prefer[0] if prefer else "ollama-local"),
                        zombie_steps=stuck.get("zombie_steps") or [],
                        why="step %s marked running with no open task - reclaim and reroute"
                            % (stuck.get("zombie_steps") or ["?"]))
    elif stuck.get("retries_left") and stuck["kind"] == "stale":
        # still has step attempts — ask for another path instead of parking
        decision = dict(action="reroute", job_id=j["id"], agent=(prefer[0] if prefer else "ollama-local"),
                        why="todo steps still have attempts left after %.0fs idle - try another agent" % stuck["age_s"])
    elif cfg.get("auto_block", True) and stuck["kind"] == "stale":
        decision = dict(action="block", job_id=j["id"],
                        why="running %.0fs with no open task - blocking until watchdog/human moves it" % stuck["age_s"])
    else:
        decision = dict(action="wait", job_id=j["id"], why="watching")

    if not cfg.get("use_llm", True) or decision["action"] != "wait":
        decision["via"] = "rule"                                                       # the rules already decided: the small local model is only asked when they cannot
        return decision
    # skip LLM when GPU/local unavailable — rules already chose
    try:
        brief = dict(
            stuck_job=dict(id=j["id"], title=j["title"], state=j["state"], why=j.get("why"), age_s=stuck["age_s"],
                           steps=[{"n": s["n"], "state": s["state"], "attempts": s.get("attempts"),
                                   "objective": _wfit(s.get("objective") or "", 1500, "objective")} for s in (j.get("steps") or [])],   # every step; a long objective keeps its head and tail with a marker (it was 80 characters of 6 steps)
                           depends_on=(j.get("meta") or {}).get("depends_on_jobs") or []),
            failed_deps=stuck["failed_deps"],
            retries_left=stuck.get("retries_left"),
            rule_suggestion=decision,
            prefer_agents=prefer,
            note="Do NOT block when retries_left is true — prefer reroute or wait.",
        )
        llm = _ask_local(cfg, "Stuck job report:\n" + json.dumps(brief, ensure_ascii=False))
        act = (llm.get("action") or "").strip().lower()
        if act in ("block", "reroute", "wait", "reclaim"):                               # retry_dep / reissue copy jobs: only the rules (with the copy cap) may do that
            # hard veto: never let the LLM park a job that still has step attempts
            if act == "block" and (stuck.get("retries_left") or stuck["kind"] == "zombie_running"):
                act = "reclaim" if stuck["kind"] == "zombie_running" else "reroute"
                llm["agent"] = llm.get("agent") or (prefer[0] if prefer else "ollama-local")
                llm["why"] = "overrode block: attempts remain — " + str(llm.get("why") or "")[:180]
            decision["action"] = act
            if llm.get("dep_id"):
                decision["dep_id"] = llm["dep_id"]
            if llm.get("agent"):
                decision["agent"] = llm["agent"]
            if llm.get("why"):
                decision["why"] = str(llm["why"])[:240]
            decision["via"] = "llm"
        else:
            decision["via"] = "rule"
    except Exception as e:
        decision["via"] = "rule"
        decision["llm_err"] = "%s: %s" % (e.__class__.__name__, e)
    return decision


def apply_decision(app, decision, actor="watchdog"):
    """Execute one decision. Returns result dict."""
    act = decision.get("action")
    jid = decision.get("job_id")
    why = (decision.get("why") or act)[:240]
    if act == "wait":
        return dict(ok=True, action="wait", why=why)
    if act == "replan":
        return replan(app, jid, decision.get("dep_id") or jid, why, actor)
    if act == "escalate":
        dep = decision.get("dep_id") or jid
        return escalate(app, jid, dep, why, failure_history(app, app.jobs.get(dep)), actor)
    if act == "reclaim":
        j = app.jobs.get(jid)
        if not j:
            return dict(ok=False, action="reclaim", why="missing job")
        zsteps = decision.get("zombie_steps") or [
            int(s.get("n") or 0) for s in (j.get("steps") or []) if s.get("state") == "running"
        ]
        for n in zsteps:
            try:
                # close any stale open lease on this step, then put step back to todo
                row = app.jobs.open_task(jid, "step", int(n))
                if row:
                    app.jobs.task_close(row["id"] if isinstance(row, dict) else dict(row)["id"], "abandoned")
            except Exception:
                pass
            app.jobs.step_set(jid, int(n), state="todo", agent="", attempt_id="",
                              evidence=("watchdog reclaim: " + why)[:400], bump=False)
        agent = decision.get("agent") or ""
        try:
            app.jobs.note(jid, ("watchdog reclaim steps %s" % zsteps)
                          + (("; prefer " + agent) if agent else ""))
        except Exception:
            pass
        return dict(ok=True, action="reclaim", job_id=jid, steps=zsteps, agent=agent, why=why)
    if act == "block":
        j = app.jobs.get(jid)
        if j["state"] == "blocked":
            return dict(ok=True, action="block", job_id=jid, why="already blocked")
        if j["state"] not in ("running", "planning", "queued", "reviewing"):
            return dict(ok=False, action="block", why="cannot block from %s" % j["state"])
        # jobs.set_state may require legal transition; running->blocked is legal
        app.jobs.set_state(jid, "blocked", actor, "watchdog: " + why)
        return dict(ok=True, action="block", job_id=jid, why=why)
    if act == "retry_dep":
        dep = decision.get("dep_id")
        if not dep:
            return dict(ok=False, action="retry_dep", why="no dep_id")
        old = app.jobs.get(dep)
        if old["state"] not in ("failed", "cancelled"):
            return dict(ok=False, action="retry_dep", why="dep %s is %s (need failed/cancelled)" % (dep, old["state"]))
        base = _base_title(old["title"])                                                   # copies of the same job that already failed for the same reason are not re-created forever
        failed = failed_copies(app, old)
        if failed >= MAX_FAILED_COPIES:
            return dict(ok=False, action="retry_dep", why="%d copies of %r already failed: not creating another (needs the owner or a plan fix)" % (failed, base[:60]))
        meta = dict(old.get("meta") or app.jobs.meta(dep) or {})
        # Limit failures (Claude session / Codex plan / Gemini quota) must not re-pin the same agent.
        why_l = (why + " " + str(old.get("why") or "")).lower()
        limitish = any(
            k in why_l
            for k in (
                "session limit",
                "usage limit",
                "plan limit",
                "rate limit",
                "quota",
                "resets",
                "429",
            )
        )
        if limitish:
            agent = decision.get("agent") or "codex-cli"
            limited = (meta.get("prefer_agent") or meta.get("preferred_agent") or "").strip()
            if agent == limited:
                agent = "ollama-local" if limited != "ollama-local" else "gemini-cli"
            reg = getattr(app, "registry", None)
            if (not reg) or (agent in getattr(reg, "agents", {})):
                meta["prefer_agent"] = agent
                meta["preferred_agent"] = agent  # legacy alias; dispatcher honors both
                meta["free_zone"] = True
                meta.pop("min_tier", None)
        title = old["title"]
        if "(retry" not in title.lower():
            title = (title[:95] + " (retry)").strip()[:120]
        new = app.jobs.create(actor, title, old["goal"], old["kind"], old["target"],
                              old.get("acceptance"), old.get("budget"), old["priority"], meta or None)
        n = app.jobs.replace_dependency(dep, new["id"])
        # waiters parked as blocked on the failed dep need an explicit unblock
        unblocked = _unblock_waiters(app, dep, new["id"], actor)
        return dict(ok=True, action="retry_dep", old=dep, new=new["id"], remapped=n,
                    unblocked=unblocked, why=why)
    if act == "reissue":
        old = app.jobs.get(jid)
        if old["state"] not in ("failed", "cancelled", "blocked"):
            # allow reissue from blocked by cancelling first? safer: only failed/cancelled/blocked
            if old["state"] == "running":
                app.jobs.set_state(jid, "blocked", actor, "watchdog: parking before reissue - " + why)
                old = app.jobs.get(jid)
            if old["state"] not in ("failed", "cancelled", "blocked"):
                return dict(ok=False, action="reissue", why="cannot reissue from %s" % old["state"])
        if old["state"] == "blocked":
            app.jobs.set_state(jid, "failed", actor, "watchdog: failed so it can be reissued - " + why)
            old = app.jobs.get(jid)
        new = app.jobs.create(actor, old["title"], old["goal"], old["kind"], old["target"],
                              old.get("acceptance"), old.get("budget"), old["priority"], old.get("meta") or None)
        n = app.jobs.replace_dependency(jid, new["id"])
        return dict(ok=True, action="reissue", old=jid, new=new["id"], remapped=n, why=why)
    if act == "reroute":
        agent = decision.get("agent") or (DEFAULTS["prefer_agents"][0])
        if agent not in app.registry.agents:
            return dict(ok=False, action="reroute", why="unknown agent %s" % agent)
        m = dict(app.jobs.meta(jid) or {})
        m["prefer_agent"] = agent
        m["preferred_agent"] = agent  # legacy alias; dispatcher.choose reads both
        with app.jobs.lock:
            app.jobs.db.execute("INSERT OR REPLACE INTO job_meta(job_id,data) VALUES(?,?)", (jid, json.dumps(m)))
        # if blocked solely for stale, nudge back to running
        j = app.jobs.get(jid)
        if j["state"] == "blocked":
            try:
                app.jobs.unblock(jid, actor, "watchdog reroute to %s: %s" % (agent, why))
            except Exception:
                pass
        return dict(ok=True, action="reroute", job_id=jid, agent=agent, why=why)
    return dict(ok=False, action=act, why="unknown action")


class Watchdog:
    def __init__(self, app, now=time.time):
        self.app, self.now = app, now
        self._last = 0.0

    def cfg(self):
        return _load_cfg(self.app.data)

    def recent(self, n=30):
        return _tail_log(self.app.data, n)

    def tick(self, force=False):
        cfg = self.cfg()
        if not cfg.get("enabled", True):
            return dict(ok=True, action="off")
        t = self.now()
        if not force and t - self._last < float(cfg.get("interval_s") or 90):
            return dict(ok=True, action="skip", next_in=round(float(cfg["interval_s"]) - (t - self._last), 1))
        self._last = t
        stuck = find_stuck(self.app, cfg)
        if not stuck:
            # surface live leases so the DNA Activity pane shows work in flight
            watching = []
            for j in self.app.jobs.list(state="running", limit=20):
                for r in _any_open_task(self.app, j["id"])[:2]:
                    age = round(self.now() - float(r.get("issued") or self.now()), 0)
                    watching.append("%s step %s on %s (%.0fs)" % (
                        j["id"], r.get("step_n"), r.get("agent") or "?", age))
            why = ("watching " + "; ".join(watching[:3])) if watching else "no stuck jobs"
            row = _log(self.app.data, dict(kind="scan", action="clear", why=why, n=0,
                                           watching=watching[:5]))
            return dict(ok=True, action="clear", log=row)
        results = []
        budget = int(cfg.get("max_actions_per_tick") or 2)
        # prefer failed_dep / zombie_running first
        order = {"failed_dep": 0, "zombie_running": 1, "stale": 2}
        stuck.sort(key=lambda s: (order.get(s["kind"], 9), -s["age_s"]))
        for s in stuck[: max(1, budget)]:
            decision = decide(self.app, s, cfg)
            try:
                res = apply_decision(self.app, decision)
            except Exception as e:
                res = dict(ok=False, action=decision.get("action"), error="%s: %s" % (e.__class__.__name__, e))
            row = _log(self.app.data, dict(kind="action", stuck=s["job"]["id"], decision=decision, result=res))
            results.append(row)
            if res.get("action") in ("wait", "escalate") or not res.get("ok"):
                continue                                                               # escalations post their own issue once; waits and refusals are only logged
            try:
                self.app.board.post("agency", "status",
                                    ("watchdog %s %s" % (res.get("action"), s["job"]["id"]))[:140],
                                    json.dumps(dict(decision=decision, result=res), ensure_ascii=False)[:1500],
                                    tags=["watchdog", res.get("action") or ""], severity="info")
            except Exception:
                pass
        return dict(ok=True, action="acted", n=len(results), results=results)


def activity_log(app, limit=40):
    """Merged rolling log for the brain DNA pane: watchdog + recent board pulses."""
    from . import brain as B
    pulses, _, _ = B.pulses(app, 0, 0.0, limit=limit)
    rows = []
    for p in pulses[-limit:]:
        rows.append(dict(ts=p.get("t") or 0, source="pulse", kind=p.get("kind") or "event",
                         text=p.get("text") or "", job=p.get("job") or "", agent=p.get("agent") or ""))
    for w in _tail_log(app.data, limit):
        text = w.get("why") or (w.get("decision") or {}).get("why") or w.get("action") or w.get("kind")
        if w.get("kind") == "action":
            r = w.get("result") or {}
            text = "%s %s — %s" % (r.get("action") or (w.get("decision") or {}).get("action"), w.get("stuck") or "", r.get("why") or text)
        rows.append(dict(ts=w.get("ts") or 0, source="watchdog", kind=w.get("kind") or "watch",
                         text=str(text)[:200], job=w.get("stuck") or "", agent="watchdog"))
    rows.sort(key=lambda x: x.get("ts") or 0)
    return rows[-limit:]
