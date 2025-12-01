import os
import platform
import ollama
import json
import re
import sqlite3
import time
from dotenv import load_dotenv
from datetime import datetime
import requests # Import för externa API-anrop

# Ladda miljövariabler
load_dotenv()

# --- INSTÄLLNINGAR ---
# MODELL SOM ÖNSKAS AV ANVÄNDAREN
OLLAMA_MODEL = os.environ.get("OLLAMA_MODEL", "gpt-oss:120b-cloud") 
OLLAMA_HOST = 'http://localhost:11434' 
DB_NAME = 'system_agent.db'
INITIAL_BALANCE = 10000.0 # Max budget i SEK
# Denna variabel behålls men används nu endast för databaspåfyllning. 
# Köpcykeln gör endast ett försök.
MAX_RETRIES_UNIQUE_CPU = 50 

# NYA INSTÄLLNINGAR FÖR EXTERNT API
RAPIDAPI_HOST = os.environ.get("RAPIDAPI_HOST")
RAPIDAPI_KEY = os.environ.get("RAPIDAPI_KEY")

# --- DATABAS HANTERING (Oförändrad) ---
class AgentDB:
    """Klass för att hantera Agentens SQLite-databas."""
    def __init__(self, db_name=DB_NAME):
        self.conn = sqlite3.connect(db_name)
        self.cursor = self.conn.cursor()
        self._initialize_db()

    def _initialize_db(self):
        """Skapar tabeller och sätter initialt saldo samt skapar hardware_details."""
        
        # purchases (Logg)
        self.cursor.execute("""
            CREATE TABLE IF NOT EXISTS purchases (
                id INTEGER PRIMARY KEY,
                item_name TEXT NOT NULL,
                item_type TEXT NOT NULL,
                cost_sek REAL NOT NULL,
                purchase_date TEXT NOT NULL
            )
        """)
        
        # status (Nyckel/Värde)
        self.cursor.execute("""
            CREATE TABLE IF NOT EXISTS status (
                key TEXT PRIMARY KEY,
                value TEXT
            )
        """)
        
        # hardware_details (Specifikationer - cpu_name är PRIMARY KEY för unika poster)
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
        """Sparar hårdvarudetaljer, använder INSERT OR REPLACE för att undvika dubbletter."""
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
    
    def get_all_cpu_names(self) -> set[str]:
        """Hämtar alla CPU-namn från hardware_details som en uppsättning."""
        self.cursor.execute("SELECT cpu_name FROM hardware_details")
        return {row[0] for row in self.cursor.fetchall()}

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
    """Robust funktion för att rensa LLM-svar till en parsbar JSON."""
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

def fetch_cpu_details_from_rapidapi(cpu_name: str) -> dict | None:
    """Hämtar pris och detaljer från en simulerad RapidAPI Product Search."""
    
    if not RAPIDAPI_HOST or not RAPIDAPI_KEY:
        return None
        
    # Använd en generisk URL/endpoint-struktur för ett RapidAPI-anrop
    url = f"https://{RAPIDAPI_HOST}/search?q={cpu_name}"
    headers = {
        "X-RapidAPI-Key": RAPIDAPI_KEY,
        "X-RapidAPI-Host": RAPIDAPI_HOST
    }
    
    print(f"    > Försöker hämta pris via RapidAPI för: {cpu_name}...")
    
    try:
        # Gör det externa API-anropet
        response = requests.get(url, headers=headers, timeout=5)
        response.raise_for_status() 
        
        data = response.json()
        
        # Simulerad parsning av API-svar (Måste anpassas efter det verkliga API:et)
        if data and 'products' in data and len(data['products']) > 0:
            product = data['products'][0]
            
            # Antag att API:et returnerar ett pris i SEK
            price = product.get('price_sek') or product.get('price') 
            
            if price:
                 # Konvertera till float och returnera
                 price_float = float(re.sub(r'[^\d\.]', '', str(price)))
                 print(f"    ✅ Hittade pris via RapidAPI: {price_float} SEK.")
                 return {
                    "price_sek": price_float,
                 }
        
    except requests.exceptions.RequestException as e:
        print(f"    ❌ FEL vid RapidAPI-anrop för {cpu_name}: {e}")
    except ValueError:
        print(f"    ❌ RapidAPI: Hittade pris, men kunde inte konvertera till nummer.")
        
    return None

