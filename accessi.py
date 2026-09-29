"""Accesso a token: niente sessioni sul server.

Due cookie:
- «palinsesto»: token di ACCESSO, un JWT (HS256) che dura DURATA_ACCESSO. Ogni
  richiesta lo verifica solo con la firma, senza toccare il database.
- «palinsesto_rinnovo»: token di RINNOVO, una stringa casuale che il browser
  manda solo a /token (Path del cookie). Vale DURATA_RINNOVO dall'ultimo uso:
  finche' il dispositivo apre Palinsesto almeno una volta in quel tempo, non
  chiede di nuovo password e codice.

La pagina rinnova il token di accesso poco prima che scada (palinsesto.js); una
pagina aperta dopo ore, o il link di una notifica, passa da /token/rinnova e
torna dove voleva andare.

Il token di rinnovo si usa UNA volta: ogni rinnovo ne da' uno nuovo. Se uno gia'
usato si ripresenta dopo TOLLERANZA_RIUSO secondi, qualcuno ne ha una copia: il
dispositivo viene chiuso, e con lui anche la copia. Dentro la tolleranza e' solo
la gara fra due schede che rinnovano insieme: si da' l'accesso senza ruotare.

Sul server restano solo i dispositivi e l'impronta (sha256) dei token di
rinnovo, mai i token. Chiudere un dispositivo ferma i rinnovi; il suo token di
accesso vale ancora al massimo DURATA_ACCESSO. Cambiare utente (utente.py)
cambia `gen` e chiude tutto.
"""
import hashlib, secrets, time

import jwt

DURATA_ACCESSO = 10 * 60
DURATA_RINNOVO = 30 * 24 * 3600
TOLLERANZA_RIUSO = 60
ALG = "HS256"

# le tabelle `dispositivi` e `rinnovi` stanno nello schema di db.py



def impronta(tok):
    return hashlib.sha256(tok.encode()).hexdigest()


def nome_dispositivo(ua):
    """Una descrizione corta dallo User-Agent: «Chrome su Android»."""
    ua = ua or ""
    so = next((n for k, n in (("Android", "Android"), ("iPhone", "iPhone"), ("iPad", "iPad"),
                              ("Windows", "Windows"), ("Mac OS X", "macOS"), ("CrOS", "ChromeOS"),
                              ("Linux", "Linux")) if k in ua), "")
    br = next((n for k, n in (("Firefox/", "Firefox"), ("Edg/", "Edge"), ("OPR/", "Opera"),
                              ("SamsungBrowser/", "Samsung Internet"), ("Chrome/", "Chrome"),
                              ("Safari/", "Safari")) if k in ua), "")
    return " su ".join(x for x in (br, so) if x) or "Dispositivo sconosciuto"


# --- token di accesso -----------------------------------------------------------
def firma_accesso(chiave, d, nome, ora=None):
    """Il JWT di accesso per il dispositivo d (riga di `dispositivi`)."""
    ora = int(ora or time.time())
    return jwt.encode({"sub": nome, "dsp": d["id"], "gen": d["gen"], "csrf": d["csrf"],
                       "iat": ora, "exp": ora + DURATA_ACCESSO}, chiave, algorithm=ALG)


def leggi_accesso(chiave, tok, gen, ora=None):
    """Il contenuto del token se valido (firma, scadenza, gen), altrimenti None."""
    if not tok:
        return None
    try:
        # ora esplicita per i collaudi: PyJWT confronta exp e iat con
        # l'orologio vero, quindi con un'ora finta la scadenza si controlla qui
        v = jwt.decode(tok, chiave, algorithms=[ALG],
                       options={"require": ["exp", "sub", "dsp", "gen", "csrf"],
                                "verify_exp": ora is None, "verify_iat": ora is None})
    except jwt.InvalidTokenError:
        return None
    if ora is not None and v["exp"] <= ora:
        return None
    return v if v["gen"] == gen else None


