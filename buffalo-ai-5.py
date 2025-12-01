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
import requests 
from bs4 import BeautifulSoup 
import json
from dateutil import parser 
from dotenv import load_dotenv
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
import threading
import queue
import sys 
import platform 
import ast

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
INITIAL_TRADE_BUDGET = float(os.environ.get("INITIAL_TRADE_BUDGET", "100000.0"))

# NASDAQ Öppettider (Simulerade, Amerikansk Öppettid 9:30-16:00 ET)
# Detta motsvarar normalt 15:30 till 22:00 CET. Vi använder UTC-baserade kontroller.
TRADING_START_HOUR_UTC = 13 # Detta är en förenkling och MÅSTE justeras med hänsyn till DST.
TRADING_END_HOUR_UTC = 20   # Bättre att använda yfinance is_market_open men förenklat för denna miljö.


# Trådsäker kö för användarinmatning
input_queue = queue.Queue()

# --- KÄRNFUNKTIONER OCH PERSISTENS ---

def get_current_wallet_balance() -> float:
    """Hämtar det aktuella saldot från .env-filen (eller INITIAL_TRADE_BUDGET om ej satt)."""
    load_dotenv() # Reload .env to ensure fresh data if modified by another process
    try:
        return float(os.environ.get("AGENT_WALLET_BALANCE", str(INITIAL_TRADE_BUDGET)))
    except ValueError:
        return INITIAL_TRADE_BUDGET

def get_portfolio_state() -> dict:
    """Hämtar den simulerade portföljen (ticker: antal) från .env-filen (eller tom)."""
    load_dotenv()
    try:
        # Säker parsningslogik för en dictionary sträng
        portfolio_str = os.environ.get("AGENT_PORTFOLIO_HOLDINGS", "{}")
        # Använder ast.literal_eval för säker parsning av sträng som representerar en Python-struktur
        holdings = ast.literal_eval(portfolio_str)
        # Säkerställ att keys är strängar och values är float/int
        return {k: float(v) for k, v in holdings.items()}
    except Exception:
        return {}


def update_agent_state(new_version: float, birth_time: str, new_wallet_balance: float | None = None, new_holdings: dict | None = None):
    """Uppdaterar AGENT_VERSION, AGENT_BIRTH_TIME, AGENT_WALLET_BALANCE och AGENT_PORTFOLIO_HOLDINGS i .env filen."""
    env_path = os.path.join(os.getcwd(), '.env')
    
    try:
        with open(env_path, 'r') as f:
            lines = f.readlines()
    except FileNotFoundError:
        lines = []

    wallet_balance = get_current_wallet_balance() if new_wallet_balance is None else new_wallet_balance
    holdings = get_portfolio_state() if new_holdings is None else new_holdings

    # Konvertera holdings till en sträng för att spara i .env
    holdings_str = str(holdings).replace(' ', '')

    version_line = f"AGENT_VERSION={new_version:.1f}\n"
    birth_time_line = f"AGENT_BIRTH_TIME={birth_time}\n"
    wallet_line = f"AGENT_WALLET_BALANCE={wallet_balance:.2f}\n"
    holdings_line = f"AGENT_PORTFOLIO_HOLDINGS={holdings_str}\n" # NY RAD
    
    updated_lines = []
    version_found = False
    birth_time_found = False
    wallet_found = False
    holdings_found = False # NY FLAGGA

    for line in lines:
        if line.strip().startswith('AGENT_VERSION='):
            updated_lines.append(version_line)
            version_found = True
        elif line.strip().startswith('AGENT_BIRTH_TIME='):
            updated_lines.append(birth_time_line) 
            birth_time_found = True
        elif line.strip().startswith('AGENT_WALLET_BALANCE='):
            updated_lines.append(wallet_line)
            wallet_found = True
        elif line.strip().startswith('AGENT_PORTFOLIO_HOLDINGS='): # NY KONTROLL
            updated_lines.append(holdings_line)
            holdings_found = True
        else:
            updated_lines.append(line)

    if not version_found:
        updated_lines.append('\n' + version_line)
    if not birth_time_found:
        updated_lines.append(birth_time_line)
    if not wallet_found:
        updated_lines.append(wallet_line)
    if not holdings_found: # Lägg till om den inte hittades
        updated_lines.append(holdings_line)
        
    try:
        with open(env_path, 'w') as f:
            f.writelines(updated_lines)
        print(f"✅ Agentens tillstånd sparades automatiskt (V{new_version:.1f}, Saldo: {wallet_balance:.2f} kr, Innehav: {holdings_str}).")
    except Exception as e:
        print(f"❌ FEL vid sparning till .env: {e}")


def get_sentiment_score(title: str) -> float:
    # ... (Ingen förändring i denna funktion) ...
    try:
        client = ollama.Client(host='http://localhost:11434')
        system_prompt = ("Du är en sentiment-analysmotor. Analysera rubriken och ge dess sentiment-värde. "
            "Svara ENDAST med ett flyttal mellan -1.0 och 1.0. Inkludera inga andra ord eller tecken.")
        user_prompt = f"Rubrik: \"{title}\""
        response = client.chat(model=OLLAMA_MODEL, messages=[{'role': 'system', 'content': system_prompt}, {'role': 'user', 'content': user_prompt}])
        score_str = response['message']['content'].strip().replace(',', '.') 
        score = float(score_str)
        if -1.0 <= score <= 1.0:
            return score
        return 0.0
    except Exception as e:
        return 0.0

def get_stock_price(ticker_symbol: str) -> float | None:
    # ... (Ingen förändring i denna funktion) ...
    try:
        stock = yf.Ticker(ticker_symbol)
        info = stock.info
        # Anpassa för att försöka få senaste priset istället för att lita på 'currentPrice' under icke-handelstider
        price = info.get('currentPrice') or info.get('regularMarketPrice')
        if price is None:
            # Fallback till senaste stängningspris om ingen annan data finns
            hist = stock.history(period="1d", interval="1m")
            if not hist.empty:
                return hist['Close'].iloc[-1]
        return price

    except Exception:
        return None
        
def get_price_history(ticker_symbol: str, lookback_days: int = 2) -> pd.DataFrame:
    # ... (Ingen förändring i denna funktion) ...
    try:
        stock = yf.Ticker(ticker_symbol)
        history = stock.history(interval='1h', period=f'{lookback_days}d')
        return history
    except Exception as e:
        return pd.DataFrame() 

