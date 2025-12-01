import os
import platform
import ollama
import json
import re
import sqlite3
import time
from dotenv import load_dotenv
from datetime import datetime

# Ladda miljövariabler
load_dotenv()

# --- INSTÄLLNINGAR ---
# MODELL SOM ÖNSKAS AV ANVÄNDAREN
OLLAMA_MODEL = os.environ.get("OLLAMA_MODEL", "gpt-oss:120b-cloud") 
OLLAMA_HOST = 'http://localhost:11434' 
DB_NAME = 'system_agent.db'
INITIAL_BALANCE = 10000.0 # Max budget i SEK

# --- DATABAS HANTERING ---
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
        
    def check_if_cpu_exists(self, cpu_name: str) -> bool:
        """Kontrollerar om en processor redan finns i hårdvarudetaljtabellen."""
        self.cursor.execute("SELECT 1 FROM hardware_details WHERE cpu_name = ?", (cpu_name,))
        return self.cursor.fetchone() is not None

    def close(self):
        self.conn.close()

# --- HJÄLPFUNKTIONER ---

def get_current_hardware_info() -> dict:
    """Samlar in den grundläggande informationen om maskinvaran."""
    processor_name = platform.processor()
    if not processor_name or "unknown" in processor_name.lower():
         processor_name = f"AMD Ryzen 5 3600" # Simulerad bas-CPU
    return {
        "OS": platform.system(),
        "Architecture": platform.machine(),
        "Processor": processor_name,
        "CPU Cores": os.cpu_count(),
        "Python Version": platform.python_version()
    }

def clean_and_parse_json(llm_response: str) -> dict | list | None:
    """Robust funktion för att rensa LLM-svar till en parsbar JSON (stöder dict och list)."""
    llm_response = llm_response.strip()
    
    if llm_response.startswith('```'):
        llm_response = llm_response.strip('```json\n').strip('```')
        
    if llm_response.startswith('{') and llm_response.endswith('}') or \
       llm_response.startswith('[') and llm_response.endswith(']'):
        cleaned_response = llm_response.replace("'", '"')
        cleaned_response = re.sub(r'(\:\s*\d+),(\d+)', r'\1\2', cleaned_response) 
        
        try:
            return json.loads(cleaned_response)
        except json.JSONDecodeError:
            pass
            
    return None

def fetch_cpu_details_from_llm(client: ollama.Client, cpu_name: str) -> dict | None:
    """Hämtar detaljerade specifikationer och pris för en given CPU via LLM."""
    
    system_prompt_details = (
        "Du är en strikt databas för hårdvaruspecifikationer och priser. "
        "För den angivna processorn, svara ENDAST med ETT JSON-objekt innehållande följande fält: "
        "\"cpu_name\" (str - exakt namn), \"price_sek\" (int - nuvarande pris utan decimaler/komma), \"cores\" (int), "
        "\"threads\" (int), \"base_clock_ghz\" (float), \"boost_clock_ghz\" (float), och \"tdp_watts\" (int)."
        "Priset måste vara ett heltal utan valutasymboler eller kommatecken."
    )
    
    print(f"    > Hämtar detaljer för: {cpu_name}...")
    
    try:
        response = client.chat(
            model=OLLAMA_MODEL,
            messages=[
                {'role': 'system', 'content': system_prompt_details},
                {'role': 'user', 'content': cpu_name},
            ]
        )
        
        detailed_data = clean_and_parse_json(response['message']['content'])
        
        if detailed_data and 'price_sek' in detailed_data:
            return detailed_data
        else:
            print(f"    ⚠️ Varning: LLM returnerade inte giltiga detaljer för {cpu_name}.")
            return None
            
    except Exception as e:
        print(f"    ❌ FEL vid hämtning av detaljer för {cpu_name}: {e}")
        return None

# --- DATABAS PÅFYLLNING (BULK) ---

