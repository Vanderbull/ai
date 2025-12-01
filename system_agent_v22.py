import os
import platform
import ollama
import json
import re
import sqlite3
import time
from dotenv import load_dotenv
from datetime import datetime
import requests 
import subprocess 

# Ladda miljövariabler
load_dotenv()

# --- INSTÄLLNINGAR ---
OLLAMA_MODEL = os.environ.get("OLLAMA_MODEL", "gpt-oss:120b-cloud") 
OLLAMA_HOST = 'http://localhost:11434' 
DB_NAME = 'system_agent.db'
INITIAL_BALANCE = 10000.0 
MAX_RETRIES_UNIQUE_CPU = 50 

# NYA INSTÄLLNINGAR FÖR EXTERNT API
RAPIDAPI_HOST = os.environ.get("RAPIDAPI_HOST")
RAPIDAPI_KEY = os.environ.get("RAPIDAPI_KEY")

# NY LISTA MED KOMPONENTTYPER ATT SÖKA EFTER
COMPONENT_TYPES = ["CPU", "GPU", "RAM", "SSD", "Motherboard"]


# --- DATABAS HANTERING (OFÖRÄNDRAD) ---
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
                component_name TEXT PRIMARY KEY,
                component_type TEXT NOT NULL,
                price_sek REAL NOT NULL,
                date_fetched TEXT NOT NULL,
                details_json TEXT 
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

    def log_purchase(self, item_name: str, item_type: str, cost: float):
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        self.cursor.execute(
            "INSERT INTO purchases (item_name, item_type, cost_sek, purchase_date) VALUES (?, ?, ?, ?)",
            (item_name, item_type, cost, now)
        )
        self.conn.commit()
        
    def log_hardware_details(self, details: dict):
        """Sparar hårdvarudetaljer, använder INSERT OR REPLACE för att undvika dubbletter."""
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        
        component_name = details['component_name']
        component_type = details['component_type']
        price_sek = details['price_sek']
        
        specific_details = {k: v for k, v in details.items() if k not in ['component_name', 'component_type', 'price_sek']}
        details_json = json.dumps(specific_details)
        
        self.cursor.execute(
            """INSERT OR REPLACE INTO hardware_details 
            (component_name, component_type, price_sek, date_fetched, details_json) 
            VALUES (?, ?, ?, ?, ?)""",
            (component_name, component_type, price_sek, now, details_json)
        )
        self.conn.commit()
        
    def check_if_component_exists(self, component_name: str) -> bool:
        """Kontrollerar om en komponent redan finns i hårdvarudetaljtabellen."""
        self.cursor.execute("SELECT 1 FROM hardware_details WHERE component_name = ?", (component_name,))
        return self.cursor.fetchone() is not None
    
    def get_all_component_names(self) -> set[str]:
        """Hämtar alla komponentnamn från hardware_details som en uppsättning."""
        self.cursor.execute("SELECT component_name FROM hardware_details")
        return {row[0] for row in self.cursor.fetchall()}

    def close(self):
        self.conn.close()

# --- HJÄLPFUNKTIONER (OFÖRÄNDRAD) ---

def get_current_hardware_info() -> dict:
    """Samlar in detaljerad information om maskinvaran, föredrar lscpu/dmidecode på Linux."""
    info = {}
    
    # Grundläggande systeminfo
    info["OS"] = platform.system()
    info["Architecture"] = platform.machine()
    info["Python Version"] = platform.python_version()
    
    # Simulerad processor för fallback/initialt värde
    simulated_processor = "AMD Ryzen 5 3600 (Simulated)"
    info["Processor"] = simulated_processor
    info["CPU_Cores"] = os.cpu_count()
    info["Hardware_Info_Source"] = f"Standard Library ({info['OS']})"


    # Försök att få detaljerad CPU-information via lscpu på Linux
    if info["OS"] == "Linux":
        try:
            # Använd lscpu för detaljerad CPU-info
            result = subprocess.run(['lscpu'], capture_output=True, text=True, check=True, timeout=5)
            output = result.stdout.strip()
            
            # Parsar lscpu output
            for line in output.split('\n'):
                if ':' in line:
                    key, value = line.split(':', 1)
                    # För att få renare nycklar i dictionaryt
                    key = key.strip().replace(' ', '_').replace('(', '').replace(')', '')
                    info[f"CPU_{key}"] = value.strip()
            
            # Sätter primär processor och kärnor baserat på lscpu output
            info["Processor"] = info.get("CPU_Model_name", info.get("CPU_Architecture", simulated_processor))
            info["CPU_Cores"] = info.get("CPU_CPU(s)", info["CPU_Cores"])
            info["Hardware_Info_Source"] = "Linux (lscpu)"

        except FileNotFoundError:
            info["Hardware_Info_Source"] = "Fallback (lscpu not found)"
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as e:
            info["Hardware_Info_Source"] = f"Fallback (lscpu error: {type(e).__name__})"
        
    return info

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