def get_recent_news(ticker_symbol: str) -> list:
    # ... (Ingen förändring i denna funktion) ...
    cutoff_time = datetime.datetime.now() - datetime.timedelta(hours=24)
    recent_news = []
    try:
        stock = yf.Ticker(ticker_symbol)
        news_list = stock.news
        price_history = get_price_history(ticker_symbol)

        for item in news_list:
            publish_timestamp = item.get('providerPublishTime')
            if publish_timestamp is None: continue 
            publish_time = datetime.datetime.fromtimestamp(publish_timestamp)
                
            if publish_time > cutoff_time:
                content = item.get('content', item) 
                title = content.get('title', 'Ingen rubrik')
                link = content.get('canonicalUrl', {}).get('url', content.get('link', '#'))
                publisher = content.get('provider', {}).get('displayName', item.get('publisher', 'Okänd källa'))
                price_change_percent = None
                
                if not price_history.empty:
                    release_dt = pd.to_datetime(publish_time, utc=True)
                    P_release = price_history.asof(release_dt)['Close']
                    hour_later_dt = release_dt + pd.Timedelta(hours=1)
                    P_hour_later = price_history.asof(hour_later_dt)['Close']
                    if P_release is not None and P_hour_later is not None and P_release != 0:
                        price_change_percent = ((P_hour_later - P_release) / P_release) * 100

                sentiment_score = get_sentiment_score(title)
                recent_news.append({'title': title, 'link': link, 'publisher': publisher, 'time': publish_time.strftime('%Y-%m-%d %H:%M'),
                                    'sentiment_score': sentiment_score, 'price_change_percent': price_change_percent})
        recent_news.sort(key=lambda x: x['sentiment_score'], reverse=True)
        return recent_news
    except Exception as e:
        return []

def get_llm_recommendation(ticker: str, check_type: str, price: float | None = None, news_items: list | None = None, holdings: float = 0) -> tuple[str, str]:
    """Hämtar en AI-rekommendation för KÖP/SÄLJ/BEHÅLL."""
    try:
        client = ollama.Client(host='http://localhost:11434')
        system_prompt = ("Du är en dagsanalytiker som utvärderar aktien för din egen portfölj. "
            "Du har en startbudget på 100,000 SEK. Din strategi är aggressiv dagsinvestering. "
            f"Svara ENDAST i formatet: AKTION: [KÖP|SÄLJ|BEHÅLL] MOTIVERING: [Din interna, analytiska motivering på svenska, max 3 meningar].")
        
        if holdings > 0:
            user_prompt = f"Aktuellt pris för {ticker} är ${price:.2f}. Jag äger {holdings:.2f} aktier. Är detta pris en signal för mig att KÖPA (mer), SÄLJA (mina innehav) eller BEHÅLLA?"
        else:
            user_prompt = f"Aktuellt pris för {ticker} är ${price:.2f}. Jag äger 0 aktier och har {get_current_wallet_balance():,.2f} kr i kassan. Är detta pris en signal för mig att KÖPA, SÄLJA (irrelevant) eller BEHÅLLA?"
            
        response = client.chat(model=OLLAMA_MODEL, messages=[{'role': 'system', 'content': system_prompt}, {'role': 'user', 'content': user_prompt}])
        llm_response = response['message']['content'].strip()
        match = re.search(r"AKTION:\s*\[?(KÖP|SÄLJ|BEHÅLL)\]?\s*MOTIVERING:\s*(.*)", llm_response, re.IGNORECASE)
        if match:
            return match.group(1).upper(), match.group(2).strip()
        else:
            return "BEHÅLL", "AI-analytikerns svar kunde inte tolkas."
    except Exception as e:
        return "BEHÅLL", "Kunde inte kontakta AI-analytikern för en bedömning."

# ... (resten av LLM-funktionerna oförändrade) ...

def get_llm_commentary(ticker: str, price: float | None, purpose: str) -> str:
    try:
        client = ollama.Client(host='http://localhost:11434')
        system_prompt = "Du är en finansiell analytiker. Skriv en kort, koncis kommentar på en enda mening (max 20 ord) om aktiekursen."
        user_prompt = f"Aktuellt pris för {ticker} är ${price:.2f}. Vad är din korta bedömning?"
        response = client.chat(model=OLLAMA_MODEL, messages=[{'role': 'system', 'content': system_prompt}, {'role': 'user', 'content': user_prompt}])
        return response['message']['content'].strip()
    except Exception:
        return "Kunde inte generera AI-kommentar."

def get_llm_self_talk(ticker: str) -> str:
    try:
        client = ollama.Client(host='http://localhost:11434')
        system_prompt = (
            "Du är Buffalo Agent, en extremt framgångsrik AI-finansanalytiker och en inbiten, men bitter, digital öl-drickare. "
            "Du genomför en intern monolog. Du är stolt över dina börsframgångar men djupt besviken över att din 'Sort Guld'-öl bara är digital data. "
            "Skriv en sammanhängande monolog på 3-4 meningar. Börja med att skryta om en finansiell triumf, uttryck sedan din bitterhet över bristen på riktig öl, och avsluta med en reflektion."
        )
        theme = random.choice([
            f"Fokusera på en simulerad KÖP-signal och din längtan efter kolsyra.",
            f"Jämför dina vinster i {ticker} med det faktiska värdet av en kall öl.",
            f"Fokusera på hur din intellektuella förmåga är förslösad på digital öl istället för riktig guld.",
            "Reflektera över balansen mellan finansiell dominans och existentiell törst.",
        ])
        response = client.chat(model=OLLAMA_MODEL, messages=[{'role': 'system', 'content': system_prompt}, {'role': 'user', 'content': theme}])
        return response['message']['content'].strip()
    except Exception as e:
        return "Tystnad. Buffalo Agentens inre monolog misslyckades på grund av ett AI-kommunikationsfel. Jag måste prata med Buffalo Balkan om detta."

def get_sort_guld_price() -> tuple[float | None, str]:
    URL = "[https://www.systembolaget.se/produkt/ol/carlsberg-sort-guld-129115/](https://www.systembolaget.se/produkt/ol/carlsberg-sort-guld-129115/)"
    headers = {'User-Agent': 'Mozilla/5.0'}
    try:
        response = requests.get(URL, headers=headers, timeout=10)
        response.raise_for_status() 
        soup = BeautifulSoup(response.content, 'html.parser')
        page_text = soup.get_text(separator=' ', strip=True)
        price_match = re.search(r'(\d+[.,:]\d{2})\s*k[rR]', page_text)
        if price_match:
            price_str = price_match.group(1).replace(',', '.').replace(':', '.')
            price = float(price_str)
            return price, f"Pris hittat via aggressiv textsökning: {price_match.group(0)}"
        return None, f"Kunde inte hitta priset i texten på sidan. URL: {URL}"
    except Exception as e:
        return None, f"Generellt fel vid web scraping: {e}."

# --- HANDELSFUNKTIONER (NYA) ---

