"""mbank-parser-mcp — MCP server for parsing mBank CSV exports.

Parses the CSV format that mBank's web banking exports for the "Lista
operacji" report. Everything is local: files stay on your disk, and this
server does no network I/O at all.

Primary use case: a Polish JDG (or personal accountant) doing monthly cost
reconciliation, VAT prep, or category-based expense summaries without
uploading bank data to any cloud service.

Tools: list_operations, summarize_operations, read_statement_header.

Note: PDF statements are not supported in v1 — mBank's PDF layout varies
across statement types and OCR-lite extraction is unreliable. Use the CSV
export from the mBank web app ("Historia" → "Eksportuj do CSV").

Author: Bartosz Kuć <firma@bartosza.pl>
Repo:   https://github.com/bartosz-kuc/honest-mbank-parser-mcp
License: MIT
"""

import asyncio
import csv
import io
import json
import os
import re
from collections import defaultdict
from decimal import Decimal, InvalidOperation
from typing import Any

from mcp import types
from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import CallToolResult, ListToolsResult, TextContent, Tool

DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
AMOUNT_RE = re.compile(r"^\s*(-?)\s*([\d\s ]+),(\d{2})\s*([A-Z]{3})?\s*$")


def _read_text(path: str) -> str:
    if not os.path.isfile(path):
        raise FileNotFoundError(f"File not found: {path}")
    with open(path, "rb") as fh:
        raw = fh.read()
    # mBank CSV exports are UTF-8 with BOM. Fall back to cp1250 for older exports.
    for enc in ("utf-8-sig", "utf-8", "cp1250"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    raise ValueError(f"Cannot decode {path} as UTF-8 or cp1250")


def _parse_amount(raw: str) -> tuple[Decimal, str]:
    """Parse '−3 283,33 PLN' → (Decimal('-3283.33'), 'PLN'). Non-breaking space is common in mBank exports."""
    m = AMOUNT_RE.match(raw)
    if not m:
        raise ValueError(f"Cannot parse amount {raw!r}")
    sign, ints, decs, currency = m.groups()
    # Strip regular and non-breaking spaces from integer part
    ints_clean = re.sub(r"[\s ]", "", ints)
    try:
        value = Decimal(f"{sign}{ints_clean}.{decs}")
    except InvalidOperation as exc:
        raise ValueError(f"Cannot parse amount {raw!r}: {exc}")
    return value, currency or "PLN"


def _dequote(s: str) -> str:
    s = s.strip()
    if len(s) >= 2 and s[0] == '"' and s[-1] == '"':
        s = s[1:-1]
    # mBank pads with many trailing spaces inside the quotes; collapse
    s = re.sub(r"\s+", " ", s).strip()
    return s


def _parse_file(path: str) -> dict:
    text = _read_text(path)
    # mBank uses ';' as delimiter throughout, including headers.
    reader = csv.reader(io.StringIO(text), delimiter=";", quotechar='"')
    rows = [row for row in reader if any(cell.strip() for cell in row)]

    header: dict[str, Any] = {
        "klient": None,
        "period_from": None,
        "period_to": None,
        "accounts": [],
        "totals": {},
    }

    ops: list[dict] = []
    in_ops = False
    seen_ops_header = False
    seen_currency_header = False

    for row in rows:
        first = row[0].strip() if row else ""

        if not in_ops:
            # Section markers vary in casing/prefix — probe fuzzily.
            if first == "#Klient" and len(rows) > rows.index(row) + 1:
                # next non-empty row is name; but we can also read the immediately following row here
                continue
            if header["klient"] is None and rows.index(row) > 0 and rows[rows.index(row) - 1][0].strip() == "#Klient":
                header["klient"] = first
                continue
            if first == "#Za okres:":
                # Next row has the two dates.
                continue
            if header["period_from"] is None and rows.index(row) > 0 and rows[rows.index(row) - 1][0].strip() == "#Za okres:":
                # Format: dd.mm.yyyy;dd.mm.yyyy;
                if len(row) >= 2:
                    header["period_from"] = row[0].strip()
                    header["period_to"] = row[1].strip()
                continue
            # Account rows are indented and contain " - <IBAN-like>"
            if " - " in first and ("konto" in first.lower() or "rachunek" in first.lower() or re.search(r"\d{10,}", first)):
                header["accounts"].append(first.strip())
                continue
            # Currency/totals header
            if first == "#Waluta":
                seen_currency_header = True
                continue
            if seen_currency_header and len(row) >= 3 and re.match(r"^[A-Z]{3}$", first):
                header["totals"][first] = {
                    "inflow": row[1].strip(),
                    "outflow": row[2].strip(),
                }
                seen_currency_header = False
                continue
            # Operation header
            if first == "#Data operacji":
                seen_ops_header = True
                in_ops = True
                continue

        if in_ops:
            if len(row) < 5:
                continue
            date_str = row[0].strip()
            if not DATE_RE.match(date_str):
                continue
            desc = _dequote(row[1])
            account = _dequote(row[2])
            category = _dequote(row[3])
            amount_raw = row[4].strip()
            try:
                amount, currency = _parse_amount(amount_raw)
            except ValueError:
                # Skip malformed row rather than fail whole file
                continue
            ops.append({
                "date": date_str,
                "description": desc,
                "account": account,
                "category": category,
                "amount": float(amount),
                "currency": currency,
                "amount_raw": amount_raw,
            })

    if not seen_ops_header:
        raise ValueError("Could not locate '#Data operacji' header row. Is this really an mBank operations CSV export?")

    return {"header": header, "operations": ops}


def _iso_or_none(d: str | None) -> str | None:
    if d is None:
        return None
    d = d.strip()
    if not DATE_RE.match(d):
        raise ValueError(f"Date must be YYYY-MM-DD, got {d!r}")
    return d


def _apply_filters(ops: list[dict], filters: dict) -> list[dict]:
    date_from = _iso_or_none(filters.get("date_from"))
    date_to = _iso_or_none(filters.get("date_to"))
    category_sub = (filters.get("category") or "").strip().lower()
    account_sub = (filters.get("account") or "").strip().lower()
    contains_sub = (filters.get("contains") or "").strip().lower()
    min_amount = filters.get("min_amount")
    max_amount = filters.get("max_amount")

    out: list[dict] = []
    for op in ops:
        if date_from and op["date"] < date_from:
            continue
        if date_to and op["date"] > date_to:
            continue
        if category_sub and category_sub not in op["category"].lower():
            continue
        if account_sub and account_sub not in op["account"].lower():
            continue
        if contains_sub and contains_sub not in op["description"].lower():
            continue
        if min_amount is not None and op["amount"] < float(min_amount):
            continue
        if max_amount is not None and op["amount"] > float(max_amount):
            continue
        out.append(op)
    return out


server = Server("mbank-parser")


_FILTER_PROPERTIES = {
    "date_from": {"type": "string", "description": "YYYY-MM-DD, inclusive"},
    "date_to": {"type": "string", "description": "YYYY-MM-DD, inclusive"},
    "category": {"type": "string", "description": "Substring match against mBank's assigned category (case-insensitive)"},
    "account": {"type": "string", "description": "Substring match against account label (case-insensitive)"},
    "contains": {"type": "string", "description": "Substring match against operation description (case-insensitive)"},
    "min_amount": {"type": "number", "description": "Minimum amount (inclusive). Signed — negative for expenses."},
    "max_amount": {"type": "number", "description": "Maximum amount (inclusive)"},
}


async def _list_tools() -> list[Tool]:
    return [
        Tool(
            name="read_statement_header",
            description=(
                "Read only the header block of an mBank CSV export — client name, date range, listed accounts, "
                "inflow/outflow totals — without loading the operation rows. Fast preview to confirm you have the right file."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "file_path": {"type": "string", "description": "Absolute path to the mBank CSV file"},
                },
                "required": ["file_path"],
            },
        ),
        Tool(
            name="list_operations",
            description=(
                "Parse an mBank CSV operations export and return the operation rows (date, description, account, category, "
                "signed amount, currency). All filters are optional; combine them freely. Amounts are already parsed to "
                "floats (negative = expense). For big statements consider a `limit` to keep response size in check."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "file_path": {"type": "string", "description": "Absolute path to the mBank CSV file"},
                    **_FILTER_PROPERTIES,
                    "limit": {"type": "integer", "description": "Max operations to return (default: all)"},
                    "include_header": {"type": "boolean", "default": False, "description": "If true, include the header block alongside operations"},
                },
                "required": ["file_path"],
            },
        ),
        Tool(
            name="summarize_operations",
            description=(
                "Aggregate operations by category, by account, or by month. Returns count, total inflow, total outflow, "
                "net per bucket, plus the grand totals over the filtered set. Ideal for building a monthly expense report or "
                "checking VAT-relevant categories."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "file_path": {"type": "string", "description": "Absolute path to the mBank CSV file"},
                    "group_by": {"type": "string", "enum": ["category", "account", "month"], "default": "category"},
                    **_FILTER_PROPERTIES,
                },
                "required": ["file_path"],
            },
        ),
    ]


