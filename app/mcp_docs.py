"""
MCP filesystem integration.

The filesystem MCP server (Node.js) runs as a subprocess and exposes the docs/
folder via the MCP protocol. This module:
  1. Spawns the MCP server process pointing at the docs/ folder
  2. Sends read_file requests over stdio using the MCP protocol
  3. Returns doc content for the agent's tools

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
        self._initialize()

    def _initialize(self):
        """Perform the MCP initialization handshake."""
        # Step 1: Send initialize request
        response = self._send("initialize", {
            "protocolVersion": "2024-11-05",
            "capabilities": {},
            "clientInfo": {"name": "rca-agent", "version": "1.0"}
        })
        
        # Step 2: Send initialized notification
        notification = {
            "jsonrpc": "2.0",
            "method": "notifications/initialized",
            "params": {}
        }
        self._proc.stdin.write(json.dumps(notification) + "\n")
        self._proc.stdin.flush()

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


_client = MCPFilesystemClient(DOCS_DIR)


def read_doc(doc_key: str) -> str:
    """
    Read a reference doc by key.
    """
    filename = DOC_FILES.get(doc_key)
    if not filename:
        raise ValueError(
            f"Unknown doc key: {doc_key}. Available: {list(DOC_FILES.keys())}"
        )

    content = _client.read_file(filename)
    print(f"[MCP] ✅ Successfully read '{filename}' via MCP server")
    return content
