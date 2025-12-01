import os
import smtplib
import yfinance as yf
import ollama
import schedule
import time
import random
import datetime 
import re 
from dateutil import parser 
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
    """
    Hämtar nyheter publicerade under de senaste 24 timmarna.
    Hantera två olika tidstämplingsformat från yfinance/Yahoo Finance: 
    1. providerPublishTime (UNIX timestamp)
    2. content['pubDate'] (ISO 8601 string)
    """
    cutoff_time = datetime.datetime.now() - datetime.timedelta(hours=24)
    recent_news = []
    
    try:
        stock = yf.Ticker(ticker_symbol)
        news_list = stock.news
        
        for item in news_list:
            publish_timestamp = None
            
            # 1. FÖRSÖK: Hämta UNIX-timestamp (äldre, direkt format)
            if 'providerPublishTime' in item:
                publish_timestamp = item['providerPublishTime']
            
            # 2. FÖRSÖK: Hämta ISO-sträng (nytt, inbäddat format)
            elif 'content' in item and 'pubDate' in item['content']:
                try:
                    publish_dt = parser.isoparse(item['content']['pubDate'])
                    publish_timestamp = publish_dt.timestamp()
                except Exception:
                    continue 
            
            if publish_timestamp is None:
                continue # Hoppa över artiklar där ingen tidsinformation hittades
            
            try:
                publish_time = datetime.datetime.fromtimestamp(publish_timestamp)
                
                if publish_time > cutoff_time:
                    
                    content = item.get('content', item) 
                    
                    title = content.get('title', 'Ingen rubrik')
                    link = content.get('canonicalUrl', {}).get('url', content.get('link', '#'))
                    publisher = content.get('provider', {}).get('displayName', item.get('publisher', 'Okänd källa'))
                    
                    recent_news.append({
                        'title': title,
                        'link': link,
                        'publisher': publisher,
                        'time': publish_time.strftime('%Y-%m-%d %H:%M')
                    })
            except Exception as e:
                print(f"Varning: Fel vid tidsomvandling för nyhetsartikel: {e}")
                
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
        
        # Denna gren ska inte längre anropas för nyheter, men behålls för LLM-logiken.
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
            # Returnera tydligt felmeddelande för att undvika meningslösa svar.
            return "BEHÅLL", "AI-analytikerns svar kunde inte tolkas."

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
    """Skickar proaktiv e-post vid KÖP/SÄLJ rekommendation ELLER direkt nyhetsnotis."""
    
    price_str = f"${price:,.2f}" if price is not None else "PRIS EJ TILLGÄNGLIGT"
    
    # Bestäm typ av notis och färg
    if action == 'KÖP':
        alert_text = "🚨 KÖP-SIGNAL Upptäckt!"
        color = "#28a745" # Grön
        display_action = "KÖP"
        source_message = "Prisdata analyserad av AI-analytiker."
    elif action == 'SÄLJ':
        alert_text = "⚠️ SÄLJ-SIGNAL Upptäckt!"
        color = "#dc3545" # Röd
        display_action = "SÄLJ"
        source_message = "Prisdata analyserad av AI-analytiker."
    else: # NOTIS (För direkta nyhetsnotiser)
        alert_text = "🔔 NYHETSNOTIS: Viktiga Uppdateringar!"
        color = "#007bff" # Blå (Neutral/Information)
        display_action = "NYHETER FUNNA"
        source_message = "Direkt notis baserad på publicerade nyheter de senaste 24 timmarna."

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
        
        <h3>🔬 Analys / Notis:</h3>
        <p>{source_message}</p>
        <p style="font-size: 36px; font-weight: bold; color: {color}; margin: 5px 0;">{display_action}</p>
        
        <h3>Motivering:</h3>
        <blockquote style="border-left: 4px solid {color}; padding-left: 15px; margin: 15px 0; background: #f8f9fa;">
          "{reasoning}"
        </blockquote>
        
        <hr>
        
        {"" if action in ['KÖP', 'SÄLJ'] else "<h3>📰 Aktuella Nyheter (Inkluderat i Notisen)</h3><ul>" + "".join([f'<li><strong>{item["title"]}</strong> ({item["publisher"]})<br><a href="{item["link"]}">Läs mer</a></li>' for item in news_items]) + "</ul>"}
        
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
    """Kör slumpmässig koll av Pris ELLER Nyheter och agerar vid KÖP/SÄLJ eller nyhetshändelser."""
    
    check_type = random.choice(['PRICE', 'NEWS']) 
    print(f"\n--- Kör PROAKTIV KONTROLL (Fokus: {check_type}) ---")
    
    price = get_stock_price(TICKER_SYMBOL)
    news_items = get_recent_news(TICKER_SYMBOL)

    if check_type == 'PRICE':
        if price is not None:
            # Använd LLM-analys för pris
            recommendation, reasoning = get_llm_recommendation(TICKER_SYMBOL, 'PRICE', price=price)
            
            if recommendation in ['KÖP', 'SÄLJ']:
                print(f"** PROAKTIV HÄNDELSE TRIGGAD! Rekommendation: {recommendation} (Pris) **")
                # Skicka e-post med KÖP/SÄLJ action och motivering
                send_proactive_email(price, TICKER_SYMBOL, recommendation, reasoning, 'PRIS', [])
            else:
                # Logga BEHÅLL eller analysfel.
                print(f"Agenten avstår från aktion. Beslut: {recommendation}. Motivering: {reasoning}")
        else:
            print("Kontrollen hoppades över: Prisdata ej tillgänglig för PRIS-kontroll.")

    elif check_type == 'NEWS':
        if news_items:
            # Direkt notis för nyheter, ingen LLM-analys
            action = "NOTIS"
            reasoning = "Nya nyheter har publicerats under de senaste 24 timmarna som kräver din uppmärksamhet."
            print(f"** PROAKTIV HÄNDELSE TRIGGAD! Nyheter funna (Direkt Notis) **")
            # Skicka e-post med nyheterna
            send_proactive_email(price, TICKER_SYMBOL, action, reasoning, 'NYHETER', news_items)
        else:
            print("Kontrollen hoppades över: Inga nya nyheter hittades.")


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