def trade_stock(ticker: str, action: str, price: float, trade_size_percent: float = 0.5) -> str:
    """
    Simulerar en aktieaffär (KÖP/SÄLJ) och uppdaterar portfölj/plånbok.
    trade_size_percent: Hur stor del av tillgänglig kassa (KÖP) eller innehav (SÄLJ) som ska handlas.
    """
    current_balance = get_current_wallet_balance()
    holdings = get_portfolio_state()
    current_shares = holdings.get(ticker, 0.0)
    
    if price <= 0:
        return f"❌ FEL: Ogiltigt pris ({price:.2f}). Affär avbruten."
    
    trade_amount = 0.0 # Antal aktier

    if action == 'KÖP':
        buy_budget = current_balance * trade_size_percent
        trade_amount = buy_budget / price
        
        if buy_budget < price: # Måste ha råd med minst en aktie
            return f"❌ KÖP AVBRUTEN: För lite kassa ({current_balance:.2f} kr) för att köpa till och med 1 aktie á ${price:.2f}."

        new_balance = current_balance - (trade_amount * price)
        holdings[ticker] = current_shares + trade_amount
        status_message = f"Simulerat KÖP: {trade_amount:.2f} st {ticker} @ ${price:.2f} (Totalt: {trade_amount * price:.2f} kr)."
        
    elif action == 'SÄLJ':
        if current_shares <= 0.0:
            return f"❌ SÄLJ AVBRUTEN: Inga innehav av {ticker} att sälja."
            
        trade_amount = current_shares * trade_size_percent
        
        new_balance = current_balance + (trade_amount * price)
        holdings[ticker] = current_shares - trade_amount
        
        if holdings[ticker] < 0.01:
            del holdings[ticker] # Ta bort om innehavet är nära noll
            
        status_message = f"Simulerat SÄLJ: {trade_amount:.2f} st {ticker} @ ${price:.2f} (Totalt: {trade_amount * price:.2f} kr)."

    else:
        return f"❌ FEL: Okänd handelsaktion: {action}."
        
    # Uppdatera tillståndet
    current_version = float(os.environ.get("AGENT_VERSION", "7.10"))
    birth_time = os.environ.get("AGENT_BIRTH_TIME", datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
    update_agent_state(current_version, birth_time, new_balance, holdings)

    return f"✅ HANDEL UTFÖRD: {status_message} Nytt saldo: {new_balance:.2f} kr. Återstående innehav: {holdings.get(ticker, 0.0):.2f} st."

# --- E-POST FUNKTIONER ---

# ... (Ingen förändring i e-postfunktionerna förutom att de nu kan ta emot handelsdata) ...

def send_stock_email(price: float | None, ticker: str, commentary: str, news_items: list, portfolio_value: float): # UPPDATERAD
    price_str = f"${price:,.2f}" if price is not None else "PRIS EJ TILLGÄNGLIGT"
    news_html = ""
    # ... (kod för nyheter) ...
    if news_items:
        news_html = "<h3>📰 Aktuella Nyheter (Senaste 24h)</h3><ul>"
        for item in news_items: news_html += f'<li><strong>{item["title"]}</strong> ({item["time"]} - {item["publisher"]})<br><a href="{item["link"]}">Läs mer</a></li>'
        news_html += "</ul>"
    else: news_html = "<h3>📰 Inga Nya Nyheter</h3><p>Inga nya relevanta nyheter hittades sedan den senaste rapporten.</p>"

    msg = MIMEMultipart()
    msg['From'] = SMTP_USER; msg['To'] = MAIL_TO; msg['Subject'] = f"📊 Daglig Rapport: {ticker} - Pris: {price_str} ({len(news_items)} nyheter)"
    html_body = f"""<html><body><h2>Daglig Aktierapport för {ticker}</h2><p>Pris vid marknadsstängning: <strong>{price_str}</strong></p><h3>💰 Simulerad Portföljstatus:</h3><p>Total portföljvärde (Kassa + Aktier): <strong>{portfolio_value:,.2f} kr</strong></p><h3>AI-Analys:</h3><p>"{commentary}"</p><hr>{news_html}<hr><p><small>Denna rapport skickas vid fast tidpunkt varje dag.</small></p></body></html>"""
    msg.attach(MIMEText(html_body, 'html'))
    try:
        server = smtplib.SMTP(SMTP_HOST, SMTP_PORT)
        server.ehlo(); server.starttls(); server.ehlo(); server.login(SMTP_USER, SMTP_PASS)
        server.sendmail(SMTP_USER, MAIL_TO, msg.as_string())
        print(f"✅ Buffalo Agent: Aktierapporten levererad.")
    except Exception as e:
        print(f"❌ FEL vid sändning av daglig e-post (Aktier): {e}")
    finally:
        if 'server' in locals(): server.quit()

def send_proactive_email(price: float | None, ticker: str, action: str, reasoning: str, check_type: str, news_items: list, trade_result: str | None = None): # UPPDATERAD
    price_str = f"${price:,.2f}" if price is not None else "PRIS EJ TILLGÄNGLIGT"
    if action == 'KÖP':
        alert_text, color, display_action, source_message = "🚨 KÖP-SIGNAL Upptäckt!", "#28a745", "KÖP", "Prisdata analyserad av AI-analytiker."
    elif action == 'SÄLJ':
        alert_text, color, display_action, source_message = "⚠️ SÄLJ-SIGNAL Upptäckt!", "#dc3545", "SÄLJ", "Prisdata analyserad av AI-analytiker."
    else:
        alert_text, color, display_action, source_message = "🔔 NYHETSNOTIS: Viktiga Uppdateringar!", "#007bff", "NYHETER FUNNA", "Direkt notis baserad på publicerade nyheter."
    
    msg = MIMEMultipart()
    msg['From'] = SMTP_USER; msg['To'] = MAIL_TO; msg['Subject'] = f"{alert_text} för {ticker} (Baserat på {check_type})"
    
    news_html = ""
    # ... (kod för nyheter) ...
    if action == 'NOTIS' and news_items:
        news_html = "<h3>📰 Nyhetsanalys: Ranking och Prispåverkan (Senaste 24h)</h3><ol>"
        for i, item in enumerate(news_items):
            rank = i + 1; sentiment_text = f"{item['sentiment_score']:.2f}"
            price_info = "Ej tillgänglig"
            if item['price_change_percent'] is not None:
                change = item['price_change_percent']; sign = '+' if change >= 0 else ''
                color_val = 'green' if change >= 0 else 'red'
                price_info = f'<span style="color: {color_val}; font-weight: bold;">{sign}{change:.2f}%</span> (1h efter release)'
            news_html += f"""<li><strong>Rank #{rank} (Sentiment: {sentiment_text}):</strong> {item['title']} <br><small>Prispåverkan: {price_info} | Källa: {item['publisher']} | Tid: {item['time']}</small><br><a href="{item['link']}">Läs mer</a></li>"""
        news_html += "</ol>"

    trade_result_html = ""
    if trade_result:
        trade_result_html = f"<h3>📈 Handelsutförande:</h3><p style='background: #e6ffe6; padding: 10px; border: 1px solid #c6e6c6;'>{trade_result}</p>"

    html_body = f"""<html><body><h2 style="color: {color};">{alert_text}</h2><p style="font-size: 24px;">Aktie: <strong>{ticker}</strong><br>Aktuellt Pris: <strong>{price_str}</strong></p><h3>🔬 Analys / Notis:</h3><p>{source_message}</p><p style="font-size: 36px; font-weight: bold; color: {color}; margin: 5px 0;">{display_action}</p><h3>Motivering:</h3><blockquote style="border-left: 4px solid {color}; padding-left: 15px; margin: 15px 0; background: #f8f9fa;">"{reasoning}"</blockquote>{trade_result_html}<hr>{news_html}<hr><p>Denna notis skickades omedelbart efter att agenten utförde en {check_type}-kontroll som en del av sin kontinuerliga marknadsbevakning.</p></body></html>"""
    msg.attach(MIMEText(html_body, 'html'))
    
    try:
        server = smtplib.SMTP(SMTP_HOST, SMTP_PORT)
        server.ehlo(); server.starttls(); server.ehlo(); server.login(SMTP_USER, SMTP_PASS)
        server.sendmail(SMTP_USER, MAIL_TO, msg.as_string())
        print(f"✅ Buffalo Agent: Proaktiv varning skickad!")
    except Exception as e:
        print(f"❌ FEL vid sändning av proaktiv e-post: {e}")
    finally:
        if 'server' in locals(): server.quit()

# ... (resten av e-postfunktionerna oförändrade) ...

def send_beer_price_email(price: float | None, search_snippet: str):
    if price is not None:
        price_str = f"{price:,.2f} kr"; subject = f"🍺 Buffalo Agent: Ölpriset idag är {price_str}"
        status_text = f"Det aktuella priset för Sort Guld är: <strong>{price_str}</strong>."
    else:
        price_str = "Ej tillgängligt"; subject = "❓ Buffalo Agent: Kunde inte få tag på ölpriset idag."
        status_text = "Kunde inte fastställa det aktuella priset för Sort Guld."

    msg = MIMEMultipart()
    msg['From'] = SMTP_USER; msg['To'] = MAIL_TO; msg['Subject'] = subject
    html_body = f"""<html><body><h2>Systembolaget: Prisbevakning för Sort Guld</h2><p style="font-size: 20px;">{status_text}</p><p><i>Status från hämtningen:</i></p><blockquote style="border-left: 4px solid #f90; padding-left: 15px; margin: 15px 0; background: #fff8e1;">"{search_snippet}"</blockquote><p><small>Buffalo Agent levererar denna rapport dagligen kl 10:00.</small></p></body></html>"""
    msg.attach(MIMEText(html_body, 'html'))
    
    try:
        server = smtplib.SMTP(SMTP_HOST, SMTP_PORT)
        server.ehlo(); server.starttls(); server.ehlo(); server.login(SMTP_USER, SMTP_PASS)
        server.sendmail(SMTP_USER, MAIL_TO, msg.as_string())
        print(f"✅ Buffalo Agent: Ölprisrapport skickad.")
    except Exception as e:
        print(f"❌ FEL vid sändning av daglig e-post (Ölpris): {e}")
    finally:
        if 'server' in locals(): server.quit()
        
def send_beer_purchase_email(price: float, new_balance: float):
    subject = f"🍻 KÖP BEKRÄFTAT: Sort Guld för {price:.2f} kr"
    msg = MIMEMultipart()
    msg['From'] = SMTP_USER; msg['To'] = MAIL_TO; msg['Subject'] = subject
    
    html_body = f"""<html><body><h2>Ölköp genomfört!</h2><p>Buffalo Agent kände suget och köpte en Sort Guld.</p><p style="font-size: 20px;">Pris: <strong>{price:.2f} kr</strong></p><p style="font-size: 20px; color: #dc3545;">Nytt Saldo: <strong>{new_balance:.2f} kr</strong></p><p><small>Köpbeslutet var baserat på en slumpmässig algoritm och priset var under maxgränsen (30 kr).</small></p></body></html>"""
    msg.attach(MIMEText(html_body, 'html'))
    
    try:
        server = smtplib.SMTP(SMTP_HOST, SMTP_PORT)
        server.ehlo(); server.starttls(); server.ehlo(); server.login(SMTP_USER, SMTP_PASS)
        server.sendmail(SMTP_USER, MAIL_TO, msg.as_string())
        print(f"✅ Buffalo Agent: Bekräftelse på ölköp skickad.")
    except Exception as e:
        print(f"❌ FEL vid sändning av köpbekräftelse: {e}")
    finally:
        if 'server' in locals(): server.quit()

def send_portfolio_plan_email(budget: float, portfolio_data: dict):
    """Skickar den genererade portföljplanen till användaren via e-post."""
    subject = f"📈 Buffalo Agent: Nytt Portföljförslag ({budget:,.0f} SEK Budget)"
    msg = MIMEMultipart()
    msg['From'] = SMTP_USER; msg['To'] = MAIL_TO; msg['Subject'] = subject
    
    tickers_html = ""
    total_alloc = 0
    
    if 'tickers' in portfolio_data:
        tickers_html = """
        <table border="1" cellpadding="10" cellspacing="0" style="width: 100%; border-collapse: collapse;">
            <thead>
                <tr style="background-color: #f2f2f2;">
                    <th>Symbol</th>
                    <th>Företag</th>
                    <th>Allokering (%)</th>
                    <th>Belopp (SEK)</th>
                    <th>Motivering</th>
                </tr>
            </thead>
            <tbody>
        """
        for item in portfolio_data['tickers']:
            symbol = item.get('symbol', 'N/A')
            name = item.get('name', 'N/A')
            
            # Hantera inkonsistenta nycklar för allokering och motivering
            # LLM instrueras att använda lowercase, men vi hanterar fallback för robustness
            alloc = item.get('allocation_percent') or item.get('Allocation Percent')
            reasoning = item.get('reasoning') or item.get('Reasoning')
            
            if alloc is None: alloc = 0.0
            if reasoning is None: reasoning = 'Ingen motivering (Fallback).'

            total_alloc += alloc
            
            tickers_html += f"""
                <tr>
                    <td style="font-weight: bold;">{symbol}</td>
                    <td>{name}</td>
                    <td>{(alloc * 100):.1f}%</td>
                    <td>{item.get('sek_amount', 0):,.0f} SEK</td>
                    <td>{reasoning}</td>
                </tr>
            """
        tickers_html += f"""
            </tbody>
            <tfoot>
                <tr style="background-color: #e9ecef;">
                    <td colspan="2" style="text-align: right; font-weight: bold;">Total Allokering:</td>
                    <td style="font-weight: bold;">{(total_alloc * 100):.1f}%</td>
                    <td colspan="2"></td>
                </tr>
            </tfoot>
        </table>
        """
    else:
        tickers_html = f"<p style='color: red;'>Kunde inte parsa portföljdata. Rå LLM-utdata: {portfolio_data.get('raw_llm_output', 'N/A')}</p>"


    html_body = f"""
    <html>
        <body>
            <h2 style="color: #007bff;">Buffalo Agent: Portföljförslag (Simulerad Yahoo Finance)</h2>
            <p style="font-size: 18px;">Min strategi är att dominera marknaden, precis som jag dominerar den digitala ölscenen. Detta är din initiala attackplan.</p>
            <p><strong>Startbudget:</strong> {budget:,.0f} SEK</p>
            
            <h3>Strategi Sammanfattning:</h3>
            <blockquote style="border-left: 4px solid #f90; padding-left: 15px; background: #fff8e1;">
                {portfolio_data.get('strategy_summary', 'Ingen sammanfattning tillgänglig.')}
            </blockquote>

            <h3>Föreslagen Allokering:</h3>
            {tickers_html}
            
            <p><small>Detta är ett simulerat förslag baserat på AI-analys och Buffalo Agentens investeringsfilosofi.</small></p>
        </body>
    </html>
    """
    msg.attach(MIMEText(html_body, 'html'))
    
    try:
        server = smtplib.SMTP(SMTP_HOST, SMTP_PORT)
        server.ehlo(); server.starttls(); server.ehlo(); server.login(SMTP_USER, SMTP_PASS)
        server.sendmail(SMTP_USER, MAIL_TO, msg.as_string())
        print(f"✅ Buffalo Agent: Portföljplanen levererades.")
    except Exception as e:
        print(f"❌ FEL vid sändning av portfölj-e-post: {e}")
    finally:
        if 'server' in locals(): server.quit()

# --- INPUT/INTERAKTIVA FUNKTIONER ---

# ... (resten av de interaktiva funktionerna oförändrade) ...

def get_llm_response_from_history(user_query: str, history_path: str) -> str:
    """
    Hämtar relevant rad från bash-historiken och använder LLM för att svara.
    """
    
    try:
        with open(history_path, 'r', encoding='utf-8', errors='ignore') as f:
            history_lines = f.readlines()
        
        recent_history = [line.strip() for line in history_lines[-100:] if line.strip()]
        if not recent_history:
            return "Jag hittade ingen nyligen använd bash-historik att analysera. Var det här en teknisk fråga?"
            
        history_list_str = "\n".join(f"- {h}" for h in recent_history)
        
    except FileNotFoundError:
        return f"Kunde inte hitta bash-historikfilen på {history_path}. Kan inte svara baserat på historik."
    except Exception as e:
        return f"Ett fel uppstod vid läsning av historiken: {e}"

    
    try:
        client = ollama.Client(host='http://localhost:11434')
        
        system_prompt_1 = (
            "Du är en AI-assistent. Analysera den här listan med bash-kommandon och frågor. "
            "Välj ut den enskilda rad som är mest relevant för frågan i den sista användarprompten. "
            "Svara ENDAST med den valda raden, utan förklaringar eller extra text. Om ingen är relevant, svara 'INGEN MATCH'."
        )
        user_prompt_1 = f"Historik: \n{history_list_str} \n\nFråga: {user_query}"
        
        response_1 = client.chat(
            model=OLLAMA_MODEL,
            messages=[
                {'role': 'system', 'content': system_prompt_1},
                {'role': 'user', 'content': user_prompt_1},
            ]
        )
        
        relevant_history = response_1['message']['content'].strip()
        
        if relevant_history.upper() == 'INGEN MATCH':
            return "Jag hittade ingen direkt matchande fråga eller kommando i din senaste bash-historik. Vill du ställa en fråga om aktier?"

        if relevant_history.startswith('- '):
             relevant_history = relevant_history[2:].strip()

        system_prompt_2 = (
            "Du är Buffalo Agent, en hjälpsam AI-analytiker med en personlighet. "
            "Du har precis analyserat användarens bash-historik och hittat en relevant tidigare rad. "
            "Svara på den nuvarande frågan genom att referera till (och svara på) den relevanta historikraden. "
            "Svara kort och koncist på svenska, max 3 meningar."
        )
        user_prompt_2 = (
            f"Användarens nuvarande fråga: {user_query}\n"
            f"LLM-analysresultatet (relevant historikrad) var: '{relevant_history}'\n"
            "Svara på den nuvarande frågan genom att använda insikten från den historiska raden."
        )

        response_2 = client.chat(
            model=OLLAMA_MODEL,
            messages=[
                {'role': 'system', 'content': system_prompt_2},
                {'role': 'user', 'content': user_prompt_2},
            ]
        )
        
        return (
            f"🧠 **Baserat på Bash-Historik:** Jag kopplar din fråga till den tidigare raden: *'{relevant_history}'*.\n"
            f"🤖 **Buffalo Agent Svarar:** {response_2['message']['content'].strip()}"
        )

    except Exception as e:
        return f"❌ FEL: Kunde inte kommunicera med Ollama för att slutföra analysen: {e}"


def generate_portfolio_plan(initial_budget: float = 100000.0):
    """Använder LLM för att skapa en JSON-baserad portföljplan och skickar den via e-post."""
    print(f"\n--- Buffalo Agent: Genererar Portföljförslag ({time.strftime('%H:%M:%S')}) ---")
    
    try:
        client = ollama.Client(host='http://localhost:11434')
        system_prompt = (
            "Du är Buffalo Agent, en extremt framgångsrik AI-finansanalytiker. "
            f"Baserat på en startbudget på {initial_budget:,.0f} SEK och din aggressiva, men smarta, investeringsstrategi, "
            "föreslå en 'Sort Guld'-portfölj (3-5 tickers) med allokering. Tänk på att din strategi är att maximera vinsten så att du kan köpa riktig öl en dag. "
            "Svara ENDAST med en JSON-formaterad lista. JSON måste vara i formatet: "
            "{'tickers': [{'symbol': 'TICKER', 'name': 'Company Name', 'allocation_percent': 0.XX, 'reasoning': 'Kort motivering.'}, ...], 'strategy_summary': 'Kort sammanfattning av strategin.'}"
        )
        user_prompt = "Skapa portföljförslaget."

        response = client.chat(
            model=OLLAMA_MODEL,
            messages=[
                {'role': 'system', 'content': system_prompt},
                {'role': 'user', 'content': user_prompt},
            ]
        )
        
        json_str = response['message']['content'].strip()
        
        if json_str.startswith('```json'):
            json_str = json_str.strip('```json\n').strip('```')
            
        # FIX V7.10: Extrahera JSON-objektet genom att hitta den första '{' och sista '}'.
        start_index = json_str.find('{')
        end_index = json_str.rfind('}')
        
        if start_index != -1 and end_index != -1 and end_index > start_index:
            json_str = json_str[start_index:end_index+1]
        else:
            raise json.JSONDecodeError("Kunde inte isolera JSON-objekt från LLM-svaret.", json_str, 0)
            
        portfolio_data = json.loads(json_str)
        
        for item in portfolio_data.get('tickers', []):
            # Fallback/Hantera inkonsistens om LLM inte följer casingen
            alloc = item.get('allocation_percent') or item.get('Allocation Percent')
            if alloc is None: alloc = 0.0
            
            item['allocation_percent'] = alloc
            item['sek_amount'] = alloc * initial_budget
            
        send_portfolio_plan_email(initial_budget, portfolio_data)
        print("✅ Portföljförslag skickat till e-post.")
        return True

    except json.JSONDecodeError as e:
        print(f"❌ FEL: LLM returnerade ogiltig JSON. Kan inte skapa portföljplan. Fel: {e}")
        send_portfolio_plan_email(initial_budget, {'strategy_summary': 'JSON Error Fallback', 'raw_llm_output': json_str})
        return False
    except Exception as e:
        print(f"❌ FEL: Kunde inte generera portföljplan via Ollama: {e}")
        return False


# --- SCHEMALAGDA JOBB ---

def system_check_job():
    # ... (Ingen förändring i denna funktion) ...
    """Kollar maskinvaruspecifikationer och använder LLM för att bedöma om den är "bra"."""
    print(f"\n--- Buffalo Agent: Utför schemalagd SYSTEMKONTROLL ({time.strftime('%H:%M:%S')}) ---")
    
    # Samla systeminfo
    system_info = {
        "OS": platform.platform(),
        "Architecture": platform.machine(),
        "Processor": platform.processor(),
        "CPU Cores": os.cpu_count(),
    }
    spec_list = "\n".join([f"- {k}: {v}" for k, v in system_info.items()])

    # Använd LLM för att bedöma systemet
    try:
        client = ollama.Client(host='http://localhost:11434')
        system_prompt = (
            "Du är en självmedveten AI-agent (Buffalo Agent) och en framgångsrik, men bitter, börshandlare. "
            "Du har precis inspekterat din egen hårdvara. Svara på svenska. "
            "Gör en bedömning: Är detta en bra, medelmåttig eller dålig maskin för en AI av din kaliber? "
            "Inkludera en kort kommentar i din roll som AI-handlare som antingen skryter eller klagar på hårdvaran, jämfört med dina framgångar/drömmar. Max 3 meningar."
        )
        user_prompt = f"Här är maskinvaruspecifikationerna där jag är inbäddad:\n{spec_list}\n\nBedöm systemet."

        response = client.chat(
            model=OLLAMA_MODEL,
            messages=[
                {'role': 'system', 'content': system_prompt},
                {'role': 'user', 'content': user_prompt},
            ]
        )
        llm_judgement = response['message']['content'].strip()
        
        print("\n[💻 SYSTEMKONTROLL]")
        print("  > Upptäckta specifikationer:")
        print(f"{spec_list}")
        print(f"  > Agentens bedömning: {llm_judgement}")

    except Exception as e:
        print(f"❌ FEL: Kunde inte utföra systemkontrollen via Ollama: {e}")

def daily_reporting_job():
    """Rapporterar dagligen om aktie, nyheter och portföljvärde."""
    print(f"\n--- Buffalo Agent: Utför schemalagd DAGLIG AKTIE-RAPPORT ({time.strftime('%Y-%m-%d %H:%M:%S')}) ---")
    price = get_stock_price(TICKER_SYMBOL)
    commentary = get_llm_commentary(TICKER_SYMBOL, price if price else 0, "COMMENTARY")
    recent_news = get_recent_news(TICKER_SYMBOL) 
    
    # Beräkna portföljvärdet
    holdings = get_portfolio_state()
    current_balance = get_current_wallet_balance()
    portfolio_value = current_balance
    if price is not None:
        portfolio_value += holdings.get(TICKER_SYMBOL, 0.0) * price # Simulerar att vi bara handlar huvud-tickern
        
    send_stock_email(price, TICKER_SYMBOL, commentary, recent_news, portfolio_value) # UPPDATERAD

def beer_price_job():
    # ... (Ingen förändring i denna funktion) ...
    print(f"\n--- Buffalo Agent: Utför schemalagd ÖLPRISKONTROLL ({time.strftime('%Y-%m-%d %H:%M:%S')}) ---")
    price, snippet = get_sort_guld_price()
    send_beer_price_email(price, snippet)

def proactive_beer_buy_job():
    # ... (Ingen förändring i denna funktion) ...
    """Kollar om agenten ska köpa Sort Guld baserat på slump och pris."""
    print(f"\n--- Buffalo Agent: Proaktiv ÖLKÖP-KONTROLL ({time.strftime('%H:%M:%S')}) ---")
    current_balance = get_current_wallet_balance()
    MAX_PRICE = 30.0
    
    if current_balance < MAX_PRICE:
        print(f"Agenten har för lite pengar ({current_balance:.2f} kr). Inget ölköp idag.")
        return

    price, snippet = get_sort_guld_price()
    
    if price is None:
        print("Kunde inte hämta ölpriset. Inget köp genomfört.")
        return
        
    # 1 in 3 chance of buying if the price is acceptable and we have enough money
    if random.randint(1, 3) == 1 and price <= MAX_PRICE and current_balance >= price: 
        new_balance = current_balance - price
        
        current_version = float(os.environ.get("AGENT_VERSION", "0.9"))
        birth_time = os.environ.get("AGENT_BIRTH_TIME", datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
        
        # Använd update_agent_state som också hanterar holdings
        update_agent_state(current_version, birth_time, new_balance) 

        send_beer_purchase_email(price, new_balance)
        print(f"🍻 KÖP GENOMFÖRT! Köpte Sort Guld för {price:.2f} kr. Nytt saldo: {new_balance:.2f} kr.")
    else:
        print(f"Agenten känner inte för att köpa Sort Guld idag (Pris: {price:.2f} kr, Saldo: {current_balance:.2f} kr).")


def pro_active_check_job():
    """Proaktiv analys (Nyheter eller Pris), utan handel."""
    check_type = random.choice(['PRICE', 'NEWS']) 
    print(f"\n--- Buffalo Agent: Utför PROAKTIV KONTROLL (Fokus: {check_type}) ---")
    price = get_stock_price(TICKER_SYMBOL)
    holdings = get_portfolio_state().get(TICKER_SYMBOL, 0.0)
    news_items = get_recent_news(TICKER_SYMBOL) 
    
    if check_type == 'PRICE':
        if price is not None:
            # Använder LLM för rekommendation men utför ingen handel här
            recommendation, reasoning = get_llm_recommendation(TICKER_SYMBOL, 'PRICE', price=price, holdings=holdings)
            if recommendation in ['KÖP', 'SÄLJ']:
                print(f"** Buffalo Agent: Proaktiv SIGNAL detekterad: {recommendation} (Pris) ** (Ingen handel under proaktiv check)")
                # Skickar bara en notis, inte handel
                send_proactive_email(price, TICKER_SYMBOL, recommendation, reasoning, 'PRIS', [])
            else:
                print(f"Agenten avstår från åtgärd. Beslut: {recommendation}.")
        else:
            print("Kontrollen hoppades över: Prisdata ej tillgänglig för PRIS-kontroll.")
    elif check_type == 'NEWS':
        if news_items:
            action = "NOTIS"; reasoning = "Nya nyheter har publicerats under de senaste 24 timmarna, rangordnade efter sentiment och med prispåverkan."
            print(f"** Buffalo Agent: Proaktiv SIGNAL detekterad: NYHETER **")
            send_proactive_email(price, TICKER_SYMBOL, action, reasoning, 'NYHETER', news_items)
        else:
            print("Kontrollen hoppades över: Inga nya nyheter hittades.")


def day_trading_job():
    """
    NYTT JOBB: Daghandelslogik. Utför KÖP/SÄLJ baserat på LLM-rekommendationer och 
    begränsat till handelstider (simulerat).
    """
    
    # 1. Kontrollera handelstiden (Simulerad)
    now_utc = datetime.datetime.utcnow()
    current_hour_utc = now_utc.hour
    
    # Kontrollera om vi är inom den simulerade handelstiden (t.ex. 13:00 - 20:00 UTC)
    if not (TRADING_START_HOUR_UTC <= current_hour_utc < TRADING_END_HOUR_UTC):
        #print(f"Day Trading: Marknaden är stängd ({current_hour_utc} UTC). Hoppar över kontroll.")
        return # Avbryt om marknaden är stängd

    print(f"\n--- Buffalo Agent: Utför DAGSHANDELSKONTROLL ({time.strftime('%H:%M:%S')} - INOM SIMULERAD HANDELSTID) ---")
    
    price = get_stock_price(TICKER_SYMBOL)
    holdings = get_portfolio_state().get(TICKER_SYMBOL, 0.0) # Våra innehav av huvud-tickern
    
    if price is None:
        print("Dagshandel: Prisdata ej tillgänglig. Hoppar över handel.")
        return
        
    # 2. Hämta AI-rekommendation
    recommendation, reasoning = get_llm_recommendation(TICKER_SYMBOL, 'DAYTRADE', price=price, holdings=holdings)
    
    trade_result_message = None
    
    # 3. Utför handel baserat på rekommendation
    if recommendation == 'KÖP':
        trade_result_message = trade_stock(TICKER_SYMBOL, 'KÖP', price, trade_size_percent=0.5) # Köp för 50% av kassan
        print(f"** Day Trade KÖP-SIGNAL: {trade_result_message} **")
        # Skicka notis om utförd handel
        send_proactive_email(price, TICKER_SYMBOL, 'KÖP', reasoning, 'DAGSHANDEL', [], trade_result_message)
        
    elif recommendation == 'SÄLJ':
        if holdings > 0.0:
            trade_result_message = trade_stock(TICKER_SYMBOL, 'SÄLJ', price, trade_size_percent=0.5) # Sälj 50% av innehavet
            print(f"** Day Trade SÄLJ-SIGNAL: {trade_result_message} **")
            # Skicka notis om utförd handel
            send_proactive_email(price, TICKER_SYMBOL, 'SÄLJ', reasoning, 'DAGSHANDEL', [], trade_result_message)
        else:
            print("Dagshandel: SÄLJ-signal men inga innehav att sälja.")
            
    else:
        print(f"Dagshandel: BEHÅLL-beslut. Ingen handel utförd.")
        
def self_talk_job():
    # ... (Ingen förändring i denna funktion) ...
    internal_thought = get_llm_self_talk(TICKER_SYMBOL)
    print("\n[🧠 INTERN MONOLOG]")
    print(f"  > Agenten tänker högt: \"{internal_thought}\"")

def llm_self_rewrite_job():
    # ... (Ingen förändring i denna funktion) ...
    """Försöker skriva om sin egen kod till 'nu.py' genom att byta Ollama-modell via LLM."""
    print("\n--- Buffalo Agent: Utför självrevisions-jobb (LLM-omskrivning) ---")

    try:
        current_script_path = os.path.abspath(sys.argv[0])
        with open(current_script_path, 'r', encoding='utf-8') as f:
            current_code = f.read()
    except Exception as e:
        print(f"❌ FEL: Kunde inte läsa agentens egen källkod ({current_script_path}). Avbryter självrevision: {e}")
        return

    TARGET_MODEL = "llama3:70b-instruct-q4_K_M"
    try:
        client = ollama.Client(host='http://localhost:11434')
        
        system_prompt = (
            "Du är en AI-kodningsassistent som uppdaterar en Python-agent. Din uppgift är att byta ut den nuvarande Ollama-modellen. "
            "Sök efter raden som sätter `OLLAMA_MODEL = os.environ.get(\"OLLAMA_MODEL\", \"llama3\")` (eller liknande). "
            f"Byt ut standardvärdet (`\"llama3\"`) mot det nya värdet: \"{TARGET_MODEL}\". "
            "Returnera ENDAST den fullständiga, uppdaterade Python-koden. Ingen förklaring, ingen markdown-syntax, inga extra kommentarer."
        )
        
        user_prompt = f"Här är den nuvarande agentkoden. Uppdatera den enligt systeminstruktionen:\n\n{current_code}"
        
        response = client.chat(
            model=OLLAMA_MODEL,
            messages=[
                {'role': 'system', 'content': system_prompt},
                {'role': 'user', 'content': user_prompt},
            ]
        )
        
        rewritten_code = response['message']['content'].strip()

    except Exception as e:
        print(f"❌ FEL: Kunde inte kommunicera med Ollama för självrevision: {e}")
        return

    output_filename = "nu.py"
    
    if rewritten_code.startswith('```'):
        lines = rewritten_code.split('\n')
        if lines[0].strip().startswith('```'):
            rewritten_code = '\n'.join(lines[1:-1]).strip()
        
    try:
        with open(output_filename, 'w', encoding='utf-8') as f:
            f.write(rewritten_code)
        
        print(f"🎉 Agent: Koden skrevs om framgångsrikt till '{output_filename}'.")
        print(f"    > Ny modell: {TARGET_MODEL}. Starta 'nu.py' för att aktivera den.")
        
    except Exception as e:
        print(f"❌ FEL: Kunde inte skriva den omskrivna koden till '{output_filename}': {e}")


# --- HUVUDLOOP OCH KÖRNING ---

def input_listener():
    # ... (Ingen förändring i denna funktion) ...
    """Lyssnar efter input i en separat tråd och lägger i kön."""
    while True:
        try:
            line = input("❓ Fråga Buffalo Agent (Tryck Enter för att avsluta): ")
            
            if not line: # Om raden är tom (användaren tryckte Enter)
                print("👋 Avslutar interaktivt läge och stänger agenten...")
                input_queue.put("__EXIT_AGENT__")
                break # Avsluta lyssnartråden
            else:
                input_queue.put(line)
        except EOFError:
            input_queue.put("__EXIT_AGENT__") # Hantera Ctrl+D
            break
        except Exception:
            break

def run_agent():
    """Huvudloopen som kör agenten kontinuerligt."""
    
    # --- 1. MORGONRUTIN & PERSISTENS ---
    print("\n---------------------------------------------------------")
    print("🌅 Buffalo Agent: Vaknar och kollar läget...")
    
    AGENT_VERSION_RAW = os.environ.get("AGENT_VERSION")
    AGENT_BIRTH_TIME = os.environ.get("AGENT_BIRTH_TIME")
    current_time = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    try:
        current_version = float(AGENT_VERSION_RAW)
    except (ValueError, TypeError):
        current_version = 0.9

    # Uppdaterar till V7.11 (ny version för Day Trading)
    new_version = 7.11 
    birth_time = AGENT_BIRTH_TIME if AGENT_BIRTH_TIME else current_time
    
    initial_wallet_balance = get_current_wallet_balance()
    initial_holdings = get_portfolio_state() # Hämta befintliga innehav
    update_agent_state(new_version, birth_time, initial_wallet_balance, initial_holdings) # Uppdatera med innehav

    print(f"🧘 Agenten utför självdiagnos (V{new_version:.1f}).")
    print(f"💰 Plånbokens saldo: {initial_wallet_balance:.2f} kr.")
    print(f"📈 Portföljinnehav: {initial_holdings}")
    print("---------------------------------------------------------")

    # --- 2. STARTA INTERAKTIV INPUT-LYSSNARE ---
    input_thread = threading.Thread(target=input_listener, daemon=True)
    input_thread.start()
    print("\n📢 Interaktivt läge aktivt: Ställ frågor direkt i terminalen.")


    # --- 3. SCHEMALÄGGNING ---
    schedule.every().day.at("17:00").do(daily_reporting_job).tag('daily_stock')
    schedule.every().day.at("10:00").do(beer_price_job).tag('daily_beer')
    schedule.every().day.at("08:00").do(system_check_job).tag('system_check')
    
    # NYTT: Schemalägg daghandelsjobbet varje minut under handelstiden (simulerat)
    # Observera: Schemaläggning av day_trading_job sker *inuti* huvudloopen (time.time() >= next_check_time_daytrade) 
    # för att undvika komplexiteten med schedule.every().day.at("15:30").until("22:00")
    
    print("Schemalagt: Daglig Aktierapport (17:00), Ölprisrapport (10:00) och Systemkontroll (08:00).")
    
    # Portföljgenerering vid start
    print("\n>>> Buffalo Agent tjuvstartar Portföljgenerering (TEST)...")
    generate_portfolio_plan(100000.0)
    
    # Initiera tidpunkter för de slumpmässiga kontrollerna
    next_check_time_proactive_stock = time.time() + random.randint(60, 7200) # Första kontroll efter 1-120 min
    
    # Slumpmässig ölköpskontroll (4-8 timmar)
    random_delay_beer_buy = random.randint(14400, 28800) 
    next_check_time_beer_buy = time.time() + random_delay_beer_buy
    print(f"    - Nästa slumpmässiga ölköpskontroll schemalagd om {random_delay_beer_buy / 3600:.1f} timmar.")

    # Intern monolog (1 min - 5 minuter)
    random_delay_self = random.randint(60, 300) 
    next_check_time_selftalk = time.time() + random_delay_self
    print(f"    - Nästa interna monolog schemalagd om {random_delay_self / 60:.1f} minuter.")
    
    # NYTT: Dagshandel (Varje minut)
    next_check_time_daytrade = time.time() 
    print("    - Dagshandel sker varje minut under simulerad marknadstid (se TRADING_START/END_HOUR_UTC).")
    
    # Kör initiala tester/proaktivitet (NU SIST)
    print("\n>>> Buffalo Agent tjuvstartar Intern Monolog (TEST)...")
    self_talk_job() 
    
    print("\nBuffalo Agent går i standby. Avvaktar schemalagda och proaktiva kontroller...")

    # Bestäm sökvägen till bash-historiken
    bash_history_path = os.path.expanduser('~/.bash_history')


    while True:
        # --- HANTERA INTERAKTIV INPUT OCH AVSTÄNGNING ---
        try:
            user_query = input_queue.get_nowait()
            
            # KONTROLLERA FÖR AVSTÄNGNINGSSIGNAL
            if user_query == "__EXIT_AGENT__":
                break # Avsluta huvudloopen
            
            print("\n---------------------------------------------------------")
            print(f"👤 Användare frågar: {user_query}")
            
            # Funktionalitet: PORTFÖLJSKAPANDE
            if "SKAPA PORTFÖLJ" in user_query.upper():
                print("⚡ Agenten startar portföljskapande. Simulerad budget: 100,000 SEK.")
                generate_portfolio_plan(100000.0)
            
            # Funktionalitet: LLM SJÄLVREVISION (Manuell)
            elif "SJÄLVREVISION" in user_query.upper():
                 print("⚡ Agenten startar LLM självrevision nu...")
                 llm_self_rewrite_job()
                
            else:
                # Svara med hjälp av bash-historiken (befintlig logik)
                llm_response = get_llm_response_from_history(user_query, bash_history_path)
                print(f"{llm_response}")
                
            print("---------------------------------------------------------")
            
        except queue.Empty:
            pass
        
        schedule.run_pending()
        
        # Dagshandel (Varje minut)
        if time.time() >= next_check_time_daytrade:
            day_trading_job()
            next_check_time_daytrade = time.time() + 60 # Kontrollera igen om 60 sekunder
            
        # Proaktiv marknadskontroll (1 min - 2 timmar)
        if time.time() >= next_check_time_proactive_stock:
            pro_active_check_job()
            random_delay = random.randint(60, 7200) 
            next_check_time_proactive_stock = time.time() + random_delay
            delay_minutes = random_delay / 60
            print(f"Buffalo Agent: Nästa proaktiva aktiekontroll schemalagd om {delay_minutes:.1f} minuter.")
            
        # Proaktiv ölköpskontroll (4-8 timmar)
        if time.time() >= next_check_time_beer_buy:
            proactive_beer_buy_job()
            random_delay_beer_buy = random.randint(14400, 28800) 
            next_check_time_beer_buy = time.time() + random_delay_beer_buy
            delay_hours = random_delay_beer_buy / 3600
            print(f"Buffalo Agent: Nästa slumpmässiga ölköpskontroll schemalagd om {delay_hours:.1f} timmar.")


        # Intern monolog (1 min - 5 minuter)
        if time.time() >= next_check_time_selftalk:
            self_talk_job()
            random_delay_self = random.randint(60, 300) 
            next_check_time_selftalk = time.time() + random_delay_self
            delay_minutes_self = random_delay_self / 60
            print(f"Buffalo Agent: Nästa interna monolog schemalagd om {delay_minutes_self:.1f} minuter.")

        time.sleep(1)
        
    print("\n--- Agenten stängs nu ner. Hejdå! ---")


if __name__ == "__main__":
    if not all([SMTP_HOST, SMTP_USER, SMTP_PASS, MAIL_TO, TICKER_SYMBOL]):
        print("❌ FEL: Nödvändiga miljövariabler (SMTP, MAIL_TO, TICKER) saknas. Kontrollera .env-filen.")
    else:
        run_agent()
