"""the dispatcher: picks the next runnable step, chooses an agent (cheapest capable one that is available, escalating after failures), records the attempt on the board, runs it through an
adapter, learns cooldowns from limit errors, and when every step is done asks a DIFFERENT agent to review and lets the gate decide. One tick = at most one unit of work.
Two ways work reaches an agent: PUSH (the dispatcher launches it) and PULL (an agent that cannot be launched - pull agent, any open session - asks `offer()` for work and hands the answer back
with `submit()`). Every unit of work, either way, is a claimed task with a lease, so the two can never take the same thing. Nothing here applies a change to a repo unless the owner's policy
says so and an apply runner is supplied (Stage 2)."""
import time

import json
import re

from . import policy as POLICY
from .adapters import Result, detect_limit, parse_reset
from .brain import difficulty, expected_seconds, lobe_for
from .gate import decide
from .jobs import is_code
from .registry import COST_RANK, TIER_RANK



from . import profiles as PROF
from ..textfit import fit as _fit
from . import realadapters as _RA
PLANNER_ORDER = ("claude-code", "codex-cli", "gemini-cli")                                       # who writes a job's steps, strongest first
PLANNER_NEVER = ("openrouter", "ollama-local", "ollama-agent")                                    # never planners: free / local models

class Dispatcher:
    def __init__(self, board, jobs, registry, availability, adapter, data_dir, cfg=None, checker=None, apply_fn=None, now=time.time, say=None, hooks=None):
        """`hooks` (the campaign governor) may say a job is not ready to start (dependencies, gates, approvals), runs a job's controls, and holds an accepted result back when a stop rule trips"""
        self.hooks = hooks
        self.b, self.jobs, self.reg, self.av, self.adapter = board, jobs, registry, availability, adapter
        self.data, self.cfg = data_dir, cfg or POLICY.load(data_dir)
        self.checker, self.apply_fn, self.now, self.say = checker, apply_fn, now, say or (lambda text: None)
        self._first_seen = {}
        self._plan_failed = {}
        self._pull_rounds = {}
        self._fmt_retry = {}
        self._last_rejects = []
        try:                                                                                  # the planner is shown what each agent really can do now (agent_profiles.json + availability + route_stats)
            adapter.profiles_fn = lambda: PROF.text(self.reg, self.av, self.jobs, self.data)
            adapter.profiles_dir = self.data
            adapter.plan_check_fn = self.plan_problems
        except Exception:
            pass

    def _unpin_limited_prefer(self, jid, limited_agent_id, until=None):
        """When prefer_* pins a limited agent, clear it so choose() can take the next available.

        Remembers prefer_restore so when the cooldown expires (e.g. Claude resets 3:20am)
        the job can pin that agent again instead of staying forever on a stopgap.
        """
        m = dict(self.jobs.meta(jid) or {})
        changed = False
        for key in ("prefer_agent", "preferred_agent"):
            if m.get(key) == limited_agent_id:
                m.pop(key, None)
                changed = True
        m["prefer_restore"] = limited_agent_id
        if until:
            m["prefer_restore_after"] = float(until)
        changed = True
        # Prefer the next free/local/cheap agent when the job was in free_zone; else leave ranking open.
        if m.get("free_zone") or m.get("prefer_cost") == "free":
            for cand in ("ollama-local", "openrouter", "gemini-cli", "codex-cli", "grok-cli"):
                if cand == limited_agent_id:
                    continue
                a = self.reg.agents.get(cand)
                if not a:
                    continue
                ok, _ = self.av.check(a)
                if ok:
                    m["prefer_agent"] = cand
                    m["preferred_agent"] = cand
                    break
        with self.jobs.lock:
            self.jobs.db.execute(
                "INSERT OR REPLACE INTO job_meta(job_id,data) VALUES(?,?)",
                (jid, json.dumps(m)),
            )
        try:
            self.jobs.note(
                jid,
                "unpinned prefer after %s hit a usage limit; next available agent will take over "
                "(will restore prefer after reset)" % limited_agent_id,
            )
        except Exception:
            pass
        return changed

    # ------------------------------------------------------------ choosing
    def min_tier(self, job, step):
        """the escalation ladder (see _min_tier), raised to the tier the job itself demands (a repair that retries a failed job at a stronger model)"""
        t = self._min_tier(job, step)
        want = (job.get("meta") or {}).get("min_tier") or ""
        return max(t, want, key=lambda x: TIER_RANK.get(x, 0)) if want else t

    def _min_tier(self, job, step):
        """the escalation ladder from this step's own failed attempts: local twice, then the mid tier twice, then the strong tier"""
        fails = {"ollama": 0, "mid": 0, "strong": 0}
        if str(step.get("objective", "")).startswith("Address the review"):                  # a review already rejected the cheaper agent's work: a rework never starts at the tier that just failed
            rounds = sum(1 for s in job.get("steps", []) if str(s.get("objective", "")).startswith("Address the review") and s["n"] <= step["n"])
            return "strong" if rounds >= 2 else "mid"
        if step.get("post_id"):
            for a in self.b.db.execute("SELECT actor FROM attempts WHERE post_id=? AND outcome='failed'", (step["post_id"],)):
                if a["actor"] in self.reg.agents:
                    fails[self.reg.tier(a["actor"])] += 1
        if fails["ollama"] < 2 and not fails["mid"] and not fails["strong"]:
            return "ollama"
        if fails["mid"] < 2 and not fails["strong"]:
            return "mid"
        return "strong"

    @staticmethod
    def _transient_block(why):
        """True when the agent is temporarily blocked (reroute), not permanently broken."""
        w = (why or "").lower()
        keys = (
            "cooling down", "plan limit", "gpu", "busy", "concurrency",
            "daily cap", "every model is out", "usage limit", "stale",
            "already holding", "already at its", "render may be running",
        )
        return any(k in w for k in keys)

    def _infra_failed(self, jid, n):
        """agents whose run on this step ended as an agent fault (their own set-up, not the work)"""
        try:
            return {r["agent"] for r in self.jobs.db.execute("SELECT DISTINCT agent FROM runs WHERE job_id=? AND step_n=? AND outcome='fault'", (jid, n)).fetchall()}
        except Exception:
            return set()

    def choose(self, job, step, force_reroute=False):
        """-> (agent | None, [why each rejected agent was rejected])

        Blocked agents are skipped and work is rerouted to the next available one.
        A sticky prefer_agent that is blocked is cleared (with prefer_restore) — we do
        not queue waiting for Codex-at-99% or Ollama-on-busy-GPU.
        """
        floor = 0 if set(step["needs"]) == {"tool"} else TIER_RANK[self.min_tier(job, step)]
        if force_reroute:
            floor = 0  # any capable agent; do not wait on an empty local tier
        rejects = []
        kk = "code" if is_code(job) else "step"
        router = getattr(self.av, "router", None)
        cands = list(self.reg.capable(step["needs"]))
        if cands and not any(TIER_RANK[self.reg.tier(a["id"])] >= floor for a in cands) and "fast" in step["needs"]:
            cands = list(self.reg.capable([n for n in step["needs"] if n != "fast"] or step["needs"]))
        declined = set((self.jobs.step_spec(job["id"], step["n"]) or {}).get("exclude") or [])          # an agent that honestly said it cannot do this step is not offered it again
        for a_ in [a_ for a_ in cands if a_["id"] in declined]:
            rejects.append("%s: said it cannot do this step" % a_["id"])
        cands = [a_ for a_ in cands if a_["id"] not in declined]
        meta = job.get("meta") or {}
        prefer = meta.get("prefer_agent") or meta.get("preferred_agent") or ""
        prefer_cost = meta.get("prefer_cost") or ""
        avoid_premium = bool(meta.get("free_zone") or prefer_cost == "free")

        # If the preferred agent is blocked, unpin now and reroute — do not queue for it.
        if prefer and prefer in self.reg.agents:
            pok, pwhy = self.av.check(self.reg.get(prefer))
            if not pok:
                rejects.append("%s: preferred but blocked (%s) — rerouting" % (prefer, pwhy))
                try:
                    until = self.jobs.cooldown_until(prefer) or None
                    if not until and self._transient_block(pwhy):
                        until = self.now() + 3600  # retry prefer in ~1h if plan/GPU clears
                    self._unpin_limited_prefer(job["id"], prefer, until=until)
                except Exception:
                    pass
                prefer = ""
                meta = dict(self.jobs.meta(job["id"]) or {})
                avoid_premium = bool(meta.get("free_zone") or meta.get("prefer_cost") == "free")

        # a step that already failed (or a rework after a rejected review) against an exact acceptance check goes first to an agent that can RUN the check and fix what it shows (the container agent):
        # a text-only agent cannot see why an exact-format check fails, so it repeats the same mistake
        verify_boost = bool(((step.get("attempts") or 0) >= 1 or str(step.get("objective", "")).startswith("Address the review")) and (job.get("acceptance") or {}).get("check"))
        infra = self._infra_failed(job["id"], step["n"])                                      # agents whose own set-up already failed on this step (unknown model, CLI missing, Docker down): no boost, last in line

        # the planner may name who should do a step ("prefer"/"agent"): honoured when that agent is capable, not excluded and healthy right now; otherwise ignored, never waited for
        step_prefer = str(((self.jobs.step_spec(job["id"], step["n"]) or {}).get("prefer")) or "")
        if step_prefer and (step_prefer not in [a_["id"] for a_ in cands] or step_prefer in infra or not self.av.check(self.reg.get(step_prefer))[0]):
            step_prefer = ""

        def _rate(a):
            if not (verify_boost and router):
                return 0
            rate, _ = router.success_rate(a["id"], kk)
            return -round(rate if rate is not None else 0.5, 1)                                # on a retry, the agents that have actually been finishing this kind of work go first

        def _rank(a):
            unrel = router.unreliable(a["id"], kk) if router else 0
            boost = verify_boost and (a.get("run") or {}).get("via") == "agentic" and a["id"] not in infra
            return (
                0 if prefer and a["id"] == prefer else 1 if step_prefer and a["id"] == step_prefer else 2,
                1 if a["id"] in infra else 0,
                0 if boost else 1,
                _rate(a),
                0 if prefer_cost and a.get("cost") == prefer_cost else 1,
                2 if avoid_premium and a.get("cost") == "premium" else 0,
                1 if avoid_premium and a.get("cost") == "plan" else 0,
                unrel,
                COST_RANK.get(a.get("cost"), 9),
                a["id"],
            )

        def _pick(floor_rank):
            local_rejects = []
            ordered = sorted(cands, key=_rank)
            for a in ordered:
                if TIER_RANK[self.reg.tier(a["id"])] < floor_rank and not (verify_boost and (a.get("run") or {}).get("via") == "agentic" and a["id"] not in infra) and a["id"] != step_prefer:       # the container agent runs the check itself: it is not the same kind of attempt as the text-only agent that failed
                    local_rejects.append("%s: below the current escalation tier" % a["id"])
                    continue
                if avoid_premium and a.get("cost") == "premium" and any(
                    x.get("cost") in ("free", "local", "cheap") for x in cands
                ):
                    still = False
                    for b in cands:
                        if b["id"] == a["id"] or b.get("cost") == "premium":
                            continue
                        if TIER_RANK[self.reg.tier(b["id"])] < floor_rank:
                            continue
                        okb, _ = self.av.check(b)
                        if okb:
                            still = True
                            break
                    if still:
                        local_rejects.append(
                            "%s: free-zone repair — premium held while free/local available" % a["id"]
                        )
                        continue
                ok, why = self.av.check(a)
                if ok:
                    return a, local_rejects
                local_rejects.append("%s: %s" % (a["id"], why))
            return None, local_rejects

        agent, more = _pick(floor)
        rejects.extend(more)
        # All agents at this tier blocked (GPU / plan / cool) → escalate floor in the same tick.
        if agent is None and floor > 0 and any(self._transient_block(r) for r in rejects):
            agent, more = _pick(0)
            rejects.extend(["reroute: escalated past blocked tier"] + more)
        if agent is None and not force_reroute:
            # Last chance: ignore free-zone premium hold if nothing cheaper is up.
            avoid_premium = False
            agent, more = _pick(0)
            if agent:
                rejects.extend(["reroute: took next available after cheaper tiers blocked"] + more)
        self._last_rejects = list(rejects)
        return agent, rejects

    # ------------------------------------------------------------ the route: which lobe, which model, why
    def _route(self, job, kind, step_n, agent, step=None, via="push", rejects=()):
        """record the decision on the board (an event the brain page reads): the lobe the work belongs to, who took it, and why that one (what was passed over and why)"""
        needs = (step or {}).get("needs") or []
        tier = self.reg.tier(agent["id"])
        d = difficulty(job, step or {}, attempts=(step or {}).get("attempts", 0), tier_rank=TIER_RANK[tier], kind=kind)
        if via == "pull":
            why = "%s asked for work and is capable" % agent.get("name", agent["id"])
        elif rejects:
            why = "first available agent after passing over %d" % len(rejects)
        else:
            why = "cheapest capable agent that is available"
        pick = None
        router = getattr(self.av, "router", None)
        if router and via != "pull" and router.ladder(agent):                           # which model and how much thinking: the cheapest rung that is good enough for this difficulty
            attempts = (step or {}).get("attempts", 0)
            kk = "code" if kind == "step" and is_code(job) else kind
            try:
                pick = router.pick(agent, d, kk, attempts)
            except Exception:
                pick = None
            if hasattr(self.adapter, "hint"):
                self.adapter.hint = dict(difficulty=d, kind=kk, attempts=attempts)
            if pick:
                why += "; " + pick["why"]
        try:
            self.jobs._ev(job["id"], "route", agent["id"], dict(kind=kind, step=step_n, agent=agent["id"], model=agent.get("name", agent["id"]), lobe=lobe_for(needs, kind), tier=tier, cost=agent["cost"],
                                                                 why=why, considered=list(rejects)[:6], difficulty=d, expected_s=round(expected_seconds(self.b.db, self.reg, agent["id"])), via=via, needs=needs,
                                                                 pick_model=(pick or {}).get("model"), pick_effort=(pick or {}).get("effort"), need=(pick or {}).get("need")))
        except Exception:
            pass

    def _model_done(self, job, kind, step_n, agent, t0, outcome, model=None):
        """which concrete model actually answered (the router knows; a CLI agent is its own model) and how long it took"""
        model = model or getattr(self.adapter, "last_model", None) or agent.get("name", agent["id"])
        try:
            self.jobs._ev(job["id"], "model", agent["id"], dict(kind=kind, step=step_n, model=model, secs=round(self.now() - t0, 1), outcome=outcome))
        except Exception:
            pass
        u = getattr(self.adapter, "last_usage", None)
        if u and u.get("model") and getattr(self.av, "report", None) and agent.get("er_id") == "gemini":       # the engine room counts Gemini calls per model: tell it about this one
            self.av.report("gemini", u["model"], (u["chars_in"] + u["chars_out"]) // 4)
        if hasattr(self.adapter, "last_model"):
            self.adapter.last_model = None
            self.adapter.last_usage = None
        if hasattr(self.adapter, "hint"):
            self.adapter.hint = None

    def _pull_first_refusal(self, key, needs, cost_rank, floor_rank=0, exclude=()):
        """True while an agent that asks for work (pull) and checked in recently could take this - it is capable, not an author, and no dearer than the push choice - so the push
        dispatcher leaves it for up to cfg['pull_wait_s'] (default 5 min) before taking it itself"""
        for a in self.reg.agents.values():
            if (a.get("run") or {}).get("via") != "pull" or not a.get("enabled", True) or a["id"] in exclude:
                continue
            if not set(needs) <= set(a["strengths"]) or COST_RANK[a["cost"]] > cost_rank or TIER_RANK[self.reg.tier(a["id"])] < floor_rank:
                continue
            if self.jobs.heartbeat_age(a["id"]) > 90 or self.jobs.task_for_agent(a["id"]) or not self.av.check_pull(a)[0]:
                continue
            first = self._first_seen.setdefault(key, self.now())
            return self.now() - first < self.cfg.get("pull_wait_s", 300)
        return False

    def _held(self, job_id):
        """a rule (dependencies, a gate that is not closed yet, an approval the owner has not given) says this job must not start yet"""
        if not self.hooks:
            return False
        ok, why = self.hooks.ready(self.jobs.get(job_id))
        if not ok:
            try:
                self.jobs.note(job_id, "waiting: " + (why or "a rule holds it")[:240])          # the board says what a held job waits for (it used to keep a stale note from long ago)
            except Exception:
                pass
        return not ok

    # ------------------------------------------------------------ housekeeping
    def _expire_tasks(self):
        """a lease ran out (the agent never answered): the run is abandoned and a step goes back to todo, unpenalised"""
        for t in self.jobs.tasks_expired():
            self.jobs.task_close(t["id"], "expired")
            if t.get("run_id"):
                self.jobs.run_done(t["run_id"], "abandoned")
            if t["kind"] == "step":
                self.jobs.step_set(t["job_id"], t["step_n"], "todo")
            self.say("agency: task %s of %s expired" % (t["id"], t["agent"]))

    def _park_held_running(self):
        """running jobs whose deps failed/cancelled must not stay 'running' forever (helix looks alive while nothing moves)"""
        if not self.hooks:
            return
        for j in self.jobs.list(state="running", limit=80):
            ok, why = self.hooks.ready(self.jobs.get(j["id"]))
            if ok:
                continue
            # only park when a dependency is terminal — still-waiting-on-live-dep stays running/held
            bad = False
            for dj in ((j.get("meta") or self.jobs.meta(j["id"]) or {}).get("depends_on_jobs") or []):
                try:
                    st = self.jobs.get(dj)["state"]
                except Exception:
                    st = "missing"
                if st in ("failed", "cancelled", "missing"):
                    bad = True
                    break
            if not bad:
                continue
            try:
                self.jobs.set_state(j["id"], "blocked", "agency", "held by failed dependency: " + (why or "")[:220])
                self.say("agency: parked %s — %s" % (j["id"], (why or "")[:120]))
            except Exception:
                pass

    # ------------------------------------------------------------ one tick
    def _wake_after_cooldowns(self):
        """When a provider limit resets (e.g. Claude 'resets 3:20am'), restore prefers and reissue limit-failed jobs.

        Gap this closes: before detect_limit matched Claude session text, steps burned attempts and
        jobs went failed — so at 3:20 nothing was left waiting. After a cooldown expires we:
          1) sweep expired cooldown rows
          2) restore prefer_agent on live jobs that remembered prefer_restore
          3) reissue recent failed/cancelled jobs whose why was a limit (once per job)
        """
        expired = self.jobs.cooldown_sweep()
        woken = []
        now = self.now()
        # Restore prefer on still-open jobs
        for j in self.jobs.list(state="running", limit=80) + self.jobs.list(state="queued", limit=40) + self.jobs.list(state="blocked", limit=40):
            m = dict(self.jobs.meta(j["id"]) or {})
            restore = m.get("prefer_restore")
            after = float(m.get("prefer_restore_after") or 0)
            if not restore:
                continue
            if after and after > now:
                continue
            a = self.reg.agents.get(restore)
            if not a:
                continue
            ok, _ = self.av.check(a)
            if not ok:
                continue
            m["prefer_agent"] = restore
            m["preferred_agent"] = restore
            m.pop("prefer_restore", None)
            m.pop("prefer_restore_after", None)
            with self.jobs.lock:
                self.jobs.db.execute(
                    "INSERT OR REPLACE INTO job_meta(job_id,data) VALUES(?,?)",
                    (j["id"], json.dumps(m)),
                )
            try:
                self.jobs.note(j["id"], "restored prefer_agent=%s after usage-limit cooldown expired" % restore)
            except Exception:
                pass
            woken.append(dict(action="restore_prefer", job=j["id"], agent=restore))
        # Reissue failed limit jobs once the limited agent (or any capable stand-in) is back
        reissues = 0
        for row in self.jobs.limit_failed_jobs(since_s=36 * 3600, limit=12):
            if reissues >= 2:
                break
            jid = row["id"]
            m = dict(self.jobs.meta(jid) or {})
            if m.get("limit_woken"):
                continue
            why = row.get("why") or ""
            # Prefer the agent named in evidence / restore / default Claude if session-limit
            agent = m.get("prefer_restore") or m.get("prefer_agent") or m.get("preferred_agent") or ""
            if not agent:
                if "session limit" in why.lower() or "claude" in why.lower():
                    agent = "claude-code"
                elif "openrouter" in why.lower() or "429" in why.lower():
                    agent = "openrouter"
                elif "gemini" in why.lower() or "retry in" in why.lower():
                    agent = "gemini-cli"
                elif "codex" in why.lower() or "plan limit" in why.lower():
                    agent = "codex-cli"
                else:
                    agent = "ollama-local"
            pick = agent
            a = self.reg.agents.get(pick)
            ok = False
            if a:
                ok, _ = self.av.check(a)
            if not ok:
                for cand in ("ollama-local", "openrouter", "gemini-cli", "codex-cli", "grok-cli", "claude-code"):
                    aa = self.reg.agents.get(cand)
                    if not aa:
                        continue
                    ook, _ = self.av.check(aa)
                    if ook:
                        pick = cand
                        ok = True
                        break
            if not ok:
                continue
            try:
                old = self.jobs.get(jid)
                title_l = (old.get("title") or "").lower()
                # Do not auto-revive owner-sealed jobs: the owner decides when they run again.
                if "owner-sealed" in title_l or "(wake after limit)" in title_l:
                    m_skip = dict(m)
                    m_skip["limit_woken"] = True
                    with self.jobs.lock:
                        self.jobs.db.execute(
                            "INSERT OR REPLACE INTO job_meta(job_id,data) VALUES(?,?)",
                            (jid, json.dumps(m_skip)),
                        )
                    continue
                meta = dict(m)
                meta["prefer_agent"] = pick
                meta["preferred_agent"] = pick
                meta["limit_woken"] = True
                meta["limit_woken_from"] = jid
                meta.pop("prefer_restore", None)
                meta.pop("prefer_restore_after", None)
                title = old["title"]
                if "(wake" not in title.lower():
                    title = (title[:90] + " (wake after limit)").strip()[:120]
                new = self.jobs.create(
                    "agency",
                    title,
                    old["goal"],
                    old["kind"],
                    old["target"],
                    old.get("acceptance"),
                    old.get("budget"),
                    old["priority"],
                    meta,
                )
                # Mark the failed original so we do not wake it again.
                m_done = dict(self.jobs.meta(jid) or {})
                m_done["limit_woken"] = True
                m_done["limit_woken_to"] = new["id"]
                with self.jobs.lock:
                    self.jobs.db.execute(
                        "INSERT OR REPLACE INTO job_meta(job_id,data) VALUES(?,?)",
                        (jid, json.dumps(m_done)),
                    )
                n = self.jobs.replace_dependency(jid, new["id"])
                woken.append(dict(action="reissue", old=jid, new=new["id"], agent=pick, remapped=n))
                reissues += 1
                self.say("agency: woke limit-failed %s -> %s via %s" % (jid, new["id"], pick))
            except Exception as e:
                woken.append(dict(action="wake_error", job=jid, why=str(e)[:160]))
        if expired or woken:
            return dict(action="cooldown_wake", expired=[e["agent"] for e in expired], woken=woken[:8])
        return None

    def tick(self, dry=False):
        """dry=True only says what would happen (which agent for which step) and runs nothing"""
        if POLICY.killed(self.data):
            return dict(action="idle", why="kill switch is on (agency_off)")
        if POLICY.in_quiet_hours(self.cfg, self.now()):
            return dict(action="idle", why="quiet hours")
        if not dry:
            self._expire_tasks()
            self._park_held_running()
            wake = self._wake_after_cooldowns()
            if wake and wake.get("woken"):
                return wake
        if self.hooks:                                                                  # a job parked "held by a dependency" resumes by itself once that dependency is satisfied (nobody has to unblock it by hand)
            for j in self.jobs.list(state="blocked", limit=50):
                if "dependency" in (j.get("why") or "").lower() and self.hooks.ready(self.jobs.get(j["id"]))[0] and not dry:
                    try:
                        self.jobs.unblock(j["id"], "agency", "its dependencies are satisfied: resumed")
                    except Exception:
                        pass
        for j in self.jobs.list(state="reviewing", limit=20):
            if self.jobs.open_task(j["id"], "review"):
                continue                                                           # somebody is already reviewing it
            if dry:
                return dict(action="would_review", job=j["id"])
            return self.review(self.jobs.get(j["id"]))
        for j in self.jobs.list(state="queued", limit=20):
            if self._held(j["id"]):
                continue
            if not self.jobs.get(j["id"])["steps"] and not self.jobs.open_task(j["id"], "plan"):
                res = self.plan(self.jobs.get(j["id"]), dry)
                if res.get("action") != "plan_retry":                                    # a planner that failed is retried next tick; meanwhile runnable steps are not held up
                    return res
        skip = {j["id"] for j in self.jobs.list(state="running", limit=50) if self._held(j["id"])}
        held_up = None                                                                       # the first job that could not be served: it must not starve the jobs behind it
        while True:
            nxt = self.jobs.next_runnable(skip_jobs=skip)
            if not nxt:
                return held_up or dict(action="idle", why="nothing to run")
            job, step = nxt
            agent, rejects = self.choose(job, step)
            if not agent:
                # Do not sit in a queue while a capable agent is up — force full reroute once.
                agent, rejects2 = self.choose(job, step, force_reroute=True)
                rejects = list(rejects) + list(rejects2)
            if not agent:
                # Truly nobody available: note it as blocked-reroute, not a silent queue.
                why = rejects or ["no capable agent available"]
                try:
                    self.jobs.note(job["id"], "reroute waiting: " + "; ".join(why)[:240])
                except Exception:
                    pass
                held_up = held_up or dict(action="wait", job=job["id"], step=step["n"], why=why)
                skip.add(job["id"])
                continue
            # Skip pull-first hold when we already rerouted off a blocked prefer — run now.
            rerouted = any("rerout" in (r or "").lower() or "preferred but blocked" in (r or "").lower() for r in rejects)
            if (not rerouted) and self._pull_first_refusal(
                ("step", job["id"], step["n"]), step["needs"], COST_RANK[agent["cost"]], TIER_RANK[self.min_tier(job, step)]
            ):
                held_up = held_up or dict(action="reserved", job=job["id"], step=step["n"], why="waiting for an agent that asks for work (pull) to take it")
                skip.add(job["id"])
                continue
            if dry:
                return dict(action="would_run", agent=agent["id"], job=job["id"], step=step["n"], objective=step["objective"][:120])
            return self.run_step(job, step, agent)

    # ------------------------------------------------------------ planning
    def planners(self):
        """agents that may plan, in order: the configured order (claude-code, codex-cli, gemini-cli), then any other plan-capable agent that is not free / local
        (the free OpenRouter models and the local 4B write plans that do not hold up: they never plan)"""
        order = list(self.cfg.get("planner_order") or PLANNER_ORDER)
        never = set(self.cfg.get("planner_never") or PLANNER_NEVER)
        cands = [a for a in self.reg.capable(["plan"]) if a["id"] not in never and a.get("cost") not in ("free", "local")]
        return sorted(cands, key=lambda a: (order.index(a["id"]) if a["id"] in order else len(order), COST_RANK.get(a.get("cost"), 9), a["id"]))

    HOST_PATH = re.compile(r"(?<![A-Za-z0-9])[A-Za-z]:[\\/][^\s\"',;)]*|\\\\[A-Za-z0-9_.-]+\\[^\s\"',;)]*")

    def plan_problems(self, steps, job):
        """-> [problems] with a plan before it becomes steps: a step whose needs no enabled, installed agent has (agents that are only busy or cooling down are fine: the step waits), and a step that
        relies on a file that exists only on the host (steps run in a sandbox copy). The planner is told these once and asked again."""
        from .jobs import NEEDS, is_code
        out = []
        target = str(job.get("target") or "").replace("\\", "/").rstrip("/").lower()
        for i, st in enumerate(steps or [], 1):
            st = st if isinstance(st, dict) else {}
            needs = [n for n in (st.get("needs") or []) if isinstance(n, str)] or (["tool"] if isinstance(st.get("tool"), dict) else ["code" if is_code(job) else "research"])
            bad = [n for n in needs if n not in NEEDS]
            if bad:
                out.append("step %d: unknown needs %s (use only: %s)" % (i, bad, ", ".join(NEEDS)))
                continue
            capable = [a for a in self.reg.capable(needs, push=False)]
            usable = [a for a in capable if not any(w in (self.av.check(a)[1] if (a.get("run") or {}).get("via") != "pull" else self.av.check_pull(a)[1]) for w in ("disabled", "is not installed"))]
            if not usable:
                have = "; ".join("%s: %s" % (a["id"], "/".join(a["strengths"])) for a in self.reg.agents.values() if a.get("enabled", True))
                out.append("step %d needs %s but no enabled, installed agent has all of those (agents: %s)" % (i, "+".join(needs), have))      # the planner is told every agent (it was cut at 600 characters)
            blob = " ".join([str(st.get("objective") or "")] + ([json.dumps(st["tool"])] if isinstance(st.get("tool"), dict) else []))
            host = [m for m in self.HOST_PATH.findall(blob) if not (target and m.replace("\\", "/").lower().startswith(target))]
            if host:
                out.append("step %d relies on host-only path(s) %s: steps run in a sandbox copy of the project; use paths relative to the project folder" % (i, ", ".join(sorted(set(host))[:3])))
        return out

    def plan(self, job, dry=False):
        """a queued job with no steps: the cheapest available agent that can plan writes them"""
        tried = self._plan_failed.setdefault(job["id"], set())
        cands = [a for a in self.planners() if self.av.check(a)[0]]
        fresh = [a for a in cands if a["id"] not in tried]                                  # a planner whose answer was unusable is not asked again for this job
        if not cands:
            return dict(action="wait", job=job["id"], why="no strong planner available (planning is never given to the free or local models)")
        agent = (fresh or cands)[0]
        if self._pull_first_refusal(("plan", job["id"]), ["plan"], COST_RANK[agent["cost"]]):
            return dict(action="reserved", job=job["id"], why="waiting for an agent that asks for work (pull) to plan it")
        if dry:
            return dict(action="would_plan", agent=agent["id"], job=job["id"])
        tid = self.jobs.try_open_task("plan", job["id"], 0, agent["id"], via="push", lease_s=agent["run"].get("timeout_s", 1800) + 120)
        if not tid:
            return dict(action="taken", job=job["id"])
        run_id = self.jobs.run_started(agent["id"], job["id"], 0)                    # planning is a run too: it counts against the agent's caps
        self._route(job, "plan", 0, agent)
        t0 = self.now()
        try:
            steps = self.adapter.plan(agent, job)
            self.jobs.run_done(run_id, "ok")
            self._model_done(job, "plan", 0, agent, t0, "ok")
            self.jobs.add_steps(job["id"], steps, agent["id"])
        except NotImplementedError as e:
            self.jobs.run_done(run_id, "failed")
            self.jobs.set_state(job["id"], "blocked", "agency", str(e))
            return dict(action="blocked", job=job["id"], why=str(e))
        except Exception as e:
            self.jobs.run_done(run_id, "failed")
            why = "%s: %s" % (e.__class__.__name__, str(e)[:4000])
            if detect_limit(why):
                self.jobs.cooldown_set(agent["id"], why, until=parse_reset(why, self.now()))
                return dict(action="cooldown", agent=agent["id"], job=job["id"])
            tried.add(agent["id"])
            if len(tried) < 3 and len(cands) > len(tried):                                  # not the job's fault: another planner gets it before it is given up on
                self.jobs.note(job["id"], "planning by %s failed (%s); trying another planner" % (agent["id"], why[:80]))
                return dict(action="plan_retry", job=job["id"], agent=agent["id"], why=why)
            self.jobs.set_state(job["id"], "failed", "agency", "planning failed: " + why)
            return dict(action="job_failed", job=job["id"], why=why)
        finally:
            self.jobs.task_close(tid)
        return dict(action="planned", agent=agent["id"], job=job["id"], steps=len(steps))

    # ------------------------------------------------------------ steps (push)
    def run_step(self, job, step, agent):
        aid = agent["id"]
        tid = self.jobs.try_open_task("step", job["id"], step["n"], aid, via="push", lease_s=agent["run"].get("timeout_s", 1800) + 120)
        if not tid:
            return dict(action="taken", job=job["id"], step=step["n"])
        try:
            challenge = "escalating or retrying after an earlier failed attempt on this step" if step["attempts"] else ""
            att = self.b.attempt(aid, step.get("post_id") or job["post_id"], step["objective"], expected="step done", challenge=challenge)
            if not att.get("ok"):
                self.jobs.step_set(job["id"], step["n"], "todo", bump=True)
                return self._after_failure(job, step, aid, "the ledger refused the attempt: %s" % att.get("error"), counted=True)
            self.jobs.step_set(job["id"], step["n"], "running", agent=aid, attempt_id=att["attempt"])
            run_id = self.jobs.run_started(aid, job["id"], step["n"])
            self.jobs.task_update(tid, run_id=run_id, attempt_id=att["attempt"])
            self._route(job, "step", step["n"], agent, step, "push", self._last_rejects)
            self.say("agency: %s running %s step %d" % (aid, job["id"], step["n"]))
            t0 = self.now()
            try:
                res = self.adapter.run(agent, step, job, None)
            except NotImplementedError as e:
                res = Result(ok=False, error=str(e))
            except Exception as e:
                res = Result(ok=False, error="%s: %s" % (e.__class__.__name__, e))
            out = self._finish_step(job, step, agent, att["attempt"], run_id, res)
            self._model_done(job, "step", step["n"], agent, t0, out.get("action"))
            return out
        finally:
            self.jobs.task_close(tid)

    def _finish_step(self, job, step, agent, attempt_id, run_id, res):
        """what happens with a step's result, whoever produced it (a launched agent or one that asked for the work)"""
        aid = agent["id"]
        if not res.limit_hit and not res.ok and detect_limit(res.text + " " + res.error):
            res.limit_hit = True
        fault = (not res.ok) and not res.limit_hit and (res.error or "").startswith("agent_fault:")
        self.jobs.run_done(run_id, "limit" if res.limit_hit else "ok" if res.ok else "fault" if fault else "failed")
        if fault:                                                                              # the agent's own set-up is broken (unknown model, CLI missing / logged out, Docker down): not the step's fault, not an attempt used
            why = res.error[12:].strip()
            until = self.jobs.cooldown_set(aid, "set-up problem: " + why[:180], base_min=30)
            self.b.result(aid, attempt_id, "partial", _fit("agent set-up problem, not the step: " + why, 4000, "set-up problem"))
            self.jobs.step_set(job["id"], step["n"], "todo")
            self.jobs.note(job["id"], "%s could not run (set-up problem: %s): cooling it down, step %d goes to another agent" % (aid, why[:120], step["n"]))
            try:
                self._unpin_limited_prefer(job["id"], aid, until=until)
            except Exception:
                pass
            return dict(action="agent_fault", agent=aid, job=job["id"], step=step["n"], until=until, why=why[:200])
        if not res.limit_hit and getattr(self.av, "router", None):
            try:
                self.av.router.record(aid, "code" if is_code(job) else "step", bool(res.ok))
            except Exception:
                pass
        if res.limit_hit:
            until = self.jobs.cooldown_set(aid, (res.error or res.text)[:200], until=parse_reset(res.error + " " + res.text, self.now()))
            self.b.result(aid, attempt_id, "partial", "hit a usage limit; cooling down")
            self.jobs.step_set(job["id"], step["n"], "todo")                   # a limit is not the step's fault: it goes back, unpenalised
            # Unpin a sticky prefer_* pointing at the limited agent so the next tick picks the next available.
            try:
                self._unpin_limited_prefer(job["id"], aid, until=until)
            except Exception:
                pass
            return dict(action="cooldown", agent=aid, job=job["id"], step=step["n"], until=until)
        if res.ok:
            self.jobs.cooldown_clear(aid)
            self.b.result(aid, attempt_id, "worked", _fit(res.text or "done", 4000, "result"))      # later agents read this (prior attempts); was a silent 600-character cut, the whole result is also attached
            ref = res.ref
            if len(res.text or "") > 600 and step.get("post_id"):                  # a long result lives in an attachment, the step keeps a short summary and the link
                try:
                    ref = self.b.attach(aid, step["post_id"], "result of %s step %d" % (job["id"], step["n"]), res.text).get("doc") or ref
                except Exception:
                    pass
            self.jobs.step_set(job["id"], step["n"], "done", evidence=res.text, result_ref=ref or None)
            self.jobs.result_add(job["id"], res.touched, res.diff_lines)
            if self.jobs.all_steps_done(job["id"]):
                self.jobs.set_state(job["id"], "reviewing", "agency", "all steps done")
            return dict(action="done", agent=aid, job=job["id"], step=step["n"])
        if (res.error or "").startswith("declined:"):                                          # the agent honestly said it cannot do this: not its quality failure, not an attempt used; it is not offered the step again
            self.b.result(aid, attempt_id, "partial", "declined: " + _fit(res.error[9:].strip(), 4000, "reason"))      # a later agent reads this attempt: the whole reason (it was 291 characters)
            self.jobs.step_exclude(job["id"], step["n"], aid)
            self.jobs.step_set(job["id"], step["n"], "todo")
            self.jobs.note(job["id"], "%s said it cannot do step %d (%s): handed to another agent" % (aid, step["n"], res.error[9:120].strip()))
            return dict(action="declined", agent=aid, job=job["id"], step=step["n"], why=res.error[9:].strip())
        self.b.result(aid, attempt_id, "failed", _fit(res.error or res.text or "failed", 4000, "failure"))      # a later agent reads this to avoid the dead end (it was cut at 600 characters)
        if "no usable" in (res.error or "") and step.get("post_id"):                          # the same agent twice unable to give the required answer format on this step: it is not asked again (a weak model would burn every attempt)
            try:
                bad = self.b.db.execute("SELECT COUNT(*) c FROM attempts WHERE post_id=? AND actor=? AND outcome='failed'", (step["post_id"], aid)).fetchone()["c"]
                if bad >= 2:
                    self.jobs.step_exclude(job["id"], step["n"], aid)
            except Exception:
                pass
        self.jobs.step_set(job["id"], step["n"], "todo", bump=True, evidence="FAILED (%s, attempt %d): %s" % (aid, step.get("attempts", 0) + 1, _fit(res.error or res.text or "failed", 12000, "failure report")))   # the next try is told what went wrong (whole, or head + tail with a marker)
        return self._after_failure(job, step, aid, res.error or res.text or "failed")

    def _after_failure(self, job, step, aid, why, counted=False):
        cur = self.jobs.get(job["id"])
        n = next(s["attempts"] for s in cur["steps"] if s["n"] == step["n"])
        if n >= cur["budget"]["max_attempts_per_step"]:
            self.jobs.step_set(job["id"], step["n"], "failed", evidence=why)
            self.jobs.cancel_steps(job["id"])
            self.jobs.set_state(job["id"], "failed", "agency", "step %d failed after %d attempt(s): %s" % (step["n"], n, why))
            return dict(action="job_failed", job=job["id"], step=step["n"], why=why[:200])
        return dict(action="failed", agent=aid, job=job["id"], step=step["n"], attempts=n, why=why[:200])

    # ------------------------------------------------------------ review + gate
    def _prepare_review(self, job, reviewer, via, lease_s):
        """claim the review and run the acceptance check. -> (task id, early result); when there is an early result the review does not go on"""
        tid = self.jobs.try_open_task("review", job["id"], 0, reviewer["id"], via=via, lease_s=lease_s)
        if not tid:
            return None, dict(action="taken", job=job["id"])
        acc = job.get("acceptance") or {}
        if acc.get("check"):
            if not self.checker:
                self.jobs.task_close(tid)
                self.jobs.set_state(job["id"], "blocked", "agency", "the acceptance check cannot run yet: the sandbox runner arrives in Stage 1")
                return None, dict(action="blocked", job=job["id"], why="no check runner")
            out = self.checker(job)
            out, check_out = out if isinstance(out, tuple) else (out, "")
            if out is None:
                self.jobs.task_close(tid)
                self.jobs.set_state(job["id"], "blocked", "agency", "the acceptance check could not run: " + (check_out or "no isolation available")[:200])
                return None, dict(action="blocked", job=job["id"], why="check could not run")
            self.jobs.result_set(job["id"], check_ok=bool(out), check_out=check_out)
        if self.hooks:                                                                       # positive controls must pass and adverse controls must fail as intended
            c_ok, c_note = self.hooks.controls(job)
            if c_ok is not None:
                self.jobs.result_set(job["id"], controls_ok=bool(c_ok), controls_note=c_note)
        return tid, None

    def review(self, job):
        authors = {s["agent"] for s in job["steps"] if s["agent"]}
        reviewer, busy = None, []
        for a in self.reg.reviewers(exclude=authors):
            ok, why = self.av.check(a)
            if ok:
                reviewer = a
                break
            busy.append("%s: %s" % (a["id"], why))
        if not reviewer:
            if busy:                                                                  # capable reviewers exist, they are only busy / cooling down / over a limit: wait in review
                self.jobs.note(job["id"], "waiting for an independent reviewer: " + "; ".join(busy)[:200])
                return dict(action="wait", job=job["id"], why=["waiting for an independent reviewer"] + busy)
            if self._pull_waiting_reviewer(authors):
                self.jobs.note(job["id"], "waiting for an agent that asks for work (pull) to review it")
                return dict(action="wait", job=job["id"], why=["waiting for an agent that asks for work (pull) to review it"])
            self.jobs.set_state(job["id"], "blocked", "agency", "no independent reviewer exists: every agent that can review is a step author or disabled")
            return dict(action="blocked", job=job["id"], why="no independent reviewer")
        if self._pull_first_refusal(("review", job["id"]), ["review"], COST_RANK[reviewer["cost"]], 0, exclude=authors):
            return dict(action="reserved", job=job["id"], why="waiting for an agent that asks for work (pull) to review it")
        tid, early = self._prepare_review(job, reviewer, "push", reviewer["run"].get("timeout_s", 1800) + 120)
        if early:
            return early
        try:
            job = self.jobs.get(job["id"])
            run_id = self.jobs.run_started(reviewer["id"], job["id"], 0)                 # a review is a run: it counts against the agent's concurrency and daily cap
            self._route(job, "review", 0, reviewer, None, "push", busy)
            t0 = self.now()
            try:
                verdict = self.adapter.review(reviewer, job, job.get("result"))
                self.jobs.run_done(run_id, "ok")
                self._model_done(job, "review", 0, reviewer, t0, "approve" if verdict.get("approve") else "reject")
            except NotImplementedError as e:
                self.jobs.run_done(run_id, "failed")
                self.jobs.set_state(job["id"], "blocked", "agency", str(e))
                return dict(action="blocked", job=job["id"], why=str(e))
            except Exception as e:
                if detect_limit(str(e)):                                                  # a limit is not a failure: cool the reviewer down and let the next one take it next tick
                    self.jobs.run_done(run_id, "limit")
                    until = self.jobs.cooldown_set(reviewer["id"], str(e)[:200], until=parse_reset(str(e), self.now()))
                    return dict(action="cooldown", agent=reviewer["id"], job=job["id"], until=until)
                self.jobs.run_done(run_id, "failed")
                if any(w in str(e).lower() for w in ("is not recognized", "not installed", "could not start", "no such file", "cannot find", "routeerror", "no backend", "no local model", "no valid verdict")):   # the reviewer's program is broken here: leave THAT reviewer alone, the job waits for another
                    self.jobs.cooldown_set(reviewer["id"], "cannot run: %s" % str(e)[:150], base_min=30)
                    self.jobs.note(job["id"], "the reviewer %s cannot run on this machine (%s); waiting for another" % (reviewer["id"], str(e)[:90]))
                    return dict(action="wait", job=job["id"], why=["the reviewer %s cannot run: %s" % (reviewer["id"], str(e)[:100])])
                self.jobs.set_state(job["id"], "blocked", "agency", "the reviewer failed: %s: %s" % (e.__class__.__name__, str(e)[:200]))
                return dict(action="blocked", job=job["id"], why="the reviewer failed")
            return self._decide_review(job, reviewer["id"], verdict)
        finally:
            self.jobs.task_close(tid)

    def _pull_waiting_reviewer(self, authors):
        return any((a.get("run") or {}).get("via") == "pull" and a.get("enabled", True) and "review" in a["strengths"] and a["id"] not in authors for a in self.reg.agents.values())

    def _decide_review(self, job, reviewer_id, verdict):
        """the gate's decision on a verdict, whoever gave it"""
        job = self.jobs.get(job["id"])
        res = job.get("result") or {}
        authors = {s["agent"] for s in job["steps"] if s["agent"]}
        author = sorted(authors)[-1] if authors else ""
        if res.get("controls_ok") is False:
            verdict = dict(verdict or {}, approve=False, reasons=[res.get("controls_note") or "a control was not met"] + list((verdict or {}).get("reasons") or []))
        dec = decide(job, author, reviewer_id, res.get("check_ok"), verdict, res.get("touched", []), res.get("diff_lines", 0), max_diff=self.cfg["max_diff_lines"])
        if dec["ok"] and self.hooks:
            stops = self.hooks.stop_reasons(job)
            if stops:                                                                        # a stop rule: nothing is promoted; artifacts and logs are kept; the owner decides
                self.jobs.set_state(job["id"], "blocked", "agency", "STOPPED by rule: " + "; ".join(stops)[:260])
                return dict(action="blocked", job=job["id"], reasons=stops)
        if dec["ok"]:
            return self._accept(job)
        fixable = res.get("check_ok") is False or not (verdict or {}).get("approve")
        only_size = dec["needs_owner"] and not any("protected files" in r_ for r_ in dec["reasons"])
        if dec["needs_owner"] and not (fixable and only_size):                          # a person is needed only when nothing a worker could fix remains; too big AND failing is a rework (split it), not a parked job
            self.jobs.set_state(job["id"], "blocked", "agency", "; ".join(dec["reasons"]))
            return dict(action="blocked", job=job["id"], reasons=dec["reasons"])
        if dec["needs_owner"]:
            dec["reasons"] = [r_ for r_ in dec["reasons"] if "lines (limit" not in r_] + ["the change is far too large for one step: split the work into smaller modules and keep each file under about 400 lines"]
        n = self.jobs.rework_step(job["id"], dec["reasons"])
        if n is None:
            self.jobs.set_state(job["id"], "failed", "agency", "review failed twice: " + "; ".join(dec["reasons"]))
            return dict(action="job_failed", job=job["id"], reasons=dec["reasons"])
        self.jobs.set_state(job["id"], "running", "agency", "review asked for changes: " + "; ".join(dec["reasons"]))
        return dict(action="rework", job=job["id"], step=n, reasons=dec["reasons"])

    def _accept(self, job):
        if not is_code(job):
            self.jobs.set_state(job["id"], "delivered", "agency", "result delivered with its evidence")
            return dict(action="delivered", job=job["id"])
        safe = POLICY.is_safe_repo(self.cfg, job["target"])
        if safe and self.cfg["auto_merge"] and self.jobs.merges_today() < self.cfg["max_merges_per_day"] and self.apply_fn:
            outcome = self.apply_fn(job)
            if outcome.get("ok"):
                self.jobs.set_state(job["id"], "merged", "agency", "merged: %s" % outcome.get("summary", ""))
                return dict(action="merged", job=job["id"])
            self.jobs.set_state(job["id"], "failed", "agency", "merge failed and was reverted: %s" % outcome.get("why", ""))
            return dict(action="job_failed", job=job["id"], why=outcome.get("why"))
        why = ("approve to merge" if not safe else "auto-merge is off" if not self.cfg["auto_merge"] else
               "daily merge cap reached" if self.jobs.merges_today() >= self.cfg["max_merges_per_day"] else "the apply runner arrives in Stage 2")
        self.jobs.set_state(job["id"], "ready", "agency", "passed the gate; %s" % why)
        return dict(action="ready", job=job["id"], why=why)

    # ------------------------------------------------------------ pull: agents that ask for work
    FORMATS = dict(step_code='{"files": {"<relative path>": "<COMPLETE new content of that file>"}, "notes": "one or two sentences"}', step_text='{"answer": "<your complete result>", "notes": "one sentence"}',
                   plan='{"steps": [{"objective": "<what to do: complete and self-contained - the files, the exact change or result, and how it is checked>", "needs": ["code"]}]}', review='{"approve": true or false, "reasons": ["<a concrete reason: file, line, what is wrong and what to change>"]}')

    def _payload(self, t):
        job = self.jobs.get(t["job_id"])
        fmt = self.FORMATS["plan" if t["kind"] == "plan" else "review" if t["kind"] == "review" else "step_code" if is_code(job) else "step_text"]
        step = next((s for s in job["steps"] if s["n"] == t["step_n"]), None)
        return dict(task_id=t["id"], kind=t["kind"], job_id=job["id"], job_title=job["title"], step=t["step_n"] or None, objective=(step or {}).get("objective"), prompt=t["prompt"],
                    answer_format=fmt, expires_in_s=max(0, int(t["expires"] - self.now())), how_to_answer="reply by calling agency_submit with task_id and your answer text (the JSON object above, nothing else)")

    def offer(self, agent_id):
        """an agent that cannot be launched (an app, an open session) asks for work: the next task it is eligible for - a review, then a plan, then a step - or none.
        Asking also counts as a heartbeat: while it keeps asking, the push side leaves work it could take for it (first refusal)."""
        a = self.reg.agents.get(agent_id)
        if not a or (a.get("run") or {}).get("via") != "pull" or not a.get("enabled", True):
            return dict(ok=False, error="not_a_pull_agent", message="%s is not an enabled pull agent in the registry" % agent_id)
        self.jobs.heartbeat(agent_id)
        if POLICY.killed(self.data):
            return dict(ok=True, task=None, why="the agency's kill switch is on")
        if POLICY.in_quiet_hours(self.cfg, self.now()):
            return dict(ok=True, task=None, why="quiet hours")
        self._expire_tasks()
        mine = self.jobs.task_for_agent(agent_id)
        if mine:
            return dict(ok=True, task=self._payload(mine), note="you already hold this task: answer it first")
        ok, why = self.av.check_pull(a)
        if not ok:
            return dict(ok=True, task=None, why=why)
        lease = int(a["run"].get("lease_min", 30)) * 60
        if "review" in a["strengths"]:
            for j in self.jobs.list(state="reviewing", limit=20):
                job = self.jobs.get(j["id"])
                if agent_id in {s["agent"] for s in job["steps"] if s["agent"]} or self.jobs.open_task(job["id"], "review"):
                    continue
                tid, early = self._prepare_review(job, a, "pull", lease)
                if early:
                    continue
                job = self.jobs.get(job["id"])
                run_id = self.jobs.run_started(agent_id, job["id"], 0)
                self._route(job, "review", 0, a, None, "pull")
                self.jobs.task_update(tid, run_id=run_id, prompt=self.adapter.review_request(job, job.get("result"), a))
                return dict(ok=True, task=self._payload(self.jobs.task_get(tid)))
        if "plan" in a["strengths"]:
            for j in self.jobs.list(state="queued", limit=20):
                job = self.jobs.get(j["id"])
                if job["steps"] or self.jobs.open_task(job["id"], "plan") or self._held(job["id"]):
                    continue
                tid = self.jobs.try_open_task("plan", job["id"], 0, agent_id, via="pull", lease_s=lease)
                if tid:
                    run_id = self.jobs.run_started(agent_id, job["id"], 0)
                    self._route(job, "plan", 0, a, None, "pull")
                    self.jobs.task_update(tid, run_id=run_id, prompt=_RA.plan_request_for(self.adapter, job, a))
                    return dict(ok=True, task=self._payload(self.jobs.task_get(tid)))
        for job, step in self.jobs.iter_runnable():
            if self._held(job["id"]):
                continue
            if agent_id in ((self.jobs.step_spec(job["id"], step["n"]) or {}).get("exclude") or []):        # it said it cannot do this step: not offered again
                continue
            if not set(step["needs"]) <= set(a["strengths"]) or TIER_RANK[self.reg.tier(agent_id)] < TIER_RANK[self.min_tier(job, step)]:
                continue
            tid = self.jobs.try_open_task("step", job["id"], step["n"], agent_id, via="pull", lease_s=lease)
            if not tid:
                continue
            challenge = "escalating or retrying after an earlier failed attempt on this step" if step["attempts"] else ""
            att = self.b.attempt(agent_id, step.get("post_id") or job["post_id"], step["objective"], expected="step done", challenge=challenge)
            if not att.get("ok"):
                self.jobs.task_close(tid, "refused")
                continue
            self.jobs.step_set(job["id"], step["n"], "running", agent=agent_id, attempt_id=att["attempt"])
            run_id = self.jobs.run_started(agent_id, job["id"], step["n"])
            try:
                prompt = self.adapter.step_request(a, step, job)
            except Exception as e:                                                  # e.g. the target folder is gone: give the step back and say so
                self.jobs.run_done(run_id, "failed")
                self.jobs.task_close(tid, "failed")
                self.b.result(agent_id, att["attempt"], "failed", "could not prepare the task: %s" % str(e)[:200])
                self.jobs.step_set(job["id"], step["n"], "todo", bump=True)
                continue
            self.jobs.task_update(tid, run_id=run_id, attempt_id=att["attempt"], prompt=prompt)
            self._route(job, "step", step["n"], a, step, "pull")
            self.say("agency: %s took %s step %d (pull)" % (agent_id, job["id"], step["n"]))
            return dict(ok=True, task=self._payload(self.jobs.task_get(tid)))
        return dict(ok=True, task=None, why="nothing for you right now (check again in a minute)")

    def submit(self, agent_id, task_id, text):
        """the answer to a task handed out by offer(): read with the same logic as a launched agent's answer"""
        t = self.jobs.task_get(task_id)
        if not t or t["agent"] != agent_id:
            return dict(ok=False, error="no_such_task", message="no such task for %s" % agent_id)
        if t["state"] != "open":
            return dict(ok=False, error="task_closed", message="this task is %s (leases last %s)" % (t["state"], "a limited time"))
        if t["expires"] <= self.now():
            self._expire_tasks()
            return dict(ok=False, error="task_expired", message="the lease ran out; ask for work again")
        a, job = self.reg.get(agent_id), self.jobs.get(t["job_id"])
        text = text or ""
        if t["kind"] == "step":
            step = next(s for s in job["steps"] if s["n"] == t["step_n"])
            calls = self.adapter._tool_calls(text) if hasattr(self.adapter, "_tool_calls") and getattr(self.adapter, "toolbox", None) else None
            if calls:                                                                          # the prompt told this agent to ask for tools: run them here and hand the results back on the SAME task
                n_round = self._pull_rounds.get(task_id, 0) + 1
                self._pull_rounds[task_id] = n_round
                if n_round <= getattr(self.adapter, "MAX_TOOL_ROUNDS", 3):
                    outs = self.adapter.run_tool_calls(calls, job, agent_id)
                    base = t["prompt"].split("\n\nYOUR PREVIOUS REPLY (tool calls):")[0]
                    follow = _RA.tool_round_prompt(base, text, outs, n_round >= self.adapter.MAX_TOOL_ROUNDS)
                    self.jobs.task_update(task_id, prompt=follow)
                    return dict(ok=True, result=dict(action="tool_results", round=n_round, task_id=task_id, tool_results=outs,
                                                     note="the task is still yours: answer it with agency_submit using the required format (or ask for more tools with tool_calls); the results are also in the task prompt"))
            res = self.adapter.step_apply(a, step, job, text)
            if (not res.ok) and "no usable" in (res.error or "") and not (res.error or "").startswith("declined:") and self._fmt_retry.get(task_id, 0) < 1:
                self._fmt_retry[task_id] = 1                                                    # the format was wrong, not the work: one free re-try on the same task before an attempt is used
                fix = self.adapter.repair_prompt(t["prompt"], text, res.error) if hasattr(self.adapter, "repair_prompt") else t["prompt"] + "\n\nYOUR REPLY WAS REJECTED: " + res.error
                self.jobs.task_update(task_id, prompt=fix)
                return dict(ok=True, result=dict(action="format_error", task_id=task_id, problem=res.error,
                                                 note="NOT counted as an attempt: the task is still yours. Reply again with ONLY the required JSON object (first character `{`); the corrected instructions are in the task prompt."))
            out = self._finish_step(job, step, a, t["attempt_id"], t["run_id"], res)
        elif t["kind"] == "plan":
            try:
                steps = self.adapter.plan_apply(text)
                problems = self.plan_problems(steps, job)                                    # the same validation a launched planner's plan gets: told once on the same task, then refused
                if problems and self._fmt_retry.get(task_id, 0) < 1:
                    from .jobs import NEEDS
                    self._fmt_retry[task_id] = 1
                    base = t["prompt"].split("\n\nYOUR PLAN WAS REFUSED:")[0]
                    self.jobs.task_update(task_id, prompt=base + "\n\nYOUR PLAN WAS REFUSED:\n- " + "\n- ".join(problems) + "\nThe full list of needs is: " + ", ".join(NEEDS)
                                          + ". Fix these and reply again with the whole plan in the same JSON shape.")
                    return dict(ok=True, result=dict(action="plan_refused", task_id=task_id, problems=problems,
                                                     note="NOT counted: the task is still yours. Fix the plan and submit it again (the problems are also in the task prompt)."))
                if problems:
                    raise RuntimeError("plan refused after one re-ask: " + "; ".join(problems))
                self.jobs.add_steps(job["id"], steps, agent_id)
            except Exception as e:
                self.jobs.run_done(t["run_id"], "failed")
                self.jobs.task_close(task_id, "failed")
                return dict(ok=False, error="bad_plan", message=str(getattr(e, "args", [e])[0])[:20000])
            self.jobs.run_done(t["run_id"], "ok")
            out = dict(action="planned", agent=agent_id, job=job["id"], steps=len(steps))
        else:
            verdict = self.adapter.review_apply(text)
            if (verdict.get("reasons") or []) == ["the reviewer's answer was not a valid verdict"]:   # not a verdict at all: one free re-try on the same task, then this reviewer is set aside; the work is not rejected
                if self._fmt_retry.get(task_id, 0) < 1:
                    self._fmt_retry[task_id] = 1
                    fix = self.adapter.repair_prompt(t["prompt"], text, "it was not a valid verdict") if hasattr(self.adapter, "repair_prompt") else t["prompt"]
                    self.jobs.task_update(task_id, prompt=fix)
                    return dict(ok=True, result=dict(action="format_error", task_id=task_id, problem="not a valid verdict",
                                                     note="NOT counted: the task is still yours. Reply again with ONLY {\"approve\": true|false, \"reasons\": [...]} (first character `{`)."))
                self.jobs.run_done(t["run_id"], "failed")
                self.jobs.task_close(task_id, "failed")
                self.jobs.cooldown_set(agent_id, "gave no valid review verdict twice", base_min=30)
                self.jobs.note(job["id"], "reviewer %s gave no valid verdict: waiting for another reviewer" % agent_id)
                return dict(ok=True, result=dict(action="review_invalid", agent=agent_id, job=job["id"]))
            self.jobs.run_done(t["run_id"], "ok")
            out = self._decide_review(job, agent_id, verdict)
        self._model_done(job, t["kind"], t["step_n"], a, t["issued"], out.get("action") if isinstance(out, dict) else "", model=a.get("name", agent_id))
        self.jobs.task_close(task_id)
        return dict(ok=True, result=out)

    # ------------------------------------------------------------ daemon
    def startup_check(self):
        """the gate's own files must match the owner's lock (`agentdispatch agency lock`); anything else stops the daemon before it runs an agent"""
        import os
        from .gate import verify_lock
        root = self.cfg.get("repo_root") or os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        return verify_lock(root, self.data)

    def loop(self, stop=None):
        """the daemon: tick every cfg['tick_s'] seconds until `stop` (a threading.Event) is set"""
        problems = self.startup_check()
        if problems:
            raise RuntimeError("the agency will not start: " + "; ".join(problems))
        self.jobs.recover()
        while not (stop and stop.is_set()):
            try:
                self.cfg = POLICY.load(self.data)                                  # the owner's switches (auto-merge, safe folders) take effect without restarting the daemon
                self.tick()
            except Exception as e:                                             # one bad tick must not end the daemon
                self.say("agency error: %s: %s" % (e.__class__.__name__, e))
            time.sleep(self.cfg["tick_s"])
