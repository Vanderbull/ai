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

# Standardbudget och Laptop-budget
DESKTOP_BUDGET = 10000.0
LAPTOP_BUDGET = 50000.0 
INITIAL_BALANCE_RESET = DESKTOP_BUDGET 

MAX_RETRIES_UNIQUE_CPU = 50 

# NYA INSTÄLLNINGAR FÖR EXTERNT API
RAPIDAPI_HOST = os.environ.get("RAPIDAPI_HOST")
RAPIDAPI_KEY = os.environ.get("RAPIDAPI_KEY")

# Komponenttyper för Desktop-läge
DESKTOP_COMPONENT_TYPES = ["CPU", "GPU", "RAM", "SSD", "Motherboard"]
# Komponenttyper för Laptop-läge
LAPTOP_COMPONENT_TYPES = ["Laptop"] 


# --- DATABAS HANTERING (V30) ---
class AgentDB:
    """Klass för att hantera Agentens SQLite-databas."""
    def __init__(self, db_name=DB_NAME):
        self.conn = sqlite3.connect(db_name)
        self.cursor = self.conn.cursor()
        self._initialize_db()

    def _initialize_db(self):
        """Skapar tabeller och Tvingar fram ÅTERSTÄLLNING av saldo till INITIAL_BALANCE_RESET."""
        
        # Tabell 1: purchases
        self.cursor.execute("""
            CREATE TABLE IF NOT EXISTS purchases (
                id INTEGER PRIMARY KEY,
                item_name TEXT NOT NULL,
                item_type TEXT NOT NULL,
                cost_sek REAL NOT NULL,
                purchase_date TEXT NOT NULL
            )
        """)
        
        # Tabell 2: sales 
        self.cursor.execute("""
            CREATE TABLE IF NOT EXISTS sales (
                id INTEGER PRIMARY KEY,
                item_name TEXT NOT NULL,
                item_type TEXT NOT NULL,
                sale_price_sek REAL NOT NULL,
                sale_date TEXT NOT NULL
            )
        """)

        # Tabell 3: status
        self.cursor.execute("""
            CREATE TABLE IF NOT EXISTS status (
                key TEXT PRIMARY KEY,
                value TEXT
            )
        """)
        
        # Tabell 4: hardware_details
        self.cursor.execute("""
            CREATE TABLE IF NOT EXISTS hardware_details (
                component_name TEXT PRIMARY KEY,
                component_type TEXT NOT NULL,
                price_sek REAL NOT NULL,
                date_fetched TEXT NOT NULL,
                details_json TEXT 
            )
        """)
        
        # --- ROBUST ÅTERSTÄLLNINGSLOGIK FÖR PLÅNBOK ---
        self.cursor.execute("DELETE FROM status WHERE key = 'wallet_balance'")
        self.cursor.execute(
            "INSERT INTO status (key, value) VALUES (?, ?)", 
            ('wallet_balance', str(INITIAL_BALANCE_RESET))
        )
        self.conn.commit()
        
        print(f"✅ Databas ansluten/skapad. Plånbokssaldo ÅTERSTÄLLT till {INITIAL_BALANCE_RESET:.2f} kr (Desktop Default).")

    def set_balance(self, new_balance: float):
        self.cursor.execute("UPDATE status SET value = ? WHERE key = 'wallet_balance'", (str(new_balance),))
        self.conn.commit()
        print(f"💰 Plånbokssaldo uppdaterat till: {new_balance:,.2f} kr.")
        
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
        
    def log_sale(self, item_name: str, item_type: str, sale_price: float): 
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        self.cursor.execute(
            "INSERT INTO sales (item_name, item_type, sale_price_sek, sale_date) VALUES (?, ?, ?, ?)",
            (item_name, item_type, sale_price, now)
        )
        self.conn.commit()
        
    def get_current_component_name(self, component_type: str) -> str | None:
        """Hämtar namnet på den nuvarande installerade komponenten av en given typ."""
        key = f"current_{component_type.lower()}"
        self.cursor.execute("SELECT value FROM status WHERE key = ?", (key,))
        result = self.cursor.fetchone()
        return result[0] if result else None

    def set_current_component_name(self, component_type: str, component_name: str):
        """Sparar namnet på den nya installerade komponenten."""
        key = f"current_{component_type.lower()}"
        self.cursor.execute("INSERT OR REPLACE INTO status (key, value) VALUES (?, ?)", (key, component_name))
        self.conn.commit()
        
    def get_component_details_by_name(self, component_name: str) -> dict | None: 
        """Hämtar alla lagrade detaljer för en komponent för att simulera dess specifikationer."""
        self.cursor.execute(
            "SELECT component_name, component_type, price_sek, details_json FROM hardware_details WHERE component_name = ?", 
            (component_name,)
        )
        row = self.cursor.fetchone()
        if row:
            details = json.loads(row[3]) if row[3] else {}
            details['component_name'] = row[0]
            details['component_type'] = row[1]
            details['price_sek'] = row[2]
            return details
        return None

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

