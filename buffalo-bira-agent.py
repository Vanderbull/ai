import os
import smtplib
import yfinance as yf
from dotenv import load_dotenv
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
import ollama # <--- NYTT: Ollama-biblioteket

# 1. Ladda miljövariablerna
load_dotenv()

# Hämta alla variabler (se till att alla finns i .env-filen)
SMTP_HOST = os.environ.get("SMTP_HOST")
SMTP_PORT = os.environ.get("SMTP_PORT")
SMTP_USER = os.environ.get("SMTP_USER")
SMTP_PASS = os.environ.get("SMTP_PASS")
MAIL_TO = os.environ.get("MAIL_TO")
TICKER_SYMBOL = os.environ.get("YFINANCE_TICKER", "AMD")

OLLAMA_MODEL = os.environ.get("OLLAMA_MODEL", "llama3.1") # Lägg till i .env

# --- OLLAMA FUNKTION ---

def get_llm_commentary(ticker: str, price: float) -> str:
    """Hämtar en kort analys från Ollama-modellen."""
    try:
        client = ollama.Client(host='http://localhost:11434')
        
        # Systemmeddelande ger modellen dess roll
        system_prompt = (
            "Du är en finansiell analytiker. Skriv en kort, koncis kommentar "
            f"på en enda mening (max 20 ord) om aktiekursen för {ticker}."
            "Kommentera endast priset och trenden, och inkludera inte emojis."
        )
        
        # Användarmeddelande med de aktuella uppgifterna
        user_prompt = f"Aktuellt pris för {ticker} är ${price:.2f}. Vad är din korta bedömning?"
        
        response = client.chat(
            model=OLLAMA_MODEL,
            messages=[
                {'role': 'system', 'content': system_prompt},
                {'role': 'user', 'content': user_prompt},
            ]
        )
        # Returnerar modellens svar
        return response['message']['content'].strip()

    except Exception as e:
        print(f"FEL vid kommunikation med Ollama: {e}")
        return "Kunde inte generera AI-kommentar."

# --- AKTIE FUNKTION (från tidigare) ---

def get_stock_price(ticker_symbol: str) -> float | None:
    # ... (Använd samma get_stock_price-funktion som tidigare)
    try:
        stock = yf.Ticker(ticker_symbol)
        info = stock.info
        return info.get('currentPrice') or info.get('regularMarketPrice')
    except Exception as e:
        print(f"FEL vid hämtning av aktiedata: {e}")
        return None

# --- E-POST FUNKTION (uppdaterad) ---

def send_stock_email(price: float, ticker: str, commentary: str):
    """Skickar e-postmeddelandet med pris och AI-kommentar."""
    
    price_str = f"${price:,.2f}" if price is not None else "PRIS EJ TILLGÄNGLIGT"
    
    msg = MIMEMultipart()
    msg['From'] = SMTP_USER
    msg['To'] = MAIL_TO
    msg['Subject'] = f"Aktuell Kursrapport: {ticker} (med AI-kommentar)"
    
    html_body = f"""\
    <html>
      <body>
        <h2>Aktuell Kurs för {ticker}</h2>
        <p style="font-size: 20px; color: #007bff;">
          Aktuellt pris: <strong>{price_str}</strong>
        </p>
        
        <h3>AI-Analys (Ollama)</h3>
        <blockquote style="border-left: 4px solid #f0ad4e; padding-left: 15px; margin: 15px 0; background: #f9f9f9;">
          "{commentary}"
        </blockquote>
        
        <p>Denna rapport genererades automatiskt av din lokala AI-agent.</p>
      </body>
    </html>
    """
    
    msg.attach(MIMEText(html_body, 'html'))
    
    try:
        # ... (Använd samma SMTP-logik som tidigare)
        server = smtplib.SMTP(SMTP_HOST, int(SMTP_PORT))
        server.ehlo()
        server.starttls()
        server.ehlo()
        server.login(SMTP_USER, SMTP_PASS)
        server.sendmail(SMTP_USER, MAIL_TO, msg.as_string())
        print(f"✅ E-post skickat framgångsrikt till {MAIL_TO}!")
    except Exception as e:
        print(f"❌ FEL vid sändning av e-post: {e}")
    finally:
        if 'server' in locals():
            server.quit()


# --- KÖR KODEN ---

if __name__ == "__main__":
    
    # 1. Hämta priset
    amd_price = get_stock_price(TICKER_SYMBOL)
    
    if amd_price is not None:
        # 2. Hämta Ollama-kommentaren
        ai_commentary = get_llm_commentary(TICKER_SYMBOL, amd_price)
        print(f"AI Kommentar: {ai_commentary}")
        
        # 3. Skicka e-post
        send_stock_email(amd_price, TICKER_SYMBOL, ai_commentary)
    else:
        print("Kan inte köra agenten eftersom aktiekursen inte kunde hämtas.")
