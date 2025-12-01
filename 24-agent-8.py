import os
import smtplib
import yfinance as yf
import ollama
import schedule
import time
import random
import datetime 
import re 
import pandas as pd
from dateutil import parser 
from dotenv import load_dotenv
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

# Ladda miljövariabler från .env-filen
load_dotenv()

# --- MOCK GOOGLE SEARCH (ERSÄTT DENNA DEL I EN VERKLIG MILJÖ) ---
class GoogleSearchMock:
    def search(self, queries):
        print(f"DEBUG: Mock-sökning körd för: {queries}")
        # Returnerar ett simulerat sökresultat för att testa prisutvinningen.
        return [{
            'snippet': 'Sort Guld 33 cl, 5,7%, Artikelnr: 1475. Pris: 15,90 kr.',
            'source': 'Systembolaget'
        }]

google_search = GoogleSearchMock() 
# --- SLUT PÅ MOCK ---


# --- INSTÄLLNINGAR FRÅN .env ---
SMTP_HOST = os.environ.get("SMTP_HOST")
SMTP_PORT = int(os.environ.get("SMTP_PORT", 587))
SMTP_USER = os.environ.get("SMTP_USER")
SMTP_PASS = os.environ.get("SMTP_PASS")
MAIL_TO = os.environ.get("MAIL_TO")
TICKER_SYMBOL = os.environ.get("YFINANCE_TICKER", "AMD")
OLLAMA_MODEL = os.environ.get("OLLAMA_MODEL", "llama3.1")

# --- KÄRNFUNKTIONER FÖR AKTIER ---

def get_sentiment_score(title: str) -> float:
    """Använder Ollama för att ge ett sentiment-värde (-1.0 till 1.0) för en rubrik."""
    try:
        client = ollama.Client(host='http://localhost:11434')
        system_prompt = (
            "Du är en sentiment-analysmotor. Analysera rubriken och ge dess sentiment-värde. "
            "Svara ENDAST med ett flyttal mellan -1.0 (mycket negativ) och 1.0 (mycket positiv). "
            "Exempel: '0.8', '-0.5', '0.0'. Inkludera inga andra ord eller tecken."
        )
        user_prompt = f"Rubrik: \"{title}\""
        
        response = client.chat(
            model=OLLAMA_MODEL,
            messages=[
                {'role': 'system', 'content': system_prompt},
                {'role': 'user', 'content': user_prompt},
            ]
        )
        
        score_str = response['message']['content'].strip()
        score = float(score_str)
        if -1.0 <= score <= 1.0:
            return score
        return 0.0
        
    except Exception as e:
        print(f"FEL vid sentiment-analys: {e}")
        return 0.0

def get_stock_price(ticker_symbol: str) -> float | None:
    """Hämtar det aktuella aktiepriset."""
    try:
        stock = yf.Ticker(ticker_symbol)
        info = stock.info
        return info.get('currentPrice') or info.get('regularMarketPrice')
    except Exception:
        return None

def get_price_history(ticker_symbol: str, lookback_days: int = 2) -> pd.DataFrame:
    """Hämtar historisk prisdata med 1-timmarsintervall."""
    try:
        stock = yf.Ticker(ticker_symbol)
        history = stock.history(interval='1h', period=f'{lookback_days}d')
        return history
    except Exception as e:
        print(f"FEL vid hämtning av prisdata: {e}")
        return pd.DataFrame() 

