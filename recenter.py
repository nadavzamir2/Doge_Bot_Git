import time

def _edge_hit(last_price: float, lower: float, upper: float, edge_pct: int) -> bool:
    width = upper - lower
    if width <= 0:
        return False
    lower_edge = lower + width * (edge_pct / 100.0)
    upper_edge = upper - width * (edge_pct / 100.0)
    return (last_price <= lower_edge) or (last_price >= upper_edge)

def need_recenter(last_price: float,
                  lower: float,
                  upper: float,
                  dwell_state: dict,
                  dwell_seconds: int = 600,
                  edge_pct: int = 90,
                  center_drift_pct_of_width: int | None = None) -> bool:
    """
    Return True if we should recenter:
    - price has dwelled near a range edge for at least dwell_seconds, OR
    - deviation from center exceeds a configured percentage of the width.
    dwell_state: {"now": time.time(), "hit_since": Optional[float]}
    """
    now = dwell_state.get("now") or time.time()
    dwell_state["now"] = now

    # Criterion 1: edge dwell
    hit = _edge_hit(last_price, lower, upper, edge_pct)
    if hit:
        if dwell_state.get("hit_since") is None:
            dwell_state["hit_since"] = now
    else:
        dwell_state["hit_since"] = None
    edge_ok = hit and (now - (dwell_state.get("hit_since") or now)) >= dwell_seconds

    # Criterion 2: center drift
    drift_ok = False
    if center_drift_pct_of_width is not None:
        width = upper - lower
        if width > 0:
            center = (lower + upper) / 2.0
            drift_ok = abs(last_price - center) >= width * (center_drift_pct_of_width / 100.0)

    return bool(edge_ok or drift_ok)

def recenter_bounds(around_price: float, lower: float, upper: float) -> tuple[float, float]:
    """
    Shift the range so around_price is centered, keeping same width.
    Clamp lower bound to a small positive minimum.
    """
    width = max(upper - lower, 1e-9)
    new_lower = max(1e-9, around_price - width / 2.0)
    new_upper = new_lower + width
    return (new_lower, new_upper)
