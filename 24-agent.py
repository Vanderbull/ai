import os
import smtplib
import yfinance as yf
import ollama
import schedule # <--- NYTT: För schemaläggning
import time     # För pauser
from dotenv import load_dotenv
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

# Ladda miljövariabler
load_dotenv()

# --- INSTÄLLNINGAR FRÅN .env ---
SMTP_HOST = os.environ.get("SMTP_HOST")
SMTP_PORT = int(os.environ.get("SMTP_PORT", 587))
SMTP_USER = os.environ.get("SMTP_USER")
SMTP_PASS = os.environ.get("SMTP_PASS")
MAIL_TO = os.environ.get("MAIL_TO")
TICKER_SYMBOL = os.environ.get("YFINANCE_TICKER", "AMD")
OLLAMA_MODEL = os.environ.get("OLLAMA_MODEL", "llama3")

# --- KÄRNFUNKTIONER (Från tidigare) ---

def get_stock_price(ticker_symbol: str) -> float | None:
    # (Samma logik som tidigare för att hämta priset)
    try:
        stock = yf.Ticker(ticker_symbol)
        info = stock.info
        return info.get('currentPrice') or info.get('regularMarketPrice')
    except Exception as e:
        print(f"FEL vid hämtning av aktiedata: {e}")
        return None

def get_llm_commentary(ticker: str, price: float) -> str:
    # (Samma logik som tidigare för Ollama-analysen)
    try:
        client = ollama.Client(host='http://localhost:11434')
        system_prompt = (
            "Du är en finansiell analytiker. Skriv en kort, koncis kommentar "
            f"på en enda mening (max 20 ord) om aktiekursen för {ticker}."
            "Kommentera endast priset och trenden, och inkludera inte emojis."
        )
        user_prompt = f"Aktuellt pris för {ticker} är ${price:.2f}. Vad är din korta bedömning?"
        response = client.chat(
            model=OLLAMA_MODEL,
            messages=[
                {'role': 'system', 'content': system_prompt},
                {'role': 'user', 'content': user_prompt},
            ]
        )
        return response['message']['content'].strip()
    except Exception as e:
        # Säkerställer att agenten inte kraschar om Ollama är nere
        print(f"FEL vid Ollama-kommunikation: {e}")
        return "Kunde inte generera AI-kommentar."

def send_stock_email(price: float, ticker: str, commentary: str):
    # (Samma logik som tidigare för att skicka e-post)
    price_str = f"${price:,.2f}" if price is not None else "PRIS EJ TILLGÄNGLIGT"
    msg = MIMEMultipart()
    # ... (resten av e-postförberedelsen)
    try:
        server = smtplib.SMTP(SMTP_HOST, SMTP_PORT)
        server.ehlo()
        server.starttls()
        server.ehlo()
        server.login(SMTP_USER, SMTP_PASS)
        # ... (skicka meddelandet)
        print(f"✅ E-post skickat framgångsrikt till {MAIL_TO}!")
    except Exception as e:
        print(f"❌ FEL vid sändning av e-post: {e}")
    finally:
        if 'server' in locals():
            server.quit()
        
# --- AGENTENS HUVUDFUNKTION (Jobbet som ska schemaläggas) ---

def daily_reporting_job():
    """Huvudfunktion som körs en gång dagligen."""
    print(f"\n--- Kör DAGLIG RAPPORT ({time.strftime('%Y-%m-%d %H:%M:%S')}) ---")
    
    # 1. Hämta priset
    price = get_stock_price(TICKER_SYMBOL)
    
    if price is not None:
        # 2. Hämta Ollama-kommentaren
        commentary = get_llm_commentary(TICKER_SYMBOL, price)
        print(f"AI Kommentar: {commentary}")
        
        # 3. Skicka e-post
        send_stock_email(price, TICKER_SYMBOL, commentary)
    else:
        print("Kunde inte slutföra den dagliga rapporten: pris saknas.")

# --- AGENTENS KONTINUERLIGA LOOP ---

def run_agent():
    """Huvudloopen som kör agenten kontinuerligt."""
    print("🤖 Agenten startar...")

    # SCHEMALÄGG HUVUDFUNKTIONEN
    # Exempel: Kör varje dag kl. 17:00 (efter att marknaden i USA stängt)
    # OBS: Tiden är i systemets lokala tid (CET i detta fall)
    schedule.every().day.at("17:00").do(daily_reporting_job)
    print("Schemalagt: Daglig rapport körs kl 17:00 CET.")

    # Här kan du lägga till andra jobb som agenten ska utföra, t.ex. varje timme:
    # schedule.every(1).hour.do(some_other_monitoring_function)
    
    # Kör loopen som kontrollerar schemat
    while True:
        schedule.run_pending()
        time.sleep(1) # Väntar 1 sekund mellan varje schemakontroll

if __name__ == "__main__":
    if not all([SMTP_HOST, SMTP_USER, SMTP_PASS, MAIL_TO, TICKER_SYMBOL]):
        print("❌ FEL: Nödvändiga miljövariabler saknas. Kontrollera .env-filen.")
    else:
        run_agent()