def fetch_cpu_details_from_llm(client: ollama.Client, cpu_name: str) -> dict | None:
    """Hämtar ALLA detaljer (inklusive pris) från LLM som ett fallback."""
    
    system_prompt_details = (
        "Du är en strikt databas för hårdvaruspecifikationer och priser. "
        "För den angivna processorn, svara ENDAST med ETT JSON-objekt innehållande följande fält: "
        "\"cpu_name\" (str - exakt namn), \"price_sek\" (int - nuvarande pris utan decimaler/komma), \"cores\" (int), "
        "\"threads\" (int), \"base_clock_ghz\" (float), \"boost_clock_ghz\" (float), och \"tdp_watts\" (int)."
        "Priset måste vara ett heltal utan valutasymboler eller kommatecken."
    )
    
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
            return None
            
    except Exception as e:
        print(f"    ❌ FEL vid hämtning av detaljer för {cpu_name} från LLM: {e}")
        return None

def fetch_cpu_details(client: ollama.Client, cpu_name: str) -> dict | None:
    """Huvudfunktion för datahämtning: LLM för specs, RapidAPI för pris override."""
    
    # 1. Hämta alla specifikationer (och pris-fallback) från LLM
    llm_data = fetch_cpu_details_from_llm(client, cpu_name)
    
    if not llm_data:
        print(f"    ❌ Kritisk: Kunde inte hämta basspecifikationer från LLM för {cpu_name}.")
        return None 

    final_data = llm_data.copy()
    
    # 2. Försök med RapidAPI för pris override
    api_data = fetch_cpu_details_from_rapidapi(cpu_name)
    
    if api_data and 'price_sek' in api_data:
        # Åsidosätt LLM:s pris med det externhämtade priset.
        final_data['price_sek'] = api_data['price_sek']
        
    return final_data


# --- DATABAS PÅFYLLNING (BULK - ITERATIV) ---

def populate_database_with_generic_data(db: AgentDB, client: ollama.Client):
    """Fyller databasen med processorer i bulk tills LLM inte kan hitta några nya unika processorer."""
    
    print("\n--- 🧠 Steg X: Databaspåfyllning (Generell Hårdvara) Startad ---")
    
    BATCH_SIZE = 7
    total_new_cpus_logged = 0
    iteration = 0
    
    while True:
        iteration += 1
        new_cpus_in_batch = 0
        
        existing_cpus = db.get_all_cpu_names()
        
        # Begränsa listan av exklusioner som skickas i prompten
        if len(existing_cpus) > MAX_RETRIES_UNIQUE_CPU: 
             exclusion_list_str = f"flera olika AMD och Intel processorer, undvik de {len(existing_cpus)} du redan föreslagit."
        else:
             exclusion_list_str = ", ".join(list(existing_cpus))
        
        
        list_prompt_system = (
            f"Du är en hårdvarukatalog. Lista {BATCH_SIZE} moderna, högpresterande desktop CPUs (både AMD Ryzen och Intel Core) som är relevanta för AI/ML-uppgifter. "
            f"Fokusera på nya och olika modeller. Svara ENDAST med ett JSON array av strängar: [\"CPU Namn 1\", \"CPU Namn 2\", ...]. "
            f"Undvik specifikt dessa modeller: {exclusion_list_str}"
        )
        list_prompt_user = "Lista ett nytt batch av processorer."
        
        print(f"  > Iteration {iteration}: Ber LLM om {BATCH_SIZE} nya CPUer (Kända: {len(existing_cpus)}) ...")
        
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
                print("  ❌ LLM returnerade en ogiltig eller tom lista. Avslutar påfyllning.")
                break

        except Exception as e:
            print(f"  ❌ FEL vid hämtning av CPU-lista i iteration {iteration}: {e}. Avslutar påfyllning.")
            break

        print(f"  ✅ LLM föreslog {len(cpu_list)} CPUer. Börjar validera och hämta detaljer...")
        
        # Steg X.2: Iterera och hämta detaljer för varje CPU
        for cpu_name in cpu_list:
            if cpu_name in existing_cpus:
                continue
                
            details = fetch_cpu_details(client, cpu_name)
            
            if details:
                try:
                    details['price_sek'] = float(details['price_sek'])
                    db.log_hardware_details(details)
                    print(f"    ✅ Loggade NY CPU: {cpu_name} (Pris: {details['price_sek']:.0f} kr).")
                    
                    existing_cpus.add(cpu_name) 
                    total_new_cpus_logged += 1
                    new_cpus_in_batch += 1
                except (ValueError, TypeError, KeyError) as e:
                    print(f"    ⚠️ Kunde inte konvertera/logga data för {cpu_name}: {e}")
            
            time.sleep(0.1) 
        
        if new_cpus_in_batch == 0:
            print(f"  🛑 Iteration {iteration}: LLM föreslog {len(cpu_list)} CPUer, men ingen var unik/ny. Databasen är mättad.")
            break
        
        print(f"  > {new_cpus_in_batch} nya CPUer lades till. Totalt nya: {total_new_cpus_logged}. Fortsätter sökning...")

    print(f"--- Databas påfyllning slutförd. Totalt {total_new_cpus_logged} nya CPUer lades till. ---")

