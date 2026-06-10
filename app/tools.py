"""
LangChain tools for the RCA agent.
Each tool is deterministic — the LLM decides WHEN to call them, not what they return.
"""

import json
from langchain_core.tools import tool
from app.database import query
from app.rca import run_rca_for_hour, format_rca_output


@tool
def get_city_performance(city: str, date: str) -> str:
    """
    Get a performance summary for all stores in a city on a given date.
    Returns store-level breach rates, problem hour counts, and weighted avg OR2A.
    Use this when the user asks how a city did overall.

    Args:
        city: City name e.g. 'Bangalore'
        date: Date string e.g. '2026-04-22'
    """
    rows = query(f"""
        SELECT
            store,
            SUM(total_orders) as total_orders,
            SUM(breached_count) as breached_count,
            ROUND(SUM(breached_count) * 1.0 / NULLIF(SUM(total_orders), 0), 4) as breach_rate,
            ROUND(SUM(avg_or2a * total_orders) / NULLIF(SUM(total_orders), 0), 2) as weighted_avg_or2a,
            SUM(is_problem_hour) as problem_hours
        FROM orders
        WHERE city = '{city}' AND charge_date = '{date}'
        GROUP BY store
        ORDER BY breach_rate DESC
    """)

    if not rows:
        return f"No data found for city='{city}' on date='{date}'."

    total_orders = sum(r["total_orders"] or 0 for r in rows)
    total_breached = sum(r["breached_count"] or 0 for r in rows)
    city_breach_rate = (total_breached / total_orders * 100) if total_orders else 0
    problem_stores = [r["store"] for r in rows if (r["breach_rate"] or 0) > 0]

    lines = [f"## {city} — {date}"]
    lines.append(f"**City-level breach rate**: {city_breach_rate:.1f}% across {total_orders} orders")
    lines.append(f"**Stores with breaches**: {len(problem_stores)} of {len(rows)}\n")
    lines.append("| Store | Orders | Breach Rate | Avg OR2A | Problem Hours |")
    lines.append("|-------|--------|-------------|----------|---------------|")
    for r in rows:
        br = (r["breach_rate"] or 0) * 100
        or2a = r["weighted_avg_or2a"] or 0
        ph = r["problem_hours"] or 0
        flag = " ⚠️" if br > 10 else ""
        lines.append(f"| {r['store']}{flag} | {r['total_orders']} | {br:.1f}% | {or2a:.1f} min | {ph} |")

    return "\n".join(lines)


@tool
def get_store_performance(store: str, date: str) -> str:
    """
    Get a full day performance summary for one store on a given date.
    Shows all hours, breach rates, and identifies which hours had problems.
    Use this when user asks how a specific store did on a day.

    Args:
        store: Store code e.g. 'STORE_101'
        date: Date string e.g. '2026-04-22'
    """
    rows = query(f"""
        SELECT
            hour, total_orders, breached_count, breached_rate,
            is_problem_hour, avg_or2a, order_projection,
            pileup_flag, pileup_count, man_hour,
            current_size, booked_size, noshow_count,
            current_capacity_booked
        FROM orders
        WHERE store = '{store}' AND charge_date = '{date}'
        ORDER BY hour
    """)

    if not rows:
        return f"No data found for store='{store}' on date='{date}'. Check store code and date."

    total_orders = sum(r["total_orders"] or 0 for r in rows)
    total_breached = sum(r["breached_count"] or 0 for r in rows)
    breach_rate = (total_breached / total_orders * 100) if total_orders else 0
    or2a_num = sum((r["avg_or2a"] or 0) * (r["total_orders"] or 0) for r in rows)
    weighted_or2a = or2a_num / total_orders if total_orders else 0
    problem_hours = [r for r in rows if (r["is_problem_hour"] or 0) == 1]

    lines = [f"## {store} — {date}"]
    lines.append(f"**Total orders**: {total_orders} | **Breach rate**: {breach_rate:.1f}% | **Weighted avg OR2A**: {weighted_or2a:.1f} min")
    lines.append(f"**Problem hours**: {len(problem_hours)}\n")
    lines.append("| Hour | Orders | Breach Rate | Avg OR2A | Pileup | Man-Hour |")
    lines.append("|------|--------|-------------|----------|--------|----------|")
    for r in rows:
        br = (r["breached_rate"] or 0) * 100
        or2a = r["avg_or2a"] or 0
        pf = "✓" if (r["pileup_flag"] or 0) == 1 else "-"
        mh = r["man_hour"] or 0
        flag = " ⚠️" if (r["is_problem_hour"] or 0) == 1 else ""
        lines.append(f"| {int(r['hour']):02d}:00{flag} | {r['total_orders']} | {br:.1f}% | {or2a:.1f} min | {pf} | {mh:.2f} |")

    return "\n".join(lines)