def fetch_cpu_details_from_rapidapi(component_name: str) -> dict | None:
    """Hämtar pris och detaljer från en simulerad RapidAPI Product Search."""
    
    if not RAPIDAPI_HOST or not RAPIDAPI_KEY:
        return None
        
    url = f"https://{RAPIDAPI_HOST}/search?q={component_name}"
    headers = {
        "X-RapidAPI-Key": RAPIDAPI_KEY,
        "X-RapidAPI-Host": RAPIDAPI_HOST
    }
    
    print(f"    > Försöker hämta pris via RapidAPI för: {component_name}...")
    
    try:
        response = requests.get(url, headers=headers, timeout=5)
        response.raise_for_status() 
        
        data = response.json()
        
        if data and 'products' in data and len(data['products']) > 0:
            product = data['products'][0]
            price = product.get('price_sek') or product.get('price') 
            
            if price:
                 price_float = float(re.sub(r'[^\d\.]', '', str(price)))
                 print(f"    ✅ Hittade pris via RapidAPI: {price_float} SEK.")
                 return {
                    "price_sek": price_float,
                 }
        
    except requests.exceptions.RequestException as e:
        print(f"    ❌ FEL vid RapidAPI-anrop för {component_name}: {e}")
    except ValueError:
        print(f"    ❌ RapidAPI: Hittade pris, men kunde inte konvertera till nummer.")
        
    return None

def fetch_component_specs_from_llm(client: ollama.Client, component_name: str, component_type: str) -> dict | None:
    """Hämtar alla detaljer (inklusive pris) och typ från LLM."""
    
    if component_type == "CPU":
        spec_example = " (t.ex. \"cores\", \"threads\", \"base_clock_ghz\")"
    elif component_type == "GPU":
        spec_example = " (t.ex. \"VRAM_GB\", \"Bus_Width\", \"Ray_Tracing_Support\")"
    elif component_type == "RAM":
        spec_example = " (t.ex. \"capacity_gb\", \"speed_mhz\", \"type\")"
    elif component_type == "SSD":
        spec_example = " (t.ex. \"capacity_gb\", \"interface\", \"read_speed_mbps\")"
    else:
        spec_example = ""

    system_prompt_details = (
        f"Du är en strikt databas för hårdvaruspecifikationer. För {component_name} ({component_type}), svara ENDAST med ETT JSON-objekt innehållande: "
        "\"component_name\" (str - exakt namn), \"component_type\" (str - exakt typ), \"price_sek\" (int - nuvarande pris utan kommatecken/valuta), och de viktigaste tekniska specifikationerna som nyckel/värde-par"
        f"{spec_example}. Priset måste vara ett heltal."
    )
    
    print(f"    > Hämtar detaljer från LLM för: {component_name} ({component_type})...")
    
    try:
        response = client.chat(
            model=OLLAMA_MODEL,
            messages=[
                {'role': 'system', 'content': system_prompt_details},
                {'role': 'user', 'content': component_name},
            ]
        )
        
        detailed_data = clean_and_parse_json(response['message']['content'])
        
        if detailed_data and 'price_sek' in detailed_data and 'component_type' in detailed_data:
            return detailed_data
        else:
            print(f"    ⚠️ Varning: LLM returnerade inte giltiga detaljer (saknar pris/typ) för {component_name}.")
            return None
            
    except Exception as e:
        print(f"    ❌ FEL vid hämtning av detaljer för {component_name} från LLM: {e}")
        return None

