"""pull the JSON object out of a model's reply, even when the model wrote its reasoning first (Gemma 4 and other thinking models do): scan for balanced {...} blocks, try them from the LAST one backwards"""
import json


def _blocks(text):
    """yield (start, end) of every balanced top-level {...} block, respecting JSON strings and escapes"""
    depth, start, in_str, esc = 0, None, False, False
    for i, ch in enumerate(text):
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"' and depth > 0:
            in_str = True
        elif ch == "{":
            if depth == 0:
                start = i
            depth += 1
        elif ch == "}" and depth > 0:
            depth -= 1
            if depth == 0 and start is not None:
                yield start, i + 1
                start = None


def extract_json(text, require_key=None):
    """the last parseable JSON object in `text` that contains `require_key` (any object if no key is required); None if there is none"""
    text = text or ""
    try:
        whole = json.loads(text)
        if isinstance(whole, dict) and (require_key is None or require_key in whole):
            return whole
    except Exception:
        pass
    found = list(_blocks(text))
    for s, e in reversed(found):
        try:
            obj = json.loads(text[s:e])
        except Exception:
            continue
        if isinstance(obj, dict) and (require_key is None or require_key in obj):
            return obj
    # a stray unbalanced '{' in the model's reasoning swallows every later block: decode from each '{' on its own, last one first
    dec = json.JSONDecoder()
    pos = text.rfind("{")
    while pos != -1:
        try:
            obj, _ = dec.raw_decode(text, pos)
            if isinstance(obj, dict) and (require_key is None or require_key in obj):
                return obj
        except Exception:
            pass
        pos = text.rfind("{", 0, pos)
    return None