def get_recent_news(ticker_symbol: str) -> list:
    """Hämtar nyheter, analyserar sentiment och beräknar prispåverkan 1 timme efter release."""
    cutoff_time = datetime.datetime.now() - datetime.timedelta(hours=24)
    recent_news = []
    
    try:
        stock = yf.Ticker(ticker_symbol)
        news_list = stock.news
        
        price_history = get_price_history(ticker_symbol)

        for item in news_list:
            publish_timestamp = None
            
            if 'providerPublishTime' in item:
                publish_timestamp = item['providerPublishTime']
            elif 'content' in item and 'pubDate' in item['content']:
                try:
                    publish_dt = parser.isoparse(item['content']['pubDate'])
                    publish_timestamp = publish_dt.timestamp()
                except Exception:
                    continue 
            
            if publish_timestamp is None:
                continue 
            
            try:
                publish_time = datetime.datetime.fromtimestamp(publish_timestamp)
                
                if publish_time > cutoff_time:
                    
                    content = item.get('content', item) 
                    title = content.get('title', 'Ingen rubrik')
                    link = content.get('canonicalUrl', {}).get('url', content.get('link', '#'))
                    publisher = content.get('provider', {}).get('displayName', item.get('publisher', 'Okänd källa'))
                    
                    # Prispåverkansanalys
                    price_change_percent = None
                    if not price_history.empty:
                        
                        release_dt = pd.to_datetime(publish_time, utc=True)
                        
                        P_release = price_history.asof(release_dt)['Close']
                        
                        hour_later_dt = release_dt + pd.Timedelta(hours=1)
                        P_hour_later = price_history.asof(hour_later_dt)['Close']
                        
                        if P_release is not None and P_hour_later is not None and P_release != 0:
                            price_change_percent = ((P_hour_later - P_release) / P_release) * 100
                        elif P_release is not None and P_hour_later is not None:
                            price_change_percent = 0.0

                    sentiment_score = get_sentiment_score(title)

                    recent_news.append({
                        'title': title,
                        'link': link,
                        'publisher': publisher,
                        'time': publish_time.strftime('%Y-%m-%d %H:%M'),
                        'sentiment_score': sentiment_score,
                        'price_change_percent': price_change_percent
                    })
            except Exception as e:
                print(f"Varning: Ett fel uppstod vid bearbetning av nyhetsartikel: {e}")
                
        recent_news.sort(key=lambda x: x['sentiment_score'], reverse=True)
        
        return recent_news
        
    except Exception as e:
        print(f"FEL vid hämtning av nyheter för {ticker_symbol} (Yfinance-nivå): {e}")
        return []

def get_llm_recommendation(ticker: str, check_type: str, price: float | None = None, news_items: list | None = None) -> tuple[str, str]:
    """Använder Ollama för att ge en strukturerad KÖP/SÄLJ/BEHÅLL rekommendation."""
    try:
        client = ollama.Client(host='http://localhost:11434')
        
        system_prompt = (
            "Du är en analytiker som kontinuerligt utvärderar aktien för din egen portfölj. "
            "Baserat på den inkommande informationen, bedöm om aktien är värd att läggas till eller säljas från din analysportfölj just nu. "
            "Svara ENDAST i formatet: AKTION: [KÖP|SÄLJ|BEHÅLL] MOTIVERING: [Din interna, analytiska motivering på svenska, max 3 meningar]."
        )
        user_prompt = f"Aktuellt pris för {ticker} är ${price:.2f}. Normalt intervall är 50-100. Är detta pris en signal för mig att KÖPA, SÄLJA eller BEHÅLLA?"
        
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

# --- NY FUNKTION: Hämta ölpris via Google Sök ---

def get_sort_guld_price() -> tuple[float | None, str]:
    """Söker efter priset på Sort Guld på Systembolaget."""
    print("Söker efter priset på Sort Guld på Systembolaget...")
    try:
        # KORRIGERAT ANROP: Använder google_search.search
        search_results = google_search.search(queries=['Sort Guld pris Systembolaget'])
        
        if not search_results or not search_results[0].get('snippet'):
            return None, "Kunde inte hämta sökresultat."
            
        snippet = search_results[0].get('snippet', '')
        
        # Regex för att matcha svenska prisformatet (X,XX kr)
        price_match = re.search(r'(\d+[.,]\d{2})\s*k[rR]', snippet)
        
        if price_match:
            # Ersätt komma med punkt för att konvertera till float
            price_str = price_match.group(1).replace(',', '.')
            return float(price_str), snippet
        else:
            return None, snippet
    except Exception as e:
        print(f"❌ FEL vid sökning efter ölpris: {e}")
        return None, f"Kunde inte hämta sökresultat. Fel: {e}"

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
        print(f"✅ Daglig e-post (Aktier) skickad framgångsrikt till {MAIL_TO}!")
    except Exception as e:
        print(f"❌ FEL vid sändning av daglig e-post (Aktier): {e}")
    finally:
        if 'server' in locals():
            server.quit()


