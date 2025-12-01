import os
import smtplib
import yfinance as yf
import ollama
import schedule
import time
import random
import datetime 
import re 
from dotenv import load_dotenv
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

# Ladda miljövariabler från .env-filen
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
    """Hämtar det aktuella aktiepriset."""
    try:
        stock = yf.Ticker(ticker_symbol)
        info = stock.info
        return info.get('currentPrice') or info.get('regularMarketPrice')
    except Exception:
        return None

def get_recent_news(ticker_symbol: str) -> list:
    """Hämtar nyheter publicerade under de senaste 24 timmarna, med robust felhantering."""
    cutoff_time = datetime.datetime.now() - datetime.timedelta(hours=24)
    recent_news = []
    
    try:
        stock = yf.Ticker(ticker_symbol)
        news_list = stock.news
        
        for item in news_list:
            try:
                # Huvudfelhantering: Kontrollera om nyckeln finns innan vi försöker använda den
                publish_timestamp = item['pubDate'] 
                #publish_timestamp = item['providerPublishTime'] 
                publish_time = datetime.datetime.fromtimestamp(publish_timestamp)
                
                if publish_time > cutoff_time:
                    recent_news.append({
                        'title': item.get('title', 'Ingen rubrik'),
                        'link': item.get('link', '#'),
                        'publisher': item.get('publisher', 'Okänd källa'),
                        'time': publish_time.strftime('%Y-%m-%d %H:%M')
                    })
            except KeyError:
                # Fångar specifikt felet 'providerPublishTime'
                print(f"Varning: Hoppar över en nyhetsartikel för {ticker_symbol} eftersom 'providerPublishTime' saknas.")
            except Exception as e:
                # Fångar andra potentiella fel (t.ex. ogiltig timestamp)
                print(f"Varning: Ett okänt fel uppstod vid behandling av en nyhetsartikel: {e}")
                
        return recent_news
        
    except Exception as e:
        print(f"FEL vid hämtning av nyheter för {ticker_symbol} (Yfinance-nivå): {e}")
        return []

def get_llm_recommendation(ticker: str, check_type: str, price: float | None = None, news_items: list | None = None) -> tuple[str, str]:
    """
    Använder Ollama för att ge en strukturerad rekommendation (KÖP/SÄLJ/BEHÅLL) 
    och en motivering baserad på antingen pris eller nyheter, ur ett analytiskt perspektiv.
    """
    try:
        client = ollama.Client(host='http://localhost:11434')
        
        system_prompt = (
            "Du är en analytiker som kontinuerligt utvärderar aktien för din egen portfölj. "
            "Baserat på den inkommande informationen, bedöm om aktien är värd att läggas till eller säljas från din analysportfölj just nu. "
            "Svara ENDAST i formatet: AKTION: [KÖP|SÄLJ|BEHÅLL] MOTIVERING: [Din interna, analytiska motivering på svenska, max 3 meningar]."
        )
        
        user_prompt = ""
        
        if check_type == 'PRICE' and price is not None:
            user_prompt = f"Aktuellt pris för {ticker} är ${price:.2f}. Normalt intervall är 50-100. Är detta pris en signal för mig att KÖPA, SÄLJA eller BEHÅLLA?"
        
        elif check_type == 'NEWS' and news_items:
            news_text = "\n".join([f" - {n['title']} ({n['publisher']})" for n in news_items])
            user_prompt = f"Aktuella nyheter för {ticker} är:\n{news_text}\n\nSka jag KÖPA, SÄLJA eller BEHÅLLA baserat på dessa nyheter?"
        
        else:
            return "BEHÅLL", "Ingen giltig data skickades för analys."


        response = client.chat(
            model=OLLAMA_MODEL,
            messages=[
                {'role': 'system', 'content': system_prompt},
                {'role': 'user', 'content': user_prompt},
            ]
        )
        
        llm_response = response['message']['content'].strip()
        
        match = re.search(r"AKTION:\s*\[?(KÖP|SÄLJ|BEHÅLL)\]?\s*MOTIVERING:\s*(.*)", llm_response, re.IGNORECASE)
        
        if match:
            action = match.group(1).upper()
            reasoning = match.group(2).strip()
            return action, reasoning
        else:
            return "BEHÅLL", f"AI-analytikerns svar kunde inte tolkas. Agenten avstår från att agera."

    except Exception as e:
        print(f"FEL vid Ollama-kommunikation: {e}")
        return "BEHÅLL", "Kunde inte kontakta AI-analytikern för en bedömning."


def get_llm_commentary(ticker: str, price: float | None, purpose: str) -> str:
    """Genererar en standardkommentar för den dagliga rapporten."""
    try:
        client = ollama.Client(host='http://localhost:11434')
        system_prompt = "Du är en finansiell analytiker. Skriv en kort, koncis kommentar på en enda mening (max 20 ord) om aktiekursen."
        user_prompt = f"Aktuellt pris för {ticker} är ${price:.2f}. Vad är din korta bedömning?"
        response = client.chat(model=OLLAMA_MODEL, messages=[{'role': 'system', 'content': system_prompt}, {'role': 'user', 'content': user_prompt}])
        return response['message']['content'].strip()
    except Exception:
        return "Kunde inte generera AI-kommentar."


# --- E-POSTFUNKTIONER ---

