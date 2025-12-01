import os
import smtplib
import yfinance as yf
from dotenv import load_dotenv
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

# 1. Ladda miljövariablerna från .env-filen
load_dotenv()

# 2. Hämta alla nödvändiga variabler
SMTP_HOST = os.environ.get("SMTP_HOST")
SMTP_PORT = os.environ.get("SMTP_PORT")
SMTP_USER = os.environ.get("SMTP_USER")
SMTP_PASS = os.environ.get("SMTP_PASS")
MAIL_TO = os.environ.get("MAIL_TO")
# Hämta Ticker-symbolen från .env-filen (t.ex. AMD)
TICKER_SYMBOL = os.environ.get("YFINANCE_TICKER", "AMD") # Standardvärde är AMD

# --- FUNKTIONER ---

def get_stock_price(ticker_symbol: str) -> float | None:
    """Hämtar det aktuella priset för en given ticker."""
    try:
        # Skapa ett Ticker-objekt
        stock = yf.Ticker(ticker_symbol)
        
        # Hämta den senaste informationen (använder .info för att få currentPrice)
        info = stock.info
        
        # 'currentPrice' är en vanlig nyckel för det senaste priset i yfinance info-objektet
        current_price = info.get('currentPrice')
        
        if current_price:
            print(f"Hämtat pris för {ticker_symbol}: {current_price}")
            return current_price
        else:
            # Fallback om 'currentPrice' saknas (kan hända utanför handelstider)
            print(f"Varning: 'currentPrice' saknas. Försöker med 'regularMarketPrice'.")
            return info.get('regularMarketPrice')
            
    except Exception as e:
        print(f"FEL vid hämtning av aktiedata för {ticker_symbol}: {e}")
        return None

def send_stock_email(price: float, ticker: str):
    """Skickar ett e-postmeddelande med det aktuella aktiepriset."""

    # --- Förbered e-postmeddelandet ---
    
    # Formatera priset till en sträng med två decimaler
    price_str = f"${price:,.2f}" if price is not None else "PRIS EJ TILLGÄNGLIGT"
    
    msg = MIMEMultipart()
    msg['From'] = SMTP_USER
    msg['To'] = MAIL_TO
    msg['Subject'] = f"Aktuell Kursrapport: {ticker}"
    
    # Skapa innehållet
    html_body = f"""\
    <html>
      <body>
        <h2>Aktuell Kurs för {ticker}</h2>
        <p style="font-size: 20px; color: #007bff;">
          Aktuellt pris: <strong>{price_str}</strong>
        </p>
        <p>Denna rapport genererades automatiskt av ditt Python-skript.</p>
        <p><small>Vänligen notera att aktiekurser utanför marknadens öppettider kan vara det sista stängningspriset.</small></p>
      </body>
    </html>
    """
    
    # Bifoga både klartext och HTML (bästa praxis)
    msg.attach(MIMEText(html_body, 'html'))
    
    # --- Skicka e-postmeddelandet ---

    try:
        print("Försöker ansluta till SMTP-servern...")
        server = smtplib.SMTP(SMTP_HOST, int(SMTP_PORT))
        server.ehlo()
        server.starttls()  # Använd TSL/STARTTLS för säker anslutning
        server.ehlo()
        
        server.login(SMTP_USER, SMTP_PASS)
        
        server.sendmail(SMTP_USER, MAIL_TO, msg.as_string())
        print(f"✅ E-post skickat framgångsrikt till {MAIL_TO}!")
        
    except smtplib.SMTPAuthenticationError:
        print("❌ FEL: Misslyckades med inloggning. Kontrollera App-lösenordet.")
    except Exception as e:
        print(f"❌ Ett oväntat fel uppstod vid sändning: {e}")
    finally:
        if 'server' in locals():
            server.quit()


# --- KÖR KODEN ---

if __name__ == "__main__":
    
    # Kontrollera att alla variabler finns
    required_vars = [SMTP_HOST, SMTP_PORT, SMTP_USER, SMTP_PASS, MAIL_TO, TICKER_SYMBOL]
    if not all(required_vars):
        print("❌ FEL: Vissa nödvändiga miljövariabler saknas i din .env-fil.")
        print(f"Kontrollerade variabler: {', '.join([v for v, val in zip(['SMTP_HOST', 'SMTP_PORT', 'SMTP_USER', 'SMTP_PASS', 'MAIL_TO', 'YFINANCE_TICKER'], required_vars) if val is None or val == ''])}")
    else:
        # 1. Hämta priset
        amd_price = get_stock_price(TICKER_SYMBOL)
        
        if amd_price is not None:
            # 2. Skicka e-post
            send_stock_email(amd_price, TICKER_SYMBOL)
        else:
            print("Kan inte skicka e-post eftersom aktiekursen inte kunde hämtas.")
