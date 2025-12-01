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
INITIAL_BALANCE = 10000.0 # Agentens startbudget
MAX_RETRIES_UNIQUE_CPU = 50 

# NYA INSTÄLLNINGAR FÖR EXTERNT API
RAPIDAPI_HOST = os.environ.get("RAPIDAPI_HOST")
RAPIDAPI_KEY = os.environ.get("RAPIDAPI_KEY")

# NY LISTA MED KOMPONENTTYPER ATT SÖKA EFTER
COMPONENT_TYPES = ["CPU", "GPU", "RAM", "SSD", "Motherboard"]


# --- DATABAS HANTERING (V26) ---
class AgentDB:
    """Klass för att hantera Agentens SQLite-databas."""
    def __init__(self, db_name=DB_NAME):
        self.conn = sqlite3.connect(db_name)
        self.cursor = self.conn.cursor()
        self._initialize_db()

    def _initialize_db(self):
        """Skapar tabeller och Tvingar fram ÅTERSTÄLLNING av saldo till INITIAL_BALANCE."""
        
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
        # Tvingar fram återställning: Radera gammalt saldo och sätt in nytt.
        self.cursor.execute("DELETE FROM status WHERE key = 'wallet_balance'")
        self.cursor.execute(
            "INSERT INTO status (key, value) VALUES (?, ?)", 
            ('wallet_balance', str(INITIAL_BALANCE))
        )
        self.conn.commit()
        
        print(f"✅ Databas ansluten/skapad. Plånbokssaldo ÅTERSTÄLLT till {INITIAL_BALANCE:.2f} kr.")


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

# --- HJÄLPFUNKTIONER (V26) ---

def get_current_hardware_info() -> dict:
    """Samlar in detaljerad information om maskinvaran, föredrar lscpu/dmidecode på Linux."""
    info = {}
    
    info["OS"] = platform.system()
    info["Architecture"] = platform.machine()
    info["Python Version"] = platform.python_version()
    
    simulated_processor = "AMD Ryzen 5 3600 (Simulated)"
    info["Processor"] = simulated_processor
    info["CPU_Cores"] = os.cpu_count()
    info["Hardware_Info_Source"] = f"Standard Library ({info['OS']})"

    if info["OS"] == "Linux":
        try:
            result = subprocess.run(['lscpu'], capture_output=True, text=True, check=True, timeout=5)
            output = result.stdout.strip()
            
            for line in output.split('\n'):
                if ':' in line:
                    key, value = line.split(':', 1)
                    key = key.strip().replace(' ', '_').replace('(', '').replace(')', '')
                    info[f"CPU_{key}"] = value.strip()
            
            info["Processor"] = info.get("CPU_Model_name", info.get("CPU_Architecture", simulated_processor))
            info["CPU_Cores"] = info.get("CPU_CPU(s)", info["CPU_Cores"])
            info["Hardware_Info_Source"] = "Linux (lscpu)"

        except:
            pass # Fallback to simulated data/defaults
        
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
    """Simulerad RapidAPI-sökning (oförändrad)."""
    
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
    """Hämtar alla detaljer (inklusive pris) och typ från LLM (oförändrad)."""
    
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
    """Huvudfunktion för datahämtning: LLM för specs, RapidAPI för pris override (oförändrad)."""
    
    llm_data = fetch_component_specs_from_llm(client, component_name, component_type)
    
    if not llm_data:
        return None 

    final_data = llm_data.copy()
    
    api_data = fetch_cpu_details_from_rapidapi(component_name)
    
    if api_data and 'price_sek' in api_data:
        final_data['price_sek'] = api_data['price_sek']
        print(f"    ➡️ Pris uppdaterat till {final_data['price_sek']:.0f} SEK via RapidAPI.")
        
    return final_data

def get_simulated_tradein_value(client: ollama.Client, component_name: str, component_type: str) -> float: 
    """Hämtar ett simulerat andrahandsvärde för en gammal komponent via LLM (oförändrad)."""
    
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


# --- DATABAS PÅFYLLNING (BULK) ---

def populate_database_with_generic_data(db: AgentDB, client: ollama.Client):
    """Fyller databasen med komponenter i bulk (oförändrad)."""
    
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


# --- KÄRNFUNKTIONER (KÖPCYKEL - UPPDATERAD V26) ---

