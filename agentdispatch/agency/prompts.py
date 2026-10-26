"""what agents are asked, and how their answers are read. Edits use a PATCH protocol: the agent is shown the relevant files as text and replies with one JSON object holding complete new file
contents; the agency writes them into the throw-away workspace. No agent is ever given file or shell access to a real repo."""
import re

from ..jsonx import extract_json
from ..nightshift import is_protected
from ..textfit import fit, tail

RULES = ("You are one member of an automated engineering team. Work ONLY from what is shown below. Do not invent files you were not shown unless the step asks for a new one. "
         "Never change test files or the acceptance check itself. Reply with ONE JSON object and nothing else (no markdown fences, no commentary outside the JSON).")


def _files_block(picked):
    return "\n".join("=== FILE %s ===\n%s\n=== END %s ===" % (p, t, p) for p, t in picked) or "(no files shown)"


# the honest way out, in the SAME shape as the reply asked for (a weak model copies the example it is shown: a files-shaped "can't" under a verdict question used to be thrown away as a bad verdict)
CANT_EXAMPLES = {
    "files": '{"files": {}, "notes": "I cannot do this: <reason>"}',
    "answer": '{"answer": "", "notes": "I cannot do this: <reason>"}',
    "steps": '{"steps": [], "notes": "I cannot do this: <reason>"}',                                  # no steps: another planner is asked
    "approve": '{"approve": false, "reasons": ["I cannot do this check fully: <what you could not see or run>"]}',
}


def cant_example(shape):
    m = re.search(r'"(files|answer|steps|approve)"', shape or "")
    return CANT_EXAMPLES[m.group(1) if m else "files"]


def strict_format(shape, example, extra=""):
    """the last thing an agent reads: exactly what a valid reply looks like. Weak models follow the LAST instruction, so the contract is repeated here, with an example and the honest way out."""
    return ("\n\n=== REPLY FORMAT (strict - a reply that does not follow it is thrown away and counted as a failed attempt) ===\n"
            "Your whole reply is ONE JSON object: the first character is `{` and the last is `}`. No markdown fences, no text before or after it, no explanation, no thinking out loud.\n"
            "Inside JSON strings write a newline as \\n, a tab as \\t, a double quote as \\\", a backslash as \\\\ - a file's text must be ONE valid JSON string.\n"
            "Shape: " + shape + "\nExample of a valid reply: " + example + "\n"
            + extra +
            "If you cannot do this (you would have to run a program, read a file you were not shown, or you lack information), reply with the same shape and an empty result plus the reason in notes, "
            "for example " + cant_example(shape) + " - that hands the work to another agent and is not a failure. "
            "Make the reply COMPLETE: give everything the task needs (every changed file in full, every reason, the whole result) - do not shorten it to save space - but add nothing that was not asked for, and always close the JSON object.")


EARLIER_PER, EARLIER_TOTAL = 40000, 120000                                                       # a later step or a reviewer sees what earlier steps really produced; anything that must give way is marked, never cut silently
TREE_MAX = 600                                                                                   # file names listed by name (the rest are counted, with a marker)


def earlier_results(job, step, per=EARLIER_PER, total=EARLIER_TOTAL):
    """what the earlier steps of this job produced (a tool's real output, an agent's notes): a step that has to write up or build on them must be able to read them"""
    out, used, left = [], 0, []
    for s in job.get("steps") or []:
        if s.get("n", 0) < step.get("n", 0) and s.get("state") == "done" and s.get("evidence"):
            t = "STEP %d (%s) RESULT:\n%s" % (s["n"], str(s.get("objective", "")), fit(s["evidence"], per, "step result"))
            if total and used + len(t) > total:
                room = total - used
                if room > 2000:                                                              # what still fits is shown with a marker; the rest is named, not silently dropped
                    t = fit(t, room, "step result")
                else:
                    left.append(s["n"])
                    continue
            out.append(t)
            used += len(t)
    if left:
        out.append("[... the results of earlier step(s) %s are not shown here: they did not fit in this prompt ...]" % ", ".join(str(n) for n in left))
    return "\n\n".join(out)


def tree_text(tree, limit=TREE_MAX):
    tree = list(tree or [])
    names = ", ".join(tree[:limit])
    return names + (" [... and %d more files not listed ...]" % (len(tree) - limit) if len(tree) > limit else "")


