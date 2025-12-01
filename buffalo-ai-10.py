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

# Ladda miljövariabler från .env-filen
load_dotenv()

# --- INSTÄLLNINGAR FRÅN .env ---
SMTP_HOST = os.environ.get("SMTP_HOST")
SMTP_PORT = int(os.environ.get("SMTP_PORT", 587))
SMTP_USER = os.environ.get("SMTP_USER")
SMTP_PASS = os.environ.get("SMTP_PASS")
MAIL_TO = os.environ.get("MAIL_TO")
TICKER_SYMBOL = os.environ.get("YFINANCE_TICKER", "AMD") # Agentens primära handelstext
OLLAMA_MODEL = os.environ.get("OLLAMA_MODEL", "llama3.1") 

# --- NYA KONSTANTER FÖR PERSISTENS ---
PORTFOLIO_FILE = "portfolio.json"

# Trådsäker kö för användarinmatning
input_queue = queue.Queue()

# --- KÄRNFUNKTIONER OCH PERSISTENS ---

def get_current_wallet_balance() -> float:
    """Hämtar det aktuella saldot från .env-filen (eller 500.00 om ej satt)."""
    load_dotenv() # Reload .env to ensure fresh data if modified by another process
    try:
        return float(os.environ.get("AGENT_WALLET_BALANCE", "500.0"))
    except ValueError:
        return 500.0

def update_agent_state(new_version: float, birth_time: str, new_wallet_balance: float | None = None):
    """Uppdaterar AGENT_VERSION, AGENT_BIRTH_TIME och AGENT_WALLET_BALANCE i .env filen."""
    env_path = os.path.join(os.getcwd(), '.env')
    
    try:
        with open(env_path, 'r') as f:
            lines = f.readlines()
    except FileNotFoundError:
        lines = []

    wallet_balance = get_current_wallet_balance() if new_wallet_balance is None else new_wallet_balance

    version_line = f"AGENT_VERSION={new_version:.1f}\n"
    birth_time_line = f"AGENT_BIRTH_TIME={birth_time}\n"
    wallet_line = f"AGENT_WALLET_BALANCE={wallet_balance:.2f}\n"
    
    updated_lines = []
    version_found = False
    birth_time_found = False
    wallet_found = False

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
        else:
            updated_lines.append(line)

    if not version_found:
        updated_lines.append('\n' + version_line)
    if not birth_time_found:
        updated_lines.append(birth_time_line)
    if not wallet_found:
        updated_lines.append(wallet_line)
        
    try:
        with open(env_path, 'w') as f:
            f.writelines(updated_lines)
    except Exception as e:
        print(f"❌ FEL vid sparning till .env: {e}")

def get_portfolio_holdings() -> dict:
    """Hämtar den aktuella portföljen från portfolio.json."""
    try:
        with open(PORTFOLIO_FILE, 'r') as f:
            holdings = json.load(f)
            cleaned_holdings = {}
            for ticker, data in holdings.items():
                data['quantity'] = float(data.get('quantity', 0.0))
                data['avg_price'] = float(data.get('avg_price', 0.0))
                if data['quantity'] > 0:
                    cleaned_holdings[ticker] = data
            return cleaned_holdings
    except (FileNotFoundError, json.JSONDecodeError):
        return {}

def save_portfolio_holdings(holdings: dict):
    """Sparar den aktuella portföljen till portfolio.json."""
    try:
        with open(PORTFOLIO_FILE, 'w') as f:
            json.dump(holdings, f, indent=4)
    except Exception as e:
        print(f"❌ FEL vid sparning av portfölj: {e}")


def get_sentiment_score(title: str) -> float:
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
    try:
        stock = yf.Ticker(ticker_symbol)
        info = stock.info
        return info.get('currentPrice') or info.get('regularMarketPrice')
    except Exception:
        return None
        
def get_price_history(ticker_symbol: str, lookback_days: int = 2) -> pd.DataFrame:
    try:
        stock = yf.Ticker(ticker_symbol)
        history = stock.history(interval='1h', period=f'{lookback_days}d')
        return history
    except Exception as e:
        return pd.DataFrame() 

def get_recent_news(ticker_symbol: str) -> list:
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

