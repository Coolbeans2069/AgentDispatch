"""job templates: the common shapes of work, so a job is one pick and a few words instead of a long form. Built in below; the owner's own go in <data>/templates/*.json (same shape) and win by id.
A template fills the form: kind, a goal skeleton, acceptance, planned steps (a step may be a deterministic TOOL step that costs no model tokens) and the governance fields."""
import glob, json, os

from . import meta as M

def S(objective, needs=None, tool=None):
    """one planned step. With a CONCRETE tool call (args, code or file) it is a deterministic step the computer runs itself (no model tokens); with only a tool name it is a hint: the model that
    takes the step is told which local tool to use and runs it through tool_calls"""
    if tool and (tool.get("args") or tool.get("code") or tool.get("file")):
        return dict(objective=objective, needs=["tool"], tool=dict(tool))
    needs = [n for n in (needs or ["research"]) if n != "tool"] or ["code"]
    return dict(objective=objective, needs=needs, **({"hint": tool["tool"]} if tool else {}))



BUILTIN = [
    dict(id="primary-closure", title="Primary derivation / closure", summary="the authoritative derivation, code and certificate for one stage", kind="general", role="primary", reasoning="high",
         goal="Close {gate}: derive the result exactly, implement it, and produce the certificate with its evidence. State the first open premise if it does not close.",
         rubric="Every claim is derived or cited; exact arithmetic where the statement is exact; controls included; nothing outside scope changed; the conclusion does not claim more than the evidence shows.",
         steps=[S("Derive the statement exactly and list every premise it needs.", ["research", "plan"]), S("Implement the derivation and the certificate computation.", ["code"]),
                S("Run the exact / numeric checks with the local tools and record the outputs.", ["tool"], dict(tool="calc"))],
         meta=dict(role="primary", human_approval="before_promotion", stop_on=["check_failed", "control_failed", "hash_mismatch", "scope_breach"])),
    dict(id="independent-shadow", title="Independent shadow", summary="a cutoff-controlled reproduction that must not see newer work", kind="general", role="adversarial", reasoning="high",
         goal="Reproduce {gate} independently from the evidence available at the cutoff commit only. Seal your result BEFORE comparing with anything newer.",
         rubric="Derived without reading anything newer than the cutoff; differences from the primary are listed, not smoothed over.",
         steps=[S("Derive and compute the result from the cutoff evidence only.", ["research", "plan"]), S("Seal the packet: result, assumptions, and every hash.", ["write"])],
         meta=dict(role="adversarial", human_approval="none", independence=dict(mode="SEALED_SHADOW", cutoff="", compare_after_seal=True), stop_on=["independence_breach", "scope_breach", "hash_mismatch"],
                   claim_class="HEURISTIC")),
    dict(id="lean-shadow", title="Lean shadow (exact finite statements)", summary="check the finite exact structure in Lean", kind="general", role="verifier", reasoning="formal",
         goal="State and prove in Lean the finite exact facts behind {gate} (identities, finite index completeness, inequalities, monotonicity). A file counts only if it compiles with no error and no sorry.",
         rubric="Statements match the written claim; no sorry; adverse examples are included and fail as intended.",
         steps=[S("Write the Lean statements and proofs for the finite exact facts.", ["code"]), S("Compile with Lean.", ["tool"], dict(tool="lean"))],
         meta=dict(role="verifier", claim_class="FORMAL_EXACT", stop_on=["check_failed", "control_failed"])),
    dict(id="numerical-certificate", title="Numerical certificate", summary="an outward-rounded rational / interval result", kind="general", role="primary", reasoning="medium",
         goal="Compute a certified enclosure for {gate} with exact rational or interval arithmetic and report the bound with its rounding direction.",
         rubric="Arithmetic is exact or outward-rounded; the bound is reproduced by a second implementation; inputs and outputs are hashed.",
         steps=[S("Implement the enclosure with exact rationals / intervals.", ["code"]), S("Recompute it with a second method and compare.", ["tool"], dict(tool="calc"))],
         meta=dict(role="primary", claim_class="CERTIFICATE_NUMERICAL", stop_on=["check_failed", "control_failed", "hash_mismatch"])),
    dict(id="adversarial-review", title="Adversarial review", summary="attack a closed candidate", kind="general", role="reviewer", reasoning="high",
         goal="Try to break the claimed closure of {gate}: look for wrong assumptions, placeholder constants, invalid inequalities, overlooked cases. Report every defect with its evidence.",
         rubric="Each finding has a concrete counterexample or a cited line; nothing is vague.", steps=[S("Review the claim and the evidence and list defects.", ["review", "research"])],
         meta=dict(role="reviewer", human_approval="none", stop_on=["scope_breach"])),
    dict(id="governance-decision", title="Governance decision", summary="a change to theory, family or norms: always the owner's", kind="general", role="governor", reasoning="high",
         goal="Prepare the decision on {gate}: the options, what each would change, the evidence, and a recommendation. Do not change anything.",
         rubric="Options are complete and neutral; consequences are stated; the decision is left to the owner.", steps=[S("Write the decision memo with options and consequences.", ["research", "write"])],
         meta=dict(role="governor", human_approval="before_promotion", stop_on=["unauthorized_gate"])),
    dict(id="prospective-freeze", title="Prospective freeze", summary="lock every input before a result-bearing run", kind="general", role="provenance", reasoning="medium",
         goal="Freeze the configuration for {gate}: hash the theory, family, parameter domains, norms, algorithms, constants, tolerances, environment and commit into one manifest.",
         rubric="Every input is listed with its hash; nothing is tunable after this point.", steps=[S("Collect and hash every frozen input.", ["tool"], dict(tool="python")), S("Write the freeze manifest.", ["write"])],
         meta=dict(role="provenance", human_approval="before_promotion", may_create_freeze=True, stop_on=["tree_dirty", "hash_mismatch", "frozen_input_changed"])),
    dict(id="result-bearing-compute", title="Result-bearing compute", summary="a restricted production run on a frozen configuration", kind="production", role="primary", reasoning="routine",
         goal="Run {gate} exactly as frozen. No tuning, no changes after seeing the result.",
         rubric="The run matches the freeze hashes; the output is complete; failures are preserved.", steps=[S("Run the frozen computation.", ["tool"], dict(tool="python"))],
         meta=dict(role="primary", result_bearing=True, human_approval="before_run", stop_on=["tree_dirty", "hash_mismatch", "frozen_input_changed", "check_failed"])),
    dict(id="evidence-seal", title="Evidence seal", summary="hashes, manifest and environment of finished work", kind="general", role="provenance", reasoning="routine",
         goal="Seal the evidence for {gate}: hash inputs and outputs, record the environment and the command lines.", rubric="The manifest verifies.",
         steps=[S("Hash the outputs and record the environment.", ["tool"], dict(tool="python"))], meta=dict(role="provenance", human_approval="none")),
    dict(id="git-relay-sync", title="Git relay sync", summary="a guarded fast-forward of the relay into the main repository", kind="general", role="provenance", reasoning="routine",
         goal="Verify the relay commit and prepare the guarded fast-forward for {gate}: confirm ancestry and a clean tree, then give the owner the exact commands. Never run them.",
         rubric="Ancestry is verified read-only; the owner runs the merge.", steps=[S("Check ancestry and the clean tree with read-only git.", ["tool"], dict(tool="git", args=["status", "--short", "--branch"]))],
         meta=dict(role="provenance", human_approval="before_promotion", stop_on=["tree_dirty", "unauthorized_gate"])),
    dict(id="erratum", title="Erratum (append-only correction)", summary="correct a sealed result without rewriting it", kind="general", role="provenance", reasoning="routine",
         goal="Write the erratum for {gate}: what was wrong, the corrected value, and the new version of the artifact. The original stays exactly as it was.",
         rubric="The original is untouched; the correction is explicit.", steps=[S("Write the erratum text.", ["write"])], meta=dict(role="provenance", human_approval="before_promotion")),
    dict(id="alternative-search", title="Alternative search before brute force", summary="try cheaper routes before subdividing or big compute", kind="general", role="primary", reasoning="high",
         goal="For {gate}, before any subdivision or large computation: try exact algebra, a finite Lean proof, an analytic sharpening, a small interval calculation, a local web fetch if a public reference exists, then an independent route. Report which cheaper route worked or why none did.",
         rubric="Each cheaper route is tried in order and its outcome is recorded.", steps=[S("Exact algebra / a finite Lean proof.", ["research"]), S("Analytic sharpening and a small interval calculation.", ["tool"], dict(tool="calc")),
                                                                                         S("If a public reference URL is known, fetch it locally.", ["tool"], dict(tool="fetch_url")),
                                                                                         S("Independent route, then say whether brute force is still needed.", ["research", "plan"])],
         meta=dict(role="primary", alternative_search_first=True, free_zone=True, prefer_cost="free")),
    dict(id="build-a-tool", title="Engineer a missing tool", summary="an agent builds a small local tool that does not exist yet", kind="code", role="primary", reasoning="medium",
         goal="Build the tool described below as a small command-line program that reads arguments and prints its answer. Fill in manifest.json, write tool.py, and make test_tool.py pass.",
         rubric="Small, no network, no writes outside its own folder, deterministic; the examples pass.", steps=[], meta=dict(role="primary", human_approval="before_promotion", stop_on=["check_failed"])),
    # ---- tool-first (local / free-zone): computer does the fetch/extract/hash; models only summarise or judge ----
    dict(id="web-research-local", title="Web research (local fetch)", summary="fetch public pages on this computer, then a short local/free summary", kind="general", role="primary", reasoning="medium",
         goal="Research {gate} using this computer's browse tools first. Fetch the key public URLs, extract visible text, then summarise. Prefer free/local models for the write-up; do not invent citations.",
         rubric="Every claim cites a fetched URL or extracted passage; no localhost/private URLs; summary is shorter than the sources.",
         steps=[S("Fetch the primary public URL(s) with fetch_url (or browser_fetch if the page is JS-heavy).", ["tool"], dict(tool="fetch_url")),
                S("If a second source is needed, fetch it the same way and note disagreements.", ["tool"], dict(tool="fetch_url")),
                S("Write a short brief from the fetched text only; list open questions.", ["write", "research"])],
         meta=dict(role="primary", free_zone=True, prefer_cost="free", prefer_agent="openrouter", stop_on=["scope_breach"])),
    dict(id="doc-verify", title="Document verify (PDF/docx + hash)", summary="extract text from a document on disk and seal a hash — no cloud read required", kind="general", role="verifier", reasoning="routine",
         goal="Verify the document for {gate}: extract its text locally, compute SHA-256, and report page/structure notes. Cloud models only if interpretation is required after extraction.",
         rubric="Hash is recorded; extracted text matches the file; nothing outside the allowed folder is read.",
         steps=[S("Extract text with pdf_text or docx_text on the file in the project/work folder.", ["tool"], dict(tool="pdf_text")),
                S("Hash the same file with hash_file (sha256).", ["tool"], dict(tool="hash_file")),
                S("Summarise what the document claims and list anything that still needs a human eye.", ["review", "write"])],
         meta=dict(role="verifier", free_zone=True, prefer_cost="free", stop_on=["check_failed", "hash_mismatch"])),
    dict(id="sheet-probe", title="Spreadsheet / CSV probe", summary="local tabular stats before any model reasons about the numbers", kind="general", role="primary", reasoning="routine",
         goal="Probe the table for {gate} on this computer (shape, dtypes, nulls, describe, sample rows), then state what the numbers show without inventing cells.",
         rubric="Stats come from csv_stats or xlsx_probe; any claim quotes a column or sample row.",
         steps=[S("Run csv_stats (or xlsx_probe for Excel) on the file in an allowed folder.", ["tool"], dict(tool="csv_stats")),
                S("Interpret the probe output: trends, holes, outliers; no new numbers.", ["research", "write"])],
         meta=dict(role="primary", free_zone=True, prefer_cost="free", stop_on=["check_failed"])),
    dict(id="evidence-hash-seal", title="Evidence hash seal", summary="SHA-256 every listed artifact with the local hash_file tool", kind="general", role="provenance", reasoning="routine",
         goal="Seal evidence for {gate}: hash each listed input/output file and write a short manifest of digests. No model arithmetic.",
         rubric="Every path has a digest; algo is sha256; files stay inside allowed folders.",
         steps=[S("Hash each artifact with hash_file.", ["tool"], dict(tool="hash_file")),
                S("Write the digest manifest (paths + sha256) into the work folder notes.", ["write"])],
         meta=dict(role="provenance", free_zone=True, prefer_cost="free", stop_on=["hash_mismatch"])),
    dict(id="unit-check", title="Unit / numeric check", summary="exact or unit conversion on this computer before a model debates the number", kind="general", role="verifier", reasoning="routine",
         goal="Check the numeric/unit claim for {gate} with calc and/or unit_convert on this computer. Report the exact printed result.",
         rubric="The number comes from tool stdout; rounding direction stated when relevant.",
         steps=[S("Run unit_convert or calc for the stated quantity.", ["tool"], dict(tool="unit_convert")),
                S("If exact algebra is needed, recompute with calc (sympy/mpmath).", ["tool"], dict(tool="calc")),
                S("State pass/fail against the claim in one short paragraph.", ["review"])],
         meta=dict(role="verifier", free_zone=True, prefer_cost="free", stop_on=["check_failed", "control_failed"])),
    dict(id="media-ingest-local", title="Media ingest (local)", summary="probe and optionally transcribe media on this computer", kind="general", role="primary", reasoning="routine",
         goal="Ingest media for {gate}: probe streams (ffprobe/audio_info/image_info), optionally transcribe speech locally, then a short free/local summary.",
         rubric="Probe fields are from tools; transcript is local; summary does not invent unheard content.",
         steps=[S("Probe the file (ffprobe or audio_info / image_info).", ["tool"], dict(tool="ffprobe")),
                S("If speech is needed, run transcribe on the same file.", ["tool"], dict(tool="transcribe")),
                S("Write a short ingest note from probe (+ transcript if any).", ["write"])],
         meta=dict(role="primary", free_zone=True, prefer_cost="free", stop_on=["check_failed"])),
    dict(id="free-zone-repair", title="Free-zone repair pass", summary="retry failed work on OpenRouter free / local — not Claude", kind="general", role="primary", reasoning="high",
         goal="Repair {gate} after a failure: stay in the free zone (OpenRouter Nemotron/Gemma/Inkling or local coder). Do not spend Claude session budget on this pass unless free and local are unavailable.",
         rubric="Failure cause addressed; free/local agent used when available; no repeat of the failed approach.",
         steps=[S("Re-read the failure and the playbook failures for this kind of work.", ["research"]),
                S("Apply the fix (code or rewrite) with a free/local agent.", ["code", "write"]),
                S("Run the local checks (pytest/calc/lean as fits).", ["tool"], dict(tool="pytest"))],
         meta=dict(role="primary", free_zone=True, prefer_agent="openrouter", prefer_cost="free", stop_on=["check_failed"])),
]


