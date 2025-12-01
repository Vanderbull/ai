import os
import smtplib
import yfinance as yf
import ollama
import schedule
import time
import random
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

# --- KÄRNFUNKTIONER ---

def get_stock_price(ticker_symbol: str) -> float | None:
    """Hämtar det aktuella aktiepriset från Yahoo Finance."""
    try:
        stock = yf.Ticker(ticker_symbol)
        info = stock.info
        return info.get('currentPrice') or info.get('regularMarketPrice')
    except Exception as e:
        print(f"FEL vid hämtning av aktiedata: {e}")
        return None

def get_llm_commentary(ticker: str, price: float) -> str:
    """Använder Ollama för att generera en kort kommentar om priset."""
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
        print(f"FEL vid Ollama-kommunikation: {e}")
        return "Kunde inte generera AI-kommentar."

# ----------------------------------------------------
# NYA E-POSTFUNKTIONER MED KOMPLETT IMPLEMENTATION
# ----------------------------------------------------

def send_stock_email(price: float, ticker: str, commentary: str):
    """Skickar den fasta dagliga rapporten (sammanfattning)."""
    price_str = f"${price:,.2f}" if price is not None else "PRIS EJ TILLGÄNGLIGT"
    
    msg = MIMEMultipart()
    msg['From'] = SMTP_USER
    msg['To'] = MAIL_TO
    msg['Subject'] = f"📊 Daglig Rapport: {ticker} - Pris: {price_str}"

    html_body = f"""\
    <html>
      <body>
        <h2>Daglig Aktierapport för {ticker}</h2>
        <p>Pris vid marknadsstängning: <strong>{price_str}</strong></p>
        
        <h3>AI-Analys:</h3>
        <p>"{commentary}"</p>
        
        <p><small>Denna rapport skickas vid fast tidpunkt varje dag.</small></p>
      </body>
    </html>
    """
    msg.attach(MIMEText(html_body, 'html'))
    
    try:
        server = smtplib.SMTP(SMTP_HOST, SMTP_PORT)
        server.ehlo()
        server.starttls()
        server.ehlo()
        server.login(SMTP_USER, SMTP_PASS)
        server.sendmail(SMTP_USER, MAIL_TO, msg.as_string())
        print(f"✅ Daglig e-post skickad framgångsrikt till {MAIL_TO}!")
    except Exception as e:
        print(f"❌ FEL vid sändning av daglig e-post: {e}")
    finally:
        if 'server' in locals():
            server.quit()

def send_proactive_email(price: float, ticker: str, commentary: str):
    """Skickar proaktiv e-post vid en intressant prisrörelse (NOTIFY)."""
    price_str = f"${price:,.2f}"
    
    msg = MIMEMultipart()
    msg['From'] = SMTP_USER
    msg['To'] = MAIL_TO
    # Ändrad rubrik för att indikera en omedelbar händelse
    msg['Subject'] = f"🔔 ÖVERVAKNINGSVARNING: Proaktiv Notifiering för {ticker}"
    
    html_body = f"""\
    <html>
      <body>
        <h2>🎯 ALERT: Prisrörelse upptäckt för {ticker}</h2>
        <p style="font-size: 24px; color: #d9534f;">
          Aktuellt pris: <strong>{price_str}</strong>
        </p>
        
        <h3>Agentens Bedömning (Ollama)</h3>
        <blockquote style="border-left: 4px solid #d9534f; padding-left: 15px; margin: 15px 0; background: #fdf7f7;">
          "{commentary}"
        </blockquote>
        
        <p>Agenten bedömde detta pris som exceptionellt och värt en omedelbar notifiering, utöver den dagliga rapporten.</p>
        <p><small>Agenten fortsätter att övervaka marknaden.</small></p>
      </body>
    </html>
    """
    msg.attach(MIMEText(html_body, 'html'))
    
    try:
        server = smtplib.SMTP(SMTP_HOST, SMTP_PORT)
        server.ehlo()
        server.starttls()
        server.ehlo()
        server.login(SMTP_USER, SMTP_PASS)
        server.sendmail(SMTP_USER, MAIL_TO, msg.as_string())
        print(f"✅ Proaktivt e-post skickat till {MAIL_TO}!")
    except Exception as e:
        print(f"❌ FEL vid sändning av proaktiv e-post: {e}")
    finally:
        if 'server' in locals():
            server.quit()

