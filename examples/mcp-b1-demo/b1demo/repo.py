"""Data access in Service Layer-like shapes.

Tools talk to a `Repository`. `SQLiteRepository` serves the synthetic dataset. `ServiceLayerRepository` marks where a
real SAP Business One Service Layer client would plug in; it is deliberately not implemented in this demo."""
import json
import os
import sqlite3
import threading

from . import seed as _seed


class Repository:
    """The interface the tools use. Every method returns plain dicts shaped like Service Layer entities."""

    def meta(self): raise NotImplementedError
    def business_partners(self): raise NotImplementedError
    def business_partner(self, card_code): raise NotImplementedError
    def items(self): raise NotImplementedError
    def item(self, item_code): raise NotImplementedError
    def warehouses(self): raise NotImplementedError
    def stock(self, item_code=None, warehouse=None): raise NotImplementedError
    def orders(self): raise NotImplementedError
    def order_by_num(self, doc_num): raise NotImplementedError
    def invoices(self): raise NotImplementedError
    def add_draft(self, doc, created_at): raise NotImplementedError
    def drafts(self): raise NotImplementedError


class ServiceLayerRepository(Repository):
    """Stub only. A real implementation would log in to the Service Layer (`POST /b1s/v1/Login`), keep the session
    cookie, and map each method to an OData query, for example:

    - business_partners  -> GET  /b1s/v1/BusinessPartners?$select=CardCode,CardName,CardType,...
    - item / stock       -> GET  /b1s/v1/Items('<code>')  (ItemWarehouseInfoCollection holds InStock/Committed/Ordered)
    - orders / invoices  -> GET  /b1s/v1/Orders, /b1s/v1/Invoices with $filter and paging
    - add_draft          -> POST /b1s/v1/Drafts with DocObjectCode = oPurchaseOrders

    It would also need the client's own credentials, permissions and testing on their system. None of that is in
    this demo, which never connects to SAP software."""

    def __init__(self, *args, **kwargs):
        raise NotImplementedError("ServiceLayerRepository is a documented stub; this demo only ships the SQLite repository")


def default_data_dir():
    return os.environ.get("B1DEMO_DATA") or os.path.join(os.getcwd(), "b1demo-data")


class SQLiteRepository(Repository):
    def __init__(self, data_dir=None, seed=_seed.SEED):
        self.data_dir = data_dir or default_data_dir()
        os.makedirs(self.data_dir, exist_ok=True)
        self.path = os.path.join(self.data_dir, "b1demo.db")
        if not os.path.exists(self.path):
            _seed.write_db(self.path, seed)
        self.lock = threading.RLock()
        self.con = sqlite3.connect(self.path, check_same_thread=False, isolation_level=None)
        self.con.row_factory = sqlite3.Row

    def close(self):
        self.con.close()

    def _docs(self, sql, args=()):
        with self.lock:
            return [json.loads(r[0]) for r in self.con.execute(sql, args)]

    def meta(self):
        with self.lock:
            return {r["key"]: json.loads(r["value"]) for r in self.con.execute("SELECT key, value FROM meta")}

    def business_partners(self):
        return self._docs("SELECT doc FROM business_partners ORDER BY card_code")

    def business_partner(self, card_code):
        d = self._docs("SELECT doc FROM business_partners WHERE card_code = ? COLLATE NOCASE", (card_code,))
        return d[0] if d else None

    def items(self):
        return self._docs("SELECT doc FROM items ORDER BY item_code")

    def item(self, item_code):
        d = self._docs("SELECT doc FROM items WHERE item_code = ? COLLATE NOCASE", (item_code,))
        return d[0] if d else None

    def warehouses(self):
        return self._docs("SELECT doc FROM warehouses ORDER BY warehouse_code")

    def stock(self, item_code=None, warehouse=None):
        sql, args = "SELECT * FROM stock WHERE 1=1", []
        if item_code:
            sql += " AND item_code = ? COLLATE NOCASE"; args.append(item_code)
        if warehouse:
            sql += " AND warehouse_code = ? COLLATE NOCASE"; args.append(warehouse)
        with self.lock:
            rows = self.con.execute(sql + " ORDER BY item_code, warehouse_code", args).fetchall()
        return [dict(ItemCode=r["item_code"], WarehouseCode=r["warehouse_code"], InStock=r["in_stock"],
                     Committed=r["committed"], Ordered=r["ordered"]) for r in rows]

    def orders(self):
        return self._docs("SELECT doc FROM orders ORDER BY doc_entry")

    def order_by_num(self, doc_num):
        d = self._docs("SELECT doc FROM orders WHERE doc_num = ?", (doc_num,))
        return d[0] if d else None

    def invoices(self):
        return self._docs("SELECT doc FROM invoices ORDER BY doc_entry")

    def add_draft(self, doc, created_at):
        with self.lock:
            cur = self.con.execute("INSERT INTO drafts (doc_object_code, card_code, created_at, doc) VALUES (?, ?, ?, '{}')",
                                   (doc["DocObjectCode"], doc["CardCode"], created_at))
            entry = cur.lastrowid
            doc = dict(doc, DocEntry=entry)
            self.con.execute("UPDATE drafts SET doc = ? WHERE doc_entry = ?", (json.dumps(doc), entry))
        return doc

    def drafts(self):
        return self._docs("SELECT doc FROM drafts ORDER BY doc_entry")
