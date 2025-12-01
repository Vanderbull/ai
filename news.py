import yfinance as yf
import json

# --- Configuration ---
TICKER = "AMD"

# --- 1. Create the Ticker object ---
print(f"--- Fetching Raw News Data for {TICKER} ---")
amd_ticker = yf.Ticker(TICKER)

# --- 2. Retrieve the list of news articles ---
# This is the raw list of dictionaries exactly as received from yfinance.
raw_news_data = amd_ticker.news

# --- 3. Output the raw data ---
if not raw_news_data:
    print("No news articles found.")
else:
    # Using json.dumps for a cleaner, indented output of the raw data structure
    # The 'indent=4' makes it much easier to read the dictionary keys and values.
    print(json.dumps(raw_news_data, indent=4))
