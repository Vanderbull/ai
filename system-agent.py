import os
import platform
import ollama
import json
import re
import requests 
from bs4 import BeautifulSoup 
from dotenv import load_dotenv

# Ladda miljövariabler från .env-filen
load_dotenv()
OLLAMA_MODEL = os.environ.get("OLLAMA_MODEL", "llama3.1")
OLLAMA_HOST = 'http://localhost:11434' 
INITIAL_BUDGET = 10000.0 # Ny budgetgräns för köp

# --- PERSISTENSFUNKTIONER (Återanvänd från Buffalo) ---

def get_current_wallet_balance() -> float:
    """Hämtar det aktuella saldot från .env-filen (eller INITIAL_BUDGET om ej satt)."""
    load_dotenv() # Reload .env
    try:
        return float(os.environ.get("AGENT_WALLET_BALANCE", str(INITIAL_BUDGET)))
    except ValueError:
        return INITIAL_BUDGET

def update_agent_state(new_wallet_balance: float | None = None):
    """Uppdaterar AGENT_WALLET_BALANCE i .env filen."""
    env_path = os.path.join(os.getcwd(), '.env')
    
    try:
        with open(env_path, 'r') as f:
            lines = f.readlines()
    except FileNotFoundError:
        lines = []

    wallet_balance = get_current_wallet_balance() if new_wallet_balance is None else new_wallet_balance
    wallet_line = f"AGENT_WALLET_BALANCE={wallet_balance:.2f}\n"
    
    updated_lines = []
    wallet_found = False

    for line in lines:
        if line.strip().startswith('AGENT_WALLET_BALANCE='):
            updated_lines.append(wallet_line)
            wallet_found = True
        else:
            updated_lines.append(line)

    if not wallet_found:
        updated_lines.append('\n' + wallet_line)
        
    try:
        with open(env_path, 'w') as f:
            f.writelines(updated_lines)
        print(f"✅ Agentens tillstånd sparades automatiskt (Saldo: {wallet_balance:.2f} kr).")
    except Exception as e:
        print(f"❌ FEL vid sparning till .env: {e}")

# --- WEB SCRAPING FUNKTIONER ---

def get_cpu_price(cpu_model: str) -> tuple[float | None, str]:
    """Simulerar prisinhämtning via web scraping (Hårdvarukostnaden i SEK)."""
    
    # OBS: Detta är en extremt förenklad och icke-robust web scraping.
    # I verkligheten skulle man behöva API:er eller specialiserade skrapar.
    # Vi använder en svensk prisjämförelsesajt (t.ex. Prisjakt/Inet)
    
    search_query = cpu_model.replace(' ', '+')
    URL = f"https://www.google.com/search?q={search_query}+prisjakt&hl=sv"
    headers = {'User-Agent': 'Mozilla/5.0'}
    
    print(f"  > Söker efter pris för '{cpu_model}'...")
    
    try:
        # Detta kommer sannolikt att blockeras eller returnera ogiltiga data
        # på grund av brist på riktig headless-browser, men vi simulerar anropet.
        response = requests.get(URL, headers=headers, timeout=10)
        response.raise_for_status() 
        soup = BeautifulSoup(response.content, 'html.parser')
        
        # Simulerad parsning av pris (söker efter ett SEK-pris)
        price_match = re.search(r'(\d{3,9})\s*kr', soup.get_text())
        
        if price_match:
            price = float(price_match.group(1).replace('.', '').replace(',', ''))
            return price, f"Pris hittat: {price} kr via simulerad sökning."
        
        # Fallback: Använd LLM för en *uppskattning* om scraping misslyckas.
        return get_llm_price_estimate(cpu_model), "Pris kunde inte hittas via scraping, använder LLM-uppskattning."

    except Exception as e:
        print(f"  > Varning: Web scraping misslyckades: {e}")
        return get_llm_price_estimate(cpu_model), "Web scraping misslyckades, använder LLM-uppskattning."

def get_llm_price_estimate(cpu_model: str) -> float | None:
    """Använder LLM för att få en *uppskattad* kostnad i SEK."""
    try:
        client = ollama.Client(host=OLLAMA_HOST)
        system_prompt = (
            "Du är en hårdvaruprisexpert. Ge en *rimlig uppskattning* av det nuvarande marknadspriset i SEK för den givna processormodellen. "
            "Svara ENDAST med siffran (heltal), utan valuta eller extra ord."
        )
        user_prompt = f"Vad är det ungefärliga priset i SEK för: {cpu_model}"
        
        response = client.chat(model=OLLAMA_MODEL, messages=[{'role': 'system', 'content': system_prompt}, {'role': 'user', 'content': user_prompt}])
        price_str = response['message']['content'].strip().replace(',', '.').split('.')[0]
        return float(price_str)
        
    except Exception:
        return None


# --- KÄRNFUNKTIONER ---