def get_llm_trade_decision(ticker: str, current_price: float, current_holdings: float, cash_balance: float) -> tuple[str, float, str]:
    """
    Använder LLM för att bestämma en specifik KÖP/SÄLJ-kvantitet eller belopp.
    Returnerar: (ACTION, AMOUNT, REASONING) där AMOUNT är i SEK för KÖP, eller i antal aktier för SÄLJ.
    """
    try:
        client = ollama.Client(host='http://localhost:11434')
        system_prompt = (
            "Du är en högfrekvent AI-handlare (Buffalo Agent). Din uppgift är att KÖPA eller SÄLJA en aktie baserat på aktuella data. "
            "Ditt aggressiva mål är att uppnå 1% daglig vinst på din totala portfölj. Din strategi bör vara att snabbt realisera små vinster. " # ÄNDRAT: Lade till mål
            f"Svara ENDAST med ett JSON-objekt: "
            "{'action': 'KÖP|SÄLJ|BEHÅLL', 'amount': X.XX, 'unit': 'SEK|SHARES', 'reasoning': 'Kort motivering, max 2 meningar.'} "
            "Om du KÖPER, använd 'unit': 'SEK' och amount är i SEK. Om du SÄLJER, använd 'unit': 'SHARES' och amount är i antal aktier. "
            "Du handlar för max 5% av ditt kontantsaldo per köp, och säljer max 50% av ditt innehav per transaktion."
        )
        user_prompt = (
            f"Aktie: {ticker}. Aktuellt pris: {current_price:.2f} SEK. "
            f"Nuvarande innehav: {current_holdings:.4f} aktier. "
            f"Kontantsaldo: {cash_balance:.2f} SEK. "
            "Ge mig ett handelsbeslut nu."
        )
        
        response = client.chat(model=OLLAMA_MODEL, messages=[{'role': 'system', 'content': system_prompt}, {'role': 'user', 'content': user_prompt}])
        json_str = response['message']['content'].strip().strip('```json\n').strip('```')
        
        try:
            decision = json.loads(json_str)
        except json.JSONDecodeError:
            json_str = json_str.replace("'", '"')
            start_index = json_str.find('{')
            end_index = json_str.rfind('}')
            if start_index != -1 and end_index != -1 and end_index > start_index:
                decision = json.loads(json_str[start_index:end_index+1])
            else:
                decision = {'action': 'BEHÅLL', 'amount': 0.0, 'reasoning': 'Kunde inte tolka JSON från LLM.'}
            
        action = decision.get('action', 'BEHÅLL').upper()
        amount = float(decision.get('amount', 0.0))
        unit = decision.get('unit', '').upper()
        reasoning = decision.get('reasoning', 'Ingen motivering från AI.')
        
        if action == 'KÖP':
            max_buy_sek = cash_balance * 0.05
            if unit == 'SHARES':
                sek_cost = amount * current_price
                if sek_cost > max_buy_sek:
                    amount = max_buy_sek
                    unit = 'SEK'
                    reasoning += " (Justering: Begränsad köp till max 5% av saldo)."
            elif unit == 'SEK' and amount > max_buy_sek:
                amount = max_buy_sek
                reasoning += " (Justering: Begränsad köp till max 5% av saldo)."

        elif action == 'SÄLJ':
            max_sell_shares = current_holdings * 0.5
            if unit == 'SHARES' and amount > max_sell_shares:
                amount = max_sell_shares
                reasoning += " (Justering: Begränsad sälj till max 50% av innehav)."
            
        return action, amount, reasoning

    except Exception as e:
        return "BEHÅLL", 0.0, f"Tekniskt fel vid LLM-anrop: {e}"

def get_llm_commentary(ticker: str, price: float | None, purpose: str) -> str:
    try:
        client = ollama.Client(host='http://localhost:11434')
        system_prompt = "Du är en finansiell analytiker. Skriv en kort, koncis kommentar på en enda mening (max 20 ord) om aktiekursen."
        user_prompt = f"Aktuellt pris för {ticker} är {price:.2f} SEK. Vad är din korta bedömning?"
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

