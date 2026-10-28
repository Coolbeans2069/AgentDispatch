"""Deterministic generator for the synthetic dataset.

Every name, number and date comes from one seeded random generator, so the same seed always gives the same
database. All company names are obviously fake and end in "(demo)". Nothing here is real data."""
import json
import random
import sqlite3

SEED = 20261028
AS_OF = "2026-09-30"          # the dataset's "today": overdue and period questions are answered relative to it
WAREHOUSES = [("PER", "Perth (demo)"), ("SYD", "Sydney (demo)"), ("MEL", "Melbourne (demo)"), ("BNE", "Brisbane (demo)")]
GROUPS = {100: "Pumps", 101: "Valves", 102: "Fittings", 103: "Pipe and hose", 104: "Tools", 105: "Electrical", 106: "Services"}

_FAKE_PREFIX = ["Acme", "Example", "Sample", "Placeholder", "Fictional", "Mock", "Dummy", "Testline", "Imaginary", "Pretend"]
_CUSTOMER_TRADE = ["Plumbing", "Irrigation", "Builders", "Hardware Co", "Garden Supplies", "Facilities", "Electrical",
                   "Water Services", "Maintenance", "Property Group", "Farm Supplies", "Fitouts"]
_SUPPLIER_TRADE = ["Pump Supplies", "Valve Works", "Fittings Wholesale", "Pipe Traders", "Tool Distributors",
                   "Electrical Wholesale", "Industrial Supply"]
_ITEMS = {
    100: [("centrifugal pump", "kW", [0.37, 0.55, 0.75, 1.1, 1.5, 2.2]), ("submersible pump", "kW", [0.25, 0.5, 0.75, 1.0]),
          ("pressure pump", "kW", [0.6, 0.8, 1.2])],
    101: [("ball valve", "mm", [15, 20, 25, 32, 40, 50]), ("gate valve", "mm", [20, 25, 50, 80]), ("check valve", "mm", [15, 25, 40])],
    102: [("brass elbow", "mm", [15, 20, 25]), ("poly tee", "mm", [20, 25, 32, 40]), ("hose clamp pack", "mm", [16, 25, 38]),
          ("threaded nipple", "mm", [15, 20, 25])],
    103: [("PVC pipe 6 m", "mm", [20, 25, 40, 50, 90]), ("poly pipe 50 m roll", "mm", [19, 25, 32]), ("garden hose 30 m", "mm", [12, 18])],
    104: [("pipe cutter", "mm", [42, 63]), ("adjustable wrench", "mm", [200, 300]), ("thread tape pack", "pcs", [10, 50])],
    105: [("pressure switch", "kPa", [140, 280]), ("float switch", "m cable", [3, 10]), ("pump controller", "A", [10, 16])],
    106: [("installation labour hour", "", [1]), ("delivery charge", "", [1])],
}


def _rng(seed):
    return random.Random(seed)


def _money(x):
    return round(x + 1e-9, 2)


def _fake_names(r, trades, n):
    names, seen = [], set()
    while len(names) < n:
        name = "%s %s Pty Ltd (demo)" % (r.choice(_FAKE_PREFIX), r.choice(trades))
        if name not in seen:
            seen.add(name)
            names.append(name)
    return names