# --- HJÄLPFUNKTIONER (V30) ---

def get_current_hardware_info() -> dict:
    """Samlar in basinformation om maskinvaran (OS, arkitektur, etc.)."""
    info = {}
    
    info["OS"] = platform.system()
    info["Architecture"] = platform.machine()
    info["Python Version"] = platform.python_version()
    
    # Simulerad processor för att LLM ska kunna identifiera en laptop (U-series)
    simulated_processor = "Intel(R) Core(TM) i7-7600U CPU @ 2.80GHz (Simulated)" 
    info["Processor"] = simulated_processor 
    info["CPU_Cores"] = os.cpu_count()
    info["Hardware_Info_Source"] = f"Standard Library ({info['OS']})"

    if info["OS"] == "Linux":
        try:
            # ... (Läs av lscpu detaljer om möjligt) ...
            result = subprocess.run(['lscpu'], capture_output=True, text=True, check=True, timeout=5)
            output = result.stdout.strip()
            
            for line in output.split('\n'):
                if ':' in line:
                    key, value = line.split(':', 1)
                    key = key.strip().replace(' ', '_').replace('(', '').replace(')', '')
                    info[f"CPU_{key}"] = value.strip()
            
            info["Processor"] = info.get("CPU_Model_name", simulated_processor)
            info["CPU_Cores"] = info.get("CPU_CPU(s)", info["CPU_Cores"])
            info["Hardware_Info_Source"] = "Linux (lscpu)"

        except:
            pass 
        
    return info

def clean_and_parse_json(llm_response: str) -> dict | list | None:
    """Robust funktion för att rensa LLM-svar till en parsbar JSON (oförändrad)."""
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
    """Simulerad RapidAPI-sökning (oförändrad)."""
    # ... (Använder externt API om nycklar finns, annars None) ...
    return None # Simulerat API anrop - returnerar None i denna version

def fetch_component_specs_from_llm(client: ollama.Client, component_name: str, component_type: str) -> dict | None: # ÅTERINFORMAT V30
    """Hämtar alla detaljer (inklusive pris) och typ från LLM."""
    
    if component_type == "CPU":
        spec_example = " (t.ex. \"cores\", \"threads\", \"base_clock_ghz\", \"socket\")"
    elif component_type == "GPU":
        spec_example = " (t.ex. \"VRAM_GB\", \"Bus_Width\", \"Ray_Tracing_Support\")"
    elif component_type == "RAM":
        spec_example = " (t.ex. \"capacity_gb\", \"speed_mhz\", \"type\", \"latency\")"
    elif component_type == "SSD":
        spec_example = " (t.ex. \"capacity_gb\", \"interface\", \"read_speed_mbps\")"
    elif component_type == "Motherboard":
         spec_example = " (t.ex. \"socket\", \"chipset\", \"ram_slots\")"
    elif component_type == "Laptop":
        spec_example = " (t.ex. \"CPU_name\", \"GPU_name\", \"RAM_GB\", \"Screen_Size_inches\", \"Weight_kg\")"
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
    """Huvudfunktion för datahämtning: LLM för specs, RapidAPI för pris override (oförändrad)."""
    
    llm_data = fetch_component_specs_from_llm(client, component_name, component_type) # Nu definierad
    
    if not llm_data:
        return None 

    final_data = llm_data.copy()
    
    api_data = fetch_cpu_details_from_rapidapi(component_name)
    
    if api_data and 'price_sek' in api_data:
        final_data['price_sek'] = api_data['price_sek']
        print(f"    ➡️ Pris uppdaterat till {final_data['price_sek']:.0f} SEK via RapidAPI.")
        
    return final_data

