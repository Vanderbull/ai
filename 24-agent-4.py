import os
import smtplib
import yfinance as yf
import ollama
import schedule
import time
import random
import datetime 
from dotenv import load_dotenv
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

# Ladda miljövariabler från .env-filen
load_dotenv()

# --- INSTÄLLNINGAR FRÅN .env ---
# OBS: SMTP_PASS ska vara ditt Gmail App-lösenord
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
        # print(f"FEL vid hämtning av aktiedata: {e}")
        return None

def get_recent_news(ticker_symbol: str) -> list:
    """Hämtar nyheter publicerade under de senaste 24 timmarna."""
    
    cutoff_time = datetime.datetime.now() - datetime.timedelta(hours=24)
    
    try:
        stock = yf.Ticker(ticker_symbol)
        news_list = stock.news
        
        recent_news = []
        for item in news_list:
            publish_time = datetime.datetime.fromtimestamp(item['providerPublishTime'])
            
            if publish_time > cutoff_time:
                recent_news.append({
                    'title': item['title'],
                    'link': item['link'],
                    'publisher': item['publisher'],
                    'time': publish_time.strftime('%Y-%m-%d %H:%M')
                })
        
        return recent_news
        
    except Exception as e:
        print(f"FEL vid hämtning av nyheter för {ticker_symbol}: {e}")
        return []

def get_llm_commentary(ticker: str, price: float | None, purpose: str) -> str:
    """Använder Ollama för att generera en kommentar eller beslut."""
    try:
        client = ollama.Client(host='http://localhost:11434')
        
        if purpose == "COMMENTARY":
            system_prompt = (
                "Du är en finansiell analytiker. Skriv en kort, koncis kommentar "
                f"på en enda mening (max 20 ord) om aktiekursen för {ticker}."
                "Kommentera endast priset och trenden, och inkludera inte emojis."
            )
            user_prompt = f"Aktuellt pris för {ticker} är ${price:.2f}. Vad är din korta bedömning?"
        
        elif purpose == "DECISION":
            system_prompt = (
                "Du är en proaktiv, personlig assistent som övervakar marknaden. "
                "Du ska bedöma om priset är exceptionellt högt eller lågt och värt omedelbar notifiering. "
                "Svara endast med 'NOTIFY' om priset är intressant, annars svara 'HOLD'. Motivera inte svaret."
            )
            user_prompt = f"Aktuellt pris för {TICKER_SYMBOL} är ${price:.2f}. Normalt intervall är 50-100. Borde jag skicka en proaktiv notifiering?"
        
        response = client.chat(
            model=OLLAMA_MODEL,
            messages=[
                {'role': 'system', 'content': system_prompt},
                {'role': 'user', 'content': user_prompt},
            ]
        )
        return response['message']['content'].strip()
    except Exception as e:
        # print(f"FEL vid Ollama-kommunikation: {e}")
        return "Kunde inte generera AI-kommentar."


# --- E-POSTFUNKTIONER ---

def send_stock_email(price: float | None, ticker: str, commentary: str, news_items: list):
    """Skickar den fasta dagliga rapporten inklusive nyheter."""
    price_str = f"${price:,.2f}" if price is not None else "PRIS EJ TILLGÄNGLIGT"
    
    # Bygg nyhetssektionen
    news_html = ""
    if news_items:
        news_html = "<h3>📰 Aktuella Nyheter (Senaste 24h)</h3><ul>"
        for item in news_items:
            news_html += f'<li><strong>{item["title"]}</strong> ({item["time"]} - {item["publisher"]})<br><a href="{item["link"]}">Läs mer</a></li>'
        news_html += "</ul>"
    else:
        news_html = "<h3>📰 Inga Nya Nyheter</h3><p>Inga nya relevanta nyheter hittades sedan den senaste rapporten.</p>"

    msg = MIMEMultipart()
    msg['From'] = SMTP_USER
    msg['To'] = MAIL_TO
    msg['Subject'] = f"📊 Daglig Rapport: {ticker} - Pris: {price_str} ({len(news_items)} nyheter)"

    html_body = f"""\
    <html>
      <body>
        <h2>Daglig Aktierapport för {ticker}</h2>
        <p>Pris vid marknadsstängning: <strong>{price_str}</strong></p>
        
        <h3>AI-Analys:</h3>
        <p>"{commentary}"</p>
        
        <hr>
        {news_html}
        <hr>

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
    """Huvudfunktion som körs en gång dagligen (kl 17:00)."""
    print(f"\n--- Kör DAGLIG RAPPORT ({time.strftime('%Y-%m-%d %H:%M:%S')}) ---")
    
    price = get_stock_price(TICKER_SYMBOL)
    commentary = get_llm_commentary(TICKER_SYMBOL, price if price else 0, "COMMENTARY")
    recent_news = get_recent_news(TICKER_SYMBOL)
    
    send_stock_email(price, TICKER_SYMBOL, commentary, recent_news)


def pro_active_check_job():
    """Kör slumpmässig Ollama-analys och triggar e-post vid NOTIFY."""
    print(f"\n--- Kör PROAKTIV PRISKONTROLL ({time.strftime('%Y-%m-%d %H:%M:%S')}) ---")
    
    price = get_stock_price(TICKER_SYMBOL)
    if price is None:
        print("Kontrollen hoppades över: kunde inte hämta pris.")
        return

    llm_decision = get_llm_commentary(TICKER_SYMBOL, price, "DECISION").upper()

    if llm_decision == 'NOTIFY':
        commentary = get_llm_commentary(TICKER_SYMBOL, price, "COMMENTARY")
        print(f"** PROAKTIV HÄNDELSE TRIGGAD! Pris: ${price:.2f}. **")
        send_proactive_email(price, TICKER_SYMBOL, commentary)
    else:
        print(f"Priset (${price:.2f}) är normalt. Agenten håller. Beslut: {llm_decision}")


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
    else:
        run_agent()
