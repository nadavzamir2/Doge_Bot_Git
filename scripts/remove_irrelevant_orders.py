import json

input_path = "data/order_history_local.json"
output_path = input_path  # overwrite in place

def is_relevant(order):
    # Remove orders with time == "—" or execution_time == "—"
    return order.get("time") != "—" and order.get("execution_time") != "—"

def main():
    with open(input_path, "r") as f:
        orders = json.load(f)
    filtered_orders = [o for o in orders if is_relevant(o)]
    print(f"Filtered {len(orders) - len(filtered_orders)} irrelevant orders.")
    with open(output_path, "w") as f:
        json.dump(filtered_orders, f, indent=2)

if __name__ == "__main__":
    main()
