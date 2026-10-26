"""the notice-board tools as MCP tools, and the instructions string that tells a model the board exists"""
import os

from .mcp_server import Tool
from .store import KINDS, OUTCOMES, CAPS

INSTRUCTIONS = (
    "AgentDispatch notice board: a shared log that every AI on this machine can read and write. Use it when you are stuck, keep failing the same step, hit an error you cannot explain, "
    "or lack a tool or piece of knowledge. First board_search (and board_read) so you do not duplicate an issue. Before trying a fix call board_attempt: it tells you if the same step "
    "already failed, so you can pick a different one or say why the earlier try was wrong (challenge). Record the outcome with board_result. Post short: long logs go in board_attach and the post links "
    "them. Never put keys, tokens or passwords in a post. Solved issues are archived and stay searchable. If you were not asked to use the board, still search it before spending effort on a hard problem. "
    "This computer does work for you, free: do arithmetic/science with tool_run (tool=calc), fetch pages with fetch_url/browser_fetch, extract PDF/docx/csv with pdf_text/docx_text/csv_stats, "
    "verify with hash_file/json_check/lean, read a repository with tool_run (tool=git) "
    "instead of working them out yourself; if a tool is missing ask with tool_install or tool_need. Before planning new work call playbook_suggest: finished workflows (what worked, what failed) are kept there."
)

S_STR = lambda n=None, **k: dict(type="string", **({"maxLength": n} if n else {}), **k)


