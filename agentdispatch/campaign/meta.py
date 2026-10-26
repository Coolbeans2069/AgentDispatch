"""the scientific / governance fields of a job (kept next to the simple ones): which campaign and gate it belongs to, what it depends on and unlocks, what it may read and write, how independent it
must be, what it must never change, which controls decide it, and when it must stop. Defaults are the cautious ones: no authority, not result-bearing, a human approves promotion."""
import re

from ..store import BoardError

ROLES = ("primary", "adversarial", "verifier", "reviewer", "provenance", "governor")
REASONING = ("routine", "medium", "high", "formal")
INDEPENDENCE = ("NONE", "POST_EXPOSURE_REVIEW", "SEALED_SHADOW", "BLIND_INDEPENDENT")
APPROVAL = ("none", "before_run", "before_promotion", "before_result")
CLAIMS = ("FORMAL_EXACT", "CERTIFICATE_NUMERICAL", "ANALYTIC_PREMISE", "CODE_VERIFIED", "HEURISTIC", "DIAGNOSTIC", "SOURCE_BACKED", "REVIEW_DERIVED", "INDEPENDENTLY_CONFIRMED", "REFUTED", "SUPERSEDED",
          "NO_PROOF_CREDIT")
SELF_CLAIMS_FORBIDDEN = {"INDEPENDENTLY_CONFIRMED"}                     # an agent can never assign this to its own work: it needs a second, sealed, independent job
STOP_RULES = ("check_failed", "control_failed", "discrepancy", "hash_mismatch", "tree_dirty", "unauthorized_gate", "frozen_input_changed", "independence_breach", "scope_breach")
AUTHORITY_FLAGS = ("may_change_theory", "may_change_source_family", "may_change_norms", "may_consume_recovery_branch", "may_create_freeze", "may_run_propagation", "may_access_empirical")
LISTS = ("depends_on_jobs", "unlocks", "parallel_with", "exclusive_with", "expected_outputs", "stop_on", "wait_for_artifacts")
GLOBS = ("read", "write", "forbidden")
EXAMPLE = dict(campaign="", gate="", role="primary", template="", reasoning="medium", repo="", independence=dict(mode="NONE", cutoff="", forbidden_inputs=[], compare_after_seal=False),
               scope=dict(read=[], write=[], forbidden=[]), depends_on_jobs=[], depends_on_gates={}, unlocks=[], parallel_with=[], exclusive_with=[], expected_outputs=[], wait_for_artifacts=[],
               not_before=0, result_bearing=False, human_approval="before_promotion", stop_on=["check_failed", "hash_mismatch"], controls=[], seal=True, alternative_search_first=False,
               claim_class="", preferred_agent="", prefer_agent="", prefer_cost="", free_zone=False,
               prefer_restore="", prefer_restore_after=0, limit_woken=False, limit_woken_from="", limit_woken_to="",
               tool_build=None, pair=None, auto_depth=0, min_tier="", **{f: False for f in AUTHORITY_FLAGS})


def _strs(v, n=40, w=240):
    if v in (None, ""):
        return []
    if isinstance(v, str):
        v = [x.strip() for x in re.split(r"[\n,]", v) if x.strip()]
    if not isinstance(v, list) or not all(isinstance(x, str) for x in v):
        raise BoardError("bad_meta", "expected a list of text values")
    return [x.strip()[:w] for x in v if x.strip()][:n]


