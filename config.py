"""Configurazione facoltativa, in ~/.config/palinsesto/palinsesto.json:

    {
      "pagina":  "https://palinsesto.example.org",   # indirizzo pubblico: link di notifiche e calendario
      "proxy":   ["192.0.2.10"],                     # reverse proxy fidati: di loro si legge X-Real-IP
      "ammessi": ["192.0.2.0/24"]                    # indirizzi o reti che possono collegarsi direttamente
    }

Senza file (o senza una voce) valgono i predefiniti: pagina su localhost, nessun
proxy, e si accettano solo connessioni da localhost. E' una seconda serratura: la
prima resta il firewall davanti alla macchina.
"""
import ipaddress, json

from tmdb import CONF

FILE = CONF / "palinsesto.json"
PORTA_PREDEFINITA = 45090


def leggi():
    try:
        return json.loads(FILE.read_text())
    except FileNotFoundError:
        return {}


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
