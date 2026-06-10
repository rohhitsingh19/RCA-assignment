"""
Pure deterministic RCA logic.
The LLM never decides thresholds — this code does.
"""

from dataclasses import dataclass, field

# Thresholds — change here only, never in a prompt
DEMAND_SPIKE_THRESHOLD = 1.10   # actual > forecast * 1.10
BOOKING_GAP_THRESHOLD = 0.90    # current_capacity_booked < 0.90
UTIL_GAP_THRESHOLD = 0.85       # man_hour < 0.85
OR2A_SLA = 8.0                  # minutes
SUSTAINED_PILEUP_HOURS = 3      # consecutive hours with pileup


@dataclass
class HourRCA:
    store: str
    hour: int
    avg_or2a: float
    total_orders: int
    order_projection: float

    # Check 1 — Demand
    demand_spike: bool = False
    demand_pct_over: float = 0.0

    # Check 2 — Pileup
    pileup: bool = False
    pileup_count: int = 0
    sustained_pileup: bool = False

    # Check 3a — Booking
    booking_gap: bool = False
    booked_size: int = 0
    current_size: int = 0
    booking_pct: float = 0.0

    # Check 3b — Utilization
    util_gap: bool = False
    man_hour: float = 0.0
    noshow_count: int = 0

    # Flags summary
    flags: list[str] = field(default_factory=list)

    def summary_line(self) -> str:
        if not self.flags:
            return f"OR2A was {self.avg_or2a:.1f} min — no clear root cause flagged by thresholds."
        causes = ", ".join(self.flags)
        return f"OR2A was {self.avg_or2a:.1f} min over threshold due to: {causes}."


def run_rca_for_hour(row: dict, all_store_hours: list[dict]) -> HourRCA:
    """
    Run all three RCA checks for one store-hour row.
    all_store_hours is the full list of rows for that store-day (used for sustained pileup check).
    """
    store = row.get("store", "")
    hour = int(row.get("hour", 0))
    avg_or2a = float(row.get("avg_or2a") or 0)
    total_orders = int(row.get("total_orders") or 0)
    order_projection = float(row.get("order_projection") or 0)
    pileup_flag = int(row.get("pileup_flag") or 0)
    pileup_count = int(row.get("pileup_count") or 0)
    current_size = int(row.get("current_size") or 0)
    booked_size = int(row.get("booked_size") or 0)
    current_capacity_booked = float(row.get("current_capacity_booked") or 0)
    man_hour = float(row.get("man_hour") or 0)
    noshow_count = int(row.get("noshow_count") or 0)

    rca = HourRCA(
        store=store,
        hour=hour,
        avg_or2a=avg_or2a,
        total_orders=total_orders,
        order_projection=order_projection,
        booked_size=booked_size,
        current_size=current_size,
        booking_pct=current_capacity_booked * 100,
        man_hour=man_hour,
        noshow_count=noshow_count,
        pileup_count=pileup_count,
    )

    # --- Check 1: Demand Spike ---
    if order_projection and order_projection > 0:
        if total_orders > order_projection * DEMAND_SPIKE_THRESHOLD:
            rca.demand_spike = True
            rca.demand_pct_over = ((total_orders / order_projection) - 1) * 100
            rca.flags.append(f"Demand Spike (+{rca.demand_pct_over:.0f}% over forecast)")

    # --- Check 2: Pileup ---
    if pileup_flag == 1:
        rca.pileup = True
        rca.flags.append(f"Pileup ({pileup_count} orders from previous hour)")

        # Check for sustained pileup: 3+ consecutive hours with pileup_flag=1
        pileup_hours = sorted([
            int(r.get("hour", 0))
            for r in all_store_hours
            if int(r.get("pileup_flag") or 0) == 1
        ])
        rca.sustained_pileup = _has_consecutive_run(pileup_hours, hour, SUSTAINED_PILEUP_HOURS)
        if rca.sustained_pileup:
            rca.flags.append("Sustained Pileup (3+ consecutive hours)")

    # --- Check 3a: Booking Gap ---
    if current_size > 0 and current_capacity_booked < BOOKING_GAP_THRESHOLD:
        rca.booking_gap = True
        rca.flags.append(f"Booking Gap ({booked_size}/{current_size} slots = {current_capacity_booked*100:.0f}%)")

    # --- Check 3b: Utilization Gap ---
    if man_hour > 0 and man_hour < UTIL_GAP_THRESHOLD:
        rca.util_gap = True
        rca.flags.append(f"Utilization Gap (man_hour={man_hour:.2f}, {noshow_count} no-shows)")

    return rca


def _has_consecutive_run(hours: list[int], target_hour: int, min_run: int) -> bool:
    """Check if target_hour is part of a consecutive run of at least min_run hours."""
    if not hours:
        return False
    # Find all consecutive runs
    runs = []
    current_run = [hours[0]]
    for h in hours[1:]:
        if h == current_run[-1] + 1:
            current_run.append(h)
        else:
            runs.append(current_run)
            current_run = [h]
    runs.append(current_run)

    for run in runs:
        if target_hour in run and len(run) >= min_run:
            return True
    return False


def format_rca_output(rca: HourRCA) -> str:
    """Format one HourRCA into the playbook's output template."""
    demand_line = (
        f"YES — {rca.total_orders} orders vs {rca.order_projection:.0f} projected (+{rca.demand_pct_over:.0f}%)"
        if rca.demand_spike
        else f"NO — {rca.total_orders} orders vs {rca.order_projection:.0f} projected"
    )

    pileup_detail = ""
    if rca.pileup:
        pileup_detail = f"YES — {rca.pileup_count} orders carried from previous hour"
        if rca.sustained_pileup:
            pileup_detail += " + SUSTAINED PILEUP"
    else:
        pileup_detail = f"NO — no pileup"

    booking_line = f"{rca.booked_size} of {rca.current_size} slots booked ({rca.booking_pct:.0f}%)"
    util_line = f"man_hour ratio {rca.man_hour:.2f} ({rca.noshow_count} no-shows)"

    return f"""### {rca.store} — Hour {rca.hour} — avg OR2A: {rca.avg_or2a:.1f} min (threshold: {OR2A_SLA} min)

1. Demand Spike: {demand_line}
2. Pileup: {pileup_detail}
3. Supply:
   a. Booking: {booking_line}
   b. Utilization: {util_line}

**Summary**: {rca.summary_line()}"""
