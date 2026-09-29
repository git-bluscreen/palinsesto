"""Configurazione facoltativa, in ~/.config/palinsesto/palinsesto.json:

    {
      "pagina":  "https://palinsesto.example.org",   # indirizzo pubblico: link di notifiche e calendario
      "proxy":   ["192.0.2.10"],                     # reverse proxy fidati: di loro si legge X-Real-IP
      "ammessi": ["192.0.2.0/24"]                    # indirizzi o reti che possono collegarsi direttamente
    }

Le stesse voci si possono dare con variabili d'ambiente, che vincono sul file
(in Docker e' il modo naturale): PALINSESTO_PAGINA, PALINSESTO_PROXY e
PALINSESTO_AMMESSI (liste separate da virgole). PALINSESTO_HTTP=1 permette
l'accesso anche senza HTTPS (cookie di sessione senza «Secure»): solo per
provarlo in una rete di cui ti fidi, mai esposto.

Senza file (o senza una voce) valgono i predefiniti: pagina su localhost, nessun
proxy, e si accettano solo connessioni da localhost. E' una seconda serratura: la
prima resta il firewall davanti alla macchina.
"""
import ipaddress, json, os

from tmdb import CONF

FILE = CONF / "palinsesto.json"
PORTA_PREDEFINITA = 45090


def leggi():
    try:
        voci = json.loads(FILE.read_text())
    except FileNotFoundError:
        voci = {}
    for chiave, lista in (("pagina", False), ("proxy", True), ("ammessi", True)):
        v = os.environ.get("PALINSESTO_" + chiave.upper())
        if v:
            voci[chiave] = [x.strip() for x in v.split(",") if x.strip()] if lista else v.strip()
    return voci


def solo_https():
    return os.environ.get("PALINSESTO_HTTP") != "1"


def pagina():
    return (leggi().get("pagina") or f"http://localhost:{PORTA_PREDEFINITA}").rstrip("/")


def proxy():
    return {str(ipaddress.ip_address(x)) for x in leggi().get("proxy", [])}


def reti_ammesse():
    """Reti da cui si accettano connessioni: localhost, i proxy e gli «ammessi»."""
    reti = [ipaddress.ip_network("127.0.0.1/32"), ipaddress.ip_network("::1/128")]
    reti += [ipaddress.ip_network(x, strict=False) for x in proxy()]
    reti += [ipaddress.ip_network(x, strict=False) for x in leggi().get("ammessi", [])]
    return reti


def ammesso(indirizzo, reti):
    try:
        ip = ipaddress.ip_address(indirizzo)
    except ValueError:
        return False
    return any(ip in r for r in reti)
