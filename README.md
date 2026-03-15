# dig-webpage

A Claude Code plugin that automatically discovers HTTP APIs from web pages, analyzes parameters, and generates reusable Python scripts and Claude Code skills.

## Features

- **MCP live capture** (recommended): Open the target page via Chrome DevTools MCP, perform your actions, and Claude reads the network requests directly — no HAR export needed
- **HAR offline analysis**: Drop in an exported `.har` file for the same analysis
- **Auto-generates**: A working Python CLI script + a Claude Code skill, verified end-to-end with real request data

## Installation

```
/plugin marketplace add songxinjianqwe/dig-webpage-to-skills
/plugin install dig-webpage-to-skills@songxinjianqwe-dig-webpage-to-skills
```

This automatically installs the `chrome-devtools-mcp` dependency.

## Usage

**MCP mode** (recommended) — just provide a URL:
```
/dig-webpage https://example.com --goal "get comment list" --vars "doc ID" "filter"
```

**HAR mode** — provide a `.har` file:
```
/dig-webpage path/to/export.har --goal "get product details"
```

Claude will:
1. Open the page (MCP mode) or process the HAR file
2. Ask you to perform your browser actions (MCP mode only)
3. Filter noise, group requests by endpoint
4. Identify the target API, analyze parameters and auth
5. Generate a Python script + skill, then verify with real data

## Helper scripts

### `har_preprocessor.py`

Filters and groups requests from a `.har` file into a compact JSON structure for Claude to analyze.

```bash
python skills/dig-webpage/har_preprocessor.py export.har
# outputs: /tmp/export_preprocessed.json
```

### `mcp_preprocessor.py`

Two-stage preprocessor for MCP live capture mode. Keeps Claude's context lean by offloading all filtering and grouping to Python.

**Stage 1** — filter candidate reqids from the request summary list (written by Claude as JSON after `list_network_requests`):

```bash
python skills/dig-webpage/mcp_preprocessor.py filter mcp_list.json
# outputs: /tmp/mcp_candidate_reqids.json  →  [12, 34, 56, ...]
```

**Stage 2** — process full request details (collected by Claude via `get_network_request`) into the same compact format as `har_preprocessor.py`:

```bash
python skills/dig-webpage/mcp_preprocessor.py process mcp_details.json
# outputs: /tmp/mcp_requests_preprocessed.json  →  { "summary": [...], "details": {...} }
```

## Requirements

- Python 3 (for HAR mode and generated scripts)
- Node.js / npx (for MCP mode, auto-managed)
