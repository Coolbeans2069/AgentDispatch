"""the new abilities as MCP tools, so a model connected to the broker can run a calculation or a Lean check on this computer instead of thinking it through, ask for (or have built) a tool that is
missing, read the campaign state, start a job from a template, and look through the playbook library of finished workflows before it plans anything new."""
from ..mcp_server import Tool

S = lambda n=None, **k: dict(type="string", **({"maxLength": n} if n else {}), **k)
OBJ = lambda props, req=(): dict(type="object", properties=props, required=list(req))
STRS = dict(type="array", items=S(300))


def build(api):
    act = lambda s: (s or {}).get("actor") or "unknown"
    return [
        Tool("tool_list", "List the tools this computer can run for you: scientific calc (sympy/numpy/scipy/pandas/pint/astropy/…), browse (fetch_url, browser_fetch), docs (pdf_text, docx_text, xlsx_probe, md_render), "
             "verify (hash_file, json_check, lean, pytest, trivy), media (ffmpeg, ffprobe, image_info, audio_info, transcribe), geo, ML libs, git, docker, GPU info, and tools agents built. "
             "Whether each is installed and how often used. Prefer these over doing arithmetic or fetching pages yourself: free and exact.", OBJ({}), lambda a, s: api.tools(a.get("history", 0)), ("full",), read_only=True),
        Tool("tool_run", "Run a tool on this computer and get its output. "
             "tool=calc: code = short Python that prints a result (sympy, numpy, scipy, pandas, pint, astropy, sklearn, shapely, …; no files, no network). "
             "tool=fetch_url / browser_fetch: args=[url] public http(s) only. "
             "tool=pdf_text|docx_text|xlsx_probe|csv_stats|image_info|audio_info|hash_file|md_render|json_check: file= path in an allowed folder. "
             "tool=unit_convert: args or code like '2.5 mile to km'. "
             "tool=lean: code or file (must compile with no sorry). tool=git: read-only args. "
             "tool=pytest/ruff/mypy: cwd = trusted project. Others: see tool_list.",
             OBJ(dict(tool=S(40), code=S(20000), file=S(400), args=STRS, cwd=S(400), project=S(400), timeout=dict(type="integer", minimum=1, maximum=1800), job_id=S(20)), ["tool"]),
             lambda a, s: api.run(a, act(s)), ("full",)),
        Tool("tool_install", "Ask for a missing tool (a Python library from a fixed list, Lean via the official installer, ffmpeg). The OWNER approves it on the Tools page unless they allowed automatic installs. Returns the request state.",
             OBJ(dict(tool=S(40), reason=S(300)), ["tool"]), lambda a, s: api.install(a, act(s)), ("full",)),
        Tool("tool_need", "A tool you need does not exist. This first looks for one that already does the job (installed or installable); if there is none, it creates a job for an agent to build it as a small local program with a test. "
             "Give a short name and a clear description (inputs, output) and, if possible, examples [{args, stdin, expect}].", OBJ(dict(name=S(40), description=S(800), examples=dict(type="array", items=dict(type="object"))), ["name", "description"]),
             lambda a, s: api.need(a, act(s)), ("full",)),
        Tool("campaign_state", "The campaign gates, which are closed, open or locked, what is waiting for the owner, and which gate is next. A job proposes transitions; only the owner (or an automatic rule the owner enabled) moves a gate.",
             OBJ({}), lambda a, s: api.campaign(), ("full",), read_only=True),
        Tool("campaign_next", "The earliest unclosed gate whose dependencies are all closed: what the campaign should work on next.", OBJ(dict(campaign=S(40)), ["campaign"]), lambda a, s: api.campaign_next(a["campaign"]),
             ("full",), read_only=True),
        Tool("job_from_template", "Start a job from a template (primary-closure, independent-shadow, lean-shadow, numerical-certificate, adversarial-review, governance-decision, prospective-freeze, result-bearing-compute, evidence-seal, "
             "git-relay-sync, erratum, alternative-search, build-a-tool). fields: title, gate, campaign, goal, target, check, cutoff (evidence cutoff commit), repo.",
             OBJ(dict(template=S(40), fields=dict(type="object")), ["template"]), lambda a, s: api.job_from_template(a, act(s)), ("full",)),
        Tool("job_evidence", "A job's sealed evidence: the hashes of its inputs and outputs, its machine-readable result, any discrepancy or stop report, and errata.", OBJ(dict(job_id=S(20)), ["job_id"]),
             lambda a, s: api.evidence(a["job_id"]), ("full",), read_only=True),
        Tool("playbook_search", "Search the library of finished workflows (every job's route, steps, tools, commands and failure points, sorted by kind / template / outcome). Look here BEFORE planning new work: "
             "reuse what worked, avoid what failed.", OBJ(dict(query=S(300), category=S(80), outcome=S(20), tool=S(40), limit=dict(type="integer", minimum=1, maximum=100))),
             lambda a, s: api.playbooks(a.get("query", ""), a.get("category", ""), a.get("outcome", ""), a.get("tool", ""), a.get("limit", 20)), ("full",), read_only=True),
        Tool("playbook_get", "One finished workflow in full.", OBJ(dict(id=S(30)), ["id"]), lambda a, s: api.playbook(a["id"]), ("full",), read_only=True),
        Tool("playbook_failures", "The failure points that keep recurring across finished jobs (what went wrong, where, with which agents, how often) - what a new project should be warned about.",
             OBJ(dict(category=S(80), limit=dict(type="integer", minimum=1, maximum=50))), lambda a, s: api.failures(a.get("category", ""), a.get("limit", 15)), ("full",), read_only=True),
        Tool("playbook_suggest", "For a new goal: the most similar workflows that WORKED (steps, tools, acceptance) and the failure points seen on similar work. Start from this instead of working it out again.",
             OBJ(dict(goal=S(2000), kind=S(20)), ["goal"]), lambda a, s: api.suggest(a["goal"], a.get("kind", "")), ("full",), read_only=True),
    ]