def get_simulated_tradein_value(client: ollama.Client, component_name: str, component_type: str) -> float: 
    """Hämtar ett simulerat andrahandsvärde för en gammal komponent/laptop via LLM (oförändrad)."""
    # ... (logiken är oförändrad) ...
    
    system_prompt_sale = (
        "Du är en expert på andrahandsmarknaden för hårdvara. Du ska uppskatta ett realistiskt "
        f"försäljningspris i SEK för en begagnad {component_type}: {component_name}. "
        "Svara ENDAST med ett JSON-objekt: "
        "{\"trade_in_value_sek\": Siffra}. Siffran måste vara ett heltal eller ett flyttal med max två decimaler."
    )
    
    user_prompt_sale = f"Vad är ett rimligt andrahandsvärde för {component_name}?"
    
    print(f"    > Hämtar simulerat andrahandsvärde för {component_name}...")

    try:
        response = client.chat(
            model=OLLAMA_MODEL,
            messages=[
                {'role': 'system', 'content': system_prompt_sale},
                {'role': 'user', 'content': user_prompt_sale},
            ]
        )
        
        sale_data = clean_and_parse_json(response['message']['content'])
        
        if sale_data and 'trade_in_value_sek' in sale_data:
            try:
                price = float(sale_data['trade_in_value_sek'])
                if price > 0:
                    print(f"    ✅ Simulerat försäljningsvärde: {price:,.2f} kr.")
                    return price
            except ValueError:
                pass
            
    except Exception as e:
        print(f"    ❌ FEL vid hämtning av andrahandsvärde för {component_name}: {e}")
        
    return 0.0

def detect_system_type(client: ollama.Client, hardware_info: dict) -> str: 
    """Använder LLM för att avgöra om det är Desktop eller Laptop (oförändrad)."""
    system_info_str = "\n".join([f"- {k}: {v}" for k, v in hardware_info.items()])
    
    system_prompt = (
        "Du är en maskinvaruanalytiker. Bedöm om följande systemspecifikationer tillhör en stationär dator (Desktop) eller en bärbar dator (Laptop). "
        "Basera din slutsats på namn på CPU, Model name (om tillgängligt), och andra systemdetaljer som kan tyda på mobilitet (t.ex. U-series CPU eller saknade komponenter). "
        "Svara ENDAST med ett JSON-objekt: {\"system_type\": \"Desktop\"} eller {\"system_type\": \"Laptop\"}. Inga andra ord eller motiveringar."
    )
    user_prompt = f"Bedöm systemtyp baserat på dessa detaljer:\n{system_info_str}"
    
    print("\n--- Steg 0: Detekterar systemtyp (Laptop/Desktop) via LLM ---")
    try:
        response = client.chat(
            model=OLLAMA_MODEL,
            messages=[
                {'role': 'system', 'content': system_prompt},
                {'role': 'user', 'content': user_prompt},
            ]
        )
        
        detection_data = clean_and_parse_json(response['message']['content'])
        
        if detection_data and 'system_type' in detection_data and detection_data['system_type'] in ["Desktop", "Laptop"]:
            result = detection_data['system_type']
            print(f"✅ LLM detekterade systemtyp: **{result}**")
            return result
        
    except Exception as e:
        print(f"❌ FEL vid systemdetektering: {e}")
        
    print("⚠️ Återgår till standard: Desktop.")
    return "Desktop"

