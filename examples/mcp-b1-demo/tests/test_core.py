"""Core tests: dataset, read tools, the approval flow and the audit log. Standard library only (no mcp needed)."""
import json
import os
import sqlite3
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from b1demo import seed                                   # noqa: E402
from b1demo.repo import SQLiteRepository, ServiceLayerRepository   # noqa: E402
from b1demo.tools import B1Tools, ToolError               # noqa: E402


class Clock:
    def __init__(self):
        self.t = 1_900_000_000.0

    def __call__(self):
        return self.t


_OPEN = []


def fresh(read_only=False, clock=None):
    d = tempfile.mkdtemp(prefix="b1demo_")
    t = B1Tools(SQLiteRepository(d), read_only=read_only, caller="test", clock=clock)
    _OPEN.append(t.repo)
    return t


def tearDownModule():
    for r in _OPEN:
        r.close()


def read(path):
    with open(path, encoding="utf-8") as f:
        return f.read()


PO = dict(supplier="S0001", lines=[dict(item_code="A1000", quantity=20, warehouse="PER")], due_date="2026-11-15", note="restock Perth")


class Dataset(unittest.TestCase):
    def test_deterministic(self):
        self.assertEqual(json.dumps(seed.build(), sort_keys=True), json.dumps(seed.build(), sort_keys=True))
        self.assertNotEqual(json.dumps(seed.build(1), sort_keys=True), json.dumps(seed.build(2), sort_keys=True))

    def test_shape_and_sizes(self):
        d = seed.build()
        bps = d["business_partners"]
        self.assertEqual(sum(b["CardType"] == "cCustomer" for b in bps), 40)
        self.assertEqual(sum(b["CardType"] == "cSupplier" for b in bps), 15)
        self.assertEqual(len(d["items"]), 120)
        self.assertEqual([w["WarehouseCode"] for w in d["warehouses"]], ["PER", "SYD", "MEL", "BNE"])
        self.assertEqual(len(d["orders"]), 300)
        self.assertEqual(len(d["invoices"]), 250)
        self.assertIs(d["meta"]["_synthetic"], True)
        orders = {o["DocEntry"]: o for o in d["orders"]}
        for i in d["invoices"]:
            self.assertIn(i["BaseEntry"], orders)
            self.assertEqual(i["DocTotal"], orders[i["BaseEntry"]]["DocTotal"])
            self.assertLessEqual(i["PaidToDate"], i["DocTotal"])
        self.assertEqual(len({i["ItemCode"] for i in d["items"]}), 120)

    def test_names_are_obviously_fake(self):
        d = seed.build()
        for b in d["business_partners"]:
            self.assertTrue(b["CardName"].endswith("(demo)"), b["CardName"])
        for i in d["items"]:
            self.assertTrue(i["ItemName"].startswith("Demo "), i["ItemName"])

    def test_some_items_below_reorder_point_in_perth(self):
        t = fresh()
        r = t.call("low_stock", dict(warehouse="PER", limit=100))
        self.assertGreaterEqual(r["count"], 3)
        self.assertIn("A1000", [x["ItemCode"] for x in r["value"]])

    def test_service_layer_repository_is_a_stub(self):
        with self.assertRaises(NotImplementedError):
            ServiceLayerRepository()