# --- KÄRNFUNKTIONER (KÖPCYKEL - UPPDATERAD) ---

def analyze_and_upgrade_hardware_v17(db: AgentDB):
    """Agentens huvudfunktion: Analysera hårdvara, rekommendera och köp (simulerat)."""
    
    hardware_info = get_current_hardware_info()
    spec_list = "\n".join([f"- {k}: {v}" for k, v in hardware_info.items()])
    current_processor = hardware_info['Processor']
    current_balance = db.get_balance()
    
    print("\n--- 🤖 SystemAgent V17: Hårdvaruanalys & Köp (Ingen DB-koll i steg 2) Startad ---")
    print(f"🧠 Använder LLM: **{OLLAMA_MODEL}** (Lokalt)")
    print(f"💰 Startsaldo (från DB): {current_balance:.2f} kr. Max budget för köp: {INITIAL_BALANCE:.2f} kr.")
    print("  > Upptäckta specifikationer:")
    print(spec_list)

    try:
        client = ollama.Client(host=OLLAMA_HOST)
        
        print("\n--- Steg 1: LLM Utvärderar Hårdvaran... ---")
        print(f"  > (Utvärdering för {current_processor}: Bra, men för svag för krävande AI-arbetslaster.)")
        
        # --- Steg 2: Enkel Rekommendation (Tar FÖRSTA bästa förslaget) ---
        print(f"\n--- Steg 2: SystemAgent Ber om Bättre CPU (JSON) (Ett försök) ---")
        
        recommended_cpu = None
        suggestion_data = None
        
        # LLM anrop för att få en rekommendation
        system_prompt_2 = (
            "Du är världens bästa hårdvaruexpert. Föreslå en *signifikant bättre* modern processor (Intel eller AMD) för krävande AI-arbetslaster. "
            f"Priset måste vara *mindre än eller lika med* {INITIAL_BALANCE:,.0f} kr. "
            "Svara ENDAST med ett JSON-objekt: "
            "{\"recommended_cpu\": \"Namn på processor\", \"expected_price_sek\": Siffra, \"reasoning\": \"Kort motivering\"}. Använd inga kommaseparatorer i siffror."
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
            print(f"❌ LLM-svaret kunde inte tolkas. Avbryter köpcykeln.")
            return 
        
        recommended_cpu = suggestion_data.get('recommended_cpu')

        if not recommended_cpu:
            print("❌ Kunde inte få en giltig rekommendation. Avbryter köpcykeln.")
            return

        print(f"🎉 LLM Föreslår: **{recommended_cpu}**")
        print(f"  > Motivering: {suggestion_data.get('reasoning', 'N/A')}")


        # --- Steg 3: Hämta Detaljerade Specifikationer & Pris (Kombinerat) ---
        print("\n--- Steg 3: Hämta detaljerade specifikationer och pris via LLM/RapidAPI... ---")
        
        detailed_data = None
        is_new_cpu = not db.check_if_cpu_exists(recommended_cpu)

        if is_new_cpu:
            # CPU är ny, hämta detaljerna från LLM och RapidAPI och logga.
            detailed_data = fetch_cpu_details(client, recommended_cpu)
            if detailed_data:
                db.log_hardware_details(detailed_data)
                print(f"✅ Detaljerade specifikationer loggades i databasen för {recommended_cpu}.")
        else:
             # CPU finns, hämta detaljerna från DB för snabbhet/konsistens.
             db.cursor.execute("SELECT cpu_name, cores, threads, base_clock_ghz, boost_clock_ghz, tdp_watts, price_sek FROM hardware_details WHERE cpu_name = ?", (recommended_cpu,))
             row = db.cursor.fetchone()
             columns = ['cpu_name', 'cores', 'threads', 'base_clock_ghz', 'boost_clock_ghz', 'tdp_watts', 'price_sek']
             detailed_data = dict(zip(columns, row))
             print(f"  > Detaljer för {recommended_cpu} hämtades från LOKAL databas.")
            
        
        if not detailed_data or 'price_sek' not in detailed_data:
            print(f"❌ Kunde inte hämta/hitta detaljer för {recommended_cpu}. Avbryter köp.")
            return
            
        try:
            actual_price = float(detailed_data.get('price_sek')) 
        except (ValueError, TypeError):
            print(f"❌ Priset ({detailed_data.get('price_sek')}) var inte ett giltigt nummer. Avbryter köp.")
            return
        
        
        # --- KÖPLOGIK ---
        print(f"  > Pris: **{actual_price:,.2f} kr** (Hämtat från LLM/RapidAPI/DB)")
        
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
        
    print("\n--- SystemAgent V17 Avslutar ---")


if __name__ == "__main__":
    db = None
    try:
        db = AgentDB()
        client = ollama.Client(host=OLLAMA_HOST) 

        # 1. Kör den vanliga köp/analyscykeln
        analyze_and_upgrade_hardware_v17(db)
        
        # 2. Fyll på databasen med generell information (Itererar tills LLM inte kan hitta unika CPUer)
        populate_database_with_generic_data(db, client)
        
        # 3. Simulerad utskrift av köphistorik
        print("\n--- Simulerad köphistorik från DB ---")
        db.cursor.execute("SELECT item_name, cost_sek, purchase_date FROM purchases ORDER BY purchase_date DESC")
        purchases = db.cursor.fetchall()
        if not purchases:
            print("  > Ingen köphistorik finns.")
        for item, cost, date in purchases:
            print(f"  > Köp: {item} | Kostnad: {cost:,.2f} kr | Datum: {date}")

        # 4. Utskrift av ALL lagrad hårdvarudetaljer
        print("\n--- ALLA lagrade hårdvarudetaljer från DB (Sorterad efter pris) ---")
        db.cursor.execute("SELECT cpu_name, cores, threads, base_clock_ghz, boost_clock_ghz, tdp_watts, price_sek FROM hardware_details ORDER BY price_sek ASC")
        all_details = db.cursor.fetchall()
        
        if all_details:
            columns = ['CPU Namn', 'Kärn.', 'Tråd.', 'Bas (GHz)', 'Boost (GHz)', 'TDP (W)', 'Pris (SEK)']
            
            # Utskrift av kolumnrubriker
            print("  " + " | ".join([f"{col:<15}" for col in columns]))
            print("  " + "=" * (len(columns) * 10))
            
            # Utskrift av data
            for row in all_details:
                output = [
                    f"{row[0]:<15}",    # cpu_name
                    f"{row[1]:<5}",     # cores
                    f"{row[2]:<5}",     # threads
                    f"{row[3]:<9.1f}",  # base_clock_ghz
                    f"{row[4]:<11.1f}", # boost_clock_ghz
                    f"{row[5]:<7}",     # tdp_watts
                    f"{row[6]:<10,.0f} kr" # price_sek
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
