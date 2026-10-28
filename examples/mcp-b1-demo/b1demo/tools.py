"""The MCP tools as plain Python: nine read tools, `audit_tail`, and the approval-gated `create_draft_purchase_order`.

Every call goes through `B1Tools.call`, which validates, runs the tool and appends an audit entry, including for
errors. Results are compact JSON with a one-line `summary` and a `_synthetic: true` marker."""
import datetime as dt
import os

from .approval import Approvals, ApprovalError
from .audit import AuditLog
from .repo import SQLiteRepository

MAX_LIMIT = 100


class ToolError(Exception):
    pass


def _avail(s):
    return s["InStock"] - s["Committed"] + s["Ordered"]


def _card_type(t):
    if t in (None, ""):
        return None
    t = str(t).lower()
    if t in ("c", "customer", "ccustomer"):
        return "cCustomer"
    if t in ("s", "supplier", "csupplier", "vendor"):
        return "cSupplier"
    raise ToolError("type must be 'customer' or 'supplier'")


def _limit(v, default=20):
    try:
        v = int(default if v in (None, "") else v)
    except (TypeError, ValueError):
        raise ToolError("limit must be a whole number")
    return max(1, min(v, MAX_LIMIT))


def _date(v, name):
    if v in (None, ""):
        return None
    try:
        return dt.date.fromisoformat(str(v)).isoformat()
    except ValueError:
        raise ToolError("%s must be a date like 2026-09-30" % name)


def _money(x):
    return round(x + 1e-9, 2)


def _match(q, *fields):
    q = (q or "").strip().lower()
    return not q or any(q in str(f or "").lower() for f in fields)


