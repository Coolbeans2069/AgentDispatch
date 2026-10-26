"""the governor: the rules that sit between a job and the campaign. It decides whether a job may START (dependencies, gates, approvals), runs the controls that decide whether it may PASS, checks the
stop rules before anything is accepted, and when a job ends it writes the machine-readable result, seals the evidence, proposes (never makes) campaign transitions, records the playbook, pairs primary
and shadow jobs and compares them, and spawns follow-up jobs from open premises. It does no scientific work and never changes campaign state by itself."""
import json, os, re, time

from ..jsonx import extract_json
from ..store import BoardError
from ..textfit import fit as _gfit
from . import meta as M
from . import scope as SC
from . import templates as T

OK_STATES = ("merged", "delivered")
WORKING = ("queued", "planning", "running", "reviewing", "blocked")
FINISH_STATES = ("ready", "merged", "delivered", "failed", "cancelled", "blocked")
PROMOTERS = ("primary", "verifier", "provenance")                    # roles allowed to propose a transition; adversarial reviewers, reviewers and governors only give evidence
MAX_AUTO_JOBS = 6
MAX_AUTO_DEPTH = 3
STATUS_OF = dict(merged="COMPLETE", delivered="COMPLETE", ready="AWAITING_OWNER", failed="FAILED", blocked="BLOCKED", cancelled="CANCELLED")