def fetch_initial_laptop_model(client: ollama.Client, hardware_info: dict) -> str: 
    """Använder LLM för att bestämma det exakta modellnamnet på den bärbara datorn (oförändrad)."""
    
    system_info_str = "\n".join([f"- {k}: {v}" for k, v in hardware_info.items()])
    fallback_name = hardware_info.get('Processor', 'Unknown Laptop Model (Fallback)')
    
    system_prompt = (
        "Du är en hårdvaruidentifierare. Baserat på de inmatade systemspecifikationerna, vilket är det EXAKTA KOMMERSIELLA MODELLNAMNET (inklusive märke och serie, t.ex. 'Dell XPS 13 9310' eller 'MacBook Pro M3 Max') på denna bärbara dator? "
        "Svara ENDAST med ett JSON-objekt: {\"laptop_model\": \"Exakt Modellnamn\"}. Inga andra ord eller motiveringar."
    )
    user_prompt = f"Identifiera den bärbara datorns modellnamn baserat på dessa detaljer:\n{system_info_str}"
    
    print("\n--- Steg 0.1: Identifierar exakt Laptop-modell via LLM ---")
    try:
        response = client.chat(
            model=OLLAMA_MODEL,
            messages=[
                {'role': 'system', 'content': system_prompt},
                {'role': 'user', 'content': user_prompt},
            ]
        )
        
        model_data = clean_and_parse_json(response['message']['content'])
        
        if model_data and 'laptop_model' in model_data and model_data['laptop_model']:
            result = model_data['laptop_model']
            print(f"✅ LLM identifierade Laptop-modell: **{result}**")
            return result
        
    except Exception as e:
        print(f"❌ FEL vid identifiering av laptop-modell: {e}")
        
    print(f"⚠️ Återgår till simulerat CPU-namn som modell: {fallback_name}.")
    return fallback_name


# --- DATABAS PÅFYLLNING (BULK - OFÖRÄNDRAD) ---

def populate_database_with_generic_data(db: AgentDB, client: ollama.Client):
    """Fyller databasen med komponenter i bulk (oförändrad)."""
    # ... (logiken är oförändrad) ...
    
    print("\n--- 🧠 Steg X: Databaspåfyllning (Generell Hårdvara) Startad ---")
    
    BATCH_SIZE = 5
    total_new_components_logged = 0
    
    for component_type in DESKTOP_COMPONENT_TYPES + LAPTOP_COMPONENT_TYPES: 
        
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
                    if iteration > 1: break 
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
            
            if new_components_in_batch == 0 and iteration > 1:
                print(f"  🛑 Iteration {iteration}: Inga unika {component_type} lades till. Databasen är mättad för denna typ.")
                break
            
            print(f"  > {new_components_in_batch} nya {component_type} lades till. Totalt nya: {total_new_components_logged}. Fortsätter sökning...")

    print(f"\n--- Databas påfyllning slutförd. Totalt {total_new_components_logged} nya komponenter lades till. ---")


# --- KÄRNFUNKTIONER (KÖPCYKEL - V30) ---