def fetch_component_details(client: ollama.Client, component_name: str, component_type: str) -> dict | None:
    """Huvudfunktion för datahämtning: LLM för specs, RapidAPI för pris override."""
    
    # 1. Hämta alla specifikationer (och pris-fallback) från LLM
    llm_data = fetch_component_specs_from_llm(client, component_name, component_type)
    
    if not llm_data:
        return None 

    final_data = llm_data.copy()
    
    # 2. Försök med RapidAPI för pris override
    api_data = fetch_cpu_details_from_rapidapi(component_name)
    
    if api_data and 'price_sek' in api_data:
        # Åsidosätt LLM:s pris med det externhämtade priset.
        final_data['price_sek'] = api_data['price_sek']
        print(f"    ➡️ Pris uppdaterat till {final_data['price_sek']:.0f} SEK via RapidAPI.")
        
    return final_data


# --- DATABAS PÅFYLLNING (BULK - ITERATIV & GENERALISERAD) ---

def populate_database_with_generic_data(db: AgentDB, client: ollama.Client):
    """Fyller databasen med komponenter i bulk tills LLM inte kan hitta några nya unika komponenter."""
    
    print("\n--- 🧠 Steg X: Databaspåfyllning (Generell Hårdvara) Startad ---")
    
    BATCH_SIZE = 5
    total_new_components_logged = 0
    
    for component_type in COMPONENT_TYPES:
        
        print(f"\n--- Söker efter nya: {component_type} ---")
        iteration = 0
        
        while True:
            iteration += 1
            new_components_in_batch = 0
            
            existing_components = db.get_all_component_names()
            
            if len(existing_components) > MAX_RETRIES_UNIQUE_CPU: 
                 exclusion_list_str = f"flera olika modeller, undvik de {len(existing_components)} du redan föreslagit."
            else:
                 exclusion_list_str = ", ".join(list(existing_components))
            
            
            list_prompt_system = (
                f"Du är en hårdvarukatalog. Lista {BATCH_SIZE} moderna, högpresterande {component_type} modeller. "
                f"Fokusera på nya och olika modeller. Svara ENDAST med ett JSON array av strängar: [\"Modell Namn 1\", \"Modell Namn 1\", ...]. "
                f"Undvik specifikt dessa modeller: {exclusion_list_str}"
            )
            list_prompt_user = f"Lista ett nytt batch av {component_type}."
            
            print(f"  > Iteration {iteration}: Ber LLM om {BATCH_SIZE} nya {component_type} (Kända: {len(existing_components)}) ...")
            
            try:
                response_list = client.chat(
                    model=OLLAMA_MODEL,
                    messages=[
                        {'role': 'system', 'content': list_prompt_system},
                        {'role': 'user', 'content': list_prompt_user},
                    ]
                )
                
                component_list = clean_and_parse_json(response_list['message']['content'])
                
                if not isinstance(component_list, list) or not component_list:
                    print(f"  ❌ LLM returnerade en ogiltig eller tom lista för {component_type}. Går vidare.")
                    break

            except Exception as e:
                print(f"  ❌ FEL vid hämtning av {component_type}-lista i iteration {iteration}: {e}. Går vidare.")
                break

            print(f"  ✅ LLM föreslog {len(component_list)} {component_type}. Börjar validera och hämta detaljer...")
            
            for component_name in component_list:
                if component_name in existing_components:
                    continue
                    
                details = fetch_component_details(client, component_name, component_type)
                
                if details:
                    try:
                        details['price_sek'] = float(details['price_sek'])
                        db.log_hardware_details(details)
                        print(f"    ✅ Loggade NY KOMPONENT: {component_name} ({component_type}) (Pris: {details['price_sek']:.0f} kr).")
                        
                        existing_components.add(component_name) 
                        total_new_components_logged += 1
                        new_components_in_batch += 1
                    except (ValueError, TypeError, KeyError) as e:
                        print(f"    ⚠️ Kunde inte konvertera/logga data för {component_name}: {e}")
                
                time.sleep(0.1) 
            
            if new_components_in_batch == 0:
                print(f"  🛑 Iteration {iteration}: Inga unika {component_type} lades till. Databasen är mättad för denna typ.")
                break
            
            print(f"  > {new_components_in_batch} nya {component_type} lades till. Totalt nya: {total_new_components_logged}. Fortsätter sökning...")

    print(f"\n--- Databas påfyllning slutförd. Totalt {total_new_components_logged} nya komponenter lades till. ---")


