# save as test_binance_auth.py
import os, ccxt

region = os.getenv("BINANCE_REGION", "com")
if region == "us":
    ex = ccxt.binanceus({
        'apiKey': os.getenv("BINANCE_API_KEY"),
        'secret': os.getenv("BINANCE_API_SECRET"),
        'options': {'adjustForTimeDifference': True},
        'recvWindow': 5000
    })
else:
    ex = ccxt.binance({
        'apiKey': os.getenv("BINANCE_API_KEY"),
        'secret': os.getenv("BINANCE_API_SECRET"),
        'options': {'defaultType': 'spot', 'adjustForTimeDifference': True},
        'recvWindow': 5000
    })

print("Time:", ex.public_get_time())
print("Me (account):", ex.fetch_balance()['info']['accountType'])
