"""Il piano sul calendario di Nextcloud, via CalDAV.

Un calendario «abbonamenti» dell'utente Nextcloud `palinsesto` (dedicato: per
revocare basta disattivarlo), condiviso in sola lettura con l'utente vero.
Credenziali in ~/.config/palinsesto/caldav.json {url, utente, password},
da scrivere senza farle comparire a schermo.

Gli eventi hanno UID stabili (`palinsesto-<uid>`): quando il piano cambia, lo
stesso promemoria si sposta; quando non serve piu', si cancella. Si tocca solo
cio' che inizia con «palinsesto-»: il resto del calendario non e' nostro.
"""
import datetime as dt, hashlib, json, re
from xml.sax.saxutils import escape

import requests

import db
from tmdb import CONF

FILE = CONF / "caldav.json"
NOME = "abbonamenti"
PAGINA = "https://palinsesto.example.org/piano"


def conf():
    try:
        return json.loads(FILE.read_text())
    except FileNotFoundError:
        return None


def testo_ics(x):
    return x.replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,").replace("\n", "\\n")


def ics(e):
    """Un evento di un giorno intero; `avviso` = giorni prima, alle 9."""
    g = e["giorno"]
    righe = ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//Palinsesto//IT", "BEGIN:VEVENT",
             f"UID:palinsesto-{e['uid']}@palinsesto.example.org",
             f"DTSTART;VALUE=DATE:{g:%Y%m%d}", f"DTEND;VALUE=DATE:{g + dt.timedelta(days=1):%Y%m%d}",
             f"SUMMARY:{testo_ics(e['titolo'])}", f"DESCRIPTION:{testo_ics(e['testo'] + chr(10) + PAGINA)}",
             f"URL:{PAGINA}", "TRANSP:TRANSPARENT",
             "BEGIN:VALARM", "ACTION:DISPLAY", f"DESCRIPTION:{testo_ics(e['titolo'])}",
             # dall'inizio del giorno (mezzanotte): -P1DT15H = due giorni prima alle 9
             f"TRIGGER:{'-P%dDT15H' % (e['avviso'] - 1) if e['avviso'] else 'PT9H'}", "END:VALARM",
             "END:VEVENT", "END:VCALENDAR"]
    return "\r\n".join(righe) + "\r\n"


class Calendario:
    def __init__(self, cf, timeout=20):
        self.s = requests.Session()
        self.s.auth = (cf["utente"], cf["password"])
        self.utente = cf["utente"]
        self.radice = cf["url"].rstrip("/")
        self.base = f"{self.radice}/calendars/{cf['utente']}/{NOME}/"
        self.timeout = timeout

    def _r(self, metodo, url, atteso, **kw):
        r = self.s.request(metodo, url, timeout=self.timeout, **kw)
        if r.status_code not in atteso:
            raise RuntimeError(f"CalDAV {metodo} -> HTTP {r.status_code}")
        return r

    def assicura(self):
        """Crea il calendario se non c'e'. Ritorna True se l'ha creato."""
        r = self._r("PROPFIND", self.base, (207, 404), headers={"Depth": "0"})
        if r.status_code == 207:
            return False
        corpo = ('<?xml version="1.0" encoding="utf-8"?>'
                 '<c:mkcalendar xmlns:d="DAV:" xmlns:c="urn:ietf:params:xml:ns:caldav" xmlns:a="http://apple.com/ns/ical/">'
                 '<d:set><d:prop><d:displayname>Abbonamenti (Palinsesto)</d:displayname>'
                 '<a:calendar-color>#E0A444</a:calendar-color>'
                 '<c:supported-calendar-component-set><c:comp name="VEVENT"/></c:supported-calendar-component-set>'
                 '</d:prop></d:set></c:mkcalendar>')
        self._r("MKCALENDAR", self.base, (201,), data=corpo.encode(), headers={"Content-Type": "application/xml"})
        return True

    def condividi(self, utente):
        """Condivide il calendario in SOLA LETTURA (formato di condivisione di Nextcloud)."""
        corpo = ('<?xml version="1.0" encoding="utf-8"?><o:share xmlns:d="DAV:" xmlns:o="http://owncloud.org/ns">'
                 f'<o:set><d:href>principal:principals/users/{escape(utente)}</d:href></o:set></o:share>')
        self._r("POST", self.base, (200, 204), data=corpo.encode(), headers={"Content-Type": "application/xml"})

    def elenco(self):
        r = self._r("PROPFIND", self.base, (207,), headers={"Depth": "1"},
                    data=b'<?xml version="1.0"?><d:propfind xmlns:d="DAV:"><d:prop><d:getetag/></d:prop></d:propfind>')
        return {h.rsplit("/", 1)[1] for h in re.findall(r"<d:href>([^<]+\.ics)</d:href>", r.text)}

    def metti(self, nome, testo):
        self._r("PUT", self.base + nome, (201, 204), data=testo.encode(),
                headers={"Content-Type": "text/calendar; charset=utf-8"})

    def togli(self, nome):
        self._r("DELETE", self.base + nome, (204, 404))