# --- KÄRNFUNKTIONER (KÖPCYKEL - UPPDATERAD V22) ---

def analyze_and_upgrade_hardware_v22(db: AgentDB):
    """Agentens huvudfunktion: Analysera hårdvara och rekommendera den bästa *enskilda* uppgraderingen."""
    
    # Hämta detaljerad hårdvaruinformation
    full_hardware_info = get_current_hardware_info()
    
    # Skapa en formaterad lista av alla specs för utskrift och LLM-prompt
    spec_list = "\n".join([f"- {k}: {v}" for k, v in sorted(full_hardware_info.items())])
    
    current_processor = full_hardware_info.get('Processor', 'Unknown CPU')
    current_balance = db.get_balance()
    
    print("\n--- 🤖 SystemAgent V22: Hårdvaruanalys & Prioriterad Uppgradering Startad ---")
    print(f"🧠 Använder LLM: **{OLLAMA_MODEL}** (Lokalt)")
    print(f"💰 Startsaldo (från DB): {current_balance:.2f} kr. Max budget för köp: {INITIAL_BALANCE:.2f} kr.")
    
    # UTSKRIFT: Visa detaljerad systeminformationen
    print("\n**UPPTÄCKTA SYSTEMSPECIFIKATIONER:**")
    print(spec_list)
    print("------------------------------------------") 

    try:
        client = ollama.Client(host=OLLAMA_HOST)
        
        # --- Steg 1 & 2: LLM Utvärderar Systemet och Föreslår Bäst Uppgradering ---
        print("\n--- Steg 1/2: LLM Utvärderar Systemet & Föreslår Bäst Uppgradering (Endast en) ---")
        
        recommended_component = None
        
        system_prompt_2 = (
            "Du är världens bästa hårdvaruexpert. Föreslå den *enda bästa* uppgraderingen (en av CPU, GPU, RAM, SSD, eller Motherboard) för krävande AI-arbetslaster, "
            "baserat på systemets nuvarande flaskhalsar. "
            f"Priset måste vara *mindre än eller lika med* {INITIAL_BALANCE:,.0f} kr. "
            "Svara ENDAST med ett JSON-objekt: "
            "{\"recommended_component\": \"Namn på produkt\", \"component_type\": \"TYPE\", \"expected_price_sek\": Siffra, \"reasoning\": \"Kort motivering\"}. Använd inga kommaseparatorer i siffror. TYPE måste vara en av CPU, GPU, RAM, SSD, Motherboard."
        )
        
        # PROMPT: Inkludera all systeminformation till LLM:en
        user_prompt_2 = (
            f"Systemspecifikationer:\n{spec_list}\n\n"
            f"Min nuvarande processor är: {current_processor}. Vilken är den bästa enskilda uppgraderingen, och varför? Budget: {current_balance:.0f} kr."
        )

        response_2 = client.chat(
            model=OLLAMA_MODEL,
            messages=[
                {'role': 'system', 'content': system_prompt_2},
                {'role': 'user', 'content': user_prompt_2},
            ]
        )
        
        suggestion_data = clean_and_parse_json(response_2['message']['content'])
        
        if not suggestion_data or 'recommended_component' not in suggestion_data or suggestion_data.get('component_type') not in COMPONENT_TYPES:
            print(f"❌ LLM-svaret kunde inte tolkas eller föreslog ingen giltig komponenttyp. Avbryter köpcykeln.")
            return 
        
        recommended_component = suggestion_data.get('recommended_component')
        recommended_type = suggestion_data.get('component_type')

        print(f"🎉 LLM Föreslår BÄSTA UPGRADERINGEN: **{recommended_component}** ({recommended_type})")
        print(f"  > Motivering: {suggestion_data.get('reasoning', 'N/A')}")


        # --- Steg 3: Hämta Detaljerade Specifikationer & Pris (Kombinerat) ---
        print("\n--- Steg 3: Hämta detaljerade specifikationer och pris via LLM/RapidAPI... ---")
        
        detailed_data = None
        is_new_component = not db.check_if_component_exists(recommended_component)

        if is_new_component:
            detailed_data = fetch_component_details(client, recommended_component, recommended_type)
            if detailed_data:
                db.log_hardware_details(detailed_data)
                print(f"✅ Detaljerade specifikationer loggades i databasen för {recommended_component}.")
        else:
             db.cursor.execute("SELECT component_name, component_type, price_sek, details_json FROM hardware_details WHERE component_name = ?", (recommended_component,))
             row = db.cursor.fetchone()
             detailed_data = {
                 'component_name': row[0],
                 'component_type': row[1],
                 'price_sek': row[2],
                 'details_json': json.loads(row[3]) if row[3] else {}
             }
             print(f"  > Detaljer för {recommended_component} hämtades från LOKAL databas.")
            
        
        if not detailed_data or 'price_sek' not in detailed_data:
            print(f"❌ Kunde inte hämta/hitta detaljer för {recommended_component}. Avbryter köp.")
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
            db.log_purchase(recommended_component, recommended_type, actual_price)
            
            print(f"✅ KÖP GENOMFÖRT! Simulerat köp av {recommended_component} ({recommended_type}) för {actual_price:,.2f} kr. Loggat i DB.")
            print(f"💰 NYTT SALDO: **{new_balance:,.2f} kr**.")
            
        else:
            if actual_price > INITIAL_BALANCE:
                print(f"⚠️ KÖP AVSLOGS: Priset ({actual_price:,.2f} kr) överstiger budgetgränsen ({INITIAL_BALANCE:,.2f} kr).")
            else:
                 print(f"⚠️ KÖP AVSLOGS: Priset ({actual_price:,.2f} kr) överstiger nuvarande plånbokssaldo ({current_balance:,.2f} kr).")

    except Exception as e:
        print(f"❌ GENERISKT FEL: Kunde inte slutföra uppgraderingscykeln: {e}")
        
    print("\n--- SystemAgent V22 Avslutar ---")