@tool
def get_hour_detail(store: str, date: str, hour: int) -> str:
    """
    Get all raw data for one specific store-hour. Use for deep drill-downs.
    
    Args:
        store: Store code e.g. 'STORE_101'
        date: Date string e.g. '2026-04-22'
        hour: Hour as integer 0-23
    """
    rows = query(f"""
        SELECT * FROM orders
        WHERE store = '{store}' AND charge_date = '{date}' AND hour = {hour}
    """)

    if not rows:
        return f"No data for {store} at hour {hour} on {date}."

    r = rows[0]
    lines = [f"## {store} — Hour {hour:02d}:00 — {date}"]
    for k, v in r.items():
        if v is not None:
            lines.append(f"- **{k}**: {v}")
    return "\n".join(lines)


@tool
def run_store_rca(store: str, date: str) -> str:
    """
    Run the full RCA playbook for a store on a date.
    Checks all problem hours for demand spikes, pileup, booking gaps, and utilization gaps.
    Use this when user asks WHY a store underperformed or asks for root cause analysis.

    Args:
        store: Store code e.g. 'STORE_101'
        date: Date string e.g. '2026-04-22'
    """
    # Get all hours for context (needed for sustained pileup check)
    all_rows = query(f"""
        SELECT * FROM orders
        WHERE store = '{store}' AND charge_date = '{date}'
        ORDER BY hour
    """)

    if not all_rows:
        return f"No data found for store='{store}' on date='{date}'."

    # Focus on problem hours
    problem_rows = [r for r in all_rows if (r.get("is_problem_hour") or 0) == 1]

    if not problem_rows:
        return f"## {store} — {date}\n\nNo problem hours flagged. Store performed within SLA all day."

    lines = [f"## RCA: {store} — {date}"]
    lines.append(f"**{len(problem_rows)} problem hour(s) identified.**\n")

    for row in problem_rows:
        rca = run_rca_for_hour(row, all_rows)
        lines.append(format_rca_output(rca))
        lines.append("")

    return "\n".join(lines)


@tool
def run_hour_rca(store: str, date: str, hour: int) -> str:
    """
    Run RCA for a single specific hour at a store.
    Use when user asks about a specific hour e.g. 'what happened at 8am at STORE_101'.

    Args:
        store: Store code e.g. 'STORE_101'
        date: Date string e.g. '2026-04-22'
        hour: Hour as integer 0-23
    """
    all_rows = query(f"""
        SELECT * FROM orders
        WHERE store = '{store}' AND charge_date = '{date}'
        ORDER BY hour
    """)

    target_rows = [r for r in all_rows if int(r.get("hour") or 0) == hour]
    if not target_rows:
        return f"No data for {store} hour {hour} on {date}."

    rca = run_rca_for_hour(target_rows[0], all_rows)
    return format_rca_output(rca)


@tool
def list_stores(city: str, date: str) -> str:
    """
    List all store codes available for a city on a given date.
    Use this when user mentions a city but you need to know which stores exist.

    Args:
        city: City name e.g. 'Bangalore'
        date: Date string e.g. '2026-04-22'
    """
    rows = query(f"""
        SELECT DISTINCT store FROM orders
        WHERE city = '{city}' AND charge_date = '{date}'
        ORDER BY store
    """)

    if not rows:
        return f"No stores found for city='{city}' on date='{date}'."

    stores = [r["store"] for r in rows]
    return f"Stores in {city} on {date}: {', '.join(stores)}"


# Export all tools as a list for the agent
ALL_TOOLS = [
    get_city_performance,
    get_store_performance,
    get_hour_detail,
    run_store_rca,
    run_hour_rca,
    list_stores,
]
