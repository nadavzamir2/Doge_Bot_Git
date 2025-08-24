def geom_levels(lower: float, upper: float, spacing_pct: float):
    f = 1.0 + spacing_pct / 100.0
    lvls, p = [], float(lower)
    while p <= upper * (1 + 1e-9):
        lvls.append(p)
        p *= f
        if len(lvls) > 5000:
            break
    return lvls

def active_buy_window(levels, last_price: float, window_n: int):
    below = [lv for lv in levels if lv < last_price]
    below.sort(reverse=True)
    return list(reversed(below[:window_n]))