def populate_database_with_generic_data(db: AgentDB, client: ollama.Client):
    """Fyller databasen med en lista av CPUer genom att fråga LLM om varje CPU."""
    
    print("\n--- 🧠 Steg X: Databaspåfyllning (Generell Hårdvara) Startad ---")
    
    # Steg X.1: Hämta en lista med CPU-namn från LLM
    list_prompt_system = (
        "Du är en hårdvarukatalog. Lista 5-7 moderna AMD Ryzen desktop CPUs som är relevanta för AI/ML-uppgifter, och som inte är X3D-modeller. "
        "Svara ENDAST med ett JSON array av strängar: [\"CPU Namn 1\", \"CPU Namn 2\", ...]"
    )
    list_prompt_user = "Lista moderna AMD Ryzen CPUer"
    
    print("  > Ber LLM om en lista med moderna AMD-processorer...")
    
    try:
        response_list = client.chat(
            model=OLLAMA_MODEL,
            messages=[
                {'role': 'system', 'content': list_prompt_system},
                {'role': 'user', 'content': list_prompt_user},
            ]
        )
        
        cpu_list = clean_and_parse_json(response_list['message']['content'])
        
        if not isinstance(cpu_list, list) or not cpu_list:
            print("  ❌ LLM returnerade inte en giltig lista med CPU-namn. Avbryter påfyllning.")
            return

    except Exception as e:
        print(f"  ❌ FEL vid hämtning av CPU-lista: {e}. Avbryter påfyllning.")
        return

    print(f"  ✅ LLM föreslog {len(cpu_list)} CPUer. Börjar hämta detaljer...")
    
    # Steg X.2: Iterera och hämta detaljer för varje CPU
    for cpu_name in cpu_list:
        if db.check_if_cpu_exists(cpu_name):
            print(f"    ℹ️ {cpu_name} finns redan i DB. Hoppar över detaljhämtning.")
            continue
            
        details = fetch_cpu_details_from_llm(client, cpu_name)
        
        if details:
            try:
                details['price_sek'] = float(details['price_sek'])
                db.log_hardware_details(details)
                print(f"    ✅ Loggade {cpu_name} (Pris: {details['price_sek']:.0f} kr).")
            except (ValueError, TypeError, KeyError) as e:
                print(f"    ⚠️ Kunde inte konvertera/logga data för {cpu_name}: {e}")
        
        time.sleep(0.5) 
        
    print("--- Databas påfyllning slutförd. ---")

# --- KÄRNFUNKTIONER (KÖPCYKEL) ---