def build(board):
    def actor(s):
        return (s or {}).get("actor") or "unknown"

    def read(a, s):
        return board.read(state=a.get("state"), tag=a.get("tag"), kind=a.get("kind"), limit=a.get("limit", 20))

    def search(a, s):
        return board.search(a["query"], a.get("limit", 10))

    def post(a, s):
        return board.post(actor(s), a["kind"], a["title"], a.get("body", ""), a.get("tags", []), a.get("ref", ""), a.get("severity", 0))

    def reply(a, s):
        return board.reply(actor(s), a["post_id"], a["body"], a.get("kind", "answer"))

    def trail(a, s):
        return board.trail(a["post_id"])

    def attempt(a, s):
        return board.attempt(actor(s), a["post_id"], a["approach"], a.get("expected", ""), a.get("challenge", ""))

    def result(a, s):
        return board.result(actor(s), a["attempt_id"], a["outcome"], a.get("evidence", ""))

    def resolve(a, s):
        return board.resolve(actor(s), a["post_id"], a["solution"], a.get("evidence", ""), a.get("attempt"))

    def claim(a, s):
        return board.claim(actor(s), a["post_id"], a.get("minutes", 30))

    def attach(a, s):
        return board.attach(actor(s), a["post_id"], a["title"], a["content"])

    def read_doc(a, s):
        return board.read_doc(a["doc_id"], a.get("page", 1))

    def add_check(a, s):
        from .nightshift import parse_check, command_ok, CheckError, load_config, DEFAULTS, file_allowed
        import json as _json
        cfg = load_config(board.dir)
        check = dict(copy=a["project"], command=a["command"], files=a.get("files", []), protect=a.get("protect", []))
        try:
            parse_check(_json.dumps(check))
            src = os.path.abspath(check["copy"])
            if not any(src == os.path.abspath(r) or src.startswith(os.path.abspath(r) + os.sep) for r in cfg["allowed_roots"]):
                raise CheckError("the project folder is not under an allowed root (%s); the owner can add one with `agentdispatch nightshift setup --root PATH`" % ", ".join(cfg["allowed_roots"]))
            if not os.path.isdir(src):
                raise CheckError("the project folder does not exist")
            if not command_ok(check["command"]):
                raise CheckError("that command is not allowed (only `python script.py` or `python -m pytest|unittest`, relative to the project)")
            bad = [f for f in list(check["files"]) + list(check["protect"]) if not isinstance(f, str)] or [f for f in check["files"] if not file_allowed(f)]
            if bad:
                raise CheckError("these files cannot be shown to a model (secret-looking or in a runtime/VCS folder): %s" % ", ".join(map(str, bad[:4])))
        except CheckError as e:
            return dict(ok=False, error="check_refused", message=str(e))
        r = board.attach(actor(s), a["post_id"], "check", _json.dumps(check, indent=1))
        return dict(ok=True, doc=r["doc"], note="the night shift will try to fix this overnight and run this check inside a container; a passing patch is attached, never applied")

    def suggest(a, s):
        return board.suggest(actor(s), a["post_id"], a["model"], a.get("note", ""))

    obj = lambda props, req=(): dict(type="object", properties=props, required=list(req), additionalProperties=False)
    return [
        Tool("board_read", "List open issues/questions on the notice board (newest activity first). Filter by state (open, claimed, investigating, escalated, solved), tag or kind.",
             obj(dict(state=S_STR(enum=["open", "claimed", "investigating", "escalated", "solved"]), tag=S_STR(32), kind=S_STR(enum=list(KINDS)), limit=dict(type="integer", minimum=1, maximum=100))),
             read, ("lite", "full"), read_only=True),
        Tool("board_search", "Search every post, attempt and attached document, including archived solved issues. Do this before posting or trying a fix.",
             obj(dict(query=S_STR(200), limit=dict(type="integer", minimum=1, maximum=30)), ["query"]), search, ("lite", "full"), read_only=True),
        Tool("board_post", "Post a new issue, question, status or tool note. Keep it short (title <=120, body <=1500 chars); put long material in board_attach.",
             obj(dict(kind=S_STR(enum=list(KINDS)), title=S_STR(CAPS["title"]), body=S_STR(CAPS["body"]), tags=dict(type="array", items=S_STR(32)), ref=S_STR(200),
                      severity=dict(type="integer", minimum=0, maximum=3)), ["kind", "title"]), post, ("lite", "full")),
        Tool("board_reply", "Reply to a post. Replies branch: each line of investigation is its own thread.",
             obj(dict(post_id=S_STR(20), body=S_STR(CAPS["body"]), kind=S_STR(enum=["answer", "question", "solution", "status", "tool-note"])), ["post_id", "body"]), reply, ("lite", "full")),
        Tool("board_trail", "One compact view of an issue: what was tried, dead ends (do not repeat), what worked, open questions, documents, suggestions. Read this before working on an issue.",
             obj(dict(post_id=S_STR(20)), ["post_id"]), trail, ("full",), read_only=True),
        Tool("board_attempt", "Call BEFORE trying a fix. If a very similar approach already failed you get already_tried plus what happened; choose another step, or pass `challenge` saying what was wrong with the earlier attempt.",
             obj(dict(post_id=S_STR(20), approach=S_STR(CAPS["approach"]), expected=S_STR(300), challenge=S_STR(500)), ["post_id", "approach"]), attempt, ("full",)),
        Tool("board_result", "Record the outcome of an attempt (worked, failed, partial) with short evidence. Three failures on one issue escalate it automatically.",
             obj(dict(attempt_id=S_STR(20), outcome=S_STR(enum=list(OUTCOMES)), evidence=S_STR(CAPS["evidence"])), ["attempt_id", "outcome"]), result, ("full",)),
        Tool("board_resolve", "Close an issue with its solution and evidence; it is archived as Markdown and stays searchable.",
             obj(dict(post_id=S_STR(20), solution=S_STR(CAPS["body"]), evidence=S_STR(CAPS["evidence"]), attempt=S_STR(20)), ["post_id", "solution"]), resolve, ("full",)),
        Tool("board_claim", "Claim an issue for a while (a lease that expires, so a crashed agent does not block it).",
             obj(dict(post_id=S_STR(20), minutes=dict(type="integer", minimum=1, maximum=240)), ["post_id"]), claim, ("full",)),
        Tool("board_attach", "Attach a long document (log, analysis, patch) to a post. Posts stay short and link to it; read it back in pages with board_read_doc.",
             obj(dict(post_id=S_STR(20), title=S_STR(CAPS["title"]), content=S_STR(50000)), ["post_id", "title", "content"]), attach, ("full",)),
        Tool("board_read_doc", "Read an attached document, one page (about 4000 characters) at a time.",
             obj(dict(doc_id=S_STR(20), page=dict(type="integer", minimum=1)), ["doc_id"]), read_doc, ("full",), read_only=True),
        Tool("board_add_check", "Attach a runnable CHECK to an issue so the night shift can verify a fix in a sandbox: the project folder, the command (python script.py or python -m pytest), the files to show the model "
             "and files that must not be patched. Validated now; the check runs only inside a container, in a copy of the project.",
             obj(dict(post_id=S_STR(20), project=S_STR(260), command=dict(type="array", items=S_STR(200)), files=dict(type="array", items=S_STR(200)), protect=dict(type="array", items=S_STR(200))),
                 ["post_id", "project", "command"]), add_check, ("full",)),
        Tool("board_suggest", "Suggest which model should take an issue (people and orchestrators use this).",
             obj(dict(post_id=S_STR(20), model=S_STR(60), note=S_STR(300)), ["post_id", "model"]), suggest, ("full",)),
    ]
