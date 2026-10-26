"""validate JSON text or a .json file against an optional JSON Schema. Args: [json_path_or_-] [schema_path]."""
import json, os, sys


def main(argv):
    if not argv:
        print(json.dumps(dict(ok=False, error="give a JSON file path, or '-' and pass JSON on stdin")))
        return 2
    src, schema_path = argv[0], (argv[1] if len(argv) > 1 else "")
    try:
        if src == "-":
            raw = sys.stdin.read()
        else:
            if not os.path.isfile(src):
                print(json.dumps(dict(ok=False, error="no such file: %s" % src)))
                return 3
            raw = open(src, encoding="utf-8").read()
        data = json.loads(raw)
    except Exception as e:
        print(json.dumps(dict(ok=False, error="invalid JSON: %s" % str(e)[:200]), ensure_ascii=False))
        return 4
    out = dict(ok=True, parsed=True, type=type(data).__name__)
    if isinstance(data, dict):
        out["keys"] = list(data.keys())[:80]
    elif isinstance(data, list):
        out["length"] = len(data)
    if schema_path:
        if not os.path.isfile(schema_path):
            print(json.dumps(dict(ok=False, error="no such schema file")))
            return 5
        try:
            import jsonschema
            schema = json.loads(open(schema_path, encoding="utf-8").read())
            jsonschema.validate(data, schema)
            out["schema_ok"] = True
        except Exception as e:
            print(json.dumps(dict(ok=False, error="schema validation failed: %s" % str(e)[:300]), ensure_ascii=False))
            return 6
    print(json.dumps(out, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
