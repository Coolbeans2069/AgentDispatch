"""the HTTP side of tools / campaign / playbooks: GET routes (open on loopback like the rest of the read API) and POST routes (a token; the owner's moves need an owner token)."""
from urllib.parse import unquote

OWNER_POST = ("/api/tools/approve", "/api/tools/deny", "/api/tools/config", "/api/campaign/import", "/api/campaign/load", "/api/campaign/transition", "/api/campaign/decide", "/api/campaign/approve_run",
              "/api/jobs/shadow", "/api/jobs/erratum", "/api/playbooks/import", "/api/campaign/define")
PAGES = {"/campaign": ("campaign", "CAMPAIGN_HTML"), "/tools": ("tools", "TOOLS_HTML"), "/library": ("library", "LIBRARY_HTML")}


def page(path):
    """-> html bytes for a page route, else None"""
    if path not in PAGES:
        return None
    from . import pages
    from ..theme import apply
    return apply(getattr(pages, PAGES[path][1]), PAGES[path][0]).encode("utf-8")


def get(app, path, q):
    """-> a dict for an API route, else None (the caller goes on to its own routes)"""
    api = app.api
    if path == "/api/tools":
        return api.tools(int(q.get("history", 20)))
    if path == "/api/tools/runs":
        return dict(ok=True, runs=app.localtools.history(int(q.get("limit", 50)), q.get("tool"), q.get("job")))
    if path == "/api/campaign":
        return api.campaign()
    if path == "/api/campaign/log":
        return api.campaign_log(q.get("campaign"), int(q.get("limit", 60)))
    if path == "/api/campaign/example":
        return api.campaign_example()
    if path == "/api/templates":
        return dict(ok=True, templates=list(__import__("agentdispatch.campaign.templates", fromlist=["x"]).all_templates(app.data).values()))
    if path == "/api/playbooks":
        return api.playbooks(q.get("q", ""), q.get("category", ""), q.get("outcome", ""), q.get("tool", ""), int(q.get("limit", 30)))
    if path == "/api/playbooks/failures":
        return api.failures(q.get("category", ""), int(q.get("limit", 15)))
    if path == "/api/playbooks/suggest":
        return api.suggest(q.get("goal", ""), q.get("kind", ""))
    if path == "/api/playbooks/export":
        return api.playbook_export()
    if path.startswith("/api/playbooks/"):
        return api.playbook(unquote(path.rsplit("/", 1)[1]))
    if path.startswith("/api/jobs/") and path.endswith("/evidence"):
        return api.evidence(path.split("/")[3])
    return None


def post(app, path, body, actor, role):
    """-> (status, dict) for an API route, else None"""
    api = app.api
    if path in OWNER_POST and role != "owner":
        return 403, dict(ok=False, error="owner_only", message="this is the owner's: use an owner token (python -m agentdispatch token NAME --owner)")
    r = None
    if path == "/api/tools/run":
        r = api.run(body, actor)
    elif path == "/api/tools/scan":
        r = api.scan()
    elif path == "/api/tools/install":
        r = api.install(body, actor)
    elif path == "/api/tools/approve":
        r = api.decide_install(str(body.get("id", "")), True, actor)
    elif path == "/api/tools/deny":
        r = api.decide_install(str(body.get("id", "")), False, actor)
    elif path == "/api/tools/config":
        r = api.tool_config(body)
    elif path == "/api/tools/need":
        r = api.need(body, actor)
    elif path == "/api/campaign/import":
        r = api.campaign_import(str(body.get("campaign", "")), str(body.get("text", "")), actor)
    elif path == "/api/campaign/load":
        r = api.campaign_load(body.get("spec") or body, actor)
    elif path == "/api/campaign/propose":
        r = api.propose(body, actor)
    elif path == "/api/campaign/decide":
        r = api.decide(str(body.get("id", "")), bool(body.get("approve")), actor, str(body.get("why", "")))
    elif path == "/api/campaign/transition":
        r = api.transition(body, actor)
    elif path == "/api/campaign/approve_run":
        r = api.approve_run(str(body.get("job_id", "")), actor)
    elif path == "/api/jobs/from-template":
        r = api.job_from_template(body, actor)
    elif path == "/api/jobs/shadow":
        r = api.shadow(body, actor)
    elif path == "/api/jobs/erratum":
        r = api.erratum(str(body.get("job_id", "")), str(body.get("text", "")), actor)
    elif path == "/api/playbooks/import":
        r = api.playbook_import(body.get("bundle") or body)
    if r is None:
        return None
    return (200 if r.get("ok") else 400), r