class Governor:
    def __init__(self, board, jobs, registry, toolbox, campaign, evidence, playbooks, work_root, data_dir, now=time.time):
        self.b, self.jobs, self.reg, self.tools, self.campaign, self.evidence, self.playbooks = board, jobs, registry, toolbox, campaign, evidence, playbooks
        self.work_root, self.data, self.now = work_root, data_dir, now
        self.pairs_path = os.path.join(data_dir, "pairs.json")
        self.pairs = {}
        try:
            self.pairs = json.load(open(self.pairs_path, encoding="utf-8"))
        except (OSError, ValueError):
            pass
        jobs.listeners.append(self.on_state)

    # ------------------------------------------------------------ small helpers
    def _jdir(self, jid):
        d = self.evidence.job_dir(jid)
        os.makedirs(d, exist_ok=True)
        return d

    def _read(self, jid, name):
        try:
            return json.load(open(os.path.join(self.evidence.job_dir(jid), name), encoding="utf-8"))
        except (OSError, ValueError):
            return None

    def _write(self, jid, name, obj):
        with open(os.path.join(self._jdir(jid), name), "w", encoding="utf-8", newline="\n") as f:
            json.dump(obj, f, indent=1, ensure_ascii=False)

    def _ws(self, job):
        from ..agency.workspace import Workspace
        return Workspace(self.work_root, job["id"], job.get("created"))

    def _save_pairs(self):
        tmp = self.pairs_path + ".tmp"
        with open(tmp, "w", encoding="utf-8", newline="\n") as f:
            json.dump(self.pairs, f, indent=1)
        os.replace(tmp, self.pairs_path)

    def _post(self, title, body, tags=(), severity=0):
        try:
            return self.b.post("agency", "status", title[:140], body[:1500], tags=list(tags), severity=severity)
        except BoardError:
            return None

    # ------------------------------------------------------------ may this job start?
    def ready(self, job):
        """-> (True, '') or (False, why). Never raises."""
        try:
            m = job.get("meta") if "meta" in job else self.jobs.meta(job["id"])
            if not m:
                return True, ""
            why = []
            if m.get("not_before") and self.now() < m["not_before"]:
                why.append("not before %s" % time.strftime("%Y-%m-%d %H:%M", time.localtime(m["not_before"])))
            for dj in m.get("depends_on_jobs", []):
                try:
                    st = self.jobs.get(dj)["state"]
                except BoardError:
                    why.append("depends on %s, which does not exist" % dj)
                    continue
                if st in ("failed", "cancelled"):
                    why.append("its dependency %s is %s" % (dj, st))
                elif st not in OK_STATES:
                    why.append("waiting for job %s (%s)" % (dj, st))
            if m.get("depends_on_gates"):
                miss = self.campaign.unmet(m.get("campaign", ""), m["depends_on_gates"])
                if miss:
                    why.append("waiting for gate(s) %s" % ", ".join(miss))
            for ex in m.get("exclusive_with", []):
                try:
                    if self.jobs.get(ex)["state"] in ("planning", "running", "reviewing"):
                        why.append("must not overlap with %s, which is running" % ex)
                except BoardError:
                    pass
            for art in m.get("wait_for_artifacts", []):
                base = m.get("repo") or ""
                if not (base and os.path.exists(os.path.join(base, art))):
                    why.append("waiting for artifact %s" % art)
            if (m.get("result_bearing") or m.get("human_approval") in ("before_run", "before_result")) and not self.campaign.run_approved(job["id"]):
                why.append("needs your approval to run")
            return (not why), "; ".join(why)
        except Exception as e:                                                          # fail CLOSED: a job whose gates cannot be read must not run as if it had none
            return False, "the governor could not evaluate this job's gates: %s: %s" % (e.__class__.__name__, str(e)[:100])

    def waiting(self):
        """queued jobs held back by a rule, with the reason: what the owner sees as 'waiting'"""
        out = []
        for j in self.jobs.list(state="queued", limit=100) + self.jobs.list(state="running", limit=100):
            ok, why = self.ready(self.jobs.get(j["id"]))
            if not ok:
                out.append(dict(id=j["id"], title=j["title"], why=why, needs_approval="approval" in why))
        return out

    # ------------------------------------------------------------ controls: positive must pass, adverse must fail
    def controls(self, job):
        """-> (ok | None, text). None = the job has no controls."""
        m = job.get("meta") or {}
        ctl = list(m.get("controls") or []) + list((job.get("acceptance") or {}).get("controls") or [])
        if not ctl:
            return None, ""
        ws = self._ws(job)
        ok, out = self.tools.controls(ctl, cwd=ws.project if os.path.isdir(ws.project) else None, caller="governor", job=job["id"])
        bad = ["%s (wanted %s, got %s)" % (c["label"], c["expect"], c["got"]) for c in out if not c["met"]]
        note = ("all %d controls met" % len(out)) if ok else "controls not met: " + "; ".join(bad)[:300]
        self.jobs.result_set(job["id"], controls=out, controls_ok=bool(ok), controls_note=note)
        return ok, note

    # ------------------------------------------------------------ the stop rules, checked before a result is accepted
    def stop_reasons(self, job):
        try:
            return self._stop_reasons(job)
        except Exception as e:
            return ["the governor could not evaluate the stop rules: %s" % str(e)[:120]]

    def _stop_reasons(self, job):
        m = job.get("meta") or {}
        rules = set(m.get("stop_on") or [])
        if not rules:
            return []
        res = job.get("result") or {}
        out = []
        touched = res.get("touched") or []
        if "check_failed" in rules and res.get("check_ok") is False:
            out.append("check_failed: the acceptance check did not pass")
        if "control_failed" in rules and res.get("controls_ok") is False:
            out.append("control_failed: " + str(res.get("controls_note") or "a control was not met")[:150])
        if "scope_breach" in rules:
            for rel in touched:
                ok, why = SC.write_ok(m, rel)
                if not ok:
                    out.append("scope_breach: " + why)
                    break
        if "independence_breach" in rules:
            forb = (m.get("independence") or {}).get("forbidden_inputs") or []
            hit = [r for r in touched if SC.match(r, forb)]
            if hit:
                out.append("independence_breach: touched forbidden input %s" % hit[0])
            ws = self._ws(job)
            if forb and os.path.isdir(ws.project) and any(SC.match(f, forb) for f in ws.files()):
                out.append("independence_breach: the workspace contains forbidden input")
        if "hash_mismatch" in rules:
            ok, why = self.evidence.verify()
            if not ok:
                out.append("hash_mismatch: " + why)
            if self.evidence.is_sealed(job["id"]):
                ok, bad = self.evidence.verify_seal(job["id"])
                if not ok:
                    out.append("hash_mismatch: " + "; ".join(bad)[:150])
        if "frozen_input_changed" in rules:
            ws = self._ws(job)
            man = self._read(job["id"], "inputs.json")
            if man and os.path.isdir(ws.base):
                from .seal import sha256_file
                for rel, h in man.items():
                    p = os.path.join(ws.base, rel)
                    if not os.path.isfile(p) or sha256_file(p) != h:
                        out.append("frozen_input_changed: %s" % rel)
                        break
        if "tree_dirty" in rules and m.get("repo"):
            r = self.tools.run("git", args=["status", "--porcelain"], cwd=m["repo"], caller="governor", job=job["id"])
            if r.get("ok") and (r.get("stdout") or "").strip():
                out.append("tree_dirty: the repository has uncommitted changes")
        if "unauthorized_gate" in rules:
            ag = self._read(job["id"], "agent_result.json") or {}
            for gate in (ag.get("requested_transition") or {}):
                if gate != m.get("gate") and gate not in (m.get("unlocks") or []):
                    out.append("unauthorized_gate: it asked to move %s, which this job may not" % gate)
        if "discrepancy" in rules and (self._read(job["id"], "flags.json") or {}).get("discrepancy"):
            out.append("discrepancy: the independent routes disagree")
        return out

    def record_inputs(self, job):
        """once, when the workspace is made: the hash of every input file, so a later change to the frozen inputs is detectable"""
        if self._read(job["id"], "inputs.json") is not None:
            return
        from .seal import sha256_file
        ws = self._ws(job)
        man = {}
        for rel in ws.files(ws.base)[:3000]:
            try:
                man[rel] = sha256_file(os.path.join(ws.base, rel))
            except OSError:
                pass
        self._write(job["id"], "inputs.json", man)

    # ------------------------------------------------------------ what a model says about its own result
    def record_agent_result(self, jid, step_n, d):
        """a step's answer carried a `result` object: keep the latest (validated later, never trusted)"""
        if isinstance(d, dict):
            d = dict(d, step=step_n, t=self.now())
            self._write(jid, "agent_result.json", d)

    def parse_and_record(self, jid, step_n, text):
        d = extract_json(text or "", "result")
        if isinstance(d, dict) and isinstance(d.get("result"), dict):
            self.record_agent_result(jid, step_n, d["result"])

    # ------------------------------------------------------------ the end of a job
    def on_state(self, job, frm, to):
        if to in FINISH_STATES:
            self.finish(job, to)
        if to == "merged":
            self.register_tool(job)

    def finish(self, job, to):
        m = job.get("meta") or {}
        sealed_now = False
        if to in ("ready", "merged", "delivered") and m.get("seal", True) and (m.get("campaign") or m.get("template")) and not self.evidence.is_sealed(job["id"]):
            self._seal(job)
            sealed_now = True
        result = self.result_json(job, to)
        self._write(job["id"], "result.json", result)
        try:
            self.playbooks.record(self.workflow(job, to, result))
        except Exception as e:
            self.b._event(job["id"], "agency.playbook_error", "agency", dict(error=str(e)[:200]))
        if sealed_now:
            self._after_seal(job, result)

    def _seal(self, job):
        ws = self._ws(job)
        res = job.get("result") or {}
        rec = dict(title=job["title"], kind=job["kind"], campaign=(job.get("meta") or {}).get("campaign"), gate=(job.get("meta") or {}).get("gate"), commands=self._commands(job), models=self._models(job["id"]),
                   environment=self._env(), check_ok=res.get("check_ok"), controls_ok=res.get("controls_ok"), steps=[dict(n=s["n"], agent=s["agent"], state=s["state"]) for s in job["steps"]])
        extra = {"steps/step_%d.txt" % s["n"]: (s.get("evidence") or "") for s in job["steps"] if s.get("evidence")}
        extra["workflow_inputs.json"] = json.dumps(dict(goal=job["goal"], acceptance=job.get("acceptance"), meta=job.get("meta")), indent=1, ensure_ascii=False)
        proj = ws.project if os.path.isdir(ws.project) else None
        base = ws.base if os.path.isdir(ws.base) else None
        seal = self.evidence.seal(job["id"], proj, res.get("touched") or [], base, rec, extra)
        self.b._event(job["id"], "job.sealed", "governor", dict(seal=seal.get("seal_sha256"), outputs=len(seal.get("outputs", []))))

    def _env(self):
        st = self.tools.reg.scan()
        return {k: (v.get("version") or "") for k, v in st["tools"].items() if v.get("available") and k in ("python", "lean", "lake", "git", "sympy", "mpmath", "numpy", "scipy", "z3", "flint")}

    def _commands(self, job):
        out = []
        chk = (job.get("acceptance") or {}).get("check")
        if chk:
            out.append(chk if isinstance(chk, str) else " ".join(chk))
        for r in self.tools.history(40, job=job["id"]):
            out.append("%s %s" % (r.get("tool"), " ".join(r.get("args") or [])[:80]))
        return out[:30]

    def _models(self, jid):
        out = []
        for r in self.b.db.execute("SELECT data FROM events WHERE post_id=? AND type='job.model' ORDER BY id", (jid,)):
            d = json.loads(r["data"] or "{}")
            if d.get("model"):
                out.append(d["model"])
        return sorted(set(out))

    # ------------------------------------------------------------ the machine-readable result
    def result_json(self, job, to=None):
        to = to or job["state"]
        m = job.get("meta") or {}
        res = job.get("result") or {}
        ag = self._read(job["id"], "agent_result.json") or {}
        claim = ag.get("claim_class") if ag.get("claim_class") in M.CLAIMS else ""
        if claim in M.SELF_CLAIMS_FORBIDDEN:
            claim = ""                                                                    # only a second sealed, independent job can establish that
        claim = claim or m.get("claim_class") or ""
        rt = {str(g): str(v)[:40] for g, v in (ag.get("requested_transition") or {}).items()} if isinstance(ag.get("requested_transition"), dict) else {}
        seal = self.evidence.read_seal(job["id"])
        arts = [dict(path=o["path"], sha256=o["sha256"]) for o in (seal or {}).get("outputs", [])] or [dict(path=p) for p in res.get("touched", [])]
        return dict(job=job["id"], stage=m.get("gate", ""), campaign=m.get("campaign", ""), status=STATUS_OF.get(to, to.upper()), claim_class=claim, first_open_premise=str(ag.get("first_open_premise") or "")[:300] or None,
                    campaign_state_change_requested=bool(rt), requested_transition=rt, unlocks=m.get("unlocks", []), result_bearing=bool(m.get("result_bearing")), artifacts=arts[:60],
                    evidence_tip=self.evidence.tip(), sealed=bool(seal), checks=dict(check_ok=res.get("check_ok"), controls_ok=res.get("controls_ok")), role=m.get("role", ""), template=m.get("template", ""),
                    models=self._models(job["id"]), values=ag.get("values") if isinstance(ag.get("values"), dict) else None, finished=self.now())

    # ------------------------------------------------------------ after the seal: proposals, pairs, follow-ups
    def _after_seal(self, job, result):
        m = job.get("meta") or {}
        stops = self.stop_reasons(job)
        if stops:
            self._write(job["id"], "stop_report.json", dict(job=job["id"], reasons=stops, t=self.now(), note="artifacts and logs are preserved; nothing was promoted"))
            self._post("[stop] %s: %s" % (job["id"], stops[0][:90]), "\n".join(stops), tags=["stop", "job"], severity=1)
        elif job["state"] in ("ready", "merged", "delivered") and (job.get("result") or {}).get("check_ok") is not False and (job.get("result") or {}).get("controls_ok") is not False:
            self._propose(job, result)
        self._pairs_after(job, result)
        prem = result.get("first_open_premise")
        if prem and not result["requested_transition"]:
            self.spawn_followup(job, prem)

    def _propose(self, job, result):
        m = job.get("meta") or {}
        if m.get("role") not in PROMOTERS or not m.get("campaign"):
            return
        wanted = dict(result["requested_transition"])
        if not wanted and m.get("gate") and (m.get("template") or "").endswith(("closure", "certificate")):
            wanted = {m["gate"]: "CANDIDATE"}                                           # a finished closure job makes its gate a CANDIDATE; closing it is for the audit / the owner
        for gate, to in wanted.items():
            if gate != m.get("gate") and gate not in (m.get("unlocks") or []):
                continue
            self.campaign.propose(m["campaign"], gate, to, job["id"], job["steps"][-1]["agent"] if job["steps"] else "agency", "job %s ended %s" % (job["id"], job["state"]), dict(evidence_tip=result["evidence_tip"]),
                                  result.get("claim_class", ""))

    # ------------------------------------------------------------ pairs: primary / shadow / reconcile
    def spawn_shadow(self, primary_id, cutoff="", actor="owner"):
        p = self.jobs.get(primary_id)
        pm = p.get("meta") or {}
        cut = cutoff or (pm.get("independence") or {}).get("cutoff") or ""
        if not cut:
            raise BoardError("no_cutoff", "a shadow job needs an evidence cutoff: say which commit it may read up to")
        forb = list(dict.fromkeys(list((pm.get("scope") or {}).get("write") or []) + [primary_id] + list((pm.get("independence") or {}).get("forbidden_inputs") or [])))
        kw = T.apply("independent-shadow", dict(gate=pm.get("gate", ""), campaign=pm.get("campaign", ""), cutoff=cut, repo=pm.get("repo", ""), target=p.get("target", ""),
                                                  goal="Shadow of %s: %s" % (primary_id, _gfit(p["goal"], 90000, "primary goal")), meta=dict(independence=dict(mode="SEALED_SHADOW", cutoff=cut, forbidden_inputs=forb, compare_after_seal=True),
                                                                                                             pair=dict(of=primary_id, role="shadow"))))
        j = self.jobs.create(actor, kw["title"], kw["goal"], kw["kind"], kw["target"], kw["acceptance"], None, p.get("priority", 1), kw["meta"])
        self.pairs[primary_id] = dict(shadow=j["id"], reconcile=None)
        self._save_pairs()
        return self.jobs.get(j["id"])

    def _partner(self, job):
        pr = (job.get("meta") or {}).get("pair")
        if pr and pr.get("role") == "shadow":
            return pr["of"], job["id"]
        if job["id"] in self.pairs:
            return job["id"], self.pairs[job["id"]]["shadow"]
        return None

    def _pairs_after(self, job, result):
        pr = self._partner(job)
        if not pr:
            return
        a, b = pr
        if not (self.evidence.is_sealed(a) and self.evidence.is_sealed(b)) or (self.pairs.get(a) or {}).get("reconcile") == "done":
            return
        cmp = self.compare(a, b)
        self.pairs.setdefault(a, dict(shadow=b))["reconcile"] = "done"
        self._save_pairs()
        self._write(a, "reconcile.json", cmp)
        self.evidence.append("reconcile", a, shadow=b, match=cmp["match"], differences=len(cmp["differences"]))
        if cmp["match"] is True:
            self._post("[confirmed] %s and %s agree" % (a, b), "Independent routes agree on %d output(s)%s. This is evidence, not a promotion." % (cmp["outputs_compared"], " and the stated values" if cmp["values_compared"] else ""),
                       tags=["reconcile", "confirmed"])
        elif cmp["match"] is False:
            for jid in (a, b):
                fl = self._read(jid, "flags.json") or {}
                fl["discrepancy"] = True
                self._write(jid, "flags.json", fl)
            self._post("[discrepancy] %s vs %s" % (a, b), "\n".join(cmp["differences"][:8]), tags=["reconcile", "discrepancy"], severity=2)
            am = self.jobs.get(a).get("meta") or {}
            self.jobs.create("governor", "Reconcile %s and %s" % (a, b), "The two independent routes disagree. Classify the object definitions, check domain / norm / theorem equivalence, find a minimal counterexample, "
                             "and say which is right or why both are.\nDifferences:\n" + "\n".join(cmp["differences"][:8]), "general", "", dict(rubric="Identify the first point of divergence with evidence."), None, 2,
                             dict(campaign=am.get("campaign", ""), gate=am.get("gate", ""), role="verifier", template="reconcile", depends_on_jobs=[], pair=dict(of=a, role="reconcile")))

    def compare(self, a, b):
        sa, sb = self.evidence.read_seal(a) or {}, self.evidence.read_seal(b) or {}
        oa, ob = {o["path"]: o["sha256"] for o in sa.get("outputs", []) if not o["path"].startswith(("steps/", "workflow_inputs", "result.json"))}, \
                 {o["path"]: o["sha256"] for o in sb.get("outputs", []) if not o["path"].startswith(("steps/", "workflow_inputs", "result.json"))}
        diffs = []
        common = set(oa) & set(ob)
        for p in sorted(common):
            if oa[p] != ob[p]:
                diffs.append("output %s differs" % p)
        va, vb = (self._read(a, "agent_result.json") or {}).get("values"), (self._read(b, "agent_result.json") or {}).get("values")
        if isinstance(va, dict) and isinstance(vb, dict):
            for k in sorted(set(va) | set(vb)):
                if va.get(k) != vb.get(k):
                    diffs.append("value %s: %s vs %s" % (k, va.get(k), vb.get(k)))
        comparable = bool(common) or (isinstance(va, dict) and isinstance(vb, dict))
        return dict(primary=a, shadow=b, match=(not diffs) if comparable else None, differences=diffs, outputs_compared=len(common), values_compared=bool(isinstance(va, dict) and isinstance(vb, dict)),
                    note="" if comparable else "nothing machine-comparable: a person or a reconcile job has to compare them", t=self.now())

    # ------------------------------------------------------------ follow-ups from an open premise
    def spawn_followup(self, job, premise):
        m = job.get("meta") or {}
        depth = int(m.get("auto_depth") or 0) + 1
        mine = [j for j in self.jobs.list(limit=200) if j["owner"] == "governor"]
        if depth > MAX_AUTO_DEPTH or len([j for j in mine if j["state"] in WORKING]) >= MAX_AUTO_JOBS:
            return None
        if any(premise[:60] in (j.get("goal") or "") for j in mine):                      # the same premise is never spawned twice, whatever became of the first try
            return None
        try:
            kw = T.apply("primary-closure", dict(gate=m.get("gate", ""), campaign=m.get("campaign", ""), repo=m.get("repo", ""), target=job.get("target", ""),
                                                   goal="Close the first open premise left by %s: %s" % (job["id"], premise),
                                                   meta=dict(depends_on_jobs=[], auto_depth=depth, scope=m.get("scope") or {})))
            j = self.jobs.create("governor", ("%s: %s" % (m.get("gate") or "premise", premise))[:120], kw["goal"], kw["kind"], kw["target"], kw["acceptance"], None, job.get("priority", 1), kw["meta"])
        except BoardError:
            return None
        return j

    # ------------------------------------------------------------ jobs from a template
    def create_from_template(self, template, fields, actor="owner", with_steps=True):
        kw = T.apply(template, fields, self.data)
        j = self.jobs.create(actor, kw["title"], kw["goal"], kw["kind"], kw["target"], kw["acceptance"], None, int((fields or {}).get("priority", 1)), kw["meta"])
        if with_steps and kw["steps"]:
            j = self.jobs.add_steps(j["id"], kw["steps"], actor)
        return j

    # ------------------------------------------------------------ the workflow (playbook)
    def workflow(self, job, to=None, result=None):
        to = to or job["state"]
        m = job.get("meta") or {}
        res = job.get("result") or {}
        steps = []
        fails = []
        for s in job["steps"]:
            spec = s.get("spec") or {}
            steps.append(dict(n=s["n"], objective=s["objective"], needs=s["needs"], agent=s["agent"], state=s["state"], attempts=s["attempts"], tool=(spec.get("tool") or {}).get("tool"), hint=spec.get("hint"),
                              evidence=(s.get("evidence") or "")[:300]))
            if s.get("post_id"):
                for a in self.b.db.execute("SELECT actor, approach, evidence FROM attempts WHERE post_id=? AND outcome='failed' ORDER BY ts", (s["post_id"],)):
                    fails.append(dict(where="step %d" % s["n"], kind="step_failed", agent=a["actor"], step=s["n"], reason=(a["evidence"] or "failed")[:240]))
        for r in self.b.db.execute("SELECT type, actor, data FROM events WHERE post_id=? AND type IN ('job.rework','agency.cooldown') ORDER BY id", (job["id"],)):
            d = json.loads(r["data"] or "{}")
            if r["type"] == "job.rework":
                fails.append(dict(where="review", kind="review_rejected", agent=None, reason="; ".join(d.get("reasons") or [])[:240]))
        if res.get("check_ok") is False:
            fails.append(dict(where="check", kind="check_failed", reason=str(res.get("check_out") or "")[-240:]))
        if res.get("controls_ok") is False:
            fails.append(dict(where="controls", kind="control_failed", reason=str(res.get("controls_note") or "")[:240]))
        for c in (res.get("controls") or []):
            if not c.get("met"):
                fails.append(dict(where="control " + str(c.get("label")), kind="control_failed", reason="wanted %s, got %s. %s" % (c.get("expect"), c.get("got"), c.get("note", ""))[:240]))
        runs = self.tools.history(200, job=job["id"])
        for r in runs:
            if r.get("ok") is False:
                fails.append(dict(where="tool " + str(r.get("tool")), kind="tool_failed", reason="; ".join(r.get("problems") or ["exit %s" % r.get("rc")])[:240]))
        for stop in (self.stop_reasons(job) if to in ("ready", "blocked") else []):
            fails.append(dict(where="stop rule", kind=stop.split(":")[0], reason=stop[:240]))
        if to in ("failed", "blocked") and job.get("why"):
            fails.append(dict(where="job", kind="job_" + to, reason=job["why"][:240]))
        lessons = []
        esc = {}
        for f in fails:
            if f["kind"] == "step_failed" and f.get("agent"):
                esc.setdefault(f["step"], []).append(f["agent"])
        for n, ags in esc.items():
            lessons.append("step %d failed with %s before it worked: start it at a stronger tier" % (n, ", ".join(dict.fromkeys(ags))))
        if any(f["kind"] == "check_failed" for f in fails):
            lessons.append("the acceptance check failed at least once: read it first and satisfy it exactly")
        if any(f["kind"] == "review_rejected" for f in fails):
            lessons.append("the reviewer asked for changes: " + next(f["reason"] for f in fails if f["kind"] == "review_rejected")[:120])
        route = []
        for r in self.b.db.execute("SELECT type, data FROM events WHERE post_id=? AND type IN ('job.route','job.model') ORDER BY id", (job["id"],)):
            d = json.loads(r["data"] or "{}")
            if r["type"] == "job.route":
                route.append(dict(kind=d.get("kind"), step=d.get("step"), agent=d.get("agent"), model=d.get("model"), lobe=d.get("lobe"), why=d.get("why"), via=d.get("via")))
            elif route:
                route[-1].update(secs=d.get("secs"), outcome=d.get("outcome"), model=d.get("model") or route[-1].get("model"))
        n_runs = self.jobs.runs_for_job(job["id"])
        t_runs = [dict(tool=r.get("tool"), args=(r.get("args") or [])[:6], ok=r.get("ok"), secs=r.get("secs"), sha256=r.get("sha256")) for r in reversed(runs)]
        outcome = "ok" if to in OK_STATES or (to == "ready" and not fails) else "failed" if to in ("failed", "cancelled") or fails and to == "ready" else "blocked" if to == "blocked" else "ok"
        if to == "ready" and not fails:
            outcome = "ok"
        return dict(job=job["id"], title=job["title"], goal=job["goal"], kind=job["kind"], template=m.get("template", ""), campaign=m.get("campaign", ""), gate=m.get("gate", ""), outcome=outcome, state=to,
                    created=job["created"], finished=self.now(), secs=round(self.now() - job["created"], 1), acceptance=job.get("acceptance"), meta=m, steps=steps, route=route, tools=t_runs,
                    checks=dict(check_ok=res.get("check_ok"), controls=res.get("controls")), failure_points=fails, lessons=lessons, artifacts=(result or {}).get("artifacts", []), result=result,
                    cost=dict(agent_runs=n_runs, local_tool_runs=len(t_runs), note="every local tool run is work a subscription model did not have to do or read"))

    # ------------------------------------------------------------ an engineered tool joins the toolbox when its job is accepted
    def register_tool(self, job):
        tb = (job.get("meta") or {}).get("tool_build")
        if not tb:
            return None
        ws = self._ws(job)
        mf = os.path.join(ws.project, "manifest.json")
        try:
            man = json.load(open(mf, encoding="utf-8"))
        except (OSError, ValueError):
            return None
        if not isinstance(man.get("argv"), list):
            return None
        import shutil
        dest = os.path.join(self.tools.reg.custom_dir, tb["name"])
        shutil.rmtree(dest, ignore_errors=True)
        shutil.copytree(ws.project, dest, ignore=shutil.ignore_patterns("test_tool.py", "__pycache__"))
        man.update(id=tb["name"], built_by_job=job["id"])
        with open(os.path.join(dest, "manifest.json"), "w", encoding="utf-8", newline="\n") as f:
            json.dump(man, f, indent=1)
        self.tools.reg.scan(force=True)
        self._post("[tool] %s is now available" % tb["name"], "Built by %s and accepted. Use it with tool_run (custom-%s)." % (job["id"], tb["name"]), tags=["tool"])
        return tb["name"]