def analyze_and_upgrade_hardware_v26(db: AgentDB, client: ollama.Client) -> bool:
    """
    Analysera hårdvara och rekommendera den bästa P/P-uppgraderingen. 
    Returnerar True om köp genomfördes, False annars.
    """
    
    # Hämta detaljerad hårdvaruinformation
    full_hardware_info = get_current_hardware_info()
    
    # Försäkra att en nuvarande CPU är satt (första körningen)
    initial_cpu_name = full_hardware_info.get('Processor', 'Unknown CPU')
    if not db.get_current_component_name("CPU"):
         db.set_current_component_name("CPU", initial_cpu_name)

    # Hämta aktuell hårdvarukonfiguration från DB/System
    current_installed_hardware = {}
    for comp_type in COMPONENT_TYPES:
        current_name = db.get_current_component_name(comp_type)
        if current_name:
            current_installed_hardware[comp_type] = current_name
            # Lägg till namnet i full_hardware_info för LLM-kontext
            full_hardware_info[f"Current_{comp_type}"] = current_name
        
    # Skapa en formaterad lista av alla specs för utskrift och LLM-prompt
    spec_list = "\n".join([f"- {k}: {v}" for k, v in sorted(full_hardware_info.items())])
    
    current_balance = db.get_balance()
    
    print(f"\n💰 Nuvarande Saldo: {current_balance:,.2f} kr. Max Budget: {INITIAL_BALANCE:,.2f} kr.")
    print("\n**AKTUELL HÅRDVARA & SYSTEMSPECIFIKATIONER:**")
    print(spec_list)
    print("------------------------------------------") 

    try:
        # --- Steg 1 & 2: LLM Utvärderar Systemet och Föreslår Bäst Uppgradering (P/P) ---
        print("\n--- Steg 1/2: LLM Utvärderar Systemet & Föreslår Bäst P/P-Uppgradering ---")
        
        system_prompt_2 = (
            "Du är världens bästa hårdvaruexpert. Föreslå den *enda bästa* uppgraderingen (en av CPU, GPU, RAM, SSD, eller Motherboard) för krävande AI-arbetslaster. "
            "BASERA DITT VAL PÅ DEN BÄSTA PRESTANDA FÖR PENGARNA (Performance-per-kronor, P/P) och adressera systemets största flaskhals. "
            "Anta att du kan sälja den gamla komponenten för att täcka delar av kostnaden. "
            f"Priset måste vara *mindre än eller lika med* {INITIAL_BALANCE:,.0f} kr. "
            "Svara ENDAST med ett JSON-objekt: "
            "{\"recommended_component\": \"Namn på produkt\", \"component_type\": \"TYPE\", \"expected_price_sek\": Siffra, \"reasoning\": \"Kort motivering, fokuserad på P/P\"}. Använd inga kommaseparatorer i siffror. TYPE måste vara en av CPU, GPU, RAM, SSD, Motherboard."
        )
        
        user_prompt_2 = (
            f"Systemspecifikationer (inklusive nuvarande installerad hårdvara):\n{spec_list}\n\n"
            f"Vilken är den bästa enskilda uppgraderingen baserat på P/P, och varför? Nuvarande saldo: {current_balance:,.0f} kr."
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
             # Hämta från lokal databas
             db.cursor.execute("SELECT price_sek FROM hardware_details WHERE component_name = ?", (recommended_component,))
             row = db.cursor.fetchone()
             if row:
                 detailed_data = {'price_sek': row[0]}
             print(f"  > Detaljer för {recommended_component} hämtades från LOKAL databas.")
            
        
        if not detailed_data or 'price_sek' not in detailed_data:
            print(f"❌ Kunde inte hämta/hitta detaljer för {recommended_component}. Avbryter köp.")
            return False
            
        try:
            actual_price = float(detailed_data.get('price_sek')) 
        except (ValueError, TypeError):
            print(f"❌ Priset ({detailed_data.get('price_sek')}) var inte ett giltigt nummer. Avbryter köp.")
            return False
        
        
        # --- KÖPLOGIK ---
        print(f"  > Pris: **{actual_price:,.2f} kr** (Hämtat från LLM/RapidAPI/DB)")
        
        # Hämta värdet av den gamla komponenten INNAN saldokontroll
        old_component_name = db.get_current_component_name(recommended_type)
        sale_value = 0.0

        if old_component_name and old_component_name != recommended_component:
            print(f"\n--- Steg 4: Inbytesanalys (Säljer gammal komponent: {old_component_name}) ---")
            sale_value = get_simulated_tradein_value(client, old_component_name, recommended_type)

        # Beräkna nettokostnaden (Köppris - Försäljningsvärde)
        net_cost = actual_price - sale_value

        if net_cost <= current_balance and actual_price <= INITIAL_BALANCE:
            
            # Utför transaktion
            new_balance = current_balance - net_cost
            db.update_balance(new_balance)
            db.log_purchase(recommended_component, recommended_type, actual_price)
            
            if sale_value > 0:
                db.log_sale(old_component_name, recommended_type, sale_value)
                print(f"✅ FÖRSÄLJNING GENOMFÖRD: {old_component_name} såldes för {sale_value:,.2f} kr. Saldo ökade.")

            # Uppdatera vilken komponent som nu är installerad i systemet
            db.set_current_component_name(recommended_type, recommended_component)
            
            print(f"✅ KÖP GENOMFÖRT! Simulerat köp av {recommended_component} ({recommended_type}) för {actual_price:,.2f} kr. Nettokostnad: {net_cost:,.2f} kr.")
            print(f"💰 NYTT SALDO (Efter transaktion): **{new_balance:,.2f} kr**.")
            
            return True # Köp lyckades
            
        else:
            if net_cost > current_balance:
                 print(f"⚠️ KÖP AVSLOGS: Nettokostnaden ({net_cost:,.2f} kr) överstiger nuvarande plånbokssaldo ({current_balance:,.2f} kr).")
            elif actual_price > INITIAL_BALANCE:
                 print(f"⚠️ KÖP AVSLOGS: Priset ({actual_price:,.2f} kr) överstiger den initiala budgetgränsen ({INITIAL_BALANCE:,.2f} kr).")
            
            return False # Köp misslyckades

    except Exception as e:
        print(f"❌ GENERISKT FEL i köpcykel: {e}")
        return False
        
def run_upgrade_cycle(db: AgentDB, client: ollama.Client):
    """Kör den kontinuerliga uppgraderingscykeln."""
    
    upgrade_count = 0
    while True:
        print(f"\n=======================================================")
        print(f"🧠 KONTINUERLIG UPPGRADERINGSANALYS #{upgrade_count + 1} STARTAR")
        print(f"=======================================================")
        
        # Kör analysen och försök köpa.
        purchase_successful = analyze_and_upgrade_hardware_v26(db, client)
        
        if purchase_successful:
            upgrade_count += 1
            # Lägg till en kort paus mellan cyklerna
            time.sleep(1) 
        else:
            current_balance = db.get_balance()
            print(f"\n--- UPGRADERINGSSTOPP ---")
            print(f"Cykeln avbröts efter {upgrade_count} genomförda uppgraderingar.")
            print(f"Återstående saldo: {current_balance:,.2f} kr.")
            print(f"Anledning: Ingen lönsam (P/P) eller överkomlig uppgradering hittades.")
            break
            
    print("\n--- SystemAgent V26 Avslutar ---")


if __name__ == "__main__":
    db = None
    try:
        db = AgentDB()
        client = ollama.Client(host=OLLAMA_HOST) 

        # 1. Kör den kontinuerliga köp/analyscykeln
        run_upgrade_cycle(db, client)
        
        # 2. Fyll på databasen med generell information (Hittas endast om cykeln stoppades snabbt)
        populate_database_with_generic_data(db, client)
        
        # 3. Simulerad utskrift av köphistorik
        print("\n--- Simulerad köphistorik (Köp & Försäljning) från DB ---")
        
        db.cursor.execute("SELECT item_name, item_type, cost_sek, purchase_date FROM purchases ORDER BY purchase_date DESC")
        purchases = db.cursor.fetchall()
        for item, item_type, cost, date in purchases:
            print(f"  > Köp: {item} ({item_type}) | Kostnad: -{cost:,.2ff} kr | Datum: {date}")

        db.cursor.execute("SELECT item_name, item_type, sale_price_sek, sale_date FROM sales ORDER BY sale_date DESC")
        sales = db.cursor.fetchall()
        for item, item_type, price, date in sales:
            print(f"  > Försäljning: {item} ({item_type}) | Intäkt: +{price:,.2f} kr | Datum: {date}")
            
        if not (purchases or sales):
            print("  > Ingen köp- eller försäljningshistorik finns.")


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