def sincronizza(c, eventi, cal=None):
    """Porta il calendario a coincidere con `eventi`. Scrive solo cio' che e'
    cambiato (impronta in meta, senza DTSTAMP che cambia sempre). Ritorna
    (scritti, tolti). Registra l'esito in meta «calendario_esito»."""
    cf = conf()
    if cal is None:
        if not cf:
            return None
        cal = Calendario(cf)
    try:
        cal.assicura()
        voluti = {f"palinsesto-{e['uid']}.ics": ics(e) for e in eventi}
        ci_sono = cal.elenco()
        scritti = tolti = 0
        for nome, testo in voluti.items():
            h = hashlib.sha256(testo.encode()).hexdigest()[:16]
            if nome not in ci_sono or db.meta(c, "cal:" + nome) != h:
                cal.metti(nome, testo.replace("BEGIN:VEVENT\r\n", f"BEGIN:VEVENT\r\nDTSTAMP:{dt.datetime.now(dt.timezone.utc):%Y%m%dT%H%M%SZ}\r\n"))
                db.meta(c, "cal:" + nome, h); scritti += 1
        for nome in sorted(ci_sono):
            if nome.startswith("palinsesto-") and nome not in voluti:
                cal.togli(nome); c.execute("DELETE FROM meta WHERE chiave=?", ("cal:" + nome,)); tolti += 1
        db.meta(c, "calendario_esito", json.dumps(dict(quando=dt.datetime.now().isoformat(timespec="seconds"),
                                                       ok=True, eventi=len(voluti), scritti=scritti, tolti=tolti)))
        c.commit()
        return scritti, tolti
    except (requests.RequestException, RuntimeError) as e:
        c.rollback()
        db.meta(c, "calendario_esito", json.dumps(dict(quando=dt.datetime.now().isoformat(timespec="seconds"),
                                                       ok=False, errore=str(e)[:200])))
        c.commit()
        raise


def aggiorna(c, oggi=None):
    """Ricalcola il piano come la pagina e sincronizza. None se non configurato."""
    import logica
    oggi = oggi or dt.date.today()
    ore = int(db.meta(c, "ore_mese") or logica.ORE_MESE)
    p = logica.piano(c, oggi, ore, ancora=logica.ancora_piano(c, oggi))
    return sincronizza(c, logica.promemoria(c, oggi, p))


if __name__ == "__main__":
    # a mano, da terminale:  python3 calendario.py            sincronizza adesso
    #                       python3 calendario.py --condividi NOME   condivide in sola lettura
    import sys
    c = db.apri()
    if len(sys.argv) == 3 and sys.argv[1] == "--condividi":
        cal = Calendario(conf()); cal.assicura(); cal.condividi(sys.argv[2])
        print(f"condiviso in sola lettura con {sys.argv[2]}")
    print("calendario:", aggiorna(c))