def analyze_and_upgrade_hardware_v30(db: AgentDB, client: ollama.Client, system_type: str, max_budget: float) -> bool:
    """
    Analysera systemet (komponent eller systembyte) och rekommendera den bästa P/P-uppgraderingen/bytet.
    """
    
    # 1. Hämta basinfo och uppdatera med installerade komponenter
    full_hardware_info = get_current_hardware_info()
    current_balance = db.get_balance()
    
    tracked_types = LAPTOP_COMPONENT_TYPES if system_type == "Laptop" else DESKTOP_COMPONENT_TYPES

    # Bygg den simulerade systembilden som LLM ska utvärdera
    for comp_type in tracked_types:
        current_name = db.get_current_component_name(comp_type)
        if current_name:
            details = db.get_component_details_by_name(current_name)
            
            if details:
                # Lägg till komponentens detaljer i full_hardware_info för LLM-kontext
                full_hardware_info[f"Current_{comp_type}_Name"] = current_name
                
                # Lägg till de tekniska specifikationerna i huvudinformationen
                for key, value in details.items():
                    if key not in ['component_name', 'component_type', 'price_sek', 'details_json']:
                        if comp_type == "Laptop":
                             full_hardware_info[f"Current_Laptop_{key}"] = value
                        else:
                             full_hardware_info[f"Current_{comp_type}_{key.capitalize()}"] = value
            else:
                 full_hardware_info[f"Current_{comp_type}_Name"] = f"{current_name} (Specs saknas)"


    # Skapa en formaterad lista av alla specs för utskrift och LLM-prompt
    spec_list = "\n".join([f"- {k}: {v}" for k, v in sorted(full_hardware_info.items())])
    
    print(f"\n💰 Nuvarande Saldo: {current_balance:,.2f} kr. Max Budget (initial): {max_budget:,.2f} kr.")
    print("\n**SIMULERADE SYSTEMSPECIFIKATIONER FÖR ANALYS:**")
    print(spec_list)
    print("------------------------------------------") 

    # --- LLM Analys & Förslag ---
    
    if system_type == "Laptop":
        comp_type_list = "Laptop"
        prompt_goal = "bästa HELA bärbara datorn (Laptop) baserat på P/P som ersätter det nuvarande systemet"
    else:
        comp_type_list = ", ".join(DESKTOP_COMPONENT_TYPES)
        prompt_goal = "enda bästa komponentuppgraderingen"
        
    print(f"\n--- Steg 2: LLM Utvärderar Simulerat System & Föreslår {prompt_goal} (P/P) ---")
    
    system_prompt_2 = (
        f"Du är världens bästa hårdvaruexpert. Föreslå den {prompt_goal} (en av {comp_type_list}) för krävande AI-arbetslaster. "
        "BASERA DITT VAL PÅ DEN BÄSTA PRESTANDA FÖR PENGARNA (Performance-per-kronor, P/P) och adressera systemets största flaskhals. "
        "Anta att du kan sälja den gamla komponenten/systemet för att täcka delar av kostnaden. "
        f"Priset måste vara *mindre än eller lika med* {max_budget:,.0f} kr. "
        "Svara ENDAST med ett JSON-objekt: "
        "{\"recommended_component\": \"Namn på produkt\", \"component_type\": \"TYPE\", \"expected_price_sek\": Siffra, \"reasoning\": \"Kort motivering, fokuserad på P/P\"}. Använd inga kommaseparatorer i siffror. TYPE måste vara en av {comp_type_list}."
    )
    
    user_prompt_2 = (
        f"Systemspecifikationer (inklusive nuvarande installerad hårdvara):\n{spec_list}\n\n"
        f"Vilken är den bästa enskilda uppgraderingen baserad på P/P, och varför? Nuvarande saldo: {current_balance:,.0f} kr."
    )

    response_2 = client.chat(
        model=OLLAMA_MODEL,
        messages=[
            {'role': 'system', 'content': system_prompt_2},
            {'role': 'user', 'content': user_prompt_2},
        ]
    )
    
    suggestion_data = clean_and_parse_json(response_2['message']['content'])
    
    if not suggestion_data or 'recommended_component' not in suggestion_data or suggestion_data.get('component_type') not in tracked_types:
        print(f"❌ LLM-svaret kunde inte tolkas eller föreslog ingen giltig komponenttyp. Avbryter köpcykeln.")
        return False 
    
    recommended_component = suggestion_data.get('recommended_component')
    recommended_type = suggestion_data.get('component_type')

    print(f"🎉 LLM Föreslår BÄSTA UPGRADERINGEN (P/P): **{recommended_component}** ({recommended_type})")
    print(f"  > Motivering: {suggestion_data.get('reasoning', 'N/A')}")


    # --- Steg 3: Hämta Detaljerade Specifikationer & Pris ---
    print("\n--- Steg 3: Hämta detaljerade specifikationer och pris via LLM/RapidAPI... ---")
    
    detailed_data = None
    is_new_component = not db.check_if_component_exists(recommended_component)

    if is_new_component:
        detailed_data = fetch_component_details(client, recommended_component, recommended_type)
        if detailed_data:
            db.log_hardware_details(detailed_data)
            print(f"✅ Detaljerade specifikationer loggades i databasen för {recommended_component}.")
    else:
         detailed_data = db.get_component_details_by_name(recommended_component)
         print(f"  > Detaljer för {recommended_component} hämtades från LOKAL databas.")
        
    
    if not detailed_data or 'price_sek' not in detailed_data:
        print(f"❌ Kunde inte hämta/hitta detaljer/pris för {recommended_component}. Avbryter köp.")
        return False
        
    try:
        actual_price = float(detailed_data.get('price_sek')) 
    except (ValueError, TypeError):
        print(f"❌ Priset ({detailed_data.get('price_sek')}) var inte ett giltigt nummer. Avbryter köp.")
        return False
    
    
    # --- KÖPLOGIK ---
    print(f"  > Pris: **{actual_price:,.2f} kr** (Hämtat från LLM/RapidAPI/DB)")
    
    # Hämta värdet av den gamla komponenten/systemet INNAN saldokontroll
    old_component_name = db.get_current_component_name(recommended_type)
    sale_value = 0.0

    if old_component_name and old_component_name != recommended_component:
        print(f"\n--- Steg 4: Inbytesanalys (Säljer gammal: {old_component_name}) ---")
        sale_value = get_simulated_tradein_value(client, old_component_name, recommended_type)

    # Beräkna nettokostnaden (Köppris - Försäljningsvärde)
    net_cost = actual_price - sale_value

    if net_cost <= current_balance and actual_price <= max_budget:
        
        # Utför transaktion
        new_balance = current_balance - net_cost
        db.update_balance(new_balance)
        db.log_purchase(recommended_component, recommended_type, actual_price)
        
        if sale_value > 0:
            db.log_sale(old_component_name, recommended_type, sale_value)
            print(f"✅ FÖRSÄLJNING GENOMFÖRD: {old_component_name} såldes för {sale_value:,.2f} kr. Saldo ökade.")

        # --- VIKTIGT: Uppdatera systemets installerade komponent ---
        db.set_current_component_name(recommended_type, recommended_component)
        
        print(f"✅ KÖP GENOMFÖRT! Simulerat köp av {recommended_component} ({recommended_type}) för {actual_price:,.2f} kr. Nettokostnad: {net_cost:,.2f} kr.")
        print(f"💰 NYTT SALDO (Efter transaktion): **{new_balance:,.2f} kr**.")
        
        return True 
        
    else:
        if net_cost > current_balance:
             print(f"⚠️ KÖP AVSLOGS: Nettokostnaden ({net_cost:,.2f} kr) överstiger nuvarande plånbokssaldo ({current_balance:,.2f} kr).")
        elif actual_price > max_budget:
             print(f"⚠️ KÖP AVSLOGS: Priset ({actual_price:,.2f} kr) överstiger den initiala budgetgränsen ({max_budget:,.2f} kr).")
        
        return False 

