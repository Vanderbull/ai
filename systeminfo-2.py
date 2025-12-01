import psutil
import platform
from datetime import datetime

# För mer detaljerad Hårdvaruinfo på Windows
try:
    import wmi
    # Initialisera WMI-objektet globalt
    C = wmi.WMI()
    WINDOWS = True
except ImportError:
    WINDOWS = False
except AttributeError:
    # Kan hända i vissa miljöer, t.ex. när scriptet körs med vissa behörigheter
    WINDOWS = False


def formatera_byte_storlek(bytes, suffix="B"):
    """Konverterar byte till läsbara enheter (KB, MB, GB, TB)."""
    for unit in ("", "Ki", "Mi", "Gi", "Ti", "Pi", "Ei", "Zi"):
        if bytes < 1024:
            return f"{bytes:.2f} {unit}{suffix}"
        bytes /= 1024
    return f"{bytes:.2f} Yi{suffix}"


def hämta_systeminfo():
    """Hämtar och skriver ut detaljerad information om datorns hårdvara och system."""

    print("="*40, "System/OS Information", "="*40)

    # 1. System/OS Information & Modeller
    uname = platform.uname()
    print(f"System: {uname.system} ({uname.version})")
    print(f"Processor Arkitektur: {uname.machine}")
    print(f"Nodnamn: {uname.node}")
    print(f"Upptid: {datetime.fromtimestamp(psutil.boot_time()).strftime('%Y-%m-%d %H:%M:%S')}")

    if WINDOWS:
        # Hämta detaljerad modellinfo via WMI (Windows-specifikt)
        system = C.Win32_ComputerSystem()[0]
        bios = C.Win32_BIOS()[0]
        print("-" * 25)
        print(f"System Tillverkare: {system.Manufacturer}")
        print(f"System Modell: {system.Model}")
        print(f"BIOS Version: {bios.SMBIOSBIOSVersion}")
        print("-" * 25)
    
    print("\n" + "="*40, "CPU Information", "="*40)
    # 2. CPU Information
    print(f"CPU Namn: {uname.processor}")
    print(f"Fysiska Kärnor: {psutil.cpu_count(logical=False)}")
    print(f"Logiska Kärnor/Trådar: {psutil.cpu_count(logical=True)}")
    print(f"Max Frekvens: {psutil.cpu_freq().max:.2f} Mhz")
    print(f"Aktuell Användning: {psutil.cpu_percent(interval=1)}%")

    print("\n" + "="*40, "Minne (RAM) Information", "="*40)
    # 3. Minne (RAM) Information
    svmem = psutil.virtual_memory()
    print(f"Total Minne: {formatera_byte_storlek(svmem.total)}")
    print(f"Tillgängligt: {formatera_byte_storlek(svmem.available)}")
    print(f"Använt: {svmem.percent}%")

    print("\n" + "="*40, "Grafikkort (GPU) Information", "="*40)
    # 4. GPU Information (Kräver Windows/WMI)
    if WINDOWS:
        try:
            for gpu in C.Win32_VideoController():
                print(f"GPU Modell: {gpu.Name}")
                print(f"Drivrutinsversion: {gpu.DriverVersion}")
                print(f"RAM: {formatera_byte_storlek(int(gpu.AdapterRAM))}")
                print("-" * 25)
        except Exception as e:
            print(f"Kunde inte hämta GPU-info via WMI: {e}")
    else:
        print("Detaljerad GPU-information kräver WMI (Windows).")

    print("\n" + "="*40, "Disk Information", "="*40)
    # 5. Disk Information
    try:
        for partition in psutil.disk_partitions():
            try:
                usage = psutil.disk_usage(partition.mountpoint)
                print("-" * 25)
                print(f"Enhet: {partition.mountpoint}")
                print(f"Filssystem: {partition.fstype}")
                print(f"Total Storlek: {formatera_byte_storlek(usage.total)}")
                print(f"Användning: {usage.percent}%")
            except PermissionError:
                continue
    except IndexError:
        print("Kunde inte hitta någon diskpartition.")

if __name__ == "__main__":
    hämta_systeminfo()