def analyze_and_upgrade_hardware_v13(db: AgentDB):
    """Agentens huvudfunktion: Analysera hårdvara, rekommendera och köp (simulerat), med check mot befintlig databas."""
    
    hardware_info = get_current_hardware_info()
    spec_list = "\n".join([f"- {k}: {v}" for k, v in hardware_info.items()])
    current_processor = hardware_info['Processor']
    current_balance = db.get_balance()
    
    print("\n--- 🤖 SystemAgent V13: Hårdvaruanalys & Köp (Ollama) Startad ---")
    print(f"🧠 Använder LLM: **{OLLAMA_MODEL}** (Lokalt)")
    print(f"💰 Startsaldo (från DB): {current_balance:.2f} kr. Max budget för köp: {INITIAL_BALANCE:.2f} kr.")
    print("  > Upptäckta specifikationer:")
    print(spec_list)

    try:
        client = ollama.Client(host=OLLAMA_HOST)
        
        # --- Steg 1: Utvärdering (Simulerad) ---
        print("\n--- Steg 1: LLM Utvärderar Hårdvaran... ---")
        print(f"  > (Utvärdering för {current_processor}: Bra, men för svag för krävande AI-arbetslaster.)")
        
        # --- Steg 2: Iterativ Rekommendation med Dubbelkoll mot DB ---
        print("\n--- Steg 2: SystemAgent Ber om Bättre CPU (JSON) & Dubbelkollar DB... ---")
        
        MAX_RETRIES = 3
        recommended_cpu = None
        suggestion_data = None
        
        for attempt in range(MAX_RETRIES):
            
            # Grundprompt
            system_prompt_2 = (
                "Du är världens bästa hårdvaruexpert. Föreslå en *signifikant bättre* modern processor (Intel eller AMD) för krävande AI-arbetslaster. "
                f"Priset måste vara *mindre än eller lika med* {INITIAL_BALANCE:,.0f} kr. "
                "Svara ENDAST med ett JSON-objekt: "
                "{\"recommended_cpu\": \"Namn på processor\", \"expected_price_sek\": Siffra, \"reasoning\": \"Kort motivering\"}. Använd inga kommaseparatorer i siffror."
            )
            user_prompt_2 = f"Föreslå en uppgradering till min nuvarande processor: {current_processor}"
            
            # Modifiera prompten om det är ett retry
            if attempt > 0 and recommended_cpu:
                print(f"  > Föregående rekommendation ({recommended_cpu}) finns redan i DB. Försök {attempt+1}/{MAX_RETRIES}: Begär ALTERNATIVT förslag...")
                user_prompt_2 = f"Föreslå en ANNAN uppgradering än '{recommended_cpu}' till min nuvarande processor: {current_processor}. Hitta en alternativ, stark CPU för AI/ML under {INITIAL_BALANCE:,.0f} kr."

            response_2 = client.chat(
                model=OLLAMA_MODEL,
                messages=[
                    {'role': 'system', 'content': system_prompt_2},
                    {'role': 'user', 'content': user_prompt_2},
                ]
            )
            
            suggestion_data = clean_and_parse_json(response_2['message']['content'])
            
            if not suggestion_data or 'recommended_cpu' not in suggestion_data:
                print(f"❌ LLM-svaret kunde inte tolkas i försök {attempt+1}.")
                continue 
            
            recommended_cpu = suggestion_data.get('recommended_cpu')
            
            if not db.check_if_cpu_exists(recommended_cpu):
                # Ny CPU hittad! Gå vidare till köp.
                break 
                
            if attempt == MAX_RETRIES - 1:
                print("❌ Max antal försök nått. Alla föreslagna CPUer finns redan i DB. Avbryter köpcykeln.")
                return # Avsluta om max försök nåtts och ingen ny CPU hittats

        # Om vi bröt loopen, har vi en unik rekommendation
        if not recommended_cpu:
            print("❌ Kunde inte få en giltig rekommendation. Avbryter köpcykeln.")
            return

        print(f"🎉 LLM Föreslår: **{recommended_cpu}**")
        print(f"  > Motivering: {suggestion_data.get('reasoning', 'N/A')}")


        # --- Steg 3: Hämta Detaljerade Specifikationer & Pris ---
        print("\n--- Steg 3: Hämta detaljerade specifikationer och pris via LLM (JSON)... ---")
        
        # Kontrollera om detaljerna finns lokalt ELLER hämta nytt
        if db.check_if_cpu_exists(recommended_cpu):
             # Om CPU:n fanns sedan tidigare, hämta detaljerna från DB för köp.
             db.cursor.execute("SELECT cpu_name, cores, threads, base_clock_ghz, boost_clock_ghz, tdp_watts, price_sek FROM hardware_details WHERE cpu_name = ?", (recommended_cpu,))
             row = db.cursor.fetchone()
             columns = ['cpu_name', 'cores', 'threads', 'base_clock_ghz', 'boost_clock_ghz', 'tdp_watts', 'price_sek']
             detailed_data = dict(zip(columns, row))
             print(f"  > Detaljer för {recommended_cpu} hämtades från LOKAL databas.")
        else:
            # CPU var ny, hämta detaljerna från LLM (kommer att sparas i DB i nästa steg)
            detailed_data = fetch_cpu_details_from_llm(client, recommended_cpu)
            
        
        if not detailed_data or 'price_sek' not in detailed_data:
            print(f"❌ Kunde inte hämta/hitta detaljer för {recommended_cpu}. Avbryter köp.")
            return
            
        try:
            actual_price = float(detailed_data.get('price_sek')) 
            
            # --- DATABAS LAGRING AV DETALJER (Sker bara om den var ny) ---
            if not db.check_if_cpu_exists(recommended_cpu):
                db.log_hardware_details(detailed_data)
                print(f"✅ Detaljerade specifikationer loggades i databasen för {recommended_cpu}.")
            # -----------------------------------
            
        except (ValueError, TypeError):
            print(f"❌ Priset ({detailed_data.get('price_sek')}) var inte ett giltigt nummer. Avbryter köp.")
            return
        
        
        # --- KÖPLOGIK ---
        print(f"  > Pris: **{actual_price:,.2f} kr** (Hämtat från LLM/DB)")
        
        if actual_price <= current_balance and actual_price <= INITIAL_BALANCE:
            
            new_balance = current_balance - actual_price
            db.update_balance(new_balance)
            db.log_purchase(recommended_cpu, actual_price)
            
            print(f"✅ KÖP GENOMFÖRT! Simulerat köp av {recommended_cpu} för {actual_price:,.2f} kr. Loggat i DB.")
            print(f"💰 NYTT SALDO: **{new_balance:,.2f} kr**.")
            
        else:
            if actual_price > INITIAL_BALANCE:
                print(f"⚠️ KÖP AVSLOGS: Priset ({actual_price:,.2f} kr) överstiger budgetgränsen ({INITIAL_BALANCE:,.2f} kr).")
            else:
                 print(f"⚠️ KÖP AVSLOGS: Priset ({actual_price:,.2f} kr) överstiger nuvarande plånbokssaldo ({current_balance:,.2f} kr).")

    except Exception as e:
        print(f"❌ GENERISKT FEL: Kunde inte slutföra uppgraderingscykeln: {e}")
        
    print("\n--- SystemAgent V13 Avslutar ---")