def run_upgrade_cycle(db: AgentDB, client: ollama.Client, system_type: str, max_budget: float):
    """Kör den kontinuerliga uppgraderingscykeln (använder V30-analysen)."""
    
    upgrade_count = 0
    while True:
        print(f"\n=======================================================")
        print(f"🧠 KONTINUERLIG UPPGRADERINGSANALYS #{upgrade_count + 1} STARTAR ({system_type}-läge)")
        print(f"=======================================================")
        
        purchase_successful = analyze_and_upgrade_hardware_v30(db, client, system_type, max_budget)
        
        if purchase_successful:
            upgrade_count += 1
            time.sleep(1) 
        else:
            current_balance = db.get_balance()
            print(f"\n--- UPGRADERINGSSTOPP ---")
            print(f"Cykeln avbröts efter {upgrade_count} genomförda uppgraderingar.")
            print(f"Återstående saldo: {current_balance:,.2f} kr.")
            print(f"Anledning: Ingen lönsam (P/P) eller överkomlig uppgradering hittades.")
            break
            
    print("\n--- SystemAgent V30 Avslutar ---")

def generate_summary(db: AgentDB, initial_budget: float, final_budget: float):
    """Genererar en sammanställning av alla transaktioner och systemets slutliga tillstånd (oförändrad)."""
    
    print("\n\n=======================================================")
    print("🚀 SLUTLIG SYSTEMSAMMANSTÄLLNING OCH EKONOMI (V30)")
    print("=======================================================")
    
    # 1. Ekonomisk sammanfattning
    total_spent = 0.0
    total_earned = 0.0
    
    db.cursor.execute("SELECT cost_sek FROM purchases")
    for cost in db.cursor.fetchall():
        total_spent += cost[0]
        
    db.cursor.execute("SELECT sale_price_sek FROM sales")
    for price in db.cursor.fetchall():
        total_earned += price[0]
        
    net_cost = total_spent - total_earned
    
    print("\n--- EKONOMI ---")
    print(f"Initial Budget (Max): {initial_budget:,.2f} kr")
    print(f"Slutligt Saldo:       {final_budget:,.2f} kr")
    print(f"Totala Köp:           {total_spent:,.2f} kr")
    print(f"Totala Försäljningar: +{total_earned:,.2f} kr")
    print(f"Netto Kostnad:        {net_cost:,.2f} kr")
    
    # 2. Hårdvarusammanfattning
    print("\n--- SLUTLIG HÅRDVARUKONFIGURATION ---")
    
    # Använd balansen för att avgöra om det kördes i laptop-läge (initial budget var 50k)
    system_type = "Laptop" if initial_budget == LAPTOP_BUDGET else "Desktop"
    
    if system_type == "Laptop":
        comp_type = "Laptop"
        current_name = db.get_current_component_name(comp_type)
        if current_name:
            details = db.get_component_details_by_name(current_name)
            if details:
                print(f"Systemtyp: **Laptop**")
                print(f"Installerad Laptop: **{current_name}**")
                
                key_specs = [f"{k}: {v}" for k, v in details.items() if k not in ['component_name', 'component_type', 'price_sek', 'details_json']]
                print("  Detaljer: " + ", ".join(key_specs[:4]) + "...")
                
    else:
        print(f"Systemtyp: **Desktop**")
        for comp_type in DESKTOP_COMPONENT_TYPES:
            current_name = db.get_current_component_name(comp_type)
            if current_name:
                details = db.get_component_details_by_name(current_name)
                specs_str = ""
                if details:
                    key_specs = [f"{k}: {v}" for k, v in details.items() if k not in ['component_name', 'component_type', 'price_sek', 'details_json']]
                    specs_str = f" ({', '.join(key_specs[:2])}...)"
                print(f"  {comp_type:<12}: **{current_name}**{specs_str}")
        
    # 3. Transaktionshistorik
    print("\n--- DETALJERAD TRANSAKTIONSHISTORIK ---")
    db.cursor.execute("SELECT item_name, item_type, cost_sek, purchase_date FROM purchases ORDER BY purchase_date ASC")
    purchases = db.cursor.fetchall()
    
    db.cursor.execute("SELECT item_name, item_type, sale_price_sek, sale_date FROM sales ORDER BY sale_date ASC")
    sales = db.cursor.fetchall()
    
    for item, item_type, cost, date in purchases:
         print(f"  [KÖP] -{cost:,.2f} kr: {item} ({item_type}) @ {date}")

    for item, item_type, price, date in sales:
         print(f"  [SÄLJ] +{price:,.2f} kr: {item} (Gammal {item_type}) @ {date}")


    print("=======================================================")