class B1Tools:
    READ_TOOLS = ("search_business_partners", "get_business_partner", "search_items", "get_stock", "low_stock",
                  "list_sales_orders", "get_sales_order", "list_invoices", "sales_summary", "audit_tail")
    WRITE_TOOLS = ("create_draft_purchase_order",)

    def __init__(self, repo=None, read_only=None, caller=None, clock=None):
        self.repo = repo or SQLiteRepository()
        self.audit = AuditLog(self.repo)
        self.approvals = Approvals(self.repo, **({"clock": clock} if clock else {}))
        self.read_only = (os.environ.get("B1DEMO_READ_ONLY", "") not in ("", "0", "false", "no")) if read_only is None else bool(read_only)
        self.caller = caller or os.environ.get("B1DEMO_CALLER") or "mcp-client"
        self.as_of = self.repo.meta()["as_of"]

    # ---------- dispatch + audit ----------
    def names(self):
        return list(self.READ_TOOLS) + list(self.WRITE_TOOLS)

    def call(self, name, args=None):
        args = dict(args or {})
        if name not in self.names():
            raise ToolError("unknown tool %r" % name)
        approval = "n/a"
        try:
            if name == "create_draft_purchase_order":
                result, approval = self._create_draft_po(**args)
            else:
                result = getattr(self, name)(**args)
        except TypeError as e:
            err = dict(error="bad arguments: %s" % e)
            self.audit.record(name, args, err, approval="n/a" if name in self.READ_TOOLS else "rejected", caller=self.caller, ok=False)
            raise ToolError(err["error"])
        except (ToolError, ApprovalError) as e:
            err = dict(error=str(e))
            if name in self.WRITE_TOOLS:
                approval = "disabled" if self.read_only else "rejected: %s" % e
            self.audit.record(name, args, err, approval=approval, caller=self.caller, ok=False)
            raise ToolError(str(e))
        result = dict(result, _synthetic=True)
        self.audit.record(name, args, result, approval=approval, caller=self.caller, ok=True)
        return result

    # ---------- read tools ----------
    def search_business_partners(self, query="", type=None, limit=20):
        ct, lim = _card_type(type), _limit(limit)
        hits = [b for b in self.repo.business_partners() if (ct is None or b["CardType"] == ct) and _match(query, b["CardCode"], b["CardName"])]
        rows = [dict(CardCode=b["CardCode"], CardName=b["CardName"], CardType=b["CardType"], CurrentAccountBalance=b["CurrentAccountBalance"]) for b in hits[:lim]]
        return dict(summary="%d business partner(s) match%s" % (len(hits), "" if len(hits) <= lim else ", showing %d" % lim), count=len(hits), value=rows)

    def get_business_partner(self, card_code):
        b = self.repo.business_partner(str(card_code))
        if not b:
            raise ToolError("no business partner %r" % card_code)
        open_orders = [o for o in self.repo.orders() if o["CardCode"] == b["CardCode"] and o["DocStatus"] == "bost_Open"]
        out = dict(b, OpenOrdersCount=len(open_orders))
        if b["CardType"] == "cCustomer":
            out["CreditAvailable"] = _money(b["CreditLimit"] - b["CurrentAccountBalance"])
        return dict(summary="%s %s: balance %.2f %s, %d open order(s)" % (b["CardCode"], b["CardName"], b["CurrentAccountBalance"], b["Currency"], len(open_orders)), value=out)

    def search_items(self, query="", group=None, limit=20):
        lim = _limit(limit)
        if group not in (None, ""):
            try:
                group = int(group)
            except (TypeError, ValueError):
                raise ToolError("group must be an item group code such as 100")
        hits = [i for i in self.repo.items() if (group in (None, "") or i["ItemsGroupCode"] == group) and _match(query, i["ItemCode"], i["ItemName"])]
        rows = [dict(ItemCode=i["ItemCode"], ItemName=i["ItemName"], ItemsGroupCode=i["ItemsGroupCode"], PurchaseItem=i["PurchaseItem"],
                     MinInventory=i["MinInventory"], Price=i["ItemPrices"][0]["Price"]) for i in hits[:lim]]
        return dict(summary="%d item(s) match%s" % (len(hits), "" if len(hits) <= lim else ", showing %d" % lim), count=len(hits), value=rows)

    def _item_or_error(self, item):
        it = self.repo.item(str(item))
        if not it:
            hits = [i for i in self.repo.items() if _match(str(item), i["ItemName"])]
            if len(hits) == 1:
                it = hits[0]
            elif hits:
                raise ToolError("%r matches %d items; use an ItemCode (e.g. %s)" % (item, len(hits), ", ".join(h["ItemCode"] for h in hits[:5])))
            else:
                raise ToolError("no item %r" % item)
        return it

    def _warehouse(self, warehouse):
        if warehouse in (None, ""):
            return None
        codes = [w["WarehouseCode"] for w in self.repo.warehouses()]
        w = str(warehouse).upper()
        if w not in codes:
            raise ToolError("warehouse must be one of %s" % ", ".join(codes))
        return w

    def get_stock(self, item, warehouse=None):
        it, wh = self._item_or_error(item), self._warehouse(warehouse)
        if it["InventoryItem"] != "tYES":
            raise ToolError("%s %s is not a stocked item" % (it["ItemCode"], it["ItemName"]))
        rows = []
        for s in self.repo.stock(it["ItemCode"], wh):
            a = _avail(s)
            rows.append(dict(s, Available=a, MinInventory=it["MinInventory"], BelowReorderPoint=a < it["MinInventory"]))
        low = [r["WarehouseCode"] for r in rows if r["BelowReorderPoint"]]
        if wh and rows:
            r = rows[0]
            summary = "%s %s in %s: %g on hand, %g committed, %g ordered, %g available (reorder point %g)%s" % (
                it["ItemCode"], it["ItemName"], wh, r["InStock"], r["Committed"], r["Ordered"], r["Available"], it["MinInventory"],
                "; BELOW reorder point, preferred supplier %s" % it["PreferredVendor"] if low else "")
        else:
            summary = "%s %s: %g available across %d warehouse(s)%s" % (it["ItemCode"], it["ItemName"], sum(r["Available"] for r in rows), len(rows),
                                                                      "; below reorder point in %s" % ", ".join(low) if low else "")
        return dict(summary=summary, ItemCode=it["ItemCode"], ItemName=it["ItemName"], PreferredVendor=it["PreferredVendor"], ItemWarehouseInfoCollection=rows)

    def low_stock(self, warehouse=None, limit=20):
        wh, lim = self._warehouse(warehouse), _limit(limit)
        items = {i["ItemCode"]: i for i in self.repo.items()}
        rows = []
        for s in self.repo.stock(None, wh):
            it = items[s["ItemCode"]]
            a = _avail(s)
            if a < it["MinInventory"]:
                rows.append(dict(ItemCode=it["ItemCode"], ItemName=it["ItemName"], WarehouseCode=s["WarehouseCode"], Available=a,
                                 MinInventory=it["MinInventory"], Shortfall=it["MinInventory"] - a, PreferredVendor=it["PreferredVendor"]))
        rows.sort(key=lambda r: (-r["Shortfall"], r["ItemCode"]))
        return dict(summary="%d item/warehouse pair(s) below the reorder point%s" % (len(rows), " in " + wh if wh else ""), count=len(rows), value=rows[:lim])

    def _order_row(self, o):
        return dict(DocEntry=o["DocEntry"], DocNum=o["DocNum"], CardCode=o["CardCode"], CardName=o["CardName"], DocDate=o["DocDate"],
                    DocDueDate=o["DocDueDate"], DocStatus=o["DocStatus"], DocTotal=o["DocTotal"])

    def list_sales_orders(self, card_code=None, status=None, from_date=None, to_date=None, limit=20):
        lim, f, t = _limit(limit), _date(from_date, "from_date"), _date(to_date, "to_date")
        st = {None: None, "": None, "open": "bost_Open", "closed": "bost_Close", "o": "bost_Open", "c": "bost_Close"}.get(str(status).lower() if status else None, "?")
        if st == "?":
            raise ToolError("status must be 'open' or 'closed'")
        hits = [o for o in self.repo.orders() if (not card_code or o["CardCode"].lower() == str(card_code).lower())
                and (st is None or o["DocStatus"] == st) and (not f or o["DocDate"] >= f) and (not t or o["DocDate"] <= t)]
        hits.sort(key=lambda o: o["DocDate"], reverse=True)
        total = _money(sum(o["DocTotal"] for o in hits))
        return dict(summary="%d order(s), total %.2f AUD%s" % (len(hits), total, "" if len(hits) <= lim else ", newest %d shown" % lim),
                    count=len(hits), total=total, value=[self._order_row(o) for o in hits[:lim]])

    def get_sales_order(self, doc_num):
        try:
            o = self.repo.order_by_num(int(doc_num))
        except (TypeError, ValueError):
            raise ToolError("doc_num must be a number such as 5001")
        if not o:
            raise ToolError("no sales order %s" % doc_num)
        return dict(summary="Order %d for %s on %s: %d line(s), %.2f AUD, %s" % (o["DocNum"], o["CardName"], o["DocDate"], len(o["DocumentLines"]),
                                                                              o["DocTotal"], "open" if o["DocStatus"] == "bost_Open" else "closed"), value=o)

    def list_invoices(self, card_code=None, overdue_only=False, limit=20):
        lim = _limit(limit)
        rows = []
        for i in self.repo.invoices():
            if card_code and i["CardCode"].lower() != str(card_code).lower():
                continue
            outstanding = _money(i["DocTotal"] - i["PaidToDate"])
            overdue = outstanding > 0 and i["DocDueDate"] < self.as_of
            if overdue_only and not overdue:
                continue
            rows.append(dict(DocNum=i["DocNum"], CardCode=i["CardCode"], CardName=i["CardName"], DocDate=i["DocDate"], DocDueDate=i["DocDueDate"],
                             DocTotal=i["DocTotal"], PaidToDate=i["PaidToDate"], Outstanding=outstanding, Overdue=overdue, BaseEntry=i["BaseEntry"]))
        rows.sort(key=lambda r: r["DocDueDate"])
        out = _money(sum(r["Outstanding"] for r in rows))
        return dict(summary="%d invoice(s), %.2f AUD outstanding (as of %s)" % (len(rows), out, self.as_of), as_of=self.as_of, count=len(rows),
                    outstanding=out, value=rows[:lim])

    def _period(self, period):
        p = str(period or "").strip().lower()
        y, m = int(self.as_of[:4]), int(self.as_of[5:7])
        q = (m - 1) // 3 + 1
        if p in ("this_quarter", "quarter"):
            p = "%d-q%d" % (y, q)
        elif p == "last_quarter":
            p = "%d-q%d" % ((y, q - 1) if q > 1 else (y - 1, 4))
        elif p in ("this_month", "month"):
            p = self.as_of[:7]
        elif p in ("this_year", "year", "ytd"):
            p = str(y)
        try:
            if len(p) == 4 and p.isdigit():
                return "%s-01-01" % p, "%s-12-31" % p, p
            if len(p) == 7 and p[4] == "-" and p[5] == "q":
                yy, qq = int(p[:4]), int(p[6])
                if not 1 <= qq <= 4:
                    raise ValueError
                start = dt.date(yy, 3 * qq - 2, 1)
                end = (dt.date(yy + (qq == 4), 1 if qq == 4 else 3 * qq + 1, 1) - dt.timedelta(days=1))
                return start.isoformat(), end.isoformat(), "%d-Q%d" % (yy, qq)
            if len(p) == 7 and p[4] == "-":
                start = dt.date(int(p[:4]), int(p[5:7]), 1)
                end = (dt.date(start.year + (start.month == 12), 1 if start.month == 12 else start.month + 1, 1) - dt.timedelta(days=1))
                return start.isoformat(), end.isoformat(), p
        except ValueError:
            pass
        raise ToolError("period must be like 2026, 2026-Q3, 2026-09, this_quarter, last_quarter, this_month or this_year")

    def sales_summary(self, period="this_quarter", by="customer", limit=5):
        start, end, label = self._period(period)
        if by not in ("item", "customer"):
            raise ToolError("by must be 'item' or 'customer'")
        lim = _limit(limit, 5)
        agg = {}
        for o in self.repo.orders():
            if not start <= o["DocDate"] <= end:
                continue
            if by == "customer":
                a = agg.setdefault(o["CardCode"], dict(CardCode=o["CardCode"], CardName=o["CardName"], Orders=0, Total=0.0))
                a["Orders"] += 1; a["Total"] += o["DocTotal"]
            else:
                for l in o["DocumentLines"]:
                    a = agg.setdefault(l["ItemCode"], dict(ItemCode=l["ItemCode"], ItemName=l["ItemDescription"], Quantity=0.0, Total=0.0))
                    a["Quantity"] += l["Quantity"]; a["Total"] += l["LineTotal"]
        rows = sorted(agg.values(), key=lambda a: (-a["Total"], a.get("CardCode") or a.get("ItemCode")))
        for a in rows:
            a["Total"] = _money(a["Total"])
        grand = _money(sum(a["Total"] for a in rows))
        top = rows[:lim]
        lead = (" top: %s %.2f" % (top[0].get("CardName") or top[0].get("ItemName"), top[0]["Total"])) if top else ""
        return dict(summary="Sales %s (%s to %s) by %s: %.2f AUD ex tax;%s" % (label, start, end, by, grand, lead or " no orders"),
                    period=label, start=start, end=end, by=by, total=grand, value=top)

    def audit_tail(self, n=20):
        try:
            n = int(n)
        except (TypeError, ValueError):
            raise ToolError("n must be a whole number")
        rows = self.audit.tail(n)
        return dict(summary="last %d audit entr%s" % (len(rows), "y" if len(rows) == 1 else "ies"), value=rows)

    # ---------- the one write tool ----------
    def _normalise_po(self, supplier, lines, due_date, note):
        sup = self.repo.business_partner(str(supplier or ""))
        if not sup:
            hits = [b for b in self.repo.business_partners() if b["CardType"] == "cSupplier" and _match(str(supplier or "-"), b["CardName"])]
            if len(hits) != 1:
                raise ToolError("supplier %r not found; use a supplier CardCode such as S0001" % supplier)
            sup = hits[0]
        if sup["CardType"] != "cSupplier":
            raise ToolError("%s is a customer, not a supplier" % sup["CardCode"])
        if not isinstance(lines, list) or not lines:
            raise ToolError("lines must be a non-empty list of {item_code, quantity, warehouse?, price?}")
        if len(lines) > 50:
            raise ToolError("at most 50 lines per draft")
        due = _date(due_date, "due_date")
        if not due:
            raise ToolError("due_date is required")
        if due < self.as_of:
            raise ToolError("due_date is before the dataset date %s" % self.as_of)
        note = "" if note is None else str(note)
        if len(note) > 254:
            raise ToolError("note is limited to 254 characters")
        norm = []
        for n, l in enumerate(lines):
            if not isinstance(l, dict):
                raise ToolError("line %d must be an object" % (n + 1))
            it = self.repo.item(str(l.get("item_code") or l.get("ItemCode") or ""))
            if not it:
                raise ToolError("line %d: unknown item %r" % (n + 1, l.get("item_code")))
            if it["PurchaseItem"] != "tYES":
                raise ToolError("line %d: %s is not a purchasable item" % (n + 1, it["ItemCode"]))
            try:
                qty = float(l.get("quantity", l.get("Quantity")))
            except (TypeError, ValueError):
                raise ToolError("line %d: quantity must be a number" % (n + 1))
            if not 0 < qty <= 100000:
                raise ToolError("line %d: quantity must be above zero (and at most 100000)" % (n + 1))
            wh = self._warehouse(l.get("warehouse") or l.get("WarehouseCode") or "PER")
            price = l.get("price", l.get("Price"))
            if price in (None, ""):
                price = it["ItemPrices"][1]["Price"]
            try:
                price = _money(float(price))
            except (TypeError, ValueError):
                raise ToolError("line %d: price must be a number" % (n + 1))
            if price < 0:
                raise ToolError("line %d: price cannot be negative" % (n + 1))
            norm.append(dict(ItemCode=it["ItemCode"], Quantity=qty, WarehouseCode=wh, Price=price))
        return sup, dict(CardCode=sup["CardCode"], DocDueDate=due, Comments=note, DocumentLines=norm)

    def _create_draft_po(self, supplier=None, lines=None, due_date=None, note="", approval_token=None, approve=False):
        if self.read_only:
            raise ToolError("writes are disabled (B1DEMO_READ_ONLY=1)")
        sup, payload = self._normalise_po(supplier, lines, due_date, note)
        items = {l["ItemCode"]: self.repo.item(l["ItemCode"]) for l in payload["DocumentLines"]}
        preview_lines = []
        for l in payload["DocumentLines"]:
            it = items[l["ItemCode"]]
            st = self.repo.stock(l["ItemCode"], l["WarehouseCode"])
            preview_lines.append(dict(l, ItemDescription=it["ItemName"], LineTotal=_money(l["Quantity"] * l["Price"]),
                                      AvailableNow=_avail(st[0]) if st else None, MinInventory=it["MinInventory"],
                                      PreferredVendor=it["PreferredVendor"]))
        total = _money(sum(l["LineTotal"] for l in preview_lines))
        warnings = ["%s's preferred supplier is %s, not %s" % (l["ItemCode"], l["PreferredVendor"], sup["CardCode"])
                    for l in preview_lines if l["PreferredVendor"] and l["PreferredVendor"] != sup["CardCode"]]
        if not approval_token:
            token, expires = self.approvals.issue(payload)
            return dict(summary="PREVIEW ONLY, nothing written: draft purchase order to %s (%s), %d line(s), %.2f AUD, due %s. "
                                "Show this to the person; only if they approve, call again with the same arguments plus approval_token "
                                "and approve=true." % (sup["CardName"], sup["CardCode"], len(preview_lines), total, payload["DocDueDate"]),
                        status="preview", approval_token=token,
                        expires_at=dt.datetime.fromtimestamp(expires, dt.timezone.utc).isoformat(timespec="seconds"),
                        preview=dict(DocObjectCode="oPurchaseOrders", CardCode=sup["CardCode"], CardName=sup["CardName"],
                                     DocDueDate=payload["DocDueDate"], Comments=payload["Comments"], DocTotal=total, DocumentLines=preview_lines),
                        warnings=warnings), "preview"
        if approve is not True:
            self.approvals.decline(approval_token)
            return dict(summary="Not approved: nothing written and the token is cancelled.", status="declined"), "declined"
        self.approvals.consume(approval_token, payload)
        doc = dict(DocObjectCode="oPurchaseOrders", CardCode=sup["CardCode"], CardName=sup["CardName"],
                   DocDate=dt.date.today().isoformat(), DocDueDate=payload["DocDueDate"], Comments=payload["Comments"],
                   DocTotal=total, DocumentLines=[dict(l, LineNum=n) for n, l in enumerate(preview_lines)],
                   _synthetic=True, _approved_by=self.caller)
        for l in doc["DocumentLines"]:
            for k in ("AvailableNow", "MinInventory", "PreferredVendor"):
                l.pop(k, None)
        doc = self.repo.add_draft(doc, dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"))
        return dict(summary="Draft purchase order %d written to Drafts for %s, %.2f AUD. It is a draft only; nothing was posted."
                            % (doc["DocEntry"], sup["CardName"], total), status="written", value=doc), "approved"