def send_proactive_email(price: float | None, ticker: str, action: str, reasoning: str, check_type: str, news_items: list):
    """Skickar proaktiv e-post vid KÖP/SÄLJ rekommendation ELLER direkt nyhetsnotis (med ranking)."""
    
    price_str = f"${price:,.2f}" if price is not None else "PRIS EJ TILLGÄNGLIGT"
    
    if action == 'KÖP':
        alert_text = "🚨 KÖP-SIGNAL Upptäckt!"
        color = "#28a745"
        display_action = "KÖP"
        source_message = "Prisdata analyserad av AI-analytiker."
    elif action == 'SÄLJ':
        alert_text = "⚠️ SÄLJ-SIGNAL Upptäckt!"
        color = "#dc3545"
        display_action = "SÄLJ"
        source_message = "Prisdata analyserad av AI-analytiker."
    else: # NOTIS
        alert_text = "🔔 NYHETSNOTIS: Viktiga Uppdateringar!"
        color = "#007bff"
        display_action = "NYHETER FUNNA"
        source_message = "Direkt notis baserad på publicerade nyheter de senaste 24 timmarna."

    msg = MIMEMultipart()
    msg['From'] = SMTP_USER
    msg['To'] = MAIL_TO
    msg['Subject'] = f"{alert_text} för {ticker} (Baserat på {check_type})"
    
    news_html = ""
    if action == 'NOTIS' and news_items:
        news_html = "<h3>📰 Nyhetsanalys: Ranking och Prispåverkan (Senaste 24h)</h3><ol>"
        
        for i, item in enumerate(news_items):
            rank = i + 1
            sentiment_text = f"{item['sentiment_score']:.2f}"
            
            price_info = "Ej tillgänglig"
            if item['price_change_percent'] is not None:
                change = item['price_change_percent']
                sign = '+' if change >= 0 else ''
                color_val = 'green' if change >= 0 else 'red'
                price_info = f'<span style="color: {color_val}; font-weight: bold;">{sign}{change:.2f}%</span> (1h efter release)'
            
            news_html += f"""
                <li>
                    <strong>Rank #{rank} (Sentiment: {sentiment_text}):</strong> {item['title']} 
                    <br>
                    <small>Prispåverkan: {price_info} | Källa: {item['publisher']} | Tid: {item['time']}</small>
                    <br><a href="{item['link']}">Läs mer</a>
                </li>
            """
        news_html += "</ol>"

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
        print(f"✅ Proaktivt e-post ({action}) skickat till {MAIL_TO}!")
    except Exception as e:
        print(f"❌ FEL vid sändning av proaktiv e-post: {e}")
    finally:
        if 'server' in locals():
            server.quit()