if __name__ == "__main__":
    db = None
    try:
        db = AgentDB()
        client = ollama.Client(host=OLLAMA_HOST) 
        
        # 1. Detektera systemtyp (använder OS-info)
        initial_hardware_info = get_current_hardware_info()
        system_type = detect_system_type(client, initial_hardware_info)
        
        # 2. Sätt rätt budget och initial komponent
        if system_type == "Laptop":
            max_budget = LAPTOP_BUDGET
            db.set_balance(max_budget)
            
            # Få det exakta Laptop-modellnamnet
            initial_system_name = fetch_initial_laptop_model(client, initial_hardware_info)
            db.set_current_component_name("Laptop", initial_system_name)
            
            # Logga initial info för att möjliggöra försäljning/detaljanalys
            if not db.check_if_component_exists(initial_system_name):
                 initial_details = fetch_component_details(client, initial_system_name, "Laptop")
                 if initial_details:
                     db.log_hardware_details(initial_details)

        else:
            max_budget = DESKTOP_BUDGET
            db.set_balance(max_budget)
            
            # Desktop initial setup (för CPU)
            initial_system_name = initial_hardware_info.get('Processor', 'Unknown CPU')
            db.set_current_component_name("CPU", initial_system_name)
            
            if not db.check_if_component_exists(initial_system_name):
                 initial_details = fetch_component_details(client, initial_system_name, "CPU")
                 if initial_details:
                     db.log_hardware_details(initial_details)


        # 3. Kör den kontinuerliga köp/analyscykeln
        run_upgrade_cycle(db, client, system_type, max_budget)
        
        # 4. Fyll på databasen med generell information (Om tid/resurser finns)
        populate_database_with_generic_data(db, client)
        
        # 5. Generera sammanställning
        generate_summary(db, max_budget, db.get_balance())
        
    except Exception as e:
        print(f"Ett kritiskt fel uppstod vid databas- eller agentkörning: {e}")
        
    finally:
        if db:
            db.close()
            print(f"\nDatabasanslutning till {DB_NAME} stängd.")