def normalize(m):
    """-> a complete, validated meta dict (unknown keys are refused, not silently dropped)"""
    m = dict(m or {})
    bad = [k for k in m if k not in EXAMPLE]
    if bad:
        raise BoardError("bad_meta", "unknown job field(s): %s" % ", ".join(sorted(bad)))
    out = {k: (v.copy() if isinstance(v, (dict, list)) else v) for k, v in EXAMPLE.items()}
    for k in ("campaign", "gate", "template", "repo", "preferred_agent", "prefer_agent", "prefer_cost"):
        if m.get(k) is not None:
            if not isinstance(m[k], str) or len(m[k]) > 300:
                raise BoardError("bad_meta", "%s must be short text" % k)
            out[k] = m[k].strip()
    # prefer_agent is the dispatcher key; preferred_agent is the legacy alias — keep them aligned.
    if out.get("prefer_agent") and not out.get("preferred_agent"):
        out["preferred_agent"] = out["prefer_agent"]
    elif out.get("preferred_agent") and not out.get("prefer_agent"):
        out["prefer_agent"] = out["preferred_agent"]
    if m.get("free_zone") is not None:
        if not isinstance(m["free_zone"], bool):
            raise BoardError("bad_meta", "free_zone must be true or false")
        out["free_zone"] = m["free_zone"]
    if m.get("limit_woken") is not None:
        if not isinstance(m["limit_woken"], bool):
            raise BoardError("bad_meta", "limit_woken must be true or false")
        out["limit_woken"] = m["limit_woken"]
    if m.get("prefer_restore") is not None:
        if not isinstance(m["prefer_restore"], str) or len(m["prefer_restore"]) > 80:
            raise BoardError("bad_meta", "prefer_restore must be short text")
        out["prefer_restore"] = m["prefer_restore"].strip()
    if m.get("prefer_restore_after") not in (None, ""):
        if not isinstance(m["prefer_restore_after"], (int, float)) or isinstance(m["prefer_restore_after"], bool):
            raise BoardError("bad_meta", "prefer_restore_after must be a time (seconds since 1970)")
        out["prefer_restore_after"] = float(m["prefer_restore_after"])
    for k in ("limit_woken_from", "limit_woken_to"):
        if m.get(k) is not None:
            if not isinstance(m[k], str) or len(m[k]) > 40:
                raise BoardError("bad_meta", "%s must be a short job id" % k)
            out[k] = m[k].strip()
    if out.get("prefer_cost") and out["prefer_cost"] not in ("", "free", "local", "cheap", "plan", "premium"):
        raise BoardError("bad_meta", "prefer_cost must be free, local, cheap, plan or premium")
    for k, allowed in (("role", ROLES), ("reasoning", REASONING), ("human_approval", APPROVAL)):
        if m.get(k) is not None:
            if m[k] not in allowed:
                raise BoardError("bad_meta", "%s must be one of %s" % (k, ", ".join(allowed)))
            out[k] = m[k]
    if m.get("claim_class"):
        if m["claim_class"] not in CLAIMS:
            raise BoardError("bad_meta", "claim_class must be one of %s" % ", ".join(CLAIMS))
        out["claim_class"] = m["claim_class"]
    for k in LISTS:
        if m.get(k) is not None:
            out[k] = _strs(m[k])
    bad = [s for s in out["stop_on"] if s not in STOP_RULES]
    if bad:
        raise BoardError("bad_meta", "unknown stop rule(s) %s (known: %s)" % (bad, ", ".join(STOP_RULES)))
    for k in ("result_bearing", "seal", "alternative_search_first") + AUTHORITY_FLAGS:
        if m.get(k) is not None:
            if not isinstance(m[k], bool):
                raise BoardError("bad_meta", "%s must be true or false" % k)
            out[k] = m[k]
    ind = m.get("independence")
    if ind is not None:
        if not isinstance(ind, dict) or ind.get("mode", "NONE") not in INDEPENDENCE:
            raise BoardError("bad_meta", "independence.mode must be one of %s" % ", ".join(INDEPENDENCE))
        out["independence"] = dict(mode=ind.get("mode", "NONE"), cutoff=str(ind.get("cutoff", ""))[:80], forbidden_inputs=_strs(ind.get("forbidden_inputs")), compare_after_seal=bool(ind.get("compare_after_seal")))
        if out["independence"]["mode"] in ("SEALED_SHADOW", "BLIND_INDEPENDENT") and not out["independence"]["cutoff"]:
            raise BoardError("bad_meta", "a sealed shadow or blind job needs an evidence cutoff (a commit or timestamp)")
    sc = m.get("scope")
    if sc is not None:
        if not isinstance(sc, dict):
            raise BoardError("bad_meta", "scope must be {read, write, forbidden}")
        out["scope"] = {g: _strs(sc.get(g)) for g in GLOBS}
    dg = m.get("depends_on_gates")
    if dg is not None:
        if isinstance(dg, list):
            dg = {str(g): "CLOSED" for g in dg}
        if not isinstance(dg, dict) or not all(isinstance(k, str) and isinstance(v, str) for k, v in dg.items()):
            raise BoardError("bad_meta", "depends_on_gates must be {gate: required status prefix} or a list of gates (meaning CLOSED)")
        out["depends_on_gates"] = {k.strip()[:80]: (v.strip()[:80] or "CLOSED") for k, v in dg.items() if k.strip()}
    nb = m.get("not_before")
    if nb not in (None, ""):
        if not isinstance(nb, (int, float)) or isinstance(nb, bool):
            raise BoardError("bad_meta", "not_before must be a time (seconds since 1970)")
        out["not_before"] = float(nb)
    ctl = m.get("controls")
    if ctl is not None:
        if not isinstance(ctl, list) or len(ctl) > 30 or not all(isinstance(c, dict) and isinstance(c.get("tool"), str) for c in ctl):
            raise BoardError("bad_meta", "controls must be a list of {label, tool, args | code | file, expect: pass | fail}")
        out["controls"] = [{k: c[k] for k in ("label", "tool", "args", "code", "file", "expect", "timeout", "project") if k in c} for c in ctl]
    if m.get("min_tier"):
        if m["min_tier"] not in ("ollama", "mid", "strong"):
            raise BoardError("bad_meta", "min_tier must be ollama, mid or strong")
        out["min_tier"] = m["min_tier"]
    if m.get("auto_depth") is not None:
        if not isinstance(m["auto_depth"], int) or isinstance(m["auto_depth"], bool) or not 0 <= m["auto_depth"] <= 20:
            raise BoardError("bad_meta", "auto_depth must be a small whole number")
        out["auto_depth"] = m["auto_depth"]
    pr = m.get("pair")
    if pr:
        if not isinstance(pr, dict) or pr.get("role") not in ("primary", "shadow", "reconcile") or not isinstance(pr.get("of", ""), str):
            raise BoardError("bad_meta", "pair must be {of: job id, role: primary | shadow | reconcile}")
        out["pair"] = dict(of=pr.get("of", "")[:20], role=pr["role"], with_=pr.get("with_", [])[:4] if isinstance(pr.get("with_"), list) else [])
    tb = m.get("tool_build")
    if tb:
        if not isinstance(tb, dict) or not re.match(r"^[a-z0-9_-]{2,40}$", str(tb.get("name", ""))):
            raise BoardError("bad_meta", "tool_build needs a name of 2-40 letters, digits, - or _")
        out["tool_build"] = dict(name=tb["name"])
    if out["result_bearing"] and out["human_approval"] == "none":
        raise BoardError("bad_meta", "a result-bearing job needs a human approval (before_run, before_promotion or before_result)")
    return out


def is_default(meta):
    return not meta or all(meta.get(k) in (None, "", [], {}, False, 0) or meta.get(k) == EXAMPLE[k] for k in EXAMPLE)
