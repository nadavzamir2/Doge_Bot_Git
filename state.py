import json, os
STATE_FILE = "state.json"

_DEFAULT = {
    "base_order_usd": 5.0,
    "bank": {"usd": 0.0, "reinvest_usd": 0.0, "bnb_usd": 0.0},
    "dyn_bounds": None,  # [lower, upper]
}

def load_state():
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE, "r") as f:
            data = json.load(f)
        for k, v in _DEFAULT.items():
            data.setdefault(k, v)
        return data
    return _DEFAULT.copy()

def save_state(d: dict):
    tmp = STATE_FILE + ".tmp"
    with open(tmp, "w") as f:
        json.dump(d, f)
    os.replace(tmp, STATE_FILE)