# --- dispositivi e token di rinnovo ---------------------------------------------
def _nuovo_rinnovo(c, dsp, ora):
    tok = secrets.token_urlsafe(32)
    c.execute("INSERT INTO rinnovi (impronta, dispositivo, scade) VALUES (?,?,?)",
              (impronta(tok), dsp, ora + DURATA_RINNOVO))
    return tok


def entra(c, gen, ua, ip, ora=None):
    """Dopo password e codice: un dispositivo nuovo. Torna (riga, token di rinnovo)."""
    ora = int(ora or time.time())
    dsp = secrets.token_urlsafe(12)
    c.execute("INSERT INTO dispositivi (id, nome, gen, csrf, creato, ultimo_uso, ip) VALUES (?,?,?,?,?,?,?)",
              (dsp, nome_dispositivo(ua), gen, secrets.token_urlsafe(32), ora, ora, ip))
    tok = _nuovo_rinnovo(c, dsp, ora)
    c.commit()
    return c.execute("SELECT * FROM dispositivi WHERE id=?", (dsp,)).fetchone(), tok


def chiudi(c, dsp, ora=None):
    c.execute("UPDATE dispositivi SET chiuso=? WHERE id=? AND chiuso IS NULL", (int(ora or time.time()), dsp))
    c.execute("DELETE FROM rinnovi WHERE dispositivo=?", (dsp,))
    c.commit()


def rinnova(c, tok, gen, ip, ora=None):
    """Controlla un token di rinnovo. Torna (esito, riga del dispositivo, token nuovo):
    esito 'ok' (token nuovo da mettere nel cookie), 'gara' (valido, nessun token
    nuovo: l'ha gia' avuto l'altra richiesta), 'no' (sconosciuto, scaduto,
    dispositivo chiuso o di un'altra gen), 'riuso' (dispositivo appena chiuso)."""
    ora = int(ora or time.time())
    pulisci(c, ora)
    r = c.execute("""SELECT r.*, d.gen, d.chiuso FROM rinnovi r JOIN dispositivi d ON d.id=r.dispositivo
                     WHERE r.impronta=?""", (impronta(tok or ""),)).fetchone()
    if r is None or r["chiuso"] is not None or r["scade"] <= ora:
        return "no", None, None
    if r["gen"] != gen:
        chiudi(c, r["dispositivo"], ora)
        return "no", None, None
    if r["usato"] is not None:
        if ora - r["usato"] <= TOLLERANZA_RIUSO:
            d = c.execute("SELECT * FROM dispositivi WHERE id=?", (r["dispositivo"],)).fetchone()
            return "gara", d, None
        chiudi(c, r["dispositivo"], ora)
        return "riuso", None, None
    c.execute("UPDATE rinnovi SET usato=? WHERE impronta=?", (ora, r["impronta"]))
    c.execute("UPDATE dispositivi SET ultimo_uso=?, ip=? WHERE id=?", (ora, ip, r["dispositivo"]))
    nuovo = _nuovo_rinnovo(c, r["dispositivo"], ora)
    c.commit()
    return "ok", c.execute("SELECT * FROM dispositivi WHERE id=?", (r["dispositivo"],)).fetchone(), nuovo


def pulisci(c, ora):
    """Token scaduti; dispositivi chiusi o fermi da piu' di DURATA_RINNOVO (non
    possono piu' rinnovare). I token gia' usati restano fino alla scadenza: sono
    loro che fanno riconoscere un riuso."""
    c.execute("DELETE FROM rinnovi WHERE scade<=?", (ora,))
    c.execute("DELETE FROM dispositivi WHERE chiuso<? OR ultimo_uso<?", (ora - DURATA_RINNOVO, ora - DURATA_RINNOVO))


def elenco(c):
    return c.execute("SELECT * FROM dispositivi WHERE chiuso IS NULL ORDER BY ultimo_uso DESC").fetchall()