def all_templates(data_dir=None):
    out = {t["id"]: t for t in BUILTIN}
    if data_dir:
        for p in glob.glob(os.path.join(data_dir, "templates", "*.json")):
            try:
                t = json.load(open(p, encoding="utf-8"))
                if isinstance(t, dict) and t.get("id") and isinstance(t.get("goal"), str):
                    out[t["id"]] = t
            except (OSError, ValueError):
                continue
    return out


def apply(template_id, fields=None, data_dir=None):
    """-> kwargs for job creation (title, goal, kind, target, acceptance, steps, meta) from a template and the user's few words (title, gate, campaign, target, check, extra goal text)"""
    t = all_templates(data_dir).get(template_id)
    if not t:
        raise KeyError("no such template: %s" % template_id)
    f = dict(fields or {})
    gate = f.get("gate") or f.get("stage") or "the stage"
    goal = t["goal"].replace("{gate}", gate)
    if f.get("goal"):
        goal = f["goal"].strip() + "\n\n" + goal                                          # whole: the agents are told this (it was cut at 4000 characters; jobs.create holds the real cap)
    meta = dict(t.get("meta") or {}, template=t["id"])
    for k in ("campaign", "gate", "repo", "preferred_agent"):
        if f.get(k):
            meta[k] = f[k]
    meta.setdefault("gate", f.get("gate", ""))
    if f.get("cutoff"):
        ind = dict(meta.get("independence") or {})
        ind["cutoff"] = f["cutoff"]
        ind.setdefault("mode", "SEALED_SHADOW")
        meta["independence"] = ind
    if f.get("meta"):
        meta.update(f["meta"])
    acc = {}
    if f.get("check"):
        acc["check"] = f["check"]
    if t.get("rubric"):
        acc["rubric"] = t["rubric"]
    return dict(title=(f.get("title") or "%s: %s" % (t["title"], gate))[:120], goal=goal, kind=t.get("kind", "general"), target=f.get("target", ""), acceptance=acc, steps=[dict(s) for s in t.get("steps", [])],
                meta=M.normalize(meta))