# --- AGENTENS JOBB ---

def daily_reporting_job():
    """Huvudfunktion som körs en gång dagligen."""
    print(f"\n--- Kör DAGLIG RAPPORT ({time.strftime('%Y-%m-%d %H:%M:%S')}) ---")
    price = get_stock_price(TICKER_SYMBOL)
    if price is not None:
        commentary = get_llm_commentary(TICKER_SYMBOL, price)
        send_stock_email(price, TICKER_SYMBOL, commentary) # <-- Använder Dagliga E-postfunktionen
    else:
        print("Kunde inte slutföra den dagliga rapporten.")


def pro_active_check_job():
    """Kör den proaktiva Ollama-analysen och triggar e-post vid NOTIFY."""
    print(f"\n--- Kör PROAKTIV PRISKONTROLL ({time.strftime('%Y-%m-%d %H:%M:%S')}) ---")
    
    price = get_stock_price(TICKER_SYMBOL)
    if price is None:
        print("Kontrollen hoppades över: kunde inte hämta pris.")
        return

    try:
        client = ollama.Client(host='http://localhost:11434')
        
        # ... (Ollama system och user prompts som tidigare) ...
        system_prompt = "Du är en proaktiv, personlig assistent som övervakar aktiemarknaden. Du ska bedöma om det aktuella priset är exceptionellt högt eller exceptionellt lågt och därmed är värt en omedelbar notifiering. Svara endast med 'NOTIFY' om priset är intressant, annars svara 'HOLD'. Motivera inte svaret."
        user_prompt = f"Aktuellt pris för {TICKER_SYMBOL} är ${price:.2f}. Normalt intervall är 50-100. Borde jag skicka en proaktiv notifiering?"
        
        response = client.chat(
            model=OLLAMA_MODEL,
            messages=[{'role': 'system', 'content': system_prompt}, {'role': 'user', 'content': user_prompt}]
        )
        
        llm_decision = response['message']['content'].strip().upper()

        if llm_decision == 'NOTIFY':
            commentary = get_llm_commentary(TICKER_SYMBOL, price)
            print(f"** PROAKTIV HÄNDELSE TRIGGAD! Pris: ${price:.2f}. **")
            send_proactive_email(price, TICKER_SYMBOL, commentary) # <-- SÄNDER E-POST HÄR
        else:
            print(f"Priset (${price:.2f}) är normalt. Agenten håller. Beslut: {llm_decision}")

    except Exception as e:
        print(f"FEL under proaktiv kontroll (Ollama eller nätverk): {e}")


# --- AGENTENS KONTINUERLIGA LOOP ---

def run_agent():
    """Huvudloopen som kör agenten kontinuerligt med blandad schemaläggning."""
    print("🤖 Agenten startar...")

    # Schemalägg Dagliga Rapport (Fast jobb)
    schedule.every().day.at("17:00").do(daily_reporting_job).tag('daily')
    print("Schemalagt: Daglig rapport körs kl 17:00 CET.")
    
    # Första proaktiva kontrollen körs omedelbart
    next_check_time = time.time() 
    
    print("Agenten går in i standby-läge. Övervakning aktiv...")

    while True:
        schedule.run_pending()
        
        if time.time() >= next_check_time:
            pro_active_check_job()
            
            # Beräkna nästa slumpmässiga tid mellan 1 minut (60 sek) och 2 timmar (7200 sek)
            random_delay = random.randint(60, 7200) 
            next_check_time = time.time() + random_delay
            
            delay_minutes = random_delay / 60
            print(f"Nästa proaktiva kontroll schemalagd om {delay_minutes:.1f} minuter ({random_delay} sekunder).")

        time.sleep(1)

if __name__ == "__main__":
    if not all([SMTP_HOST, SMTP_USER, SMTP_PASS, MAIL_TO, TICKER_SYMBOL]):
        print("❌ FEL: Nödvändiga miljövariabler saknas. Kontrollera .env-filen.")
        print("Kontrollera att SMTP_HOST, SMTP_USER, SMTP_PASS, och MAIL_TO är satta.")
    else:
        # VIKTIGT: Se till att Ollama-servern körs i bakgrunden!
        run_agent()
