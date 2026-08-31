# Varco — the approval gateway for AI agents

Your AI agents act on real business systems. You stay in control.

Varco sits between **any MCP-compatible agent** (Claude, Copilot, Cursor,
custom agents) and your backend (ERP, or any REST/Odoo system) and enforces:

- **Per-agent permissions** — each agent has an identity and sees only what
  it's allowed to; unregistered agents are rejected at the first tool call.
- **Autonomy thresholds** — writes within policy (e.g. orders under €1,000)
  execute immediately and are logged; everything else waits for a human.
  Conservative by design: no threshold, no readable amount → always ask.
- **Human approval with review-and-edit** — approvers see the request in
  plain language, can fix values before approving, and decide with one tap
  from the web app or **Telegram** (WhatsApp next). Bulk approve, scheduled
  digests (`TELEGRAM_DIGEST`), and a second web-only threshold
  (`soglia_web`) for high amounts: no one-tap above it.
- **Multiple approvers with per-department delegation** — `VARCO_APPROVERS`
  (`name:key:departments;...`): everyone sees everything, each approves only
  their delegated departments, every decision carries the approver's name.
- **Full audit trail with CSV export** — every read, request, approval,
  rejection and auto-execution is recorded and exportable
  (`/export/audit.csv`). Built for EU AI Act art. 14-style human oversight.

```
AI agent ──MCP──▶ varco_mcp ──▶ SQLite state ◀── dashboard / Telegram ◀── human
                     │                                   │
                     └─ reads (audited)                  └─ writes ONLY after
                                 ▼                          policy or approval
                     backend: mock ERP · REST · Odoo (JSON-RPC)
```

## Quickstart

```bash
python -m venv .venv
.venv/Scripts/python -m pip install -r requirements.txt   # mcp, httpx
.venv/Scripts/python test_varco.py                        # all suites: test_*.py
.venv/Scripts/python varco_dashboard.py                   # http://127.0.0.1:8420
```

The default config ships a **mock ERP with sample data**, four departments
(Sales, Administration, Purchasing, Warehouse) and five agents — a full
working demo with no external system.

Connect an agent (Claude Code example):

```bash
claude mcp add varco --scope project -e VARCO_AGENT=assistente-vendite -- <path>/.venv/Scripts/python <path>/varco_mcp.py
```

## Configuration

Everything lives in `varco_config.json`: backend (`mock`, REST base URL, or
`"tipo": "odoo"` — see [varco_config.odoo.json](varco_config.odoo.json) for a
ready-made Odoo mapping), entities, departments, agents with `read`/`write`
permissions, `soglie` (autonomy thresholds) and `soglia_web` (web-only
confirmation above this amount). Secrets never go in the config: API keys via
`ERP_API_TOKEN`, Telegram via `TELEGRAM_BOT_TOKEN` + `TELEGRAM_CHAT_ID`,
dashboard login via `VARCO_ACCESS_KEY` (open demo mode when unset).

## Tests

Five self-contained suites, no external services required:
`test_varco.py` (policy + approvals), `test_dashboard.py` (web app),
`test_varco_mcp.py` (real MCP protocol round-trip), `test_varco_rest.py` and
`test_varco_odoo.py` (backend adapters against local fake servers).

## Docs in Italian

Product docs and the SMB demo script: [README.it.md](README.it.md).

## Roadmap

WhatsApp Business approvals · Odoo App Store module · hosted cloud
(free / $99 / $399) · approval digests & bulk approve · per-approver web
confirmation above a second threshold.

MIT licensed. Built in Italy 🇮🇹
