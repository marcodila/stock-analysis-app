import os
import sqlite3
import requests
from flask import Flask, request, jsonify, render_template

app = Flask(__name__)

# ============================================================
# Stock Analysis App — Fetches market data & serves an API
# ============================================================

# Alpha Vantage API credentials (read from environment)
API_KEY = os.environ.get("ALPHA_VANTAGE_API_KEY", "")
if not API_KEY:
    raise RuntimeError("ALPHA_VANTAGE_API_KEY environment variable is required")

DATABASE = "stocks.db"


def init_db():
    with sqlite3.connect(DATABASE) as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS stocks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                symbol TEXT NOT NULL,
                date TEXT NOT NULL,
                open_price REAL,
                high_price REAL,
                low_price REAL,
                close_price REAL NOT NULL,
                volume INTEGER,
                UNIQUE(symbol, date)
            )
        """)


with app.app_context():
    init_db()


def fetch_stock_data(symbol):
    """Fetch daily stock prices from Alpha Vantage."""
    url = "https://www.alphavantage.co/query"
    params = {
        "function": "TIME_SERIES_DAILY",
        "symbol": symbol,
        "apikey": API_KEY,
    }
    response = requests.get(url, params=params, timeout=10)
    response.raise_for_status()
    data = response.json()

    if "Error Message" in data:
        raise ValueError(f"Alpha Vantage error: {data['Error Message']}")
    if "Note" in data:
        raise ValueError(f"Alpha Vantage rate limit: {data['Note']}")
    if "Time Series (Daily)" not in data:
        raise ValueError(f"Unexpected Alpha Vantage response: {list(data.keys())}")

    time_series = data["Time Series (Daily)"]
    prices = []
    for date, values in time_series.items():
        prices.append({
            "date": date,
            "open": float(values["1. open"]),
            "high": float(values["2. high"]),
            "low": float(values["3. low"]),
            "close": float(values["4. close"]),
            "volume": int(values["5. volume"]),
        })
    return prices


def store_prices(symbol, prices):
    """Save fetched prices into the database."""
    with sqlite3.connect(DATABASE) as conn:
        cursor = conn.cursor()
        for p in prices:
            cursor.execute(
                "INSERT OR IGNORE INTO stocks "
                "(symbol, date, open_price, high_price, low_price, close_price, volume) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (symbol, p["date"], p["open"], p["high"], p["low"], p["close"], p["volume"]),
            )


def compute_rolling_average_return(prices, window=5):
    """Calculate the rolling average return over a given window.

    Expects a list of dicts with 'date' and 'close' keys.
    Sorts by date ascending before computing returns.
    """
    # Convert closing prices to daily percent change first
    sorted_prices = sorted(prices, key=lambda p: p["date"])
    closes = [p["close"] for p in sorted_prices]
    daily_returns = []
    for i in range(1, len(closes)):
        pct_change = ((closes[i] - closes[i - 1]) / closes[i - 1]) * 100
        daily_returns.append(pct_change)

    # Compute rolling average of the daily returns
    rolling_averages = []
    for i in range(len(daily_returns) - window + 1):
        window_slice = daily_returns[i : i + window]
        avg = sum(window_slice) / len(window_slice)
        rolling_averages.append(round(avg, 4))

    return rolling_averages


# ----- Flask Routes -----

@app.route("/")
def index():
    """Serve the stock analysis UI."""
    return render_template("index.html")


@app.route("/api/stock", methods=["GET"])
def get_stock():
    """Look up stored stock data by symbol."""
    symbol = request.args.get("symbol")
    if not symbol:
        return jsonify({"error": "symbol query parameter is required"}), 400

    from_date = request.args.get("from_date")
    to_date = request.args.get("to_date")
    try:
        limit = min(int(request.args.get("limit", 100)), 500)
    except ValueError:
        return jsonify({"error": "limit must be an integer"}), 400

    query = (
        "SELECT id, symbol, date, open_price, high_price, low_price, close_price, volume "
        "FROM stocks WHERE symbol = ?"
    )
    params = [symbol]

    if from_date and to_date:
        query += " AND date BETWEEN ? AND ?"
        params.extend([from_date, to_date])
    elif from_date:
        query += " AND date >= ?"
        params.append(from_date)
    elif to_date:
        query += " AND date <= ?"
        params.append(to_date)

    query += " LIMIT ?"
    params.append(limit)

    with sqlite3.connect(DATABASE) as conn:
        cursor = conn.cursor()
        cursor.execute(query, params)
        rows = cursor.fetchall()

    results = [
        {
            "id": r[0], "symbol": r[1], "date": r[2],
            "open_price": r[3], "high_price": r[4], "low_price": r[5],
            "close_price": r[6], "volume": r[7],
        }
        for r in rows
    ]
    return jsonify(results)


@app.route("/api/rolling-average", methods=["GET"])
def rolling_average():
    """Return the rolling average return for a stock."""
    symbol = request.args.get("symbol", "AAPL")
    try:
        window = int(request.args.get("window", 5))
    except ValueError:
        return jsonify({"error": "window must be an integer"}), 400

    try:
        prices = fetch_stock_data(symbol)
    except (requests.RequestException, ValueError) as e:
        return jsonify({"error": str(e)}), 502

    if not prices:
        return jsonify({"error": "No data found"}), 404

    store_prices(symbol, prices)
    averages = compute_rolling_average_return(prices, window=window)
    return jsonify({"symbol": symbol, "rolling_averages": averages})


if __name__ == "__main__":
    debug = os.environ.get("FLASK_DEBUG", "false").lower() in ("1", "true")
    port = int(os.environ.get("PORT", 5000))
    print("Starting Stock Analysis server ...")
    app.run(debug=debug, host="0.0.0.0", port=port)