def _tail_for(job):
    from .jobs import is_code
    meta_c = bool((job.get("meta") or {}).get("campaign"))
    if is_code(job):
        return strict_format('{"files": {"<relative path>": "<COMPLETE text of that file as one JSON string>"}, "notes": "one or two sentences"}',
                             '{"files": {"pkg/mod.py": "def f(x):\\n    return x + 1\\n"}, "notes": "added f"}',
                             "Include ONLY the files you change, each COMPLETE (never a diff, never '...' or placeholders). A file's text is always a STRING, never a nested object: even a .json file is given as its text inside a string. "
                             "Never edit tests or the acceptance script. If the tool results above are what you need, use them exactly; do not invent values.\n")
    return strict_format('{"answer": "<your complete result as one JSON string>", "notes": "one sentence"}',
                         '{"answer": "The three hearts are ...", "notes": "summarised the sources"}',
                         ("Campaign jobs: put the result object described above inside this same JSON object as a \"result\" key.\n" if meta_c else ""))


def step_prompt(job, step, tree, picked, check_argv=None, tools_text="", hint="", earlier_total=None):
    acc = job.get("acceptance") or {}
    check = acc.get("check")
    check = " ".join(check) if isinstance(check, list) else check
    from .jobs import is_code
    if is_code(job):
        ask = ('Reply with exactly: {"files": {"<relative path>": "<COMPLETE new content of that file>"}, "notes": "one or two sentences on what you changed and why"}. '
               "Include only the files you change, each with its complete content.")
    else:
        ask = 'Reply with exactly: {"answer": "<your complete result, plain text or markdown>", "notes": "one sentence on how you approached it"}.'
    meta = job.get("meta") or {}
    result_ask = ""
    if meta.get("campaign"):
        result_ask = ('Also include in the same JSON object a "result" object: {"claim_class": one of %s, "first_open_premise": "<what is still unproven, or empty>", '
                      '"requested_transition": {"<gate>": "<new status>"} (only if this job really closes it), "values": {"<name>": "<value>"} (the numbers / identifiers you derived)}. '
                      'Never claim INDEPENDENTLY_CONFIRMED.' % ", ".join(("FORMAL_EXACT", "CERTIFICATE_NUMERICAL", "ANALYTIC_PREMISE", "CODE_VERIFIED", "HEURISTIC", "DIAGNOSTIC")))
    scope = meta.get("scope") or {}
    scope_text = ""
    if scope.get("write") or scope.get("forbidden"):
        scope_text = "YOU MAY WRITE ONLY: %s. NEVER TOUCH: %s." % (", ".join(scope.get("write") or ["(anything in the project)"]), ", ".join(scope.get("forbidden") or ["(nothing listed)"]))
    return "\n".join([RULES, "", "JOB GOAL: " + job["goal"], "THIS STEP: " + step["objective"],
                      ("PREVIOUS ATTEMPT " + step["evidence"] + "\nDo not repeat that mistake; change the approach where it applies.") if step.get("attempts") and str(step.get("evidence") or "").startswith("FAILED") else "",
                      scope_text, earlier_results(job, step, total=earlier_total or EARLIER_TOTAL), ("PREFERRED LOCAL TOOL: " + hint) if hint else "", tools_text, result_ask,
                      ("ACCEPTANCE CHECK (must exit 0): " + check) if check else "", ("REVIEW RUBRIC: " + acc["rubric"]) if acc.get("rubric") else "",
                      "PROJECT FILES (%d in total): %s" % (len(tree), tree_text(tree)), _files_block(picked), "", ask + _tail_for(job)])


def plan_prompt(job, tree, agents_text=""):
    from .jobs import NEEDS
    acc = job.get("acceptance") or {}
    return "\n".join([RULES, "", "Split this job into 1 to 4 small, ordered steps that one agent can each finish in one go.",
                      "JOB KIND: " + job["kind"], "JOB GOAL: " + job["goal"], ("ACCEPTANCE: " + str(acc)) if acc else "", "PROJECT FILES: " + tree_text(tree), agents_text, "",
                      'Reply with exactly: {"steps": [{"objective": "<what to do: complete and self-contained - the files, the exact change or result, and how it is checked>", "needs": ["code"], "prefer": "<agent id, optional>", "why": "<one line, optional>"}]}. '
                      "needs is a list from: %s. Only ask for what an AVAILABLE agent above can do; a step that must run code or read the web needs an agent that really can (or a tool step). "
                      "`prefer` names the agent best suited (honoured when it is healthy). Steps run in a sandbox copy: never rely on files that exist only on the host. Prefer ONE step when the job is small." % ", ".join(NEEDS) + strict_format('{"steps": [{"objective": "<complete, self-contained instruction for that step>", "needs": ["code"], "prefer": "<agent id, optional>", "why": "<optional>"}]}',
                                                                '{"steps": [{"objective": "In pkg/mod.py add f(x) that returns x + 1 and handles None by returning 0; tests/test_mod.py must pass", "needs": ["code"], "why": "one small code change"}]}',
                                                                "Each step's agent sees ONLY its own objective plus the job goal: write every objective so it can be done without reading the other steps. Say as much as the step needs.\n")])