def send_beer_price_email(price: float | None, search_snippet: str):
    """Skickar priset på Sort Guld."""
    
    if price is not None:
        price_str = f"{price:,.2f} kr"
        subject = f"🍺 Dagens Ölpris på Bolaget: Sort Guld kostar {price_str}"
        status_text = f"Det aktuella priset för Sort Guld är: <strong>{price_str}</strong>."
    else:
        price_str = "Ej tillgängligt"
        subject = "❓ Kunde inte hämta pris på Sort Guld idag"
        status_text = "Kunde inte fastställa det aktuella priset för Sort Guld via Systembolaget."

    msg = MIMEMultipart()
    msg['From'] = SMTP_USER
    msg['To'] = MAIL_TO
    msg['Subject'] = subject

    html_body = f"""\
    <html>
      <body>
        <h2>Systembolaget: Prisbevakning för Sort Guld</h2>
        <p style="font-size: 20px;">{status_text}</p>
        
        <p><i>Detaljer från sökresultatet (visar var priset eventuellt hittades):</i></p>
        <blockquote style="border-left: 4px solid #f90; padding-left: 15px; margin: 15px 0; background: #fff8e1;">
          "{search_snippet}"
        </blockquote>
        
        <p><small>Denna rapport skickas dagligen kl 10:00.</small></p>
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
        print(f"✅ Daglig e-post (Ölpris) skickad framgångsrikt till {MAIL_TO}!")
    except Exception as e:
        print(f"❌ FEL vid sändning av daglig e-post (Ölpris): {e}")
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

def beer_price_job():
    """Hämtar och mailar priset på Sort Guld."""
    print(f"\n--- Kör ÖLPRISKONTROLL ({time.strftime('%Y-%m-%d %H:%M:%S')}) ---")
    price, snippet = get_sort_guld_price()
    send_beer_price_email(price, snippet)


def pro_active_check_job():
    """Kör slumpmässig koll av Pris ELLER Nyheter och agerar vid KÖP/SÄLJ eller nyhetshändelser."""
    
    check_type = random.choice(['PRICE', 'NEWS']) 
    print(f"\n--- Kör PROAKTIV KONTROLL (Fokus: {check_type}) ---")
    
    price = get_stock_price(TICKER_SYMBOL)
    news_items = get_recent_news(TICKER_SYMBOL) 

    if check_type == 'PRICE':
        if price is not None:
            recommendation, reasoning = get_llm_recommendation(TICKER_SYMBOL, 'PRICE', price=price)
            
            if recommendation in ['KÖP', 'SÄLJ']:
                print(f"** PROAKTIV HÄNDELSE TRIGGAD! Rekommendation: {recommendation} (Pris) **")
                send_proactive_email(price, TICKER_SYMBOL, recommendation, reasoning, 'PRIS', [])
            else:
                print(f"Agenten avstår från aktion. Beslut: {recommendation}. Motivering: {reasoning}")
        else:
            print("Kontrollen hoppades över: Prisdata ej tillgänglig för PRIS-kontroll.")

    elif check_type == 'NEWS':
        if news_items:
            action = "NOTIS"
            reasoning = "Nya nyheter har publicerats under de senaste 24 timmarna, rangordnade efter sentiment och med prispåverkan."
            print(f"** PROAKTIV HÄNDELSE TRIGGAD! Nyheter funna (Direkt Notis) **")
            send_proactive_email(price, TICKER_SYMBOL, action, reasoning, 'NYHETER', news_items)
        else:
            print("Kontrollen hoppades över: Inga nya nyheter hittades.")


def run_agent():
    """Huvudloopen som kör agenten kontinuerligt."""
    print("🤖 Agenten startar...")

    # Schemaläggning
    schedule.every().day.at("17:00").do(daily_reporting_job).tag('daily_stock')
    print("Schemalagt: Daglig Aktierapport körs kl 17:00 CET.")
    
    schedule.every().day.at("10:00").do(beer_price_job).tag('daily_beer')
    print("Schemalagt: Daglig Ölpriskontroll (Sort Guld) körs kl 10:00 CET.")
    
    # --- NYTT: Kör ett testutskick av ölkollen vid start ---
    print("\n>>> Kör testutskick av Ölpriskontroll (Sort Guld)...")
    beer_price_job()
    print("Testutskick av Ölpriskontroll slutfört. Kontrollera din e-post.")
    # --------------------------------------------------------
    
    next_check_time = time.time() 
    
    print("Agenten går in i standby-läge. Övervakning aktiv...")

    while True:
        schedule.run_pending()
        
        if time.time() >= next_check_time:
            pro_active_check_job()
            
            random_delay = random.randint(60, 7200) 
            next_check_time = time.time() + random_delay
            
            delay_minutes = random_delay / 60
            print(f"Nästa proaktiva aktiekontroll schemalagd om {delay_minutes:.1f} minuter ({random_delay} sekunder).")

        time.sleep(1)

if __name__ == "__main__":
    if not all([SMTP_HOST, SMTP_USER, SMTP_PASS, MAIL_TO, TICKER_SYMBOL]):
        print("❌ FEL: Nödvändiga miljövariabler saknas. Kontrollera .env-filen.")
    else:
        run_agent()
