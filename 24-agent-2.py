import os
import smtplib
import yfinance as yf
import ollama
import schedule
import time
import random # <--- NYTT: För slumpmässig tidsberäkning
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
OLLAMA_MODEL = os.environ.get("OLLAMA_MODEL", "llama3.1")

# --- KÄRNFUNKTIONER (Oförändrade) ---
# get_stock_price, get_llm_commentary, send_stock_email (Daglig Rapport)

def get_stock_price(ticker_symbol: str) -> float | None:
    # ... (logik för att hämta pris) ...
    try:
        stock = yf.Ticker(ticker_symbol)
        info = stock.info
        return info.get('currentPrice') or info.get('regularMarketPrice')
    except Exception:
        return None

def get_llm_commentary(ticker: str, price: float) -> str:
    # ... (logik för att få en beskrivande kommentar) ...
    try:
        client = ollama.Client(host='http://localhost:11434')
        system_prompt = "Du är en finansiell analytiker. Skriv en kort, koncis kommentar på en enda mening (max 20 ord) om aktiekursen..."
        user_prompt = f"Aktuellt pris för {ticker} är ${price:.2f}. Vad är din korta bedömning?"
        response = client.chat(model=OLLAMA_MODEL, messages=[{'role': 'system', 'content': system_prompt}, {'role': 'user', 'content': user_prompt}])
        return response['message']['content'].strip()
    except Exception:
        return "Kunde inte generera AI-kommentar."
        
def send_proactive_email(price: float, ticker: str, commentary: str):
    # ... (logik för att skicka en VÄRNINGS-E-post) ...
    # (Använd den tidigare proaktiva e-postlogiken med varningsrubrik)
    # ... (SMTP-anslutning och sändning) ...
    print(f"✅ Proaktivt e-post skickat till {MAIL_TO}!")

def daily_reporting_job():
    """Huvudfunktion som körs en gång dagligen (fast tid)."""
    print(f"\n--- Kör DAGLIG RAPPORT ({time.strftime('%Y-%m-%d %H:%M:%S')}) ---")
    price = get_stock_price(TICKER_SYMBOL)
    if price is not None:
        commentary = get_llm_commentary(TICKER_SYMBOL, price)
        # Använd standard send_stock_email (som inte visas här, men antas finnas)
        # send_stock_email(price, TICKER_SYMBOL, commentary)
        print("Daglig rapport skickad (simulerat).")
    else:
        print("Kunde inte slutföra den dagliga rapporten.")


# --- AGENTENS SLUMPmässiga ÖVERVAKNINGSLOGIK ---

def pro_active_check_job():
    """Hämtar pris, låter Ollama bedöma om priset är intressant, och skickar e-post vid NOTIFY."""
    print(f"\n--- Kör PROAKTIV PRISKONTROLL ({time.strftime('%Y-%m-%d %H:%M:%S')}) ---")
    
    price = get_stock_price(TICKER_SYMBOL)
    if price is None:
        print("Kontrollen hoppades över: kunde inte hämta pris.")
        return

    try:
        client = ollama.Client(host='http://localhost:11434')
        
        system_prompt = (
            "Du är en proaktiv, personlig assistent som övervakar aktiemarknaden. "
            "Du ska bedöma om det aktuella priset är exceptionellt högt eller exceptionellt lågt "
            "och därmed är värt en omedelbar notifiering. Svara endast med 'NOTIFY' om priset är intressant, "
            "annars svara 'HOLD'. Motivera inte svaret."
        )
        
        user_prompt = f"Aktuellt pris för {TICKER_SYMBOL} är ${price:.2f}. Normalt intervall är 50-100. Borde jag skicka en proaktiv notifiering?"
        
        response = client.chat(
            model=OLLAMA_MODEL,
            messages=[
                {'role': 'system', 'content': system_prompt},
                {'role': 'user', 'content': user_prompt},
            ]
        )
        
        llm_decision = response['message']['content'].strip().upper()

        if llm_decision == 'NOTIFY':
            commentary = get_llm_commentary(TICKER_SYMBOL, price)
            print(f"** PROAKTIV HÄNDELSE TRIGGAD! Pris: ${price:.2f}. **")
            send_proactive_email(price, TICKER_SYMBOL, commentary)
        else:
            print(f"Priset (${price:.2f}) är normalt. Agenten håller. Beslut: {llm_decision}")

    except Exception as e:
        print(f"FEL under proaktiv kontroll (Ollama eller nätverk): {e}")


# --- AGENTENS KONTINUERLIGA LOOP (Hanterar scheman och slumptal) ---

def run_agent():
    """Huvudloopen som kör agenten kontinuerligt med blandad schemaläggning."""
    print("🤖 Agenten startar...")

    # SCHEMALÄGG DEN DAGLIGA RAPPORTEN (Fast jobb)
    schedule.every().day.at("17:00").do(daily_reporting_job).tag('daily')
    print("Schemalagt: Daglig rapport körs kl 17:00 CET.")
    
    # Första proaktiva kontrollen körs omedelbart
    next_check_time = time.time() 
    
    print("Agenten går in i standby-läge. Övervakning aktiv...")

    while True:
        # 1. Kör alla schemalagda (fasta) uppgifter (t.ex. kl. 17:00 rapporten)
        schedule.run_pending()
        
        # 2. Kontrollera om det är dags för nästa proaktiva kontroll
        if time.time() >= next_check_time:
            
            # Kör den proaktiva uppgiften
            pro_active_check_job()
            
            # Beräkna nästa slumpmässiga tid
            # Slumpmässigt intervall mellan 60 sekunder (1 minut) och 7200 sekunder (2 timmar)
            random_delay = random.randint(60, 7200) 
            next_check_time = time.time() + random_delay
            
            # Printa den nya schemalagda tiden för loggning
            delay_minutes = random_delay / 60
            if delay_minutes < 2:
                 print(f"Nästa proaktiva kontroll schemalagd om {random_delay} sekunder.")
            else:
                 print(f"Nästa proaktiva kontroll schemalagd om {delay_minutes:.1f} minuter.")

        # 3. Vila en kort stund för att spara resurser och tillåta schemaläggaren att agera
        time.sleep(1)

if __name__ == "__main__":
    if not all([SMTP_HOST, SMTP_USER, SMTP_PASS, MAIL_TO, TICKER_SYMBOL]):
        print("❌ FEL: Nödvändiga miljövariabler saknas. Kontrollera .env-filen.")
    else:
        # VIKTIGT: Se till att Ollama-servern är igång (ollama serve)
        run_agent()