def review_prompt(job, diff, check_ok, check_out, earlier=""):
    acc = job.get("acceptance") or {}
    state = "PASSED" if check_ok else "FAILED" if check_ok is False else "was not run"
    return "\n".join([RULES, "", "You are the independent REVIEWER. Approve only if the change fully meets the goal and the rubric, is minimal, adds no unrelated changes, and contains no secrets, "
                      "network calls or process spawning that the goal did not ask for.", "JOB GOAL: " + job["goal"], ("REVIEW RUBRIC: " + acc["rubric"]) if acc.get("rubric") else "",
                      "ACCEPTANCE CHECK %s. Output: %s" % (state, tail(check_out or "", 12000, "check output")),
                      ("WHAT THE STEPS FOUND (tool outputs and notes; judge the change against these real results):\n" + earlier) if earlier else "", "CHANGE (unified diff):", diff or "(no changes)", "",
                      'Reply with exactly: {"approve": true or false, "reasons": ["<a concrete reason: file, line, what is wrong and what to change>", "..."]}.' +
                      strict_format('{"approve": true or false, "reasons": ["<concrete reason>"]}', '{"approve": false, "reasons": ["probe.json lacks the invocations the rubric requires"]}',
                                    "`approve` is the JSON word true or false, never a string. Give concrete reasons that name files and lines; when you reject, every reason is passed to the agent that redoes the work, so say exactly what to change. Do not approve what you could not see.\n")])


def parse_patch(text, check_argv=None):
    """-> (files dict, notes, problem) ; problem is None when the answer is a usable patch"""
    d = extract_json(text, "files")
    if not isinstance(d, dict) or not isinstance(d.get("files"), dict) or not d["files"]:
        return {}, "", "the answer had no usable {\"files\": {...}} object"
    files = {}
    chk = {"command": list(check_argv or []), "protect": []}
    for rel, content in d["files"].items():
        if not isinstance(rel, str) or not isinstance(content, str):
            return {}, "", "a file entry was not path -> text"
        if check_argv is not None and is_protected(rel, chk):
            return {}, "", "tried to change %r, which is a test file or the check itself" % rel
        files[rel] = content
    return files, str(d.get("notes", "")), None


def parse_answer(text):
    d = extract_json(text, "answer")
    if isinstance(d, dict) and isinstance(d.get("answer"), str) and d["answer"].strip():
        return d["answer"], str(d.get("notes", ""))
    return None, ""


def parse_steps(text):
    d = extract_json(text, "steps")
    if isinstance(d, dict) and isinstance(d.get("steps"), list) and d["steps"]:
        out = []
        for s in d["steps"]:
            if not isinstance(s, dict):
                continue
            needs = s.get("needs")
            needs = [needs] if isinstance(needs, str) else needs
            st = dict(objective=str(s.get("objective", "")).strip(), needs=[n for n in (needs or []) if isinstance(n, str)] or None)
            who = s.get("prefer") or s.get("agent")                                            # the planner's choice of agent, its tool call and its reason are kept (they used to be dropped)
            if isinstance(who, str) and re.match(r"^[a-z0-9][a-z0-9_.-]{0,39}$", who.strip()):
                st["prefer"] = who.strip()
            if isinstance(s.get("tool"), dict):
                st["tool"] = s["tool"]
            if isinstance(s.get("why"), str) and s["why"].strip():
                st["why"] = s["why"].strip()
            out.append(st)
        return out
    return None


INVALID_VERDICT = "the reviewer's answer was not a valid verdict"


def parse_verdict(text):
    d = extract_json(text, "approve")
    if not isinstance(d, dict):                                                         # tolerate the other ways a model says it: {"verdict": "approve"}, {"approved": true}, {"decision": "reject"}
        d = next((x for x in (extract_json(text, "verdict"), extract_json(text, "approved"), extract_json(text, "decision")) if isinstance(x, dict)), None)
    if isinstance(d, dict):
        raw = d["approve"] if "approve" in d else d["approved"] if "approved" in d else d.get("verdict", d.get("decision"))
        if isinstance(raw, str):
            low = raw.strip().lower()
            raw = True if low in ("true", "yes", "approve", "approved", "accept", "accepted", "pass") else False if low in ("false", "no", "reject", "rejected", "revise", "fail", "changes") else raw
        if isinstance(raw, bool):
            rs = d.get("reasons") if d.get("reasons") is not None else d.get("reason")
            rs = [rs] if isinstance(rs, str) else (rs or [])
            return dict(approve=raw, reasons=[str(r).strip() for r in rs if str(r).strip()])          # every reason, whole: they become the rework step's instructions (they were cut to 200 characters and 5 reasons)
    return dict(approve=False, reasons=[INVALID_VERDICT])
