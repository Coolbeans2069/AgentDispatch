"""deterministic compaction of an MCP tool catalog: drop output schemas, inline shared $defs, trim descriptions. No model involved."""
import copy, hashlib, json, re

DROP_KEYS = {"$schema", "additionalProperties", "title", "default", "examples", "$comment", "format"}
PROP_DESC_MAX = 110
TOOL_DESC_MAX = 200


def first_sentence(text, cap=TOOL_DESC_MAX):
    t = re.sub(r"\s+", " ", text or "").strip()
    m = re.search(r"(?<=[.!?])\s", t)
    s = t[:m.start()] if m else t
    return s[:cap].rstrip()


def _resolve(node, defs, depth=0, seen=()):
    if isinstance(node, list):
        return [_resolve(x, defs, depth, seen) for x in node]
    if not isinstance(node, dict):
        return node
    ref = node.get("$ref")
    if ref and ref.startswith("#/$defs/"):
        key = ref.split("/")[-1]
        target = defs.get(key)
        extra = {k: v for k, v in node.items() if k != "$ref"}
        if target is None or key in seen or depth > 4:
            return {k: v for k, v in extra.items() if k == "description"} or {"type": "object"}
        merged = dict(_resolve(target, defs, depth + 1, seen + (key,)))
        merged.update({k: v for k, v in extra.items() if k == "description"})
        return merged
    out = {}
    for k, v in node.items():
        if k in DROP_KEYS or k == "$defs":
            continue
        if k == "description" and isinstance(v, str):
            v = first_sentence(v, PROP_DESC_MAX)
            if not v:
                continue
        if k in ("properties", "patternProperties") and isinstance(v, dict):         # keys here are PROPERTY NAMES (one may be called "description" or "title"): never drop or trim them
            out[k] = {pn: _resolve(pv, defs, depth, seen) for pn, pv in v.items()}
            continue
        out[k] = _resolve(v, defs, depth, seen)
    return out


SCHEMA_BUDGET = 3000          # characters (~750 tokens) per tool schema shown to a model; bigger ones are cut by depth and flagged, the full schema stays fetchable


def _cap(node, depth, maxd, strip_desc):
    if isinstance(node, list):
        return [_cap(x, depth, maxd, strip_desc) for x in node]
    if not isinstance(node, dict):
        return node
    if depth >= maxd and ("properties" in node or "anyOf" in node or "oneOf" in node or "allOf" in node or node.get("type") == "object"):
        return {"type": "object", "description": "(nested object: ask tool_describe with full=true)"}
    out = {}
    for k, v in node.items():
        if k == "description" and strip_desc and depth > 0:
            continue
        if k in ("properties", "patternProperties") and isinstance(v, dict):
            out[k] = {pn: _cap(pv, depth + 1, maxd, strip_desc) for pn, pv in v.items()}
        elif k == "enum" and isinstance(v, list) and len(v) > 12:
            out[k] = v[:12] + ["..."]
        else:
            out[k] = _cap(v, depth, maxd, strip_desc) if k not in ("items",) else _cap(v, depth + 1, maxd, strip_desc)
    return out


def compact_schema(input_schema, budget=SCHEMA_BUDGET):
    """-> (schema, level): level 0 = complete, 1..3 = progressively cut by nesting depth (and nested descriptions dropped) until it fits the budget"""
    defs = input_schema.get("$defs") or {}
    full = _prune(_resolve({k: v for k, v in input_schema.items() if k != "$defs"}, defs))
    if len(json.dumps(full, separators=(",", ":"))) <= budget:
        return full, 0
    for level, (maxd, strip) in enumerate(((4, False), (3, True), (2, True), (1, True)), 1):
        cut = _prune(_cap(full, 0, maxd, strip))
        if len(json.dumps(cut, separators=(",", ":"))) <= budget or level == 4:
            return cut, level
    return full, 0


def _prune(s):
    """remove noise that costs tokens but carries nothing: empty required lists, anyOf-of-one, long enums of the same kind stay"""
    if isinstance(s, dict):
        out = {}
        for k, v in s.items():
            v = _prune(v)
            if k == "required" and not v:
                continue
            out[k] = v
        if "anyOf" in out and isinstance(out["anyOf"], list) and len(out["anyOf"]) == 1:
            return out["anyOf"][0]
        return out
    if isinstance(s, list):
        return [_prune(x) for x in s]
    return s


RISK_HIGH = re.compile(r"^(delete|remove|kill|reset|wipe|format|install|uninstall|setup|root|flash|erase|push|write_|patch|mutate)")
RISK_MED = re.compile(r"^(capture|start|launch|run|pull|extract|export|open_process|attach|inject|download|fetch|annotate|set_|comment|rename)")


def risk_of(tool):
    """low / medium / high from MCP annotations and the tool name (REA's own hints are coarse, so the name matters too)"""
    ann = tool.get("annotations") or {}
    name = tool.get("name", "")
    if ann.get("destructiveHint") or RISK_HIGH.match(name):
        return "high"
    if RISK_MED.match(name) or ann.get("openWorldHint") and not ann.get("readOnlyHint"):
        return "medium"
    return "low"


def compact_catalog(tools):
    """-> {name: {title, card, schema, risk, schema_sha}} plus size stats"""
    out = {}
    raw = len(json.dumps(tools, separators=(",", ":")))
    for t in tools:
        schema, level = compact_schema(t.get("inputSchema") or {})
        out[t["name"]] = dict(title=t.get("title") or t["name"], card=first_sentence(t.get("description", "")), description=first_sentence(t.get("description", ""), 400), blurb=re.sub(r"\s+", " ", t.get("description", "") or "")[:600], schema=schema,
                              cut_level=level, risk=risk_of(t), schema_sha=hashlib.sha256(json.dumps(t.get("inputSchema") or {}, sort_keys=True).encode()).hexdigest()[:12])
    comp = len(json.dumps(out, separators=(",", ":")))
    return out, dict(raw_chars=raw, compact_chars=comp, raw_tokens_est=raw // 4, compact_tokens_est=comp // 4, tools=len(out))
