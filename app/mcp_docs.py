"""
MCP filesystem integration.

The filesystem MCP server (Node.js) runs as a subprocess and exposes the docs/
folder via the MCP protocol. This module:
  1. Spawns the MCP server process pointing at the docs/ folder
  2. Sends read_file requests over stdio using the MCP protocol
  3. Returns doc content to build the agent's system prompt

Why MCP instead of plain open()?
  - Docs folder is the single source of truth — no copy-pasting into prompts
  - Swapping or updating a doc requires zero code changes
  - The MCP server handles path validation, encoding, errors
  - Demonstrates real MCP integration as required by the assignment
"""

import os
import json
import subprocess
import threading
import time

DOCS_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "docs"))

DOC_FILES = {
    "rca_logic": "quick_commerce_rca_logic.md",
    "schema":    "quick_commerce_orders_gold.md",
    "or2a":      "order_ready_to_assignment.md",
}


class MCPFilesystemClient:
    """
    Minimal MCP stdio client.
    Spawns `npx @modelcontextprotocol/server-filesystem <docs_dir>`
    and communicates via JSON-RPC over stdin/stdout.
    """

    def __init__(self, docs_dir: str):
        self.docs_dir = docs_dir
        self._proc = None
        self._lock = threading.Lock()
        self._msg_id = 0

    def _start(self):
        if self._proc and self._proc.poll() is None:
            return  # already running

        self._proc = subprocess.Popen(
            ["npx", "-y", "@modelcontextprotocol/server-filesystem", self.docs_dir],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            bufsize=1,
        )
        # Give the server a moment to initialise
        time.sleep(1)

    def _send(self, method: str, params: dict) -> dict:
        """Send one JSON-RPC request and read one response."""
        self._msg_id += 1
        request = {
            "jsonrpc": "2.0",
            "id": self._msg_id,
            "method": method,
            "params": params,
        }
        line = json.dumps(request) + "\n"
        self._proc.stdin.write(line)
        self._proc.stdin.flush()

        # Read lines until we get a JSON response (skip any non-JSON lines)
        for _ in range(20):
            raw = self._proc.stdout.readline()
            if not raw:
                continue
            raw = raw.strip()
            if not raw:
                continue
            try:
                return json.loads(raw)
            except json.JSONDecodeError:
                continue  # skip non-JSON output (e.g. startup messages)

        raise RuntimeError("MCP server did not return a valid response")

    def read_file(self, filename: str) -> str:
        """Read a file from the docs directory via MCP."""
        with self._lock:
            self._start()
            full_path = os.path.join(self.docs_dir, filename)
            response = self._send("tools/call", {
                "name": "read_file",
                "arguments": {"path": full_path}
            })

        if "error" in response:
            raise RuntimeError(f"MCP error: {response['error']}")

        # Content is nested in result.content[0].text
        result = response.get("result", {})
        content_blocks = result.get("content", [])
        for block in content_blocks:
            if block.get("type") == "text":
                return block["text"]

        return ""

    def stop(self):
        if self._proc:
            self._proc.terminate()
            self._proc = None


# ─── Fallback: plain file read if MCP server is not available ─────────────────

def _read_file_direct(filename: str) -> str:
    """Fallback: read doc directly with Python open()."""
    path = os.path.join(DOCS_DIR, filename)
    if not os.path.exists(path):
        return f"[Doc not found: {filename}]"
    with open(path, "r") as f:
        return f.read()


# ─── Public interface ─────────────────────────────────────────────────────────

_client = MCPFilesystemClient(DOCS_DIR)


def read_doc(doc_key: str) -> str:
    """
    Read a reference doc by key.
    Tries MCP first; falls back to direct file read if MCP server isn't installed.
    """
    filename = DOC_FILES.get(doc_key)
    if not filename:
        return f"Unknown doc key: {doc_key}. Available: {list(DOC_FILES.keys())}"

    try:
        content = _client.read_file(filename)
        if content:
            print(f"[MCP] ✅ Successfully read '{filename}' via MCP server")
            return content
    except Exception as e:
        print(f"[MCP] Falling back to direct read for {filename}: {e}")

    return _read_file_direct(filename)


_system_context_cache = None

def get_system_context() -> str:
    """
    Build the agent's system prompt by reading all reference docs via MCP.
    The docs folder is the single source of truth — nothing is hardcoded here.
    Cached after first call since docs don't change at runtime.
    """
    global _system_context_cache
    if _system_context_cache is not None:
        return _system_context_cache

    rca_logic = read_doc("rca_logic")
    schema    = read_doc("schema")
    or2a      = read_doc("or2a")

    _system_context_cache = f"""You are an operations analyst agent for Loadshare's quick-commerce delivery business.
You help ops teams understand store performance and diagnose OR2A (Order Ready to Assignment) SLA breaches.

You have access to tools that query real data and run deterministic RCA checks.
Your job is to interpret results, explain findings clearly, and maintain context across a conversation.

IMPORTANT RULES:
- Always use tools to fetch data — never guess numbers
- When asked about a store, remember the city and date for follow-up questions
- When user says "what about STORE_X", use the same date as the previous question
- Present RCA findings in a clear, structured way using the playbook format
- If a store has no problem hours, say so clearly — good performance is also an answer

--- REFERENCE: OR2A METRIC ---
{or2a}

--- REFERENCE: DATA SCHEMA ---
{schema}

--- REFERENCE: RCA PLAYBOOK ---
{rca_logic}
"""
    return _system_context_cache