def build(seed=SEED):
    """Return the whole dataset as plain Python objects (Service Layer-like dicts)."""
    r = _rng(seed)
    suppliers = []
    for i, name in enumerate(_fake_names(r, _SUPPLIER_TRADE, 15), 1):
        suppliers.append(dict(CardCode="S%04d" % i, CardName=name, CardType="cSupplier", Currency="AUD",
                              PayTermsGrpCode=r.choice([1, 2]), CreditLimit=0.0, CurrentAccountBalance=0.0))
    customers = []
    for i, name in enumerate(_fake_names(r, _CUSTOMER_TRADE, 40), 1):
        customers.append(dict(CardCode="C%04d" % i, CardName=name, CardType="cCustomer", Currency="AUD",
                              PayTermsGrpCode=r.choice([1, 2, 3]), CreditLimit=float(r.choice([5000, 10000, 20000, 50000])),
                              CurrentAccountBalance=0.0))
    terms_days = {1: 30, 2: 14, 3: 7}

    # 120 items: walk the catalogue round-robin so every group is represented
    variants = []
    for g, families in _ITEMS.items():
        for fam, unit, sizes in families:
            for s in sizes:
                variants.append((g, fam, unit, s))
    r.shuffle(variants)
    items = []
    code = 1000
    while len(items) < 120:
        g, fam, unit, s = variants[len(items) % len(variants)]
        rev = len(items) // len(variants)
        size = ("%g %s" % (s, unit)).strip() if unit else ""
        name = "Demo %s%s%s" % (fam, (" " + size) if size else "", (" mk%d" % (rev + 1)) if rev else "")
        service = g == 106
        base = {100: 450, 101: 40, 102: 6, 103: 25, 104: 35, 105: 90, 106: 95}[g]
        price = _money(base * r.uniform(0.6, 2.2))
        items.append(dict(ItemCode="A%d" % code, ItemName=name, ItemsGroupCode=g, InventoryUOM="" if service else "EA",
                          PurchaseItem="tNO" if service else "tYES", SalesItem="tYES", InventoryItem="tNO" if service else "tYES",
                          MinInventory=0.0 if service else float(r.choice([5, 10, 15, 20, 30, 50])),
                          PreferredVendor=None if service else r.choice(suppliers)["CardCode"],
                          ItemPrices=[dict(PriceList=1, PriceListName="Sell (demo)", Price=price),
                                      dict(PriceList=2, PriceListName="Last purchase (demo)", Price=_money(price * r.uniform(0.5, 0.7)))]))
        code += 1
    # the headline item is a pump, so "what's our stock of A1000 in Perth" reads naturally
    pumps = [i for i in items if i["ItemsGroupCode"] == 100]
    hi = items.index(pumps[0])
    items[0], items[hi] = items[hi], items[0]
    for n, it in enumerate(items):
        it["ItemCode"] = "A%d" % (1000 + n)

    stock = []
    for it in items:
        for wh, _ in WAREHOUSES:
            if it["InventoryItem"] == "tNO":
                continue
            mn = it["MinInventory"]
            on_hand = float(r.randint(int(mn * 1.5) + 5, int(mn * 6) + 20))
            committed = float(r.randint(0, int(on_hand * 0.4)))
            ordered = float(r.choice([0, 0, 0, r.randint(5, 40)]))
            stock.append(dict(ItemCode=it["ItemCode"], WarehouseCode=wh, InStock=on_hand, Committed=committed, Ordered=ordered))
    # a few items deliberately below their reorder point in Perth (available = InStock - Committed + Ordered)
    stocked = [it for it in items if it["InventoryItem"] == "tYES"]
    low = [stocked[0]] + r.sample(stocked[1:], 5)
    for it in low:
        row = next(s for s in stock if s["ItemCode"] == it["ItemCode"] and s["WarehouseCode"] == "PER")
        mn = it["MinInventory"]
        row["InStock"] = float(r.randint(0, max(1, int(mn * 0.6))))
        row["Committed"] = float(r.randint(0, int(row["InStock"] * 0.5)))
        row["Ordered"] = 0.0

    # 300 sales orders between 2026-01-05 and AS_OF; 250 of them are invoiced
    import datetime as dt
    start, end = dt.date(2026, 1, 5), dt.date.fromisoformat(AS_OF)
    span = (end - start).days
    orders = []
    for n in range(300):
        cust = r.choice(customers)
        d = start + dt.timedelta(days=r.randint(0, span))
        lines = []
        for ln, it in enumerate(r.sample(items, r.randint(1, 4))):
            qty = float(r.randint(1, 3) if it["ItemsGroupCode"] in (100, 106) else r.randint(2, 40))
            price = it["ItemPrices"][0]["Price"]
            lines.append(dict(LineNum=ln, ItemCode=it["ItemCode"], ItemDescription=it["ItemName"], Quantity=qty, Price=price,
                              LineTotal=_money(qty * price), WarehouseCode=r.choice(["PER", "PER", "SYD", "MEL", "BNE"])))
        orders.append(dict(DocEntry=0, DocNum=0, CardCode=cust["CardCode"], CardName=cust["CardName"], DocDate=d.isoformat(),
                           DocDueDate=(d + dt.timedelta(days=r.randint(3, 21))).isoformat(), DocStatus="bost_Open",
                           DocCurrency="AUD", DocTotal=_money(sum(l["LineTotal"] for l in lines)), DocumentLines=lines))
    orders.sort(key=lambda o: o["DocDate"])
    for n, o in enumerate(orders):
        o["DocEntry"], o["DocNum"] = n + 1, 5000 + n + 1
    # invoice the 250 oldest orders except a handful, so recent orders stay open
    invoiced = orders[:245] + r.sample(orders[245:], 5)
    invoiced.sort(key=lambda o: o["DocEntry"])
    invoices = []
    for n, o in enumerate(invoiced):
        o["DocStatus"] = "bost_Close"
        cust = next(c for c in customers if c["CardCode"] == o["CardCode"])
        d = min(dt.date.fromisoformat(o["DocDate"]) + dt.timedelta(days=r.randint(0, 10)), end)
        due = d + dt.timedelta(days=terms_days[cust["PayTermsGrpCode"]])
        age = (end - d).days
        roll = r.random()
        if age > 75 or roll < 0.55:
            paid = o["DocTotal"] if roll < 0.93 else _money(o["DocTotal"] * 0.5)
        elif roll < 0.75:
            paid = _money(o["DocTotal"] * r.choice([0.25, 0.5]))
        else:
            paid = 0.0
        invoices.append(dict(DocEntry=n + 1, DocNum=8000 + n + 1, CardCode=o["CardCode"], CardName=o["CardName"],
                             DocDate=d.isoformat(), DocDueDate=due.isoformat(), DocStatus="bost_Open" if paid < o["DocTotal"] else "bost_Close",
                             DocCurrency="AUD", DocTotal=o["DocTotal"], PaidToDate=paid, BaseEntry=o["DocEntry"],
                             DocumentLines=[dict(l, BaseEntry=o["DocEntry"], BaseLine=l["LineNum"]) for l in o["DocumentLines"]]))
    for c in customers:
        c["CurrentAccountBalance"] = _money(sum(i["DocTotal"] - i["PaidToDate"] for i in invoices if i["CardCode"] == c["CardCode"]))
    return dict(meta=dict(_synthetic=True, seed=seed, as_of=AS_OF, note="Synthetic demo data. Not real companies, people or transactions."),
                business_partners=suppliers + customers, items=items, warehouses=[dict(WarehouseCode=c, WarehouseName=n) for c, n in WAREHOUSES],
                stock=stock, orders=orders, invoices=invoices)


