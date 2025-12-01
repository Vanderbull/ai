import psutil
import platform
from datetime import datetime

def hämta_systeminfo():
    """
    Hämtar och skriver ut grundläggande information om datorns hårdvara och system.
    """
    print("="*40, "System Information", "="*40)

    # 1. System/OS Information
    uname = platform.uname()
    print(f"System: {uname.system}")
    print(f"Nodnamn: {uname.node}")
    print(f"Release: {uname.release}")
    print(f"Version: {uname.version}")
    print(f"Maskin: {uname.machine}")
    print(f"Processor: {uname.processor}")
    print(f"Upptid: {datetime.fromtimestamp(psutil.boot_time()).strftime('%Y-%m-%d %H:%M:%S')}")

    print("\n" + "="*40, "CPU Information", "="*40)
    # 2. CPU Information
    print(f"Fysiska Kärnor: {psutil.cpu_count(logical=False)}")
    print(f"Logiska Kärnor/Trådar: {psutil.cpu_count(logical=True)}")
    print(f"Aktuell CPU-frekvens: {psutil.cpu_freq().current:.2f} Mhz")
    print(f"Total CPU-användning: {psutil.cpu_percent(interval=1)}%")

    print("\n" + "="*40, "Minne (RAM) Information", "="*40)
    # 3. Minne (RAM) Information
    svmem = psutil.virtual_memory()
    total_gb = svmem.total / (1024**3)
    available_gb = svmem.available / (1024**3)

    print(f"Total Minne: {total_gb:.2f} GB")
    print(f"Tillgängligt: {available_gb:.2f} GB")
    print(f"Använt: {svmem.percent}%")

    print("\n" + "="*40, "Disk Information", "="*40)
    # 4. Disk Information (första partitionen)
    # Vi antar att vi kollar den primära roten (C: på Windows, / på Linux/macOS)
    try:
        partition = psutil.disk_partitions()[0].mountpoint
        usage = psutil.disk_usage(partition)

        print(f"Enhet: {partition}")
        print(f"Total Storlek: {usage.total / (1024**3):.2f} GB")
        print(f"Använd Plats: {usage.used / (1024**3):.2f} GB")
        print(f"Ledig Plats: {usage.free / (1024**3):.2f} GB")
        print(f"Användning: {usage.percent}%")
    except IndexError:
        print("Kunde inte hitta någon diskpartition.")


if __name__ == "__main__":
    hämta_systeminfo()