def send_stock_email(price: float | None, ticker: str, commentary: str, news_items: list):
    """Skickar den fasta dagliga rapporten inklusive nyheter."""
    price_str = f"${price:,.2f}" if price is not None else "PRIS EJ TILLGÄNGLIGT"
    
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


def send_proactive_email(price: float | None, ticker: str, action: str, reasoning: str, check_type: str, news_items: list):
    """Skickar proaktiv e-post vid KÖP eller SÄLJ rekommendation, presenterad som en analys."""
    
    price_str = f"${price:,.2f}" if price is not None else "PRIS EJ TILLGÄNGLIGT"
    
    if action == 'KÖP':
        alert_text = "🚨 KÖP-SIGNAL Upptäckt!"
        color = "#28a745" # Grön
    else: # SÄLJ
        alert_text = "⚠️ SÄLJ-SIGNAL Upptäckt!"
        color = "#dc3545" # Röd
        
    news_html = ""
    if news_items:
        news_html = "<h3>📰 Aktuella Nyheter (Inkluderat i Analysen)</h3><ul>"
        for item in news_items:
            news_html += f'<li><strong>{item["title"]}</strong> ({item["publisher"]})<br><a href="{item["link"]}">Läs mer</a></li>'
        news_html += "</ul>"

    msg = MIMEMultipart()
    msg['From'] = SMTP_USER
    msg['To'] = MAIL_TO
    msg['Subject'] = f"{alert_text} för {ticker} (Baserat på {check_type})"
    
    html_body = f"""\
    <html>
      <body>
        <h2 style="color: {color};">{alert_text}</h2>
        
        <p style="font-size: 24px;">
          Aktie: <strong>{ticker}</strong><br>
          Aktuellt Pris: <strong>{price_str}</strong>
        </p>
        
        <h3>🔬 Agentens Interna Analys:</h3>
        <p>Analytikern bedömer att aktien nu är intressant för följande interna rekommendation:</p>
        <p style="font-size: 36px; font-weight: bold; color: {color}; margin: 5px 0;">{action}</p>
        
        <h3>Analytikerns Motivering (Baserat på {check_type}):</h3>
        <blockquote style="border-left: 4px solid {color}; padding-left: 15px; margin: 15px 0; background: #f8f9fa;">
          "{reasoning}"
        </blockquote>
        
        <hr>
        {news_html}
        <hr>
        <p>Denna notis skickades omedelbart efter att agenten utförde en {check_type}-kontroll som en del av sin kontinuerliga marknadsbevakning.</p>
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
        print(f"✅ Proaktivt {action}-e-post skickat till {MAIL_TO}!")
    except Exception as e:
        print(f"❌ FEL vid sändning av proaktiv e-post: {e}")
    finally:
        if 'server' in locals():
            server.quit()


# --- AGENTENS JOBB OCH LOOP ---

def daily_reporting_job():
    """Huvudfunktion som körs en gång dagligen (kl 17:00)."""
    print(f"\n--- Kör DAGLIG RAPPORT ({time.strftime('%Y-%m-%d %H:%M:%S')}) ---")
    
    price = get_stock_price(TICKER_SYMBOL)
    commentary = get_llm_commentary(TICKER_SYMBOL, price if price else 0, "COMMENTARY")
    recent_news = get_recent_news(TICKER_SYMBOL)
    
    send_stock_email(price, TICKER_SYMBOL, commentary, recent_news)


def pro_active_check_job():
    """Kör slumpmässig koll av Pris ELLER Nyheter och agerar vid KÖP/SÄLJ."""
    
    check_type = random.choice(['PRICE', 'NEWS']) 
    print(f"\n--- Kör PROAKTIV PRISKONTROLL (Fokus: {check_type}) ---")
    
    price = get_stock_price(TICKER_SYMBOL)
    news_items = get_recent_news(TICKER_SYMBOL)
    
    data_available = (check_type == 'PRICE' and price is not None) or (check_type == 'NEWS' and news_items)
    
    if not data_available:
        print(f"Kontrollen hoppades över: Ingen relevant data hittades för {check_type} just nu.")
        return

    # Hämta rekommendation och motivering från Ollama
    recommendation, reasoning = get_llm_recommendation(
        TICKER_SYMBOL, 
        check_type, 
        price=price, 
        news_items=news_items
    )
    
    # Om Ollama rekommenderar KÖP eller SÄLJ, skicka omedelbar notis
    if recommendation in ['KÖP', 'SÄLJ']:
        print(f"** PROAKTIV HÄNDELSE TRIGGAD! Rekommendation: {recommendation} **")
        send_proactive_email(price, TICKER_SYMBOL, recommendation, reasoning, check_type, news_items)
    else:
        print(f"Agenten avstår från aktion. Beslut: {recommendation}. Motivering: {reasoning}")


def run_agent():
    """Huvudloopen som kör agenten kontinuerligt."""
    print("🤖 Agenten startar...")

    schedule.every().day.at("17:00").do(daily_reporting_job).tag('daily')
    print("Schemalagt: Daglig rapport körs kl 17:00 CET.")
    
    next_check_time = time.time() 
    
    print("Agenten går in i standby-läge. Övervakning aktiv...")

    while True:
        schedule.run_pending()
        
        if time.time() >= next_check_time:
            pro_active_check_job()
            
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
