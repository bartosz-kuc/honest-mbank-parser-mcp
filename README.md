# mbank-parser-mcp

Local MCP server that parses **mBank CSV operation exports** — the exact file you can download from the mBank web app under "Historia" → "Eksportuj do CSV". Runs entirely offline. Nothing leaves your machine.

Part of the [honest-mcp family](https://github.com/bartosz-kuc?tab=repositories) of small, auditable, local-first MCP servers.

## Why

Anyone using mBank in Poland — private, JDG, sp. z o.o. — does the same monthly ritual:

1. Log into mBank, download the CSV.
2. Open in Excel, squint at Polish-locale amounts (space-thousands, comma-decimals, ` PLN` suffix).
3. Filter by date, sum by category for VAT / income tax reconciliation.

This server automates step 2 and 3 through your AI. You keep your bank data on disk; the AI just calls a tool that reads and filters it locally.

Zero network calls. The `requirements.txt` is one line — the MCP SDK. No dependency footprint to audit.

## Features

Three tools:

- `read_statement_header` — quick preview of a file: client name, date range, listed accounts, inflow/outflow totals, operation count. No operation rows loaded.
- `list_operations` — parse + filter operations. Combine any of: `date_from`, `date_to`, `category`, `account`, `contains` (description substring), `min_amount`, `max_amount`, `limit`.
- `summarize_operations` — aggregate by `category`, `account`, or `month`. Returns count, inflow, outflow, net per bucket plus grand totals.

Amounts come back as signed floats (negative = expense), currency separate. Polish-locale numbers (`-3 283,33 PLN`) and non-breaking-space thousand separators are handled.

## Supported files

- ✅ **CSV export** from mBank web banking ("Historia operacji" → "Eksportuj do CSV")
- ❌ PDF statements — mBank's layout varies too much per statement type for reliable parsing. Skipped intentionally.

## Requirements

- Python 3.10+

## Setup

```bash
git clone https://github.com/bartosz-kuc/mbank-parser-mcp.git
cd mbank-parser-mcp
python3 -m venv venv
./venv/bin/pip install -r requirements.txt
```

Register with Claude Code:

```bash
claude mcp add mbank /absolute/path/to/venv/bin/python /absolute/path/to/server.py
```

Claude Desktop `claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "mbank": {
      "command": "/absolute/path/to/venv/bin/python",
      "args": ["/absolute/path/to/server.py"]
    }
  }
}
```

## Example usage

> "How much did I spend on 'Ubezpieczenia' in Q2?"

`summarize_operations(file_path=".../lista_operacji.csv", group_by="category", date_from="2026-04-01", date_to="2026-06-30")` → totals per category, including "Ubezpieczenia".

> "List all incoming transfers over 10 000 PLN this year."

`list_operations(file_path=".../lista_operacji.csv", min_amount=10000, date_from="2026-01-01")`

> "Show me every card purchase mentioning Anthropic."

`list_operations(file_path=".../lista_operacji.csv", contains="ANTHROPIC")`

## Data flow

```
Your AI client
     ↕  MCP stdio
This server (Python, on your machine)
     ↕  local filesystem
Your mBank CSV
```

No HTTP, no external service — the server has no `requests` or `httpx` dependency at all.

## Author

**Bartosz Kuć** — Warsaw-based developer, JDG owner running [skanfirmy.pl](https://skanfirmy.pl).

- GitHub: https://github.com/bartosz-kuc

## License

MIT — see [LICENSE](LICENSE).

## Related

- Part of the honest-mcp family — see the [family index](https://github.com/bartosz-kuc?tab=repositories).
