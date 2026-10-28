"""End-to-end over MCP stdio: start `python -m b1demo serve` and speak JSON-RPC to it.
Skips (exit 0) when the optional `mcp` SDK is not installed."""
import json
import os
import subprocess
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

try:
    import mcp  # noqa: F401
    HAVE_MCP = True
except ImportError:
    HAVE_MCP = False

EXPECTED = {"search_business_partners", "get_business_partner", "search_items", "get_stock", "low_stock", "list_sales_orders",
            "get_sales_order", "list_invoices", "sales_summary", "audit_tail", "create_draft_purchase_order"}


class Session:
    def __init__(self, data, extra_env=None):
        env = dict(os.environ, B1DEMO_DATA=data, B1DEMO_CALLER="mcp-test", **(extra_env or {}))
        self.p = subprocess.Popen([sys.executable, "-m", "b1demo", "serve"], cwd=ROOT, env=env, stdin=subprocess.PIPE,
                                  stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, bufsize=1)
        self.n = 0

    def send(self, method, params=None, notify=False):
        msg = dict(jsonrpc="2.0", method=method)
        if params is not None:
            msg["params"] = params
        if not notify:
            self.n += 1
            msg["id"] = self.n
        self.p.stdin.write(json.dumps(msg) + "\n")
        self.p.stdin.flush()
        if notify:
            return None
        while True:
            line = self.p.stdout.readline()
            if not line:
                raise AssertionError("server closed: " + self.p.stderr.read()[-2000:])
            r = json.loads(line)
            if r.get("id") == self.n:
                return r

    def init(self):
        r = self.send("initialize", dict(protocolVersion="2025-06-18", capabilities={}, clientInfo=dict(name="b1demo-test", version="1")))
        assert "result" in r, r
        self.send("notifications/initialized", notify=True)
        return r["result"]

    def call(self, name, args):
        r = self.send("tools/call", dict(name=name, arguments=args))
        assert "result" in r, r
        res = r["result"]
        if res.get("isError"):
            return None, res["content"][0]["text"]
        data = res.get("structuredContent")
        if isinstance(data, dict) and set(data) == {"result"}:
            data = data["result"]
        if data is None:
            data = json.loads(res["content"][0]["text"])
        return data, None

    def close(self):
        try:
            self.p.stdin.close()
            self.p.wait(timeout=10)
        except Exception:
            self.p.kill()
        for s in (self.p.stdout, self.p.stderr):
            s.close()


@unittest.skipUnless(HAVE_MCP, "mcp SDK not installed")
class OverMCP(unittest.TestCase):
    def test_tools_and_the_approval_flow(self):
        s = Session(tempfile.mkdtemp(prefix="b1mcp_"))
        try:
            info = s.init()
            self.assertEqual(info["serverInfo"]["name"], "b1demo")
            tools = {t["name"]: t for t in s.send("tools/list", {})["result"]["tools"]}
            self.assertEqual(set(tools), EXPECTED)
            if "annotations" in tools["get_stock"]:
                self.assertTrue(tools["get_stock"]["annotations"].get("readOnlyHint"))
                self.assertFalse(tools["create_draft_purchase_order"]["annotations"].get("readOnlyHint"))
            stock, err = s.call("get_stock", dict(item="A1000", warehouse="PER"))
            self.assertIsNone(err)
            self.assertIn("BELOW reorder point", stock["summary"])
            _, err = s.call("get_stock", dict(item="A1000", warehouse="XYZ"))
            self.assertIn("warehouse must be one of", err)
            po = dict(supplier=stock["PreferredVendor"], lines=[dict(item_code="A1000", quantity=25, warehouse="PER")], due_date="2026-11-20")
            prev, err = s.call("create_draft_purchase_order", po)
            self.assertIsNone(err)
            self.assertEqual(prev["status"], "preview")
            _, err = s.call("create_draft_purchase_order", dict(po, approval_token="wrong", approve=True))
            self.assertIn("unknown approval_token", err)
            done, err = s.call("create_draft_purchase_order", dict(po, approval_token=prev["approval_token"], approve=True))
            self.assertIsNone(err)
            self.assertEqual(done["status"], "written")
            tail, _ = s.call("audit_tail", dict(n=10))
            self.assertEqual([r["approval"] for r in tail["value"] if r["tool"] == "create_draft_purchase_order"],
                             ["preview", "rejected: unknown approval_token", "approved"])
            self.assertTrue(all(r["caller"] == "mcp-test" for r in tail["value"]))
        finally:
            s.close()

    def test_read_only_server(self):
        s = Session(tempfile.mkdtemp(prefix="b1mcp_"), dict(B1DEMO_READ_ONLY="1"))
        try:
            s.init()
            _, err = s.call("create_draft_purchase_order", dict(supplier="S0001", lines=[dict(item_code="A1000", quantity=1)], due_date="2026-11-20"))
            self.assertIn("B1DEMO_READ_ONLY", err)
            ok, err = s.call("low_stock", dict(warehouse="PER"))
            self.assertIsNone(err)
        finally:
            s.close()


if __name__ == "__main__":
    if not HAVE_MCP:
        print("SKIP: the optional mcp SDK is not installed (pip install mcp); the core tests cover the logic")
        sys.exit(0)
    unittest.main(verbosity=1)
