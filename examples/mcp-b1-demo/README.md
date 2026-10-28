# mcp-b1-demo: an MCP server on synthetic ERP-style data

> **Unofficial demo. Not affiliated with, endorsed by or sponsored by SAP SE. Uses synthetic data shaped like SAP Business One Service Layer objects; it does not connect to SAP software.**

This example shows the pattern a small business needs before it lets an AI assistant near its ERP:

- **read tools first**, with no side effects;
- **one write**, a *draft* purchase order, that happens only after a preview and an explicit approval;
- **every call in an audit log** that the business can read.

It runs fully offline on a generated dataset. No real company, person, customer or transaction is in it: every name is an obvious fake ending in "(demo)", and every result carries `"_synthetic": true`. No cloud model is called; the MCP host you connect is the only "AI" involved.

This is a demo of the integration *pattern*, not a certified add-on. A real engagement would also need Service Layer authentication, permissions and testing on the client's own system.

## The headline question

*"What's our stock of A1000 in the Perth warehouse?"*

1. The assistant calls `get_stock(item="A1000", warehouse="PER")` and gets on hand, committed, ordered and available quantities, with a flag: it is below its reorder point.
2. It offers a draft purchase order to the preferred supplier and calls `create_draft_purchase_order(...)`. That first call **writes nothing**; it returns a preview and a single-use `approval_token`.
3. The person says yes. The assistant calls again with the same arguments plus the token and `approve=true`, and the draft is written to `Drafts`.
4. `audit_tail` (or `python -m b1demo audit`) shows all three calls.

Try it without any MCP client:
```bash
cd examples/mcp-b1-demo
python -m b1demo call get_stock '{"item": "A1000", "warehouse": "PER"}'
python -m b1demo call low_stock '{"warehouse": "PER"}'
python -m b1demo call create_draft_purchase_order '{"supplier": "S0011", "lines": [{"item_code": "A1000", "quantity": 30}], "due_date": "2026-11-20"}'
# copy the approval_token from the preview, then:
python -m b1demo call create_draft_purchase_order '{"supplier": "S0011", "lines": [{"item_code": "A1000", "quantity": 30}], "due_date": "2026-11-20", "approval_token": "<token>", "approve": true}'
python -m b1demo audit
```
The data lives in `./b1demo-data/` (or `$B1DEMO_DATA`). It is created on first use; `python -m b1demo seed --force` rebuilds it.

## Tools

| Tool | What it does |
|---|---|
| `search_business_partners(query, type?)` | find customers or suppliers by name or CardCode |
| `get_business_partner(card_code)` | details, balance, credit limit and open orders count |
| `search_items(query, group?)` | find items by code or name |
| `get_stock(item, warehouse?)` | on hand, committed, ordered and available per warehouse, plus the reorder flag |
| `low_stock(warehouse?, limit?)` | items below their reorder point |
| `list_sales_orders(card_code?, status?, from_date?, to_date?)` | open or closed orders with totals |
| `get_sales_order(doc_num)` | header and lines |
| `list_invoices(card_code?, overdue_only?)` | invoices with paid and outstanding amounts |
| `sales_summary(period, by="item" or "customer")` | simple totals, e.g. top 5 customers this quarter |
| `audit_tail(n)` | the last n audit entries |
| `create_draft_purchase_order(supplier, lines, due_date, note, approval_token?, approve?)` | the only write: preview, then an approved draft |

Answers about "overdue" or "this quarter" are relative to the dataset date (2026-09-30), so they stay the same on every run.

## Safety model

- **Preview first.** A call without a token validates the request (the supplier exists and is a supplier, every item exists and is purchasable, quantities are above zero, the due date is valid) and returns a preview. Nothing is written.
- **Single-use token, bound to the payload.** The token is valid for 10 minutes, can be used once, and is tied to a SHA-256 hash of the exact request. Changing a quantity, item, supplier, date or note needs a new preview. Only a hash of the token is stored, and the audit log shows only its first 4 characters.
- **Explicit approval.** The write needs both the token and `approve=true`; `approve=false` cancels the token.
- **A draft only.** The tool writes to `Drafts` (`DocObjectCode = oPurchaseOrders`). It never posts documents, payments or master-data changes.
- **Read-only switch.** `B1DEMO_READ_ONLY=1` turns the write tool off completely.
- **Append-only audit.** Every call, including refused ones, goes to the `audit_log` table (SQLite triggers refuse UPDATE and DELETE) and is mirrored to `audit.jsonl`. Each entry has the time, tool, redacted arguments, a hash of the result, the approval state and the caller label (`B1DEMO_CALLER`).

The token proves that a preview of that exact request was shown. The human "yes" itself comes from your MCP host: set it to ask before every `create_draft_purchase_order` call (the tool is annotated as not read-only, so most hosts ask by default).

## Connect an MCP client

The core is standard library only; the server needs the official SDK (1.x or 2.x):
```bash
pip install "./examples/mcp-b1-demo[mcp]"     # installs the b1demo command and the mcp SDK
b1demo --data /path/to/b1demo-data serve      # stdio; add --transport streamable-http for HTTP
```

Claude Desktop (`claude_desktop_config.json`) or any host that takes the same shape:
```json
{
  "mcpServers": {
    "b1demo": {
      "command": "b1demo",
      "args": ["serve"],
      "env": { "B1DEMO_DATA": "/path/to/b1demo-data", "B1DEMO_CALLER": "claude-desktop" }
    }
  }
}
```
Claude Code: `claude mcp add b1demo -- b1demo serve`. Cursor and other hosts: point them at the same command.

## Data

`b1demo/seed.py` generates everything from one seed (`20261028`), so every rebuild is identical:

| Entity (Service Layer-like) | Rows |
|---|---|
| `BusinessPartners` | 40 customers, 15 suppliers |
| `Items` | 120, including 4 non-purchasable service items |
| `Warehouses` | PER, SYD, MEL, BNE |
| `ItemWarehouseInfoCollection` | `InStock`, `Committed`, `Ordered` per stocked item and warehouse; available = InStock − Committed + Ordered |
| `Orders` | 300, with lines |
| `Invoices` | 250, with `PaidToDate` and `BaseEntry` back to the order |
| `Drafts` | written only by the approved write tool |

A few items are deliberately below their reorder point in Perth, so the demo has something to act on. Totals are simple sums with no tax.

Field names follow the publicly documented Service Layer entity names, so the tools could later sit on a real Service Layer through the same `Repository` interface (`b1demo/repo.py`). `ServiceLayerRepository` is a documented stub that is **not implemented**.

## Tests

```bash
python examples/mcp-b1-demo/tests/run_tests.py
```
The core tests (dataset, every tool, the approval flow including expired, reused, wrong and tampered tokens, read-only mode, and the audit log) need only the standard library. The MCP end-to-end test starts the server over stdio and skips itself when the `mcp` SDK is not installed. Both run in the AgentDispatch CI.

## Layout

```
b1demo/
  seed.py       deterministic synthetic dataset and SQLite schema
  repo.py       Repository interface, SQLite implementation, Service Layer stub
  tools.py      the read tools, audit_tail and the gated write
  approval.py   preview -> token -> commit, payload hash, expiry
  audit.py      append-only audit log (SQLite + JSONL) with redaction
  server.py     MCP server (official SDK)
  __main__.py   CLI: seed, serve, audit, call, tools
tests/
```

MIT licence, like the rest of the repository.
