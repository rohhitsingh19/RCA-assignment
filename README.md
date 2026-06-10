# OR2A RCA Agent

A conversational agent for diagnosing delivery SLA breaches (OR2A) across quick-commerce dark stores. Built with LangGraph, FastAPI, DuckDB, and MCP.

---

## Prerequisites

- Python 3.11+
- Node.js 18+ (for the MCP filesystem server)
- A free Groq API key — get one at https://console.groq.com

---

## Setup — every terminal command you need

### 1. Clone / unzip and enter the project

```bash
cd rca-agent
```

### 2. Check Python version

```bash
python3 --version
# Must be 3.11 or higher
```

### 3. Create and activate a virtual environment

```bash
python3 -m venv venv
source venv/bin/activate        # Mac/Linux
# venv\Scripts\activate         # Windows
```

### 4. Install Python dependencies

```bash
pip install -r requirements.txt
```

### 5. Set your Groq API key

```bash
cp .env.example .env
# Open .env and replace 'your_groq_api_key_here' with your real key
```

Or just run:

```bash
echo "GROQ_API_KEY=your_actual_key_here" > .env
```

### 6. Install Node.js (if not already installed)

```bash
# Ubuntu/Debian
sudo apt-get install nodejs npm

# Mac
brew install node

# Verify
node --version
npm --version
```

### 7. Install the MCP filesystem server

```bash
npm install -g @modelcontextprotocol/server-filesystem
```

### 8. Verify the MCP server works (optional but recommended)

```bash
npx @modelcontextprotocol/server-filesystem ./docs
# You should see it start. Press Ctrl+C to stop.
```

### 9. Run the server

```bash
uvicorn app.main:app --reload --port 8000
```

### 10. Open the chat UI

```
http://localhost:8000
```

---

## Sample questions to test

These exercise the full agent — city overview, store RCA, drill-downs, and context switching:

1. "How did Bangalore do on 2026-04-22?"
2. "Why did STORE_003 underperform that day?"
3. "Walk me through the morning hours at STORE_003"
4. "What about STORE_010?"
5. "What happened at hour 22 there?"

---

## Project structure

```
rca-agent/
├── app/
│   ├── __init__.py
│   ├── main.py          — FastAPI app, endpoints
│   ├── agent.py         — LangGraph graph, session management
│   ├── tools.py         — LangChain tools (deterministic data + RCA)
│   ├── rca.py           — Pure RCA logic, thresholds, formatting
│   ├── database.py      — DuckDB loader and query runner
│   └── mcp_docs.py      — MCP filesystem client, system prompt builder
├── data/
│   └── quick_commerce_orders_gold_20260422.csv
├── docs/
│   ├── quick_commerce_rca_logic.md
│   ├── quick_commerce_orders_gold.md
│   └── order_ready_to_assignment.md
├── frontend/
│   └── index.html
├── .env
├── requirements.txt
└── README.md
```

---

## Architecture decisions

**Why DuckDB?** Zero setup, native CSV import, fast analytical SQL, Python-native. No database server to spin up. Single `duckdb.connect(":memory:")` call loads the CSV into an in-memory table on startup.

**Why the MCP filesystem server?** The three reference docs (RCA playbook, schema, OR2A definition) are served via MCP rather than hardcoded into prompts. This means updating a threshold or doc requires zero code changes — you just edit the markdown file. The `mcp_docs.py` module spawns the `@modelcontextprotocol/server-filesystem` MCP server as a subprocess and reads files via JSON-RPC over stdio. It falls back to direct `open()` if Node.js is not available, so the agent works either way.

**Why is RCA logic deterministic?** Threshold decisions (demand spike >10%, booking gap <90%, utilization <85%) live in `rca.py` as constants — not in prompts. The LLM narrates and explains; the code decides the flags. This makes RCA results reproducible and auditable. An analyst can trust the output because the same input always produces the same flags.

**Why LangGraph over plain LangChain?** The state graph lets us carry conversation context (current store, city, date) across turns cleanly. When a user asks "what about STORE_102?", the agent reads `current_date` and `current_city` from state without the user re-specifying. The graph structure is: `update_context → agent → (tools → agent)* → END`.

**Tool design — what's a tool vs what's in the prompt vs what's in code:**
- **Tools** (LLM decides *when* to call): `get_city_performance`, `get_store_performance`, `get_hour_detail`, `run_store_rca`, `run_hour_rca`, `list_stores` — all return pre-formatted data, the LLM doesn't write SQL.
- **Deterministic code** (never touches LLM): RCA threshold checks, flag generation, pileup detection — all in `rca.py`.
- **Prompt** (via MCP): Reference docs for context (playbook format, schema, metric definitions). The LLM uses these to interpret results, not to make diagnostic decisions.