SCHEMA = """
CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE business_partners (card_code TEXT PRIMARY KEY, card_type TEXT NOT NULL, card_name TEXT NOT NULL, doc TEXT NOT NULL);
CREATE TABLE items (item_code TEXT PRIMARY KEY, item_name TEXT NOT NULL, group_code INTEGER NOT NULL, doc TEXT NOT NULL);
CREATE TABLE warehouses (warehouse_code TEXT PRIMARY KEY, doc TEXT NOT NULL);
CREATE TABLE stock (item_code TEXT NOT NULL, warehouse_code TEXT NOT NULL, in_stock REAL NOT NULL, committed REAL NOT NULL,
                    ordered REAL NOT NULL, PRIMARY KEY (item_code, warehouse_code));
CREATE TABLE orders (doc_entry INTEGER PRIMARY KEY, doc_num INTEGER UNIQUE NOT NULL, card_code TEXT NOT NULL, doc_date TEXT NOT NULL,
                     doc_status TEXT NOT NULL, doc TEXT NOT NULL);
CREATE TABLE invoices (doc_entry INTEGER PRIMARY KEY, doc_num INTEGER UNIQUE NOT NULL, card_code TEXT NOT NULL, doc_date TEXT NOT NULL,
                       base_entry INTEGER, doc TEXT NOT NULL);
CREATE TABLE drafts (doc_entry INTEGER PRIMARY KEY AUTOINCREMENT, doc_object_code TEXT NOT NULL, card_code TEXT NOT NULL,
                     created_at TEXT NOT NULL, doc TEXT NOT NULL);
CREATE TABLE approval_tokens (token_hash TEXT PRIMARY KEY, payload_hash TEXT NOT NULL, expires_at REAL NOT NULL, state TEXT NOT NULL);
CREATE TABLE audit_log (id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT NOT NULL, tool TEXT NOT NULL, args TEXT NOT NULL,
                        result_hash TEXT NOT NULL, approval TEXT NOT NULL, caller TEXT NOT NULL, ok INTEGER NOT NULL);
CREATE TRIGGER audit_no_update BEFORE UPDATE ON audit_log BEGIN SELECT RAISE(ABORT, 'audit_log is append-only'); END;
CREATE TRIGGER audit_no_delete BEFORE DELETE ON audit_log BEGIN SELECT RAISE(ABORT, 'audit_log is append-only'); END;
"""


def write_db(path, seed=SEED):
    """Create a fresh database at `path` from the seeded generator."""
    data = build(seed)
    con = sqlite3.connect(path)
    try:
        con.executescript(SCHEMA)
        con.executemany("INSERT INTO meta VALUES (?, ?)", [(k, json.dumps(v)) for k, v in data["meta"].items()])
        con.executemany("INSERT INTO business_partners VALUES (?, ?, ?, ?)",
                        [(b["CardCode"], b["CardType"], b["CardName"], json.dumps(b)) for b in data["business_partners"]])
        con.executemany("INSERT INTO items VALUES (?, ?, ?, ?)",
                        [(i["ItemCode"], i["ItemName"], i["ItemsGroupCode"], json.dumps(i)) for i in data["items"]])
        con.executemany("INSERT INTO warehouses VALUES (?, ?)", [(w["WarehouseCode"], json.dumps(w)) for w in data["warehouses"]])
        con.executemany("INSERT INTO stock VALUES (?, ?, ?, ?, ?)",
                        [(s["ItemCode"], s["WarehouseCode"], s["InStock"], s["Committed"], s["Ordered"]) for s in data["stock"]])
        con.executemany("INSERT INTO orders VALUES (?, ?, ?, ?, ?, ?)",
                        [(o["DocEntry"], o["DocNum"], o["CardCode"], o["DocDate"], o["DocStatus"], json.dumps(o)) for o in data["orders"]])
        con.executemany("INSERT INTO invoices VALUES (?, ?, ?, ?, ?, ?)",
                        [(i["DocEntry"], i["DocNum"], i["CardCode"], i["DocDate"], i["BaseEntry"], json.dumps(i)) for i in data["invoices"]])
        con.commit()
    finally:
        con.close()
    return data["meta"]
