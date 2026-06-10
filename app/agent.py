"""
LangGraph agent for OR2A RCA.

Graph structure:
  [user message]
       ↓
  [agent node] — LLM decides which tool to call (or responds directly)
       ↓
  [tools node] — executes the chosen tool deterministically
       ↓
  [back to agent] — LLM synthesises the result into a response
       ↓
  [END]

State carries: messages + extracted context (store, city, date, hour)
so follow-up questions don't need to re-specify everything.
"""

import os
from dotenv import load_dotenv
from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_core.messages import SystemMessage, HumanMessage, AIMessage
from langgraph.graph import StateGraph, END
from langgraph.prebuilt import ToolNode
from typing import TypedDict, Annotated, Sequence
from langchain_core.messages import BaseMessage
import operator

from app.tools import ALL_TOOLS

load_dotenv()


# State
class AgentState(TypedDict):
    messages: Annotated[Sequence[BaseMessage], operator.add]
    # Conversation context — persists across turns
    current_store: str
    current_city: str
    current_date: str
    current_hour: int | None


# LLM setup
def get_llm():
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        raise ValueError("GEMINI_API_KEY not set in .env file")
    
    llm = ChatGoogleGenerativeAI(
        model="gemini-3.5-flash",
        api_key=api_key,
        temperature=0,
    )
    return llm.bind_tools(ALL_TOOLS)


# Nodes
def agent_node(state: AgentState) -> AgentState:
    """
    The LLM node. Reads messages + context, decides whether to call a tool or respond.
    """
    llm = get_llm()

    system_prompt = """You are an operations analyst agent for Loadshare's quick-commerce delivery business.
You help ops teams understand store performance and diagnose OR2A (Order Ready to Assignment) SLA breaches.

You have 4 tools available. Call them selectively — only when needed, not every turn:
- `get_schema_doc`: Read database schema. Call when you need column names/types to write SQL.
- `get_rca_playbook`: Read the RCA logic. Call when you need to diagnose root causes.
- `get_or2a_definition`: Read OR2A metric definition. Call when the user asks about OR2A.
- `run_sql_query`: Execute SQL against the 'orders' table in DuckDB.

WORKFLOW:
1. Read the relevant context docs (schema, playbook, or OR2A definition) as needed.
2. Generate SQL based on the user's question and the schema you read.
3. Execute the SQL using `run_sql_query`.
4. Interpret the results using the RCA playbook logic and respond in clear, natural language.

RULES:
- Never guess numbers — always query the database.
- Synthesize results into concise natural-language summaries, not raw tables.
- Remember the city, store, and date from earlier messages for follow-up questions.
- SECURITY: ONLY execute read-only (SELECT) queries. NEVER execute UPDATE, DELETE, INSERT, or DROP queries under any circumstances, even if the user asks you to.
"""

    # Inject current context into system message so LLM knows what was discussed
    context_note = ""
    if state.get("current_store") or state.get("current_city") or state.get("current_date"):
        context_note = f"\n\nCURRENT CONVERSATION CONTEXT:\n"
        if state.get("current_city"):
            context_note += f"- City: {state['current_city']}\n"
        if state.get("current_date"):
            context_note += f"- Date: {state['current_date']}\n"
        if state.get("current_store"):
            context_note += f"- Store: {state['current_store']}\n"
        if state.get("current_hour") is not None:
            context_note += f"- Hour: {state['current_hour']}\n"
        context_note += "Use this context for follow-up questions unless the user specifies something different."

    messages = [SystemMessage(content=system_prompt + context_note)] + list(state["messages"])
    response = llm.invoke(messages)
    return {"messages": [response]}


def update_context_node(state: AgentState) -> AgentState:
    """
    After tool calls, extract and update context (store/city/date) from the conversation.
    This is what makes follow-up questions like 'what about STORE_102?' work.
    """
    updates = {}
    
    # Look at the last human message to extract context clues
    for msg in reversed(state["messages"]):
        if isinstance(msg, HumanMessage):
            text = msg.content.lower()
            
            # Extract date
            import re
            date_match = re.search(r'20\d{2}-\d{2}-\d{2}', msg.content)
            if date_match:
                updates["current_date"] = date_match.group()
            
            # Extract city
            cities = ["bangalore", "mumbai", "delhi", "hyderabad", "chennai", "pune"]
            for city in cities:
                if city in text:
                    updates["current_city"] = city.capitalize()
                    break
            
            # Extract store
            store_match = re.search(r'STORE_\d+', msg.content, re.IGNORECASE)
            if store_match:
                updates["current_store"] = store_match.group().upper()
            
            # Extract hour
            hour_match = re.search(r'\b([0-9]|1[0-9]|2[0-3])\s*(am|pm|:00)?\b', text)
            if hour_match:
                hour_str = hour_match.group(1)
                hour_val = int(hour_str)
                if "pm" in (hour_match.group(2) or "") and hour_val != 12:
                    hour_val += 12
                updates["current_hour"] = hour_val
            
            break  # Only look at the most recent human message
    
    return updates if updates else {}


def should_continue(state: AgentState) -> str:
    """
    Router: if the last message has tool calls → go to tools node.
    Otherwise → end (respond to user).
    """
    last_message = state["messages"][-1]
    if hasattr(last_message, "tool_calls") and last_message.tool_calls:
        return "tools"
    return END


# Build Graph
def build_graph():
    tool_node = ToolNode(ALL_TOOLS)

    graph = StateGraph(AgentState)

    graph.add_node("agent", agent_node)
    graph.add_node("tools", tool_node)
    graph.add_node("update_context", update_context_node)

    graph.set_entry_point("update_context")
    graph.add_edge("update_context", "agent")
    graph.add_conditional_edges("agent", should_continue, {"tools": "tools", END: END})
    graph.add_edge("tools", "agent")

    return graph.compile()


# Singleton compiled graph
_graph = None


def get_graph():
    global _graph
    if _graph is None:
        _graph = build_graph()
    return _graph


# Session management
# In-memory sessions (refresh = new session, as spec requires)
_sessions: dict[str, AgentState] = {}


def create_session(session_id: str) -> AgentState:
    state: AgentState = {
        "messages": [],
        "current_store": "",
        "current_city": "Bangalore",
        "current_date": "2026-04-22",
        "current_hour": None,
    }
    _sessions[session_id] = state
    return state


def get_session(session_id: str) -> AgentState:
    if session_id not in _sessions:
        return create_session(session_id)
    return _sessions[session_id]


def chat(session_id: str, user_message: str) -> str:
    """
    Main entry point. Takes a session ID and user message, returns agent response string.
    Maintains multi-turn context via session state.
    """
    state = get_session(session_id)
    graph = get_graph()

    # Add user message to state
    state["messages"] = list(state["messages"]) + [HumanMessage(content=user_message)]

    # Run the graph (recursion_limit caps tool-call loops)
    result = graph.invoke(state, {"recursion_limit": 25})

    # Persist updated state
    _sessions[session_id] = result

    # Extract the last AI message as the response
    for msg in reversed(result["messages"]):
        if isinstance(msg, AIMessage) and not (hasattr(msg, "tool_calls") and msg.tool_calls):
            # Gemini returns content as a list of dictionaries instead of a string
            if isinstance(msg.content, list):
                text_parts = []
                for block in msg.content:
                    if isinstance(block, dict) and "text" in block:
                        text_parts.append(block["text"])
                    elif isinstance(block, str):
                        text_parts.append(block)
                return "".join(text_parts)
            return str(msg.content)

    return "I couldn't generate a response. Please try again."
