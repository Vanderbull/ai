import subprocess
import platform
import os

def kör_kommando(kommando):
    """
    Kör ett Linux-kommando och returnerar stdout som en sträng.
    Returnerar None om kommandot misslyckas.
    """
    try:
        resultat = subprocess.run(
            kommando,
            shell=True,
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True
        )
        return resultat.stdout.strip()
    except subprocess.CalledProcessError as e:
        # print(f"Varning: Kommandot '{kommando}' misslyckades: {e.stderr.strip()}")
        return None
    except FileNotFoundError:
        # print(f"Varning: Kommandot '{kommando}' hittades inte.")
        return None


def hämta_linux_hårdvara():
    """Hämtar och skriver ut detaljerad hårdvaruinformation med hjälp av Linux-verktyg."""

    if platform.system() != 'Linux':
        print("Detta skript är designat för Linux-system.")
        return

    print("="*40, "System & Modell Information", "="*40)

    # 1. System/Modell (dmidecode kräver ofta sudo, men vi provar att läsa /sys först)
    try:
        # Försök läsa systemmodell utan sudo
        tillverkare = open("/sys/devices/virtual/dmi/id/board_vendor").read().strip()
        modell = open("/sys/devices/virtual/dmi/id/board_name").read().strip()
        print(f"Tillverkare: {tillverkare}")
        print(f"Modell: {modell}")
    except FileNotFoundError:
        print("Systemmodell: Kan inte läsas utan root-rättigheter (/sys/devices...).")

    # 2. BIOS Version (Kräver ofta sudo)
    bios_version = kör_kommando("sudo dmidecode -s bios-version")
    if bios_version:
        print(f"BIOS Version: {bios_version}")
    else:
        print("BIOS Version: Kan inte läsas (Kräver troligen sudo/dmidecode)")

    print("\n" + "="*40, "CPU (Processor) Information", "="*40)
    # 3. CPU Information (lscpu)
    cpu_info = kör_kommando("lscpu | grep 'Model name\\|Architecture\\|CPU(s)\\|Core(s) per socket'")
    if cpu_info:
        for rad in cpu_info.split('\n'):
            delad = rad.split(':', 1)
            if len(delad) == 2:
                print(f"{delad[0].strip()}: {delad[1].strip()}")
    else:
        print("Kunde inte hämta CPU-info med lscpu.")

    print("\n" + "="*40, "Minne (RAM) Information", "="*40)
    # 4. Minne Information (lsmem)
    mem_total = kör_kommando("lsmem | grep 'Total online memory'")
    if mem_total:
        print(mem_total.strip())

    # 5. Minnesdetaljer (t.ex. typ/hastighet, kräver sudo)
    mem_detaljer = kör_kommando("sudo dmidecode --type 17 | grep 'Size:\\|Type:\\|Speed:'")
    if mem_detaljer:
        print("-" * 25)
        print("Detaljer (per modul):")
        print(mem_detaljer)
    else:
        print("Detaljerad minnesinfo: Kan inte läsas (Kräver troligen sudo/dmidecode)")


    print("\n" + "="*40, "Grafikkort (GPU) & PCI Enheter", "="*40)
    # 6. GPU/PCI Enheter (lspci)
    gpu_info = kör_kommando("lspci -v | grep -i 'vga\\|3d controller'")
    if gpu_info:
        print("Huvudsaklig Grafikenhet:")
        print(gpu_info)
    else:
        print("Kunde inte hitta Grafikkort med lspci.")


    print("\n" + "="*40, "Disk (Lagring) Information", "="*40)
    # 7. Disk Information (lsblk)
    disk_info = kör_kommando("lsblk -o NAME,SIZE,VENDOR,MODEL,FSTYPE -e 7") # -e 7 exkluderar loop-enheter
    if disk_info:
        print(disk_info)
    else:
        print("Kunde inte hämta Disk-info med lsblk.")


if __name__ == "__main__":
    hämta_linux_hårdvara()
