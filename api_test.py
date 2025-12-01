import os
import platform
import ollama
import json
import re
import sqlite3
from dotenv import load_dotenv
from datetime import datetime

# Ladda miljövariabler
load_dotenv()

# --- INSTÄLLNINGAR ---
# NY MODELL SOM ÖNSKAS AV ANVÄNDAREN
OLLAMA_MODEL = os.environ.get("OLLAMA_MODEL", "gpt-oss:120b-cloud") 
OLLAMA_HOST = 'http://localhost:11434' 
DB_NAME = 'system_agent.db'
INITIAL_BALANCE = 10000.0 # Max budget i SEK

# --- DATABAS HANTERING (OFÖRÄNDRAD FRÅN V8) ---
class AgentDB:
    """Klass för att hantera Agentens SQLite-databas."""
    def __init__(self, db_name=DB_NAME):
        self.conn = sqlite3.connect(db_name)
        self.cursor = self.conn.cursor()
        self._initialize_db()

    def _initialize_db(self):
        """Skapar tabeller och sätter initialt saldo samt skapar hardware_details."""
        
        self.cursor.execute("""
            CREATE TABLE IF NOT EXISTS purchases (
                id INTEGER PRIMARY KEY,
                item_name TEXT NOT NULL,
                item_type TEXT NOT NULL,
                cost_sek REAL NOT NULL,
                purchase_date TEXT NOT NULL
            )
        """)
        
        self.cursor.execute("""
            CREATE TABLE IF NOT EXISTS status (
                key TEXT PRIMARY KEY,
                value TEXT
            )
        """)
        
        self.cursor.execute("""
            CREATE TABLE IF NOT EXISTS hardware_details (
                cpu_name TEXT PRIMARY KEY,
                cores INTEGER,
                threads INTEGER,
                base_clock_ghz REAL,
                boost_clock_ghz REAL,
                tdp_watts INTEGER,
                price_sek REAL NOT NULL,
                date_fetched TEXT NOT NULL
            )
        """)
        
        self.cursor.execute("SELECT value FROM status WHERE key = 'wallet_balance'")
        if self.cursor.fetchone() is None:
            self.cursor.execute("INSERT INTO status (key, value) VALUES (?, ?)", ('wallet_balance', str(INITIAL_BALANCE)))
            self.conn.commit()
            print(f"✅ Databas skapad. Initialt saldo satt till {INITIAL_BALANCE:.2f} kr.")
        else:
             print("✅ Databas ansluten. Saldo funnet.")

    def get_balance(self) -> float:
        self.cursor.execute("SELECT value FROM status WHERE key = 'wallet_balance'")
        result = self.cursor.fetchone()
        return float(result[0]) if result else 0.0

    def update_balance(self, new_balance: float):
        self.cursor.execute("UPDATE status SET value = ? WHERE key = 'wallet_balance'", (str(new_balance),))
        self.conn.commit()

    def log_purchase(self, item_name: str, cost: float):
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        self.cursor.execute(
            "INSERT INTO purchases (item_name, item_type, cost_sek, purchase_date) VALUES (?, ?, ?, ?)",
            (item_name, 'CPU', cost, now)
        )
        self.conn.commit()
        
    def log_hardware_details(self, details: dict):
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        self.cursor.execute(
            """INSERT OR REPLACE INTO hardware_details 
            (cpu_name, cores, threads, base_clock_ghz, boost_clock_ghz, tdp_watts, price_sek, date_fetched) 
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (details['cpu_name'], details['cores'], details['threads'], details['base_clock_ghz'], 
             details['boost_clock_ghz'], details['tdp_watts'], details['price_sek'], now)
        )
        self.conn.commit()
        
    def close(self):
        self.conn.close()

# --- HJÄLPFUNKTIONER (OFÖRÄNDRAD) ---

def get_current_hardware_info() -> dict:
    """Samlar in den grundläggande informationen om maskinvaran."""
    processor_name = platform.processor()
    if not processor_name or "unknown" in processor_name.lower():
         # Använder en generisk CPU för simuleringens skull
         processor_name = f"AMD Ryzen 5 3600" 
    return {
        "OS": platform.system(),
        "Architecture": platform.machine(),
        "Processor": processor_name,
        "CPU Cores": os.cpu_count(),
        "Python Version": platform.python_version()
    }

def clean_and_parse_json(llm_response: str) -> dict | None:
    """Robust funktion för att rensa LLM-svar till en parsbar JSON."""
    llm_response = llm_response.strip()
    
    # Rensa kodblock (```json ... ```)
    if llm_response.startswith('```'):
        llm_response = llm_response.strip('```json\n').strip('```')
        
    # Rensa och korrigera för Ollama-inconsistenser
    if llm_response.startswith('{') and llm_response.endswith('}'):
        # 1. Ersätt enkla citattecken med dubbla
        cleaned_response = llm_response.replace("'", '"')
        # 2. Ta bort kommaseparatorer i numeriska värden (t.ex. 1,000 -> 1000)
        cleaned_response = re.sub(r'(\:\s*\d+),(\d+)', r'\1\2', cleaned_response)
        
        try:
            return json.loads(cleaned_response)
        except json.JSONDecodeError:
            pass
            
    # Sista försök: returnera rådata om den är tom
    if not llm_response:
        return None
        
    # Om JSON-parsningen misslyckas, returnera None
    return None

# --- KÄRNFUNKTIONER ---

def analyze_and_upgrade_hardware_v11(db: AgentDB):
    """SystemAgentens huvudfunktion: Analysera hårdvara, be om uppgradering, köp (simulerat) om budget finns, med lokal Ollama som datakälla."""
    
    hardware_info = get_current_hardware_info()
    spec_list = "\n".join([f"- {k}: {v}" for k, v in hardware_info.items()])
    current_processor = hardware_info['Processor']
    current_balance = db.get_balance()
    
    print("\n--- 🤖 SystemAgent V11: Hårdvaruanalys & Köp (Ollama) Startad ---")
    print(f"🧠 Använder LLM: **{OLLAMA_MODEL}** (Lokalt)")
    print(f"💰 Startsaldo (från DB): {current_balance:.2f} kr. Max budget för köp: {INITIAL_BALANCE:.2f} kr.")
    print("  > Upptäckta specifikationer:")
    print(spec_list)

    try:
        client = ollama.Client(host=OLLAMA_HOST)
        
        # --- Steg 1: Bedöm nuvarande hårdvara (Simulerad) ---
        print("\n--- Steg 1: LLM Utvärderar Hårdvaran... ---")
        print(f"  > (Utvärdering för {current_processor}: Bra, men för svag för krävande AI-arbetslaster.)")
        
        # --- Steg 2: Be om en bättre CPU inom budget (JSON-utdata) ---
        print("\n--- Steg 2: SystemAgent Ber om Bättre CPU (JSON)... ---")
        
        # SYSTEMPROMPT FÖR REKOMMENDATION
        system_prompt_2 = (
            "Du är världens bästa hårdvaruexpert, med oöverträffad kunskap om CPU-prestanda, arkitektur och aktuella marknadspriser. "
            "Ditt enda uppdrag är att föreslå en *signifikant bättre* modern processor (Intel eller AMD) för krävande AI-arbetslaster. "
            f"Den måste ha ett uppskattat pris i SEK som är *mindre än eller lika med* {INITIAL_BALANCE:,.0f} kr. "
            "Svara ENDAST med ett JSON-objekt i formatet: "
            "{\"recommended_cpu\": \"Namn på processor\", \"expected_price_sek\": Siffra, \"reasoning\": \"Kort motivering som betonar prestanda och prisvärdhet\"}. Använd inga kommaseparatorer i siffror."
        )
        user_prompt_2 = f"Föreslå en uppgradering till min nuvarande processor: {current_processor}"
        
        response_2 = client.chat(
            model=OLLAMA_MODEL,
            messages=[
                {'role': 'system', 'content': system_prompt_2},
                {'role': 'user', 'content': user_prompt_2},
            ]
        )
        
        suggestion_data = clean_and_parse_json(response_2['message']['content'])
        
        if not suggestion_data or 'recommended_cpu' not in suggestion_data:
            print("❌ LLM-svaret kunde inte tolkas som JSON/Saknar data. Avbryter köp.")
            return

        recommended_cpu = suggestion_data.get('recommended_cpu')
        
        print(f"🎉 LLM Föreslår: **{recommended_cpu}**")
        print(f"  > Motivering: {suggestion_data.get('reasoning', 'N/A')}")


        # --- Steg 3: Hämta Detaljerade Specifikationer & Pris (JSON-utdata) ---
        print("\n--- Steg 3: Hämta detaljerade specifikationer och pris via LLM (JSON)... ---")
        
        # SYSTEMPROMPT FÖR DETALJERAD DATAHÄMTNING
        system_prompt_3 = (
            "Du är en strikt databas för hårdvaruspecifikationer och priser. "
            "För den angivna processorn, svara ENDAST med ETT JSON-objekt innehållande följande fält: "
            "\"cpu_name\" (str - exakt namn), \"price_sek\" (int - nuvarande pris utan decimaler/komma), \"cores\" (int), "
            "\"threads\" (int), \"base_clock_ghz\" (float), \"boost_clock_ghz\" (float), och \"tdp_watts\" (int)."
            "Priset måste vara ett heltal utan valutasymboler eller kommatecken."
        )
        # USER PROMPT är bara CPU-namnet, som önskat
        user_prompt_3 = recommended_cpu
        
        response_3 = client.chat(
            model=OLLAMA_MODEL,
            messages=[
                {'role': 'system', 'content': system_prompt_3},
                {'role': 'user', 'content': user_prompt_3},
            ]
        )
        
        detailed_data = clean_and_parse_json(response_3['message']['content'])
        
        if not detailed_data or 'price_sek' not in detailed_data:
            print("❌ LLM-svaret (Detaljer) kunde inte tolkas/Saknar pris. Avbryter köp.")
            return
            
        try:
            actual_price = float(detailed_data.get('price_sek')) # Använd priset från detaljdatan
            
            # --- DATABAS LAGRING AV DETALJER ---
            # Säkerställer att alla nödvändiga fält finns innan loggning
            required_fields = ['cpu_name', 'price_sek', 'cores', 'threads', 'base_clock_ghz', 'boost_clock_ghz', 'tdp_watts']
            if all(field in detailed_data for field in required_fields):
                db.log_hardware_details(detailed_data)
                print(f"✅ Detaljerade specifikationer loggades i databasen för {recommended_cpu}.")
            else:
                 print("⚠️ Kunde inte logga detaljer: Saknade fält i LLM:s svar.")
            # -----------------------------------
            
        except (ValueError, TypeError):
            print(f"❌ Priset ({detailed_data.get('price_sek')}) var inte ett giltigt nummer. Avbryter köp.")
            return
        
        
        # --- KÖPLOGIK ---
        print(f"  > Pris: **{actual_price:,.2f} kr** (Hämtat från LLM-detaljer)")
        
        if actual_price <= current_balance and actual_price <= INITIAL_BALANCE:
            
            # --- DATABAS TRANSAKTION ---
            new_balance = current_balance - actual_price
            db.update_balance(new_balance)
            db.log_purchase(recommended_cpu, actual_price)
            # ---------------------------
            
            print(f"✅ KÖP GENOMFÖRT! Simulerat köp av {recommended_cpu} för {actual_price:,.2f} kr. Loggat i DB.")
            print(f"💰 NYTT SALDO: **{new_balance:,.2f} kr**.")
            
        else:
            if actual_price > INITIAL_BALANCE:
                print(f"⚠️ KÖP AVSLOGS: Priset ({actual_price:,.2f} kr) överstiger budgetgränsen ({INITIAL_BALANCE:,.2f} kr).")
            else:
                 print(f"⚠️ KÖP AVSLOGS: Priset ({actual_price:,.2f} kr) överstiger nuvarande plånbokssaldo ({current_balance:,.2f} kr).")

    except Exception as e:
        print(f"❌ GENERISKT FEL: Kunde inte slutföra uppgraderingscykeln: {e}")
        
    print("\n--- SystemAgent V11 Avslutar ---")


if __name__ == "__main__":
    db = None
    try:
        db = AgentDB()
        analyze_and_upgrade_hardware_v11(db)
        
        # Simulerad utskrift av köphistorik
        print("\n--- Simulerad köphistorik från DB ---")
        db.cursor.execute("SELECT item_name, cost_sek, purchase_date FROM purchases ORDER BY purchase_date DESC")
        purchases = db.cursor.fetchall()
        if not purchases:
            print("  > Ingen köphistorik finns.")
        for item, cost, date in purchases:
            print(f"  > Köp: {item} | Kostnad: {cost:.2f} kr | Datum: {date}")

        # Utskrift av lagrade hårdvarudetaljer
        print("\n--- Senast loggade hårdvarudetaljer från DB ---")
        db.cursor.execute("SELECT * FROM hardware_details ORDER BY date_fetched DESC LIMIT 1")
        details = db.cursor.fetchone()
        if details:
            columns = [desc[0] for desc in db.cursor.description]
            print("  > Lagrad data:")
            for col, val in zip(columns, details):
                print(f"    - {col}: {val}")
        else:
            print("  > Inga hårdvarudetaljer loggade.")

        print("-----------------------------------")
        
    except Exception as e:
        print(f"Ett kritiskt fel uppstod vid databas- eller agentkörning: {e}")
        
    finally:
        if db:
            db.close()
            print(f"Databasanslutning till {DB_NAME} stängd.")
