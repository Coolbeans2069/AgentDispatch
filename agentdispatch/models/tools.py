"""the catalog as MCP tools, so an AI can find and choose models too: list them, scan, ask for the best one for a kind of work, and record its own choice (which a user's pin always overrides)"""
from ..mcp_server import Tool
from .catalog import FUNCTIONS, FUNC_LABEL

S = lambda n=None, **k: dict(type="string", **({"maxLength": n} if n else {}), **k)
OBJ = lambda props, req=(): dict(type="object", properties=props, required=list(req))


def slim(m):
    return {k: m.get(k) for k in ("id", "name", "label", "function", "modality", "subtype", "params_b", "quant", "size_gb", "size_class", "format", "runners", "source", "path", "favorite", "selected_for")}


def build(catalog):
    def listing(a, s):
        rows = catalog.list(function=a.get("function"), q=a.get("query"), runner=a.get("runner"), limit=a.get("limit", 30))
        return dict(ok=True, count=len(rows), models=[slim(m) for m in rows], by_function=catalog.counts(), scanned=catalog.d.get("scanned"))

    def pick(a, s):
        r = catalog.pick(a["function"], loaded=a.get("loaded") or [], max_size_gb=a.get("max_size_gb"), prefer_small=bool(a.get("prefer_small")))
        return dict(ok=True, model=slim(r["model"]) if r["model"] else None, by=r["by"], reasons=r["reasons"], alternatives=r["alternatives"])

    def select(a, s):
        return catalog.select(a["function"], a["model_id"], by="ai")

    def scan(a, s):
        started = catalog.scan_background(float(a.get("seconds", 60)), deep=bool(a.get("deep", True)))
        return dict(ok=True, started=started, status=catalog.status)

    fn = dict(type="string", enum=list(FUNCTIONS))
    return [
        Tool("model_list", "List the AI models found on this computer (any folder or drive): name, what it is for (function), size, how it is stored, what can run it. Filter by function: " + ", ".join("%s = %s" % (k, v) for k, v in FUNC_LABEL.items()),
             OBJ(dict(function=fn, query=S(80), runner=S(30), limit=dict(type="integer", minimum=1, maximum=200))), listing, ("full",), read_only=True),
        Tool("model_pick", "Ask which model to use for a kind of work. The user's own choice wins; otherwise the best match with the reasons and alternatives. loaded = names already in memory (preferred: no load time); max_size_gb limits size.",
             OBJ(dict(function=fn, loaded=dict(type="array", items=S(120)), max_size_gb=dict(type="number"), prefer_small=dict(type="boolean")), ["function"]), pick, ("full",), read_only=True),
        Tool("model_select", "Record YOUR choice of model for a kind of work (as the AI). It is refused when the user has pinned a model for that function: a user's choice is never overridden.",
             OBJ(dict(function=fn, model_id=S(20)), ["function", "model_id"]), select, ("full",)),
        Tool("model_scan", "Start a scan for models in the background (known places first, then every fixed drive until the time budget). Poll model_list afterwards.",
             OBJ(dict(seconds=dict(type="number", minimum=10, maximum=1800), deep=dict(type="boolean"))), scan, ("full",)),
    ]