class ReadTools(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.t = fresh()

    def test_every_result_is_marked_synthetic(self):
        for name, args in [("search_business_partners", {}), ("search_items", {}), ("low_stock", {}), ("list_sales_orders", {}),
                           ("list_invoices", {}), ("sales_summary", {}), ("audit_tail", {})]:
            r = self.t.call(name, args)
            self.assertIs(r["_synthetic"], True)
            self.assertIsInstance(r["summary"], str)

    def test_business_partners(self):
        r = self.t.call("search_business_partners", dict(query="demo", type="supplier", limit=100))
        self.assertEqual(r["count"], 15)
        self.assertTrue(all(b["CardType"] == "cSupplier" for b in r["value"]))
        c = self.t.call("get_business_partner", dict(card_code="c0001"))["value"]
        self.assertEqual(c["CardCode"], "C0001")
        self.assertIn("OpenOrdersCount", c)
        self.assertAlmostEqual(c["CreditAvailable"], c["CreditLimit"] - c["CurrentAccountBalance"], places=2)
        with self.assertRaises(ToolError):
            self.t.call("get_business_partner", dict(card_code="NOPE"))
        with self.assertRaises(ToolError):
            self.t.call("search_business_partners", dict(type="martian"))

    def test_items_and_stock(self):
        r = self.t.call("search_items", dict(query="pump", group=100))
        self.assertGreater(r["count"], 0)
        s = self.t.call("get_stock", dict(item="A1000", warehouse="per"))
        row = s["ItemWarehouseInfoCollection"][0]
        self.assertEqual(row["WarehouseCode"], "PER")
        self.assertEqual(row["Available"], row["InStock"] - row["Committed"] + row["Ordered"])
        self.assertTrue(row["BelowReorderPoint"])
        self.assertIn("BELOW reorder point", s["summary"])
        allw = self.t.call("get_stock", dict(item="A1000"))
        self.assertEqual(len(allw["ItemWarehouseInfoCollection"]), 4)
        with self.assertRaises(ToolError):
            self.t.call("get_stock", dict(item="A1000", warehouse="XYZ"))
        with self.assertRaises(ToolError):
            self.t.call("get_stock", dict(item="does-not-exist"))

    def test_orders_and_invoices(self):
        allo = self.t.call("list_sales_orders", dict(limit=100))
        self.assertEqual(allo["count"], 300)
        op = self.t.call("list_sales_orders", dict(status="open"))
        cl = self.t.call("list_sales_orders", dict(status="closed"))
        self.assertEqual(op["count"] + cl["count"], 300)
        self.assertEqual(cl["count"], 250)
        q3 = self.t.call("list_sales_orders", dict(from_date="2026-07-01", to_date="2026-09-30", limit=100))
        self.assertTrue(all("2026-07-01" <= o["DocDate"] <= "2026-09-30" for o in q3["value"]))
        o = self.t.call("get_sales_order", dict(doc_num=5001))["value"]
        self.assertEqual(o["DocNum"], 5001)
        self.assertTrue(o["DocumentLines"])
        inv = self.t.call("list_invoices", dict(overdue_only=True, limit=100))
        self.assertGreater(inv["count"], 0)
        self.assertTrue(all(i["Overdue"] and i["Outstanding"] > 0 and i["DocDueDate"] < "2026-09-30" for i in inv["value"]))
        with self.assertRaises(ToolError):
            self.t.call("list_sales_orders", dict(status="maybe"))

    def test_sales_summary(self):
        by_c = self.t.call("sales_summary", dict(period="2026-Q3", by="customer", limit=5))
        by_i = self.t.call("sales_summary", dict(period="2026-Q3", by="item", limit=500))
        q3 = self.t.call("list_sales_orders", dict(from_date="2026-07-01", to_date="2026-09-30"))
        self.assertAlmostEqual(by_c["total"], q3["total"], places=2)
        self.assertAlmostEqual(by_i["total"], q3["total"], places=1)
        self.assertLessEqual(len(by_c["value"]), 5)
        self.assertEqual(self.t.call("sales_summary", dict(period="this_quarter"))["period"], "2026-Q3")
        self.assertEqual(self.t.call("sales_summary", dict(period="last_quarter"))["period"], "2026-Q2")
        for bad in ("2026-Q5", "soon"):
            with self.assertRaises(ToolError):
                self.t.call("sales_summary", dict(period=bad))


class ApprovalFlow(unittest.TestCase):
    def drafts(self, t):
        return t.repo.drafts()

    def test_preview_never_writes_and_approve_writes_once(self):
        t = fresh()
        p = t.call("create_draft_purchase_order", dict(PO))
        self.assertEqual(p["status"], "preview")
        self.assertTrue(p["approval_token"])
        self.assertEqual(p["preview"]["DocObjectCode"], "oPurchaseOrders")
        self.assertEqual(self.drafts(t), [])
        w = t.call("create_draft_purchase_order", dict(PO, approval_token=p["approval_token"], approve=True))
        self.assertEqual(w["status"], "written")
        d = self.drafts(t)
        self.assertEqual(len(d), 1)
        self.assertEqual(d[0]["CardCode"], "S0001")
        self.assertEqual(d[0]["DocumentLines"][0]["Quantity"], 20.0)
        self.assertIs(d[0]["_synthetic"], True)
        with self.assertRaises(ToolError):        # single use
            t.call("create_draft_purchase_order", dict(PO, approval_token=p["approval_token"], approve=True))
        self.assertEqual(len(self.drafts(t)), 1)

    def test_no_write_without_a_valid_token(self):
        t = fresh()
        with self.assertRaises(ToolError):
            t.call("create_draft_purchase_order", dict(PO, approval_token="made-up-token", approve=True))
        self.assertEqual(self.drafts(t), [])

    def test_approve_must_be_true(self):
        t = fresh()
        p = t.call("create_draft_purchase_order", dict(PO))
        r = t.call("create_draft_purchase_order", dict(PO, approval_token=p["approval_token"], approve=False))
        self.assertEqual(r["status"], "declined")
        with self.assertRaises(ToolError):        # a declined token cannot be used afterwards
            t.call("create_draft_purchase_order", dict(PO, approval_token=p["approval_token"], approve=True))
        self.assertEqual(self.drafts(t), [])

    def test_expired_token(self):
        clock = Clock()
        t = fresh(clock=clock)
        p = t.call("create_draft_purchase_order", dict(PO))
        clock.t += 601
        with self.assertRaises(ToolError) as e:
            t.call("create_draft_purchase_order", dict(PO, approval_token=p["approval_token"], approve=True))
        self.assertIn("expired", str(e.exception))
        self.assertEqual(self.drafts(t), [])

    def test_token_just_inside_ten_minutes_works(self):
        clock = Clock()
        t = fresh(clock=clock)
        p = t.call("create_draft_purchase_order", dict(PO))
        clock.t += 599
        self.assertEqual(t.call("create_draft_purchase_order", dict(PO, approval_token=p["approval_token"], approve=True))["status"], "written")

    def test_tampered_payload(self):
        t = fresh()
        p = t.call("create_draft_purchase_order", dict(PO))
        for change in (dict(lines=[dict(item_code="A1000", quantity=200, warehouse="PER")]), dict(supplier="S0002"),
                       dict(due_date="2026-12-01"), dict(note="something else")):
            with self.assertRaises(ToolError) as e:
                t.call("create_draft_purchase_order", dict(PO, approval_token=p["approval_token"], approve=True, **change))
            self.assertIn("changed", str(e.exception))
        self.assertEqual(self.drafts(t), [])
        # the original payload still works with its token (a tampered try does not spend it)
        self.assertEqual(t.call("create_draft_purchase_order", dict(PO, approval_token=p["approval_token"], approve=True))["status"], "written")

    def test_validation(self):
        t = fresh()
        bad = [dict(PO, supplier="C0001"), dict(PO, supplier="S9999"), dict(PO, lines=[]),
               dict(PO, lines=[dict(item_code="A1000", quantity=0)]), dict(PO, lines=[dict(item_code="A1000", quantity=-3)]),
               dict(PO, lines=[dict(item_code="NOPE", quantity=1)]), dict(PO, due_date="2020-01-01"), dict(PO, due_date="tomorrow")]
        services = [i["ItemCode"] for i in t.repo.items() if i["PurchaseItem"] == "tNO"]
        bad.append(dict(PO, lines=[dict(item_code=services[0], quantity=1)]))
        for b in bad:
            with self.assertRaises(ToolError, msg=str(b)):
                t.call("create_draft_purchase_order", b)
        self.assertEqual(self.drafts(t), [])

    def test_read_only_mode(self):
        t = fresh(read_only=True)
        with self.assertRaises(ToolError) as e:
            t.call("create_draft_purchase_order", dict(PO))
        self.assertIn("B1DEMO_READ_ONLY", str(e.exception))
        self.assertEqual(self.drafts(t), [])
        self.assertEqual(t.audit.tail(1)[0]["approval"], "disabled")
        self.assertTrue(t.call("low_stock", {})["count"] >= 0)          # reads still work

    def test_read_only_from_environment(self):
        os.environ["B1DEMO_READ_ONLY"] = "1"
        try:
            t = B1Tools(SQLiteRepository(tempfile.mkdtemp(prefix="b1demo_")))
            _OPEN.append(t.repo)
            self.assertTrue(t.read_only)
        finally:
            del os.environ["B1DEMO_READ_ONLY"]


class Audit(unittest.TestCase):
    def test_every_call_is_logged_with_redacted_token(self):
        t = fresh()
        t.call("get_stock", dict(item="A1000", warehouse="PER"))
        try:
            t.call("get_stock", dict(item="nope"))
        except ToolError:
            pass
        p = t.call("create_draft_purchase_order", dict(PO))
        t.call("create_draft_purchase_order", dict(PO, approval_token=p["approval_token"], approve=True))
        rows = t.audit.tail(10)
        self.assertEqual([r["tool"] for r in rows], ["get_stock", "get_stock", "create_draft_purchase_order", "create_draft_purchase_order"])
        self.assertEqual([r["ok"] for r in rows], [True, False, True, True])
        self.assertEqual([r["approval"] for r in rows], ["n/a", "n/a", "preview", "approved"])
        self.assertTrue(all(r["caller"] == "test" and len(r["result_hash"]) == 64 for r in rows))
        logged = json.dumps(rows) + read(t.audit.jsonl)
        self.assertNotIn(p["approval_token"], logged)
        self.assertIn("(redacted)", rows[3]["args"]["approval_token"])
        lines = [json.loads(x) for x in read(t.audit.jsonl).splitlines()]
        self.assertEqual([x["id"] for x in lines], [r["id"] for r in rows])
        tail = t.call("audit_tail", dict(n=2))
        self.assertEqual(len(tail["value"]), 2)

    def test_audit_table_is_append_only(self):
        t = fresh()
        t.call("low_stock", {})
        con = sqlite3.connect(t.repo.path)
        with self.assertRaises(sqlite3.DatabaseError):
            con.execute("UPDATE audit_log SET tool = 'x'")
        with self.assertRaises(sqlite3.DatabaseError):
            con.execute("DELETE FROM audit_log")
        con.close()

    def test_cli(self):
        import subprocess
        d = tempfile.mkdtemp(prefix="b1demo_")
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        env = dict(os.environ, B1DEMO_DATA=d)
        r = subprocess.run([sys.executable, "-m", "b1demo", "call", "get_stock", '{"item": "A1000", "warehouse": "PER"}'],
                           cwd=root, env=env, capture_output=True, text=True, timeout=60)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("BELOW reorder point", json.loads(r.stdout)["summary"])
        r = subprocess.run([sys.executable, "-m", "b1demo", "audit"], cwd=root, env=env, capture_output=True, text=True, timeout=60)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("get_stock", r.stdout)


if __name__ == "__main__":
    unittest.main(verbosity=1)