def get_current_hardware_info() -> dict:
    """Samlar in den grundläggande informationen om maskinvaran."""
    
    processor_name = platform.processor()
    if not processor_name or "unknown" in processor_name.lower():
         processor_name = f"Generisk {os.cpu_count()} kärnig processor" 

    return {
        "OS": platform.system(),
        "Architecture": platform.machine(),
        "Processor": processor_name,
        "CPU Cores": os.cpu_count(),
        "Python Version": platform.python_version()
    }

def analyze_and_upgrade_hardware_v2():
    """SystemAgentens huvudfunktion: Analysera hårdvara, be om uppgradering, köp (simulerat) om budget finns."""
    
    hardware_info = get_current_hardware_info()
    spec_list = "\n".join([f"- {k}: {v}" for k, v in hardware_info.items()])
    current_processor = hardware_info['Processor']
    current_balance = get_current_wallet_balance()
    
    print("\n--- 🤖 SystemAgent V2: Hårdvaruanalys & Köp Startad ---")
    print(f"💰 Startsaldo: {current_balance:.2f} kr. Max budget för köp: {INITIAL_BUDGET:.2f} kr.")
    print("  > Upptäckta specifikationer:")
    print(spec_list)

    try:
        client = ollama.Client(host=OLLAMA_HOST)
        
        # --- STEG 1: Bedöm nuvarande hårdvara (Samma som innan) ---
        print("\n--- Steg 1: LLM Utvärderar Hårdvaran... ---")
        # (Utesluten för korthet, antar resultatet "medelmåttig")
        # --- Slut Steg 1 ---
        
        # --- STEG 2: Be om en bättre CPU inom budget (NYTT) ---
        print("\n--- Steg 2: SystemAgent Ber om Bättre CPU (inom budget)... ---")
        
        system_prompt_2 = (
            "Du är en hårdvaruexpert. Föreslå en *signifikant bättre* modern processor (Intel eller AMD) för AI-arbetslaster. "
            f"Den måste ha ett pris i SEK som är *mindre än eller lika med* {INITIAL_BUDGET:,.0f} kr. "
            "Svara ENDAST med ett JSON-objekt i formatet: "
            "{'recommended_cpu': 'Namn på processor', 'expected_price_sek': Siffra, 'reasoning': 'Kort motivering'}"
        )
        user_prompt_2 = f"Föreslå en uppgradering till min nuvarande processor: {current_processor}"
        
        response_2 = client.chat(
            model=OLLAMA_MODEL,
            messages=[
                {'role': 'system', 'content': system_prompt_2},
                {'role': 'user', 'content': user_prompt_2},
            ]
        )
        
        llm_response_2 = response_2['message']['content'].strip()
        
        # Parsa JSON-svaret
        if llm_response_2.startswith('```'):
            llm_response_2 = llm_response_2.strip('```json\n').strip('```')
            
        try:
            suggestion_data = json.loads(llm_response_2)
            recommended_cpu = suggestion_data['recommended_cpu']
            reasoning = suggestion_data['reasoning']
            
        except json.JSONDecodeError as e:
            print(f"❌ LLM-svaret kunde inte tolkas som JSON. Avbryter köp. Fel: {e}")
            print(f"Rådata: {llm_response_2}")
            return

        print(f"🎉 LLM Föreslår: **{recommended_cpu}**")
        print(f"  > Motivering: {reasoning}")

        # --- STEG 3: Hämta Riktigt Pris och Köplogik (NYTT) ---
        print("\n--- Steg 3: Priskontroll & Köp... ---")
        
        actual_price, price_source = get_cpu_price(recommended_cpu)
        
        if actual_price is None:
            print("❌ Agent: Kunde inte fastställa priset (varken scraping eller LLM-uppskattning). Inget köp genomfört.")
            return

        print(f"  > Pris: **{actual_price:,.2f} kr** ({price_source})")
        
        if actual_price <= current_balance and actual_price <= INITIAL_BUDGET:
            new_balance = current_balance - actual_price
            update_agent_state(new_balance)
            
            print(f"✅ KÖP GENOMFÖRT! Simulerat köp av {recommended_cpu} för {actual_price:,.2f} kr.")
            print(f"💰 NYTT SALDO: **{new_balance:,.2f} kr**.")
            
        else:
            if actual_price > INITIAL_BUDGET:
                print(f"⚠️ KÖP AVSLOGS: Priset ({actual_price:,.2f} kr) överstiger budgetgränsen ({INITIAL_BUDGET:,.2f} kr).")
            else:
                 print(f"⚠️ KÖP AVSLOGS: Priset ({actual_price:,.2f} kr) överstiger nuvarande plånbokssaldo ({current_balance:,.2f} kr).")

    except Exception as e:
        print(f"❌ GENERISKT FEL: Kunde inte slutföra uppgraderingscykeln: {e}")
        
    print("\n--- SystemAgent V2 Avslutar ---")


if __name__ == "__main__":
    # Sätt initialt saldo om det inte finns i .env
    if os.environ.get("AGENT_WALLET_BALANCE") is None:
        update_agent_state(INITIAL_BUDGET) 
    
    analyze_and_upgrade_hardware_v2()