async def _call_tool(name: str, arguments: dict[str, Any]) -> list[TextContent]:
    if name == "read_statement_header":
        parsed = _parse_file(arguments["file_path"])
        header = dict(parsed["header"])
        header["operation_count"] = len(parsed["operations"])
        return [TextContent(type="text", text=json.dumps(header, ensure_ascii=False, indent=2))]

    if name == "list_operations":
        parsed = _parse_file(arguments["file_path"])
        ops = _apply_filters(parsed["operations"], arguments)
        limit = arguments.get("limit")
        if limit is not None:
            ops = ops[: int(limit)]
        payload: dict[str, Any] = {"returned": len(ops), "operations": ops}
        if arguments.get("include_header"):
            payload["header"] = parsed["header"]
        return [TextContent(type="text", text=json.dumps(payload, ensure_ascii=False, indent=2))]

    if name == "summarize_operations":
        parsed = _parse_file(arguments["file_path"])
        ops = _apply_filters(parsed["operations"], arguments)
        group_by = arguments.get("group_by", "category")

        buckets: dict[str, dict] = defaultdict(lambda: {"count": 0, "inflow": 0.0, "outflow": 0.0})
        for op in ops:
            if group_by == "category":
                key = op["category"] or "(no category)"
            elif group_by == "account":
                key = op["account"] or "(no account)"
            elif group_by == "month":
                key = op["date"][:7]
            else:
                raise ValueError(f"Unknown group_by: {group_by}")
            b = buckets[key]
            b["count"] += 1
            if op["amount"] >= 0:
                b["inflow"] += op["amount"]
            else:
                b["outflow"] += op["amount"]

        summary = [
            {
                "key": k,
                "count": v["count"],
                "inflow": round(v["inflow"], 2),
                "outflow": round(v["outflow"], 2),
                "net": round(v["inflow"] + v["outflow"], 2),
            }
            for k, v in sorted(buckets.items(), key=lambda kv: (kv[1]["inflow"] + kv[1]["outflow"]))
        ]
        totals = {
            "total_operations": len(ops),
            "total_inflow": round(sum(o["amount"] for o in ops if o["amount"] >= 0), 2),
            "total_outflow": round(sum(o["amount"] for o in ops if o["amount"] < 0), 2),
        }
        totals["net"] = round(totals["total_inflow"] + totals["total_outflow"], 2)

        return [TextContent(type="text", text=json.dumps({
            "group_by": group_by,
            "buckets": summary,
            "totals": totals,
        }, ensure_ascii=False, indent=2))]

    raise ValueError(f"Unknown tool: {name}")


async def on_list_tools(ctx, params) -> ListToolsResult:
    return ListToolsResult(tools=await _list_tools())


async def on_call_tool(ctx, params) -> CallToolResult:
    return CallToolResult(content=await _call_tool(params.name, params.arguments or {}))


server.add_request_handler("tools/list", types.PaginatedRequestParams, on_list_tools)
server.add_request_handler("tools/call", types.CallToolRequestParams, on_call_tool)


async def main():
    async with stdio_server() as (read, write):
        await server.run(read, write, server.create_initialization_options())


def sync_main():
    """Sync entry point for console script."""
    asyncio.run(main())


if __name__ == "__main__":
    sync_main()
