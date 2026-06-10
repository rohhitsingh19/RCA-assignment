"""
LangChain tools for the RCA agent.

4 tools exactly as per spec:
  - get_schema_doc       → MCP reads quick_commerce_orders_gold.md
  - get_rca_playbook     → MCP reads quick_commerce_rca_logic.md
  - get_or2a_definition  → MCP reads order_ready_to_assignment.md
  - run_sql_query        → Executes generated SQL on DuckDB
"""

import json
from langchain_core.tools import tool
from app.database import query
from app.mcp_docs import read_doc


@tool
def get_schema_doc() -> str:
    """
    Read the database schema documentation via MCP.
    Returns column names, types, and descriptions for the 'orders' table.
    Call this to understand the table structure before writing SQL queries.
    """
    try:
        return read_doc("schema")
    except Exception as e:
        return f"Error reading schema doc: {str(e)}"


@tool
def get_rca_playbook() -> str:
    """
    Read the RCA playbook via MCP.
    Returns the step-by-step root cause analysis logic including
    demand spike, pileup, booking gap, and utilization gap checks with thresholds.
    Call this when you need to diagnose WHY a store underperformed.
    """
    try:
        return read_doc("rca_logic")
    except Exception as e:
        return f"Error reading RCA playbook: {str(e)}"


@tool
def get_or2a_definition() -> str:
    """
    Read the OR2A metric definition via MCP.
    Returns what OR2A means, how it is calculated, and what the SLA thresholds are.
    Call this when the user asks about the OR2A metric or you need to understand breach definitions.
    """
    try:
        return read_doc("or2a")
    except Exception as e:
        return f"Error reading OR2A definition: {str(e)}"


@tool
def run_sql_query(sql: str) -> str:
    """
    Execute a SQL query against the DuckDB database and return results as JSON.
    The table name is 'orders'. Use get_schema_doc first to understand the columns.

    Args:
        sql: The SQL query string to execute.
    """
    try:
        rows = query(sql)
        if not rows:
            return "Query returned no results."
        return json.dumps(rows, indent=2)
    except Exception as e:
        return f"Error executing SQL: {str(e)}"


# Export all tools as a list for the agent
ALL_TOOLS = [
    get_schema_doc,
    get_rca_playbook,
    get_or2a_definition,
    run_sql_query,
]