if __name__ == "__main__":
    db = None
    try:
        db = AgentDB()
        client = ollama.Client(host=OLLAMA_HOST) 

        # 1. Kör den nya prioriterade köp/analyscykeln
        analyze_and_upgrade_hardware_v22(db)
        
        # 2. Fyll på databasen med generell information (Iterativt över alla komponenttyper)
        populate_database_with_generic_data(db, client)
        
        # 3. Simulerad utskrift av köphistorik
        print("\n--- Simulerad köphistorik från DB ---")
        db.cursor.execute("SELECT item_name, item_type, cost_sek, purchase_date FROM purchases ORDER BY purchase_date DESC")
        purchases = db.cursor.fetchall()
        if not purchases:
            print("  > Ingen köphistorik finns.")
        for item, item_type, cost, date in purchases:
            print(f"  > Köp: {item} ({item_type}) | Kostnad: {cost:,.2f} kr | Datum: {date}")

        # 4. Utskrift av ALL lagrad hårdvarudetaljer
        print("\n--- ALLA lagrade hårdvarudetaljer från DB (Sorterad efter pris) ---")
        db.cursor.execute("SELECT component_name, component_type, price_sek, details_json FROM hardware_details ORDER BY price_sek ASC")
        all_details = db.cursor.fetchall()
        
        if all_details:
            columns = ['Komponent Namn', 'Typ', 'Pris (SEK)', 'Specifikationer']
            
            print("  " + " | ".join([f"{col:<25}" for col in columns]))
            print("  " + "=" * (len(columns) * 20))
            
            for row in all_details:
                spec_str = "Inga detaljer"
                try:
                    specs = json.loads(row[3])
                    spec_list = [f"{k}: {v}" for k, v in specs.items()]
                    spec_str = ", ".join(spec_list[:2]) + ("..." if len(spec_list) > 2 else "")
                except json.JSONDecodeError:
                    pass

                output = [
                    f"{row[0]:<25}",    # component_name
                    f"{row[1]:<25}",    # component_type
                    f"{row[2]:<10,.0f} kr", # price_sek
                    f"{spec_str:<30}"
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