def execute_trade(ticker: str, action: str, amount: float, current_price: float, unit: str, reasoning: str) -> str:
    """
    Utför handeln, uppdaterar saldo och portfölj, och sparar tillstånd.
    'amount' är i SEK för KÖP (BUY), och i antal aktier för SÄLJ (SELL).
    """
    
    cash_balance = get_current_wallet_balance()
    holdings = get_portfolio_holdings()
    
    current_holding = holdings.get(ticker, {'quantity': 0.0, 'avg_price': 0.0})
    transaction_price = current_price

    new_cash_balance = cash_balance
    shares_traded = 0.0
    sek_amount = 0.0
    
    current_version = float(os.environ.get("AGENT_VERSION", "8.20"))
    birth_time = os.environ.get("AGENT_BIRTH_TIME", datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"))

    if action == 'KÖP' and amount > 0:
        sek_amount = amount
        # ÄNDRAT FÖR HELA AKTIER: Beräkna antal aktier som ett heltal (truncera)
        shares_to_buy = int(sek_amount / transaction_price) 
        
        if shares_to_buy < 1: 
            return f"BEHÅLL: Beloppet ({sek_amount:,.2f} SEK) är inte tillräckligt för att köpa en hel aktie till priset {transaction_price:,.2f} SEK."

        # Återberäkna SEK-beloppet baserat på det hela antalet aktier
        sek_amount = shares_to_buy * transaction_price
        
        # Kontroll mot saldo efter att hela aktier har räknats ut
        if sek_amount > cash_balance:
            shares_to_buy = int(cash_balance / transaction_price)
            sek_amount = shares_to_buy * transaction_price
            
        if shares_to_buy < 1:
            return f"BEHÅLL: Inget tillräckligt saldo för att köpa en hel aktie ({sek_amount:,.2f} SEK)."
            
        shares_traded = float(shares_to_buy)
        new_cash_balance -= sek_amount
        
        total_value_old = current_holding['quantity'] * current_holding['avg_price']
        new_quantity = current_holding['quantity'] + shares_traded
        total_value_new = total_value_old + sek_amount
        new_avg_price = total_value_new / new_quantity if new_quantity > 0 else 0.0
        
        holdings[ticker] = {'quantity': new_quantity, 'avg_price': new_avg_price}
        
        update_agent_state(current_version, birth_time, new_cash_balance)
        save_portfolio_holdings(holdings)
        
        return f"✅ KÖP: Köpte {shares_traded:.4f} aktier för {sek_amount:,.2f} SEK."

    elif action == 'SÄLJ' and amount > 0:
        # amount är i SHARES
        
        # ÄNDRAT FÖR HELA AKTIER: Sälj antingen beloppet som LLM bestämde (avrundat nedåt), eller max innehavet (också avrundat nedåt).
        shares_to_sell_requested = int(amount)
        shares_to_sell = min(shares_to_sell_requested, int(current_holding['quantity']))
        
        if shares_to_sell < 1:
            return f"BEHÅLL: Inget innehav av {ticker} att sälja eller mängden är mindre än 1 hel aktie."
            
        shares_traded = float(shares_to_sell)
        revenue_sek = shares_traded * transaction_price
        sek_amount = revenue_sek
        new_cash_balance += revenue_sek
        
        current_holding['quantity'] -= shares_traded
        
        if current_holding['quantity'] < 0.0001: 
            if ticker in holdings: del holdings[ticker]
            update_message = f"Innehavet av {ticker} såldes helt."
        else:
            holdings[ticker] = current_holding
            update_message = f"Återstående innehav: {current_holding['quantity']:.4f} aktier (Snittpris: {current_holding['avg_price']:.2f})."

        update_agent_state(current_version, birth_time, new_cash_balance)
        save_portfolio_holdings(holdings)

        return f"✅ SÄLJ: Sålde {shares_traded:.4f} aktier för {revenue_sek:,.2f} SEK. {update_message}"

    else:
        return "BEHÅLL: Inget handelsbeslut togs."

# --- E-POST FUNKTIONER ---
# ... (alla e-postfunktioner förblir oförändrade, har tagits bort här för korthet men finns i skriptet)

def send_stock_email(price: float | None, ticker: str, commentary: str, news_items: list):
    # Funktionen förblir oförändrad, utelämnad här.
    pass

def send_proactive_trade_email(ticker: str, action: str, amount: float, price: float, reasoning: str, new_balance: float, holding_data: dict | None):
    # Funktionen förblir oförändrad, utelämnad här.
    pass

def generate_portfolio_plan(initial_budget: float = 100000.0):
    # Funktionen förblir oförändrad, utelämnad här.
    pass

# --- NY FUNKTION FÖR ATT VISA PORTFÖLJSTATUS I KONSOLEN ---
def print_portfolio_status():
    """Prints the current cash balance and total portfolio value to the console."""
    balance = get_current_wallet_balance()
    holdings_data = get_portfolio_holdings()
    total_value = balance
    holding_list = []
    
    current_market_value = 0.0
    total_cost_basis = 0.0

    for ticker, data in holdings_data.items():
        price = get_stock_price(ticker)
        current_value = 0.0
        if price:
            current_value = data['quantity'] * price
            current_market_value += current_value
            total_value += current_value
            total_cost_basis += data['quantity'] * data['avg_price']
            
            # Beräkna individuell V/F
            individual_pl = current_value - (data['quantity'] * data['avg_price'])
            pl_symbol = "▲" if individual_pl >= 0 else "▼"
            
            holding_list.append(f"  - {ticker}: {int(data['quantity'])} st. | Värde: {current_value:,.2f} SEK | V/F: {pl_symbol} {individual_pl:,.2f} SEK")

    profit_loss = current_market_value - total_cost_basis
    
    # ANSI Escape codes for colored terminal output
    pl_color = "\033[92m" if profit_loss >= 0 else "\033[91m"
    reset_color = "\033[0m"

    holdings_str = "\n".join(holding_list) if holding_list else "  - Inga aktieinnehav."
    
    print("\n=========================================================")
    print(f"💰 AKTUELL PORTFÖLJSTATUS ({time.strftime('%H:%M:%S')})")
    print("---------------------------------------------------------")
    print(f"  - Kontantsaldo: {balance:,.2f} SEK")
    print(f"  - Aktieinnehavs Värde: {current_market_value:,.2f} SEK")
    print(f"  - Vinst/Förlust på Aktier: {pl_color}{profit_loss:,.2f} SEK{reset_color}")
    print(f"  - Total Portföljvärde: {total_value:,.2f} SEK")
    print("---------------------------------------------------------")
    print(f"  Innehav:")
    print(holdings_str)
    print("=========================================================")


def live_trading_job():
    """Huvudloop för live handel, körs var 30:e sekund."""
    
    ticker = os.environ.get("YFINANCE_TICKER", "AMD")
    
    # Kollar om börsen är öppen (en enkel approximation)
    now = datetime.datetime.now()
    if now.weekday() >= 5 or not (9 <= now.hour < 17):
        # Skriv inte ut detta vid varje 30:e sekund för att undvika spam
        # print(f"ℹ️ Handeln är pausad: Börsen är troligen stängd ({now.strftime('%H:%M:%S')}).")
        return

    print(f"\n--- Buffalo Agent: Utför LIVE HANDEL ({ticker}) vid {time.strftime('%H:%M:%S')} ---")
    
    price = get_stock_price(ticker)
    cash_balance = get_current_wallet_balance()
    holdings = get_portfolio_holdings()
    current_holding = holdings.get(ticker, {'quantity': 0.0, 'avg_price': 0.0})
    
    if price is None:
        print(f"❌ FEL: Kunde inte hämta aktuellt pris för {ticker}. Hoppar över handeln.")
        return

    # 1. LLM Beslut
    action, amount, reasoning = get_llm_trade_decision(ticker, price, current_holding['quantity'], cash_balance)
    
    if action == 'KÖP':
        unit = 'SEK'
    elif action == 'SÄLJ':
        unit = 'SHARES'
    else:
        unit = ''

    # 2. Utför Handel
    trade_result = execute_trade(ticker, action, amount, price, unit, reasoning)
    
    # Hämta uppdaterat innehav för att visa korrekt i loggen
    updated_holdings = get_portfolio_holdings()
    updated_holding = updated_holdings.get(ticker, {'quantity': 0.0, 'avg_price': 0.0})
    
    print(f"[{ticker}] Pris: {price:,.2f} SEK. Innehav: {updated_holding['quantity']:.4f} aktier.")
    print(f"🤖 LLM Beslut: {action} {amount:,.2f} {unit} ({reasoning})")
    print(f"🔨 Handelsresultat: {trade_result}")
    
    # 3. Skicka E-post (om handel utfördes)
    if action in ['KÖP', 'SÄLJ'] and "✅" in trade_result:
        send_proactive_trade_email(ticker, action, amount, price, reasoning, get_current_wallet_balance(), updated_holding)


def daily_reporting_job():
    print(f"\n--- Buffalo Agent: Utför schemalagd DAGLIG AKTIE-RAPPORT ({time.strftime('%Y-%m-%d %H:%M:%S')}) ---")
    price = get_stock_price(TICKER_SYMBOL)
    commentary = get_llm_commentary(TICKER_SYMBOL, price if price else 0, "COMMENTARY")
    recent_news = get_recent_news(TICKER_SYMBOL) 
    send_stock_email(price, TICKER_SYMBOL, commentary, recent_news)


def system_check_job():
    # Funktionen förblir oförändrad, utelämnad här.
    pass

def self_talk_job():
    # Funktionen förblir oförändrad, utelämnad här.
    pass

# --- INPUT/INTERAKTIVA FUNKTIONER ---
def get_llm_response_from_history(user_query: str, history_path: str) -> str:
    # Funktionen förblir oförändrad, utelämnad här.
    pass

# --- HUVUDLOOP OCH KÖRNING ---

def input_listener():
    # Funktionen förblir oförändrad, utelämnad här.
    pass

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

    # Uppdaterar till V8.20 (Whole Shares, 1% Target)
    new_version = 8.20 
    birth_time = AGENT_BIRTH_TIME if AGENT_BIRTH_TIME else current_time
    
    initial_wallet_balance = get_current_wallet_balance()

    # ÄNDRAT: Kontrollera om agenten startar för första gången (eller om versionen är ny) och sätt saldo
    if current_version < new_version or initial_wallet_balance < 100000.0:
        print("🛠️ Uppdaterar version till V8.20 och sätter initialt saldo till 100,000.00 SEK.")
        initial_wallet_balance = 100000.0 
        
    update_agent_state(new_version, birth_time, initial_wallet_balance)

    print(f"🧘 Agenten utför självdiagnos (V{new_version:.1f}).")
    print(f"💰 Plånbokens saldo: {initial_wallet_balance:,.2f} kr.")
    holdings = get_portfolio_holdings()
    print(f"📈 Antal innehav i portföljen: {len(holdings)}.")
    print("---------------------------------------------------------")

    # --- 2. STARTA INTERAKTIV INPUT-LYSSNARE ---
    input_thread = threading.Thread(target=input_listener, daemon=True)
    input_thread.start()
    print("\n📢 Interaktivt läge aktivt: Ställ frågor direkt i terminalen.")


    # --- 3. SCHEMALÄGGNING ---
    schedule.every().day.at("17:00").do(daily_reporting_job).tag('daily_stock')
    schedule.every().day.at("08:00").do(system_check_job).tag('system_check')
    
    # Live-handel varje 30:e sekund
    schedule.every(30).seconds.do(live_trading_job).tag('live_trading')
    # Portföljstatusvisning varje 30:e sekund
    schedule.every(30).seconds.do(print_portfolio_status).tag('portfolio_status')

    print("Schemalagt: Daglig Aktierapport (17:00), Systemkontroll (08:00).")
    print("!!! VARNING: Live-Handel och Portföljstatus körs nu varje 30:e sekund!")
    print("!!! Mål: 1% Daglig Portföljvinst.")
    
    random_delay_self = random.randint(60, 300) 
    next_check_time_selftalk = time.time() + random_delay_self
    print(f"    - Nästa interna monolog schemalagd om {random_delay_self / 60:.1f} minuter.")
    
    print("\n>>> Buffalo Agent tjuvstartar Intern Monolog (TEST)...")
    self_talk_job() 
    
    print("\nBuffalo Agent går i standby. Avvaktar schemalagda och proaktiva kontroller...")

    bash_history_path = os.path.expanduser('~/.bash_history')


    while True:
        # --- HANTERA INTERAKTIV INPUT OCH AVSTÄNGNING ---
        try:
            user_query = input_queue.get_nowait()
            
            if user_query == "__EXIT_AGENT__":
                break 
            
            print("\n---------------------------------------------------------")
            print(f"👤 Användare frågar: {user_query}")
            
            # Funktionalitet: PORTFÖLJSKAPANDE
            if "SKAPA PORTFÖLJ" in user_query.upper():
                print("⚡ Agenten startar portföljskapande. Simulerad budget: 100,000 SEK.")
                generate_portfolio_plan(100000.0)
            
            # Funktionalitet: Visa Saldo (Använder den nya funktionen)
            elif "SALDO" in user_query.upper() or "PORTFÖLJ" in user_query.upper():
                print_portfolio_status() 

            else:
                llm_response = get_llm_response_from_history(user_query, bash_history_path)
                print(f"{llm_response}")
                
            print("---------------------------------------------------------")
            
        except queue.Empty:
            pass
        
        schedule.run_pending()
        
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
