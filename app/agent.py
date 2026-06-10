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
from langchain_groq import ChatGroq
from langchain_core.messages import SystemMessage, HumanMessage, AIMessage
from langgraph.graph import StateGraph, END
from langgraph.prebuilt import ToolNode
from typing import TypedDict, Annotated, Sequence
from langchain_core.messages import BaseMessage
import operator

from app.tools import ALL_TOOLS
from app.mcp_docs import get_system_context

load_dotenv()


# ─── State ────────────────────────────────────────────────────────────────────

class AgentState(TypedDict):
    messages: Annotated[Sequence[BaseMessage], operator.add]
    # Conversation context — persists across turns
    current_store: str
    current_city: str
    current_date: str
    current_hour: int | None


# ─── LLM setup ────────────────────────────────────────────────────────────────

def get_llm():
    api_key = os.getenv("GROQ_API_KEY")
    if not api_key:
        raise ValueError("GROQ_API_KEY not set in .env file")
    
    llm = ChatGroq(
        model="llama-3.3-70b-versatile",
        api_key=api_key,
        temperature=0,
        max_tokens=2048,
    )
    return llm.bind_tools(ALL_TOOLS)


# ─── Nodes ────────────────────────────────────────────────────────────────────

def agent_node(state: AgentState) -> AgentState:
    """
    The LLM node. Reads messages + context, decides whether to call a tool or respond.
    """
    llm = get_llm()
    system_prompt = get_system_context()

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


# ─── Build Graph ──────────────────────────────────────────────────────────────

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


# ─── Session management ───────────────────────────────────────────────────────

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

    # Run the graph
    result = graph.invoke(state)

    # Persist updated state
    _sessions[session_id] = result

    # Extract the last AI message as the response
    for msg in reversed(result["messages"]):
        if isinstance(msg, AIMessage) and not (hasattr(msg, "tool_calls") and msg.tool_calls):
            return msg.content

    return "I couldn't generate a response. Please try again."
