"""MCP server over the tools, using the official `mcp` Python SDK (optional extra: pip install "mcp>=1.2").

Works with SDK 1.x (FastMCP) and 2.x (MCPServer). Default transport is stdio."""
from typing import Any, Optional

from .tools import B1Tools, ToolError

INSTRUCTIONS = ("Synthetic demo data shaped like SAP Business One Service Layer objects (unofficial; not affiliated with SAP SE; "
                "no SAP software is involved). Read tools have no side effects. create_draft_purchase_order is the only write: "
                "call it once to get a preview and an approval_token, show the preview to the person, and only after they "
                "say yes call it again with the same arguments plus approval_token and approve=true.")


def _sdk():
    try:
        from mcp.server.mcpserver import MCPServer as Server   # mcp 2.x
    except ImportError:
        try:
            from mcp.server.fastmcp import FastMCP as Server   # mcp 1.x
        except ImportError:
            raise SystemExit('The MCP server needs the official SDK: pip install "mcp>=1.2" (the core and its tests do not).')
    try:
        from mcp.types import ToolAnnotations
    except ImportError:
        ToolAnnotations = None
    try:
        from mcp.server.mcpserver.exceptions import ToolError as SdkToolError   # 2.x: only ToolError messages reach the client
    except ImportError:
        try:
            from mcp.server.fastmcp.exceptions import ToolError as SdkToolError
        except ImportError:
            SdkToolError = ValueError
    return Server, ToolAnnotations, SdkToolError


def build_server(tools=None):
    Server, ToolAnnotations, SdkToolError = _sdk()
    t = tools or B1Tools()
    srv = Server("b1demo", instructions=INSTRUCTIONS)

    def ann(read_only):
        if ToolAnnotations is None:
            return {}
        return dict(annotations=ToolAnnotations(readOnlyHint=read_only, destructiveHint=False, idempotentHint=read_only, openWorldHint=False))

    def run(name, **kw):
        try:
            return t.call(name, {k: v for k, v in kw.items() if v is not None})
        except ToolError as e:
            raise SdkToolError(str(e)) from None

    @srv.tool(**ann(True))
    def search_business_partners(query: str = "", type: Optional[str] = None, limit: int = 20) -> dict:
        """Find customers or suppliers by name or CardCode. type: 'customer' or 'supplier'."""
        return run("search_business_partners", query=query, type=type, limit=limit)

    @srv.tool(**ann(True))
    def get_business_partner(card_code: str) -> dict:
        """Details for one business partner: balance, credit limit, open orders count."""
        return run("get_business_partner", card_code=card_code)

    @srv.tool(**ann(True))
    def search_items(query: str = "", group: Optional[int] = None, limit: int = 20) -> dict:
        """Find items by ItemCode or name; group is an item group code (100 pumps, 101 valves, 102 fittings, 103 pipe, 104 tools, 105 electrical, 106 services)."""
        return run("search_items", query=query, group=group, limit=limit)

    @srv.tool(**ann(True))
    def get_stock(item: str, warehouse: Optional[str] = None) -> dict:
        """On hand, committed, ordered and available per warehouse (PER, SYD, MEL, BNE), with a below-reorder-point flag."""
        return run("get_stock", item=item, warehouse=warehouse)

    @srv.tool(**ann(True))
    def low_stock(warehouse: Optional[str] = None, limit: int = 20) -> dict:
        """Items whose available quantity is below their reorder point (MinInventory)."""
        return run("low_stock", warehouse=warehouse, limit=limit)

    @srv.tool(**ann(True))
    def list_sales_orders(card_code: Optional[str] = None, status: Optional[str] = None, from_date: Optional[str] = None,
                          to_date: Optional[str] = None, limit: int = 20) -> dict:
        """Sales orders with totals; status 'open' or 'closed'; dates as YYYY-MM-DD."""
        return run("list_sales_orders", card_code=card_code, status=status, from_date=from_date, to_date=to_date, limit=limit)

    @srv.tool(**ann(True))
    def get_sales_order(doc_num: int) -> dict:
        """One sales order (header and lines) by DocNum."""
        return run("get_sales_order", doc_num=doc_num)

    @srv.tool(**ann(True))
    def list_invoices(card_code: Optional[str] = None, overdue_only: bool = False, limit: int = 20) -> dict:
        """A/R invoices with paid and outstanding amounts; overdue is judged against the dataset date."""
        return run("list_invoices", card_code=card_code, overdue_only=overdue_only, limit=limit)

    @srv.tool(**ann(True))
    def sales_summary(period: str = "this_quarter", by: str = "customer", limit: int = 5) -> dict:
        """Sales totals for a period (2026, 2026-Q3, 2026-09, this_quarter, last_quarter, this_month, this_year) by 'customer' or 'item'."""
        return run("sales_summary", period=period, by=by, limit=limit)

    @srv.tool(**ann(True))
    def audit_tail(n: int = 20) -> dict:
        """The last n audit log entries: every tool call this server handled, with redacted arguments and approval state."""
        return run("audit_tail", n=n)

    @srv.tool(**ann(False))
    def create_draft_purchase_order(supplier: str, lines: list[dict[str, Any]], due_date: str, note: str = "",
                                    approval_token: Optional[str] = None, approve: bool = False) -> dict:
        """Raise a DRAFT purchase order (never posted). lines: [{item_code, quantity, warehouse?, price?}].
        First call (no approval_token) only returns a preview and a single-use approval_token valid for 10 minutes.
        Show the preview to the person. Only after they explicitly approve, call again with identical arguments plus
        approval_token and approve=true. A changed request needs a new preview. Disabled when B1DEMO_READ_ONLY=1."""
        return run("create_draft_purchase_order", supplier=supplier, lines=lines, due_date=due_date, note=note,
                   approval_token=approval_token, approve=approve)

    return srv


def serve(transport="stdio", tools=None):
    build_server(tools).run(transport)
