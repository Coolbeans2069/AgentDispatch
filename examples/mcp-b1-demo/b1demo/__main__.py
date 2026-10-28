"""python -m b1demo [seed|serve|audit|call|tools]"""
import argparse
import json
import os
import shutil
import sys

from . import __version__
from .repo import SQLiteRepository, default_data_dir
from .tools import B1Tools, ToolError

DISCLAIMER = ("Unofficial demo. Not affiliated with, endorsed by or sponsored by SAP SE. Uses synthetic data shaped like "
              "SAP Business One Service Layer objects; it does not connect to SAP software.")


def main(argv=None):
    ap = argparse.ArgumentParser(prog="b1demo", description="Offline demo MCP server on synthetic ERP-style data. " + DISCLAIMER)
    ap.add_argument("--data", default=None, help="data folder (default: $B1DEMO_DATA or ./b1demo-data)")
    ap.add_argument("--version", action="version", version=__version__)
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("seed", help="create the synthetic database (deterministic)")
    s.add_argument("--force", action="store_true", help="delete the data folder's database, drafts and audit log first")
    s = sub.add_parser("serve", help="run the MCP server (needs: pip install mcp)")
    s.add_argument("--transport", default="stdio", choices=["stdio", "streamable-http"])
    s = sub.add_parser("audit", help="print the audit log")
    s.add_argument("-n", type=int, default=20)
    s.add_argument("--json", action="store_true")
    s = sub.add_parser("call", help="call one tool without an MCP client, e.g. call get_stock '{\"item\": \"A1000\", \"warehouse\": \"PER\"}'")
    s.add_argument("tool")
    s.add_argument("args", nargs="?", default="{}")
    sub.add_parser("tools", help="list the tools")
    a = ap.parse_args(argv)
    data = a.data or default_data_dir()

    if a.cmd == "seed":
        if a.force:
            for f in ("b1demo.db", "audit.jsonl"):
                p = os.path.join(data, f)
                if os.path.exists(p):
                    os.remove(p)
        repo = SQLiteRepository(data)
        m = repo.meta()
        print("synthetic dataset ready at %s (seed %s, as of %s)" % (repo.path, m["seed"], m["as_of"]))
        return 0
    if a.cmd == "serve":
        from .server import serve
        serve(a.transport, B1Tools(SQLiteRepository(data)))
        return 0
    t = B1Tools(SQLiteRepository(data), caller=os.environ.get("B1DEMO_CALLER") or "cli")
    if a.cmd == "tools":
        print("\n".join(t.names()))
        return 0
    if a.cmd == "audit":
        rows = t.audit.tail(a.n)
        if a.json:
            print(json.dumps(rows, indent=1))
        else:
            for r in rows:
                print("%5d %s %-28s %-4s %-30s caller=%s args=%s" % (r["id"], r["ts"], r["tool"], "ok" if r["ok"] else "ERR",
                                                                     r["approval"][:30], r["caller"], json.dumps(r["args"], sort_keys=True)))
        return 0
    if a.cmd == "call":
        try:
            out = t.call(a.tool, json.loads(a.args))
        except (ToolError, json.JSONDecodeError) as e:
            print("error: %s" % e, file=sys.stderr)
            return 2
        print(json.dumps(out, indent=1))
        return 0
    return 1


if __name__ == "__main__":
    sys.exit(main())