if __name__ == "__main__":
    db = None
    try:
        db = AgentDB()
        client = ollama.Client(host=OLLAMA_HOST) 

        # 1. Kör den vanliga köp/analyscykeln
        analyze_and_upgrade_hardware_v13(db)
        
        # 2. Fyll på databasen med generell information (Hoppar över befintliga)
        populate_database_with_generic_data(db, client)
        
        # 3. Simulerad utskrift av köphistorik
        print("\n--- Simulerad köphistorik från DB ---")
        db.cursor.execute("SELECT item_name, cost_sek, purchase_date FROM purchases ORDER BY purchase_date DESC")
        purchases = db.cursor.fetchall()
        if not purchases:
            print("  > Ingen köphistorik finns.")
        for item, cost, date in purchases:
            print(f"  > Köp: {item} | Kostnad: {cost:.2f} kr | Datum: {date}")

        # 4. Utskrift av ALL lagrad hårdvarudetaljer
        print("\n--- ALLA lagrade hårdvarudetaljer från DB (Sorterad efter pris) ---")
        db.cursor.execute("SELECT cpu_name, cores, threads, base_clock_ghz, boost_clock_ghz, tdp_watts, price_sek FROM hardware_details ORDER BY price_sek ASC")
        all_details = db.cursor.fetchall()
        
        if all_details:
            columns = ['CPU Namn', 'Kärn.', 'Tråd.', 'Bas (GHz)', 'Boost (GHz)', 'TDP (W)', 'Pris (SEK)']
            
            # Utskrift av kolumnrubriker
            print("  " + " | ".join([f"{col:<15}" for col in columns]))
            print("  " + "=" * (len(columns) * 18))
            
            # Utskrift av data
            for row in all_details:
                output = [
                    f"{row[0]:<15}",  # cpu_name
                    f"{row[1]:<5}",   # cores
                    f"{row[2]:<5}",   # threads
                    f"{row[3]:<9.1f}",  # base_clock_ghz
                    f"{row[4]:<11.1f}", # boost_clock_ghz
                    f"{row[5]:<7}",   # tdp_watts
                    f"{row[6]:<10.0f} kr" # price_sek
                ]
                print("  " + " | ".join(output))
        else:
            print("  > Inga hårdvarudetaljer loggade.")

        print("-----------------------------------")
        
    except Exception as e:
        print(f"Ett kritiskt fel uppstod vid databas- eller agentkörning: {e}")
        
    finally:
        if db:
            db.close()
            print(f"Databasanslutning till {DB_NAME} stängd.")
