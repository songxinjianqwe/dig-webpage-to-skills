# dig-webpage

A Claude Code plugin that automatically discovers HTTP APIs from web pages, analyzes parameters, and generates reusable Python scripts and Claude Code skills.

## Features

- **MCP live capture** (recommended): Open the target page via Chrome DevTools MCP, perform your actions, and Claude reads the network requests directly — no HAR export needed
- **HAR offline analysis**: Drop in an exported `.har` file for the same analysis
- **Auto-generates**: A working Python CLI script + a Claude Code skill, verified end-to-end with real request data

## Installation

```
/plugin marketplace add songxinjianqwe/dig-webpage
/plugin install dig-webpage@songxinjianqwe-dig-webpage
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

## Requirements

- Python 3 (for HAR mode and generated scripts)
- Node.js / npx (for MCP mode, auto-managed)
