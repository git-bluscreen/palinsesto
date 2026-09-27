#!/usr/bin/env python3
"""Palinsesto: film e serie sui servizi di streaming italiani, liste, avvisi
e abbonamenti. https://palinsesto.example.org -> :45090 (dietro un reverse proxy).

Accesso: un solo utente, password (scrypt) + codice TOTP, come il pannello
del watchdog (da cui questo codice e' ripreso). Credenziali in
~/.config/palinsesto/utente.json, create con utente.py dal terminale.
Le richieste sono accettate solo da NPM e da localhost: il firewall del
container dovrebbe gia' garantirlo, questa e' la seconda serratura.
Le copertine passano da /img (cache su disco): il telefono non parla mai con
TMDB e la CSP resta 'self'.
"""
import datetime as dt, hashlib, hmac, json, os, pathlib, re, secrets, sys, threading, time

import pyotp, requests
from flask import Flask, abort, g, redirect, render_template, request, send_file, session, url_for

QUI = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(QUI))
import db, logica, tmdb

CONF        = tmdb.CONF
UTENTE      = CONF / "utente.json"
CACHE_IMG   = db.DATI / "img"
PORTA       = int(os.environ.get("PALINSESTO_PORTA", 45090))
NPM         = "192.0.2.10"
AMMESSI     = {NPM, "127.0.0.1"}     # NPM e localhost (le mie prove passano da un tunnel ssh)
PROVA       = os.environ.get("PALINSESTO_PROVA") == "1"   # solo collaudo: cookie senza Secure
TENTATIVI   = 5
BLOCCO_S    = 15 * 60
DURATA_SESS = dt.timedelta(days=30)   # sul telefono: il codice una volta al mese, non ogni sera
ORE_SCHEDA  = 24                      # una scheda aperta piu' vecchia di cosi' si riscarica
MISURE_IMG  = {"w92", "w154", "w185", "w342", "w500", "w780"}


def chiave_sessione():
    f = CONF / "chiave-sessione"
    if not f.exists():
        CONF.mkdir(mode=0o700, parents=True, exist_ok=True)
        fd = os.open(f, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        os.write(fd, secrets.token_hex(32).encode()); os.close(fd)
    return f.read_text().strip()


app = Flask(__name__)
app.config.update(
    SECRET_KEY=chiave_sessione(),
    SESSION_COOKIE_NAME="palinsesto",
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SECURE=not PROVA,
    SESSION_COOKIE_SAMESITE="Lax",        # Lax: il link della notifica ntfy deve aprire la pagina gia' dentro
    PERMANENT_SESSION_LIFETIME=DURATA_SESS,
    SESSION_REFRESH_EACH_REQUEST=False,
    MAX_CONTENT_LENGTH=64 * 1024,
)


def log(msg):
    print(msg, flush=True)      # stdout -> journal -> Loki


def ip_cliente():
    if request.remote_addr == NPM:
        return request.headers.get("X-Real-IP", request.remote_addr)
    return request.remote_addr


def leggi_utente():
    try:
        return json.loads(UTENTE.read_text())
    except FileNotFoundError:
        return None


def password_giusta(u, pw):
    h = hashlib.scrypt(pw.encode(), salt=bytes.fromhex(u["sale"]),
                       n=u["n"], r=u["r"], p=u["p"], maxmem=128 * 1024 * 1024, dklen=32)
    return hmac.compare_digest(h.hex(), u["hash"])


# Un solo utente: il blocco e' globale, non per indirizzo (stessa scelta del watchdog).
_lock = threading.Lock()
_falliti = []
_ultimo_totp = [0]

def bloccato():
    ora = time.time()
    with _lock:
        _falliti[:] = [t for t in _falliti if ora - t < BLOCCO_S]
        return len(_falliti) >= TENTATIVI


# --- database e TMDB per richiesta --------------------------------------------
def c():
    if "db" not in g:
        g.db = db.apri()
    return g.db


def api():
    if "api" not in g:
        try:
            g.api = tmdb.TMDB()
        except FileNotFoundError:
            g.api = None
    return g.api


@app.teardown_appcontext
def chiudi(_):
    x = g.pop("db", None)
    if x is not None:
        x.close()


def oggi():
    return dt.date.today()


# --- filtri per i modelli -----------------------------------------------------
@app.template_filter("gm")
def f_gm(s):
    if not s:
        return ""
    d = logica.data(s) if isinstance(s, str) else s
    return d.strftime("%d/%m/%Y")


@app.template_filter("fra")
def f_fra(s):
    """'fra 3 giorni', 'oggi', 'ieri'..."""
    if not s:
        return ""
    d = logica.data(s) if isinstance(s, str) else s
    n = (d - oggi()).days
    return {0: "oggi", 1: "domani", -1: "ieri"}.get(n, f"fra {n} giorni" if n > 0 else f"{-n} giorni fa")


@app.template_global()
def img(percorso, misura="w342"):
    return url_for("immagine", misura=misura, nome=percorso.lstrip("/")) if percorso else None


@app.template_global()
def link(tid):
    tipo, n = tid.split(":")
    return url_for("scheda", tipo=tipo, tmdb_id=int(n))


@app.template_filter("euro")
def f_euro(x):
    return f"{x:.2f} €".replace(".", ",") if x is not None else ""


# --- serratura ----------------------------------------------------------------
@app.before_request
def serratura():
    if request.remote_addr not in AMMESSI:
        abort(403)
    if request.endpoint in ("accesso", "static"):
        return
    if not session.get("dentro") or session.get("gen") != (leggi_utente() or {}).get("gen"):
        session.clear()
        return redirect(url_for("accesso", dopo=request.full_path if request.method == "GET" else None))
    if request.method == "POST":
        t = request.headers.get("X-CSRF") or request.form.get("csrf", "")
        if not hmac.compare_digest(t, session.get("csrf", "")):
            abort(400)


@app.after_request
def intestazioni(r):
    r.headers["Content-Security-Policy"] = (
        "default-src 'none'; script-src 'self'; style-src 'self'; img-src 'self' data:; "
        "connect-src 'self'; manifest-src 'self'; form-action 'self'; frame-ancestors 'none'; base-uri 'none'")
    r.headers["X-Content-Type-Options"] = "nosniff"
    r.headers["Referrer-Policy"] = "no-referrer"
    r.headers["X-Frame-Options"] = "DENY"
    if request.endpoint not in ("static", "immagine"):
        r.headers["Cache-Control"] = "no-store"
    return r


@app.context_processor
def comuni():
    return dict(csrf=session.get("csrf", ""), nome_utente=session.get("nome"),
                offerte=logica.OFFERTE, sezione=request.endpoint)


@app.route("/accesso", methods=["GET", "POST"])
def accesso():
    u = leggi_utente()
    if u is None:
        return render_template("accesso.html", errore=None, senza_utente=True)
    dopo = request.values.get("dopo") or ""
    if not dopo.startswith("/") or dopo.startswith("//"):
        dopo = ""
    if request.method == "GET":
        return render_template("accesso.html", errore=None, dopo=dopo)
    ip = ip_cliente()
    if bloccato():
        log(f"accesso RIFIUTATO (bloccato) da {ip}")
        return render_template("accesso.html", errore="Troppi tentativi: riprova fra 15 minuti.", dopo=dopo), 429
    nome = request.form.get("nome", "")
    pw = request.form.get("password", "")
    cod = re.sub(r"\s", "", request.form.get("codice", ""))
    giusto = hmac.compare_digest(nome, u["nome"]) & password_giusta(u, pw)
    totp = pyotp.TOTP(u["totp"])
    passo = None
    for delta in (0, -1, 1):
        n = int(time.time()) // 30 + delta
        if hmac.compare_digest(totp.generate_otp(n), cod):
            passo = n
    with _lock:
        if giusto and passo is not None and passo > _ultimo_totp[0]:
            _ultimo_totp[0] = passo
            ok = True
        else:
            _falliti.append(time.time())
            ok = False
    if not ok:
        log(f"accesso FALLITO da {ip}")
        return render_template("accesso.html", errore="Credenziali o codice non validi.", dopo=dopo), 401
    session.clear()
    session.permanent = True
    session.update(dentro=True, gen=u["gen"], csrf=secrets.token_urlsafe(32), nome=u["nome"])
    log(f"accesso riuscito: {u['nome']} da {ip}")
    return redirect(dopo or url_for("casa"))


@app.route("/esci", methods=["POST"])
def esci():
    log(f"uscita: {session.get('nome')} da {ip_cliente()}")
    session.clear()
    return redirect(url_for("accesso"))


# --- copertine ----------------------------------------------------------------
@app.route("/img/<misura>/<nome>")
def immagine(misura, nome):
    if misura not in MISURE_IMG or not re.fullmatch(r"[A-Za-z0-9_-]{5,64}\.(jpg|png|svg)", nome):
        abort(404)
    f = CACHE_IMG / misura / nome
    if not f.exists():
        try:
            r = requests.get(f"{tmdb.IMG}/{misura}/{nome}", timeout=15)
        except requests.RequestException:
            abort(504)
        if r.status_code != 200 or len(r.content) > 2_000_000:
            abort(404)
        f.parent.mkdir(parents=True, exist_ok=True)
        tmp = f.with_suffix(".tmp")
        tmp.write_bytes(r.content)
        tmp.replace(f)
    tipo = {"jpg": "image/jpeg", "png": "image/png", "svg": "image/svg+xml"}[nome.rsplit(".", 1)[1]]
    r = send_file(f, mimetype=tipo, max_age=30 * 86400)
    return r


# --- aiuti sui miei titoli ----------------------------------------------------
def mio(tid, crea=False):
    r = c().execute("SELECT * FROM miei WHERE titolo_id=?", (tid,)).fetchone()
    if r is None and crea:
        c().execute("INSERT INTO miei (titolo_id, aggiunto) VALUES (?,?)", (tid, oggi().isoformat()))
        r = c().execute("SELECT * FROM miei WHERE titolo_id=?", (tid,)).fetchone()
    return r


def assicura_scheda(tipo, tmdb_id, forza=False):
    """La scheda completa dal database; se manca o e' vecchia, la riscarica.
    Il confronto vale anche qui: gli eventi non dipendono da chi apre la scheda."""
    tid = f"{tipo}:{tmdb_id}"
    t = c().execute("SELECT * FROM titoli WHERE id=?", (tid,)).fetchone()
    vecchia = not t or not t["dettagli"] or not t["aggiornato"] or \
        dt.datetime.fromisoformat(t["aggiornato"]) < dt.datetime.now() - dt.timedelta(hours=ORE_SCHEDA)
    if (vecchia or forza) and api():
        try:
            logica.salva_scheda(c(), api(), tipo, tmdb_id, oggi())
            c().commit()
        except tmdb.ErroreTMDB as e:
            c().rollback()
            log(f"scheda {tid}: {e}")
        t = c().execute("SELECT * FROM titoli WHERE id=?", (tid,)).fetchone()
    return t


def disponibilita(tid):
    """[(servizio o None, provider, offerta, dal)] adesso, servizi seguiti prima."""
    return c().execute("""
        SELECT d.offerta, d.dal, p.id AS pid, p.nome AS provider, p.logo AS plogo,
               s.id AS sid, s.nome AS servizio, s.stato, s.fine, s.seguito
        FROM disponibilita d JOIN provider p ON p.id=d.provider_id
        LEFT JOIN servizi s ON s.id=p.servizio_id
        WHERE d.titolo_id=? AND d.fino IS NULL
        ORDER BY (s.seguito IS NULL OR s.seguito=0), s.ordine,
                 CASE d.offerta WHEN 'flatrate' THEN 0 WHEN 'free' THEN 1 WHEN 'ads' THEN 2 WHEN 'rent' THEN 3 ELSE 4 END,
                 p.priorita""", (tid,)).fetchall()


def abbonato(s):
    return s["stato"] == "attivo" or (s["stato"] == "disdetto" and s["fine"] and logica.data(s["fine"]) >= oggi())


def schede_elenco(righe):
    """Arricchisce righe di titoli con servizi disponibili e stato mio, per le griglie."""
    out = []
    for t in righe:
        m = mio(t["id"])
        dove = logica.servizi_disponibili(c(), t["id"])
        out.append(dict(t=t, mio=m, dove=dove))
    return out


# --- pagine -------------------------------------------------------------------
@app.route("/")
def casa():
    o = oggi()
    logica.scadenze(c(), o); c().commit()
    consigli = logica.consigli(c(), o)
    dv = logica.da_vedere(c(), o)
    prossime = []
    for x in dv.values():
        t = x["t"]
        for n, u in x["in_arrivo"]:
            prossime.append((u, t, f"Stagione {n}"))
        for n, f in x["in_corso"]:
            if t["prossimo_ep"]:
                prossime.append((logica.data(t["prossimo_ep"]), t, f"{t['prossimo_ep_sigla']}"))
    prossime.sort(key=lambda y: y[0])
    eventi = c().execute("""SELECT e.*, t.poster, t.titolo AS nome FROM eventi e LEFT JOIN titoli t ON t.id=e.titolo_id
                            WHERE e.tipo != 'catalogo' AND e.quando >= ? ORDER BY e.quando DESC, e.id DESC LIMIT 12""",
                         ((o - dt.timedelta(days=14)).isoformat(),)).fetchall()
    return render_template("casa.html", consigli=consigli, prossime=prossime[:12], eventi=eventi,
                           ultimo_giro=db.meta(c(), "ultimo_giro"), senza_chiave=api() is None,
                           vuoto=not c().execute("SELECT 1 FROM miei").fetchone())


@app.route("/cerca")
def cerca():
    q = request.args.get("q", "").strip()[:100]
    risultati, errore = [], None
    if q:
        if not api():
            errore = "Chiave TMDB assente: vedi ~/.config/palinsesto/tmdb."
        else:
            try:
                for x in api().cerca(q):
                    tid = logica.salva_base(c(), x["media_type"], x)
                    risultati.append(c().execute("SELECT * FROM titoli WHERE id=?", (tid,)).fetchone())
                c().commit()
            except tmdb.ErroreTMDB as e:
                errore = f"TMDB non risponde ({e})."
    prima = c().execute("SELECT * FROM liste ORDER BY ordine, nome LIMIT 1").fetchone()
    dentro = {r[0] for r in c().execute("SELECT titolo_id FROM lista_titoli WHERE lista_id=?", (prima["id"],))}
    return render_template("cerca.html", q=q, risultati=schede_elenco(risultati), errore=errore,
                           prima=prima, dentro=dentro)


@app.route("/t/<tipo>/<int:tmdb_id>")
def scheda(tipo, tmdb_id):
    if tipo not in ("movie", "tv"):
        abort(404)
    t = assicura_scheda(tipo, tmdb_id, forza=request.args.get("aggiorna") == "1")
    if not t:
        abort(404)
    tid = t["id"]
    m = mio(tid)
    stagioni = c().execute("SELECT * FROM stagioni WHERE titolo_id=? ORDER BY numero", (tid,)).fetchall()
    liste = c().execute("""SELECT l.*, EXISTS(SELECT 1 FROM lista_titoli x WHERE x.lista_id=l.id AND x.titolo_id=?) AS dentro
                           FROM liste l ORDER BY ordine, nome""", (tid,)).fetchall()
    eventi = c().execute("SELECT * FROM eventi WHERE titolo_id=? ORDER BY quando DESC, id DESC LIMIT 10", (tid,)).fetchall()
    return render_template("scheda.html", t=t, m=m, viste=db.viste(m), stagioni=stagioni, liste=liste,
                           disp=disponibilita(tid), eventi=eventi, abbonato=abbonato, o=oggi().isoformat())


@app.route("/t/<tipo>/<int:tmdb_id>", methods=["POST"])
def scheda_azione(tipo, tmdb_id):
    if tipo not in ("movie", "tv"):
        abort(404)
    tid = f"{tipo}:{tmdb_id}"
    if not c().execute("SELECT 1 FROM titoli WHERE id=?", (tid,)).fetchone():
        abort(404)
    az = request.form.get("azione", "")
    ancora = ""
    if az in ("mi_piace", "visto", "avvisi"):
        m = mio(tid, crea=True)
        c().execute(f"UPDATE miei SET {az}=? WHERE titolo_id=?", (0 if m[az] else 1, tid))
    elif az == "stagione":
        n = request.form.get("n", type=int)
        m = mio(tid, crea=True)
        v = db.viste(m)
        v ^= {n}
        c().execute("UPDATE miei SET stagioni_viste=? WHERE titolo_id=?", (",".join(map(str, sorted(v))), tid))
        ancora = "#stagioni"
    elif az == "tutte_viste":
        m = mio(tid, crea=True)
        n = [r[0] for r in c().execute("SELECT numero FROM stagioni WHERE titolo_id=? AND uscita<=?",
                                       (tid, oggi().isoformat()))]
        c().execute("UPDATE miei SET stagioni_viste=? WHERE titolo_id=?", (",".join(map(str, n)), tid))
        ancora = "#stagioni"
    elif az == "lista":
        lid = request.form.get("lista", type=int)
        if not c().execute("SELECT 1 FROM liste WHERE id=?", (lid,)).fetchone():
            abort(400)
        mio(tid, crea=True)
        if c().execute("DELETE FROM lista_titoli WHERE lista_id=? AND titolo_id=?", (lid, tid)).rowcount == 0:
            c().execute("INSERT INTO lista_titoli VALUES (?,?,?)", (lid, tid, oggi().isoformat()))
            c().commit()
            assicura_scheda(tipo, tmdb_id)     # dalla ricerca arriva solo il minimo: serve la scheda per il consigliere
        ancora = "#liste"
    elif az == "rimuovi":
        c().execute("DELETE FROM lista_titoli WHERE titolo_id=?", (tid,))
        c().execute("DELETE FROM miei WHERE titolo_id=?", (tid,))
    else:
        abort(400)
    c().commit()
    torna = request.form.get("torna", "")
    if torna.startswith("/") and not torna.startswith("//"):
        return redirect(torna)
    return redirect(url_for("scheda", tipo=tipo, tmdb_id=tmdb_id) + ancora)


@app.route("/liste")
def liste():
    righe = c().execute("""SELECT l.*, COUNT(x.titolo_id) AS n FROM liste l LEFT JOIN lista_titoli x ON x.lista_id=l.id
                           GROUP BY l.id ORDER BY l.ordine, l.nome""").fetchall()
    speciali = dict(
        piaciuti=c().execute("SELECT COUNT(*) FROM miei WHERE mi_piace=1").fetchone()[0],
        avvisi=c().execute("SELECT COUNT(*) FROM miei WHERE avvisi=1").fetchone()[0],
        tutti=c().execute("SELECT COUNT(*) FROM miei").fetchone()[0])
    return render_template("liste.html", liste=righe, speciali=speciali)


@app.route("/liste", methods=["POST"])
def liste_azione():
    az = request.form.get("azione")
    nome = re.sub(r"[\x00-\x1f\x7f]", "", request.form.get("nome", "")).strip()[:60]
    lid = request.form.get("lista", type=int)
    if az == "nuova" and nome:
        c().execute("INSERT OR IGNORE INTO liste (nome) VALUES (?)", (nome,))
    elif az == "rinomina" and nome and lid:
        c().execute("UPDATE OR IGNORE liste SET nome=? WHERE id=?", (nome, lid))
    elif az == "elimina" and lid:
        # i titoli restano fra i miei (e nelle altre liste): si toglie solo il contenitore
        c().execute("DELETE FROM liste WHERE id=?", (lid,))
    else:
        abort(400)
    c().commit()
    return redirect(url_for("liste"))


@app.route("/liste/<chi>")
def lista(chi):
    filtri = dict(servizio=request.args.get("servizio", type=int),
                  da_vedere=request.args.get("da_vedere") == "1",
                  miei_abb=request.args.get("miei_abb") == "1",
                  tipo=request.args.get("tipo") if request.args.get("tipo") in ("movie", "tv") else None)
    if chi.isdigit():
        l = c().execute("SELECT * FROM liste WHERE id=?", (int(chi),)).fetchone()
        if not l:
            abort(404)
        nome = l["nome"]
        righe = c().execute("""SELECT t.* FROM lista_titoli x JOIN titoli t ON t.id=x.titolo_id
                               WHERE x.lista_id=? ORDER BY x.aggiunto DESC""", (l["id"],)).fetchall()
    elif chi in ("piaciuti", "avvisi", "tutti"):
        l = None
        nome = {"piaciuti": "Mi piace", "avvisi": "Con avvisi", "tutti": "Tutti i miei titoli"}[chi]
        cond = {"piaciuti": "m.mi_piace=1", "avvisi": "m.avvisi=1", "tutti": "1"}[chi]
        righe = c().execute(f"SELECT t.* FROM miei m JOIN titoli t ON t.id=m.titolo_id WHERE {cond} "
                            "ORDER BY m.aggiunto DESC").fetchall()
    else:
        abort(404)
    elenco = schede_elenco(righe)
    servizi = c().execute("SELECT * FROM servizi WHERE seguito=1 ORDER BY ordine").fetchall()
    miei_abb = {s["id"] for s in servizi if abbonato(s)}
    dv = logica.da_vedere(c(), oggi())
    for x in elenco:
        x["dv"] = dv.get(x["t"]["id"])
    if filtri["servizio"]:
        elenco = [x for x in elenco if filtri["servizio"] in x["dove"]]
    if filtri["miei_abb"]:
        elenco = [x for x in elenco if x["dove"].keys() & miei_abb]
    if filtri["da_vedere"]:
        elenco = [x for x in elenco if x["dv"] and x["dv"]["pronte"]]
    if filtri["tipo"]:
        elenco = [x for x in elenco if x["t"]["tipo"] == filtri["tipo"]]
    return render_template("lista.html", l=l, chi=chi, nome=nome, elenco=elenco, servizi=servizi, f=filtri)


@app.route("/novita")
def novita():
    giorni = 30
    dal = (oggi() - dt.timedelta(days=giorni)).isoformat()
    servizio = request.args.get("servizio", type=int)
    mie = c().execute("""SELECT e.*, t.poster, t.titolo AS nome FROM eventi e LEFT JOIN titoli t ON t.id=e.titolo_id
                         WHERE e.tipo != 'catalogo' AND e.quando >= ? ORDER BY e.quando DESC, e.id DESC""", (dal,)).fetchall()
    q = """SELECT t.*, e.quando, e.servizio_id FROM eventi e JOIN titoli t ON t.id=e.titolo_id
           WHERE e.tipo='catalogo' AND e.quando >= ?""" + (" AND e.servizio_id=?" if servizio else "") + \
        " ORDER BY e.quando DESC, e.id DESC LIMIT 200"
    cat = c().execute(q, (dal, servizio) if servizio else (dal,)).fetchall()
    servizi = c().execute("SELECT * FROM servizi WHERE seguito=1 ORDER BY ordine").fetchall()
    base = {r[0]: r[1] for r in c().execute("SELECT servizio_id, MIN(dal) FROM catalogo GROUP BY servizio_id")}
    return render_template("novita.html", mie=mie, catalogo=schede_elenco(cat), servizi=servizi,
                           servizio=servizio, base=base, giorni=giorni)


@app.route("/abbonamenti")
def abbonamenti():
    o = oggi()
    logica.scadenze(c(), o); c().commit()
    consigli = {x["s"]["id"]: x for x in logica.consigli(c(), o)}
    servizi = c().execute("SELECT * FROM servizi ORDER BY seguito DESC, ordine, nome").fetchall()
    mensile = 0.0
    for s in servizi:
        if s["stato"] == "attivo" and s["prezzo"]:
            mensile += s["prezzo"] / (12 if s["ciclo"] == "anno" else 1)
    return render_template("abbonamenti.html", servizi=servizi, consigli=consigli, mensile=mensile, o=o)


def campo_data(nome):
    v = request.form.get(nome, "").strip()
    if not v:
        return None
    try:
        return dt.date.fromisoformat(v).isoformat()
    except ValueError:
        abort(400)


@app.route("/abbonamenti/<int:sid>", methods=["POST"])
def abbonamento_salva(sid):
    if not c().execute("SELECT 1 FROM servizi WHERE id=?", (sid,)).fetchone():
        abort(404)
    stato = request.form.get("stato")
    ciclo = request.form.get("ciclo")
    if stato not in ("attivo", "disdetto", "mai") or ciclo not in ("mese", "anno"):
        abort(400)
    prezzo = request.form.get("prezzo", "").replace(",", ".").strip()
    try:
        prezzo = round(float(prezzo), 2) if prezzo else None
    except ValueError:
        abort(400)
    conserva = request.form.get("conserva_mesi", type=int)
    pulito = lambda k, n: re.sub(r"[\x00-\x1f\x7f]", " ", request.form.get(k, "")).strip()[:n] or None
    c().execute("""UPDATE servizi SET stato=?, prezzo=?, ciclo=?, rinnovo=?, fine=?, canale=?, conserva_mesi=?, note=?
                   WHERE id=?""",
                (stato, prezzo, ciclo, campo_data("rinnovo"), campo_data("fine"), pulito("canale", 60),
                 conserva if conserva and 0 < conserva < 120 else None, pulito("note", 300), sid))
    c().commit()
    log(f"abbonamento {sid}: stato {stato} da {session.get('nome')}")
    return redirect(url_for("abbonamenti") + f"#s{sid}")


@app.route("/impostazioni")
def impostazioni():
    servizi = c().execute("SELECT * FROM servizi ORDER BY seguito DESC, ordine, nome").fetchall()
    provider = c().execute("""SELECT p.*, (SELECT COUNT(DISTINCT titolo_id) FROM disponibilita d WHERE d.provider_id=p.id) AS n
                              FROM provider p ORDER BY p.servizio_id IS NULL, p.priorita, p.nome""").fetchall()
    return render_template("impostazioni.html", servizi=servizi, provider=provider)


@app.route("/impostazioni", methods=["POST"])
def impostazioni_salva():
    az = request.form.get("azione")
    if az == "seguito":
        sid = request.form.get("servizio", type=int)
        c().execute("UPDATE servizi SET seguito=1-seguito WHERE id=?", (sid,))
    elif az == "mappa":
        pid = request.form.get("provider", type=int)
        sid = request.form.get("servizio", type=int) or None
        if sid and not c().execute("SELECT 1 FROM servizi WHERE id=?", (sid,)).fetchone():
            abort(400)
        c().execute("UPDATE provider SET servizio_id=? WHERE id=?", (sid, pid))
    elif az == "nuovo":
        # un servizio nuovo nasce da un provider TMDB: ne prende nome e logo
        pid = request.form.get("provider", type=int)
        p = c().execute("SELECT * FROM provider WHERE id=?", (pid,)).fetchone()
        if not p:
            abort(400)
        cur = c().execute("INSERT OR IGNORE INTO servizi (nome, logo, ordine) VALUES (?,?,50)", (p["nome"], p["logo"]))
        sid = cur.lastrowid or c().execute("SELECT id FROM servizi WHERE nome=?", (p["nome"],)).fetchone()[0]
        c().execute("UPDATE provider SET servizio_id=? WHERE id=?", (sid, pid))
    else:
        abort(400)
    c().commit()
    return redirect(url_for("impostazioni"))


# --- importazione da JustWatch ------------------------------------------------
RIGA_ANNO = re.compile(r"^(.*?)[\s,;(\[-]*((?:19|20)\d\d)[)\]]?\s*$")

@app.route("/importa", methods=["GET", "POST"])
def importa():
    liste = c().execute("SELECT * FROM liste ORDER BY ordine, nome").fetchall()
    if request.method == "GET":
        return render_template("importa.html", liste=liste, proposte=None)
    if request.form.get("azione") == "conferma":
        lid = request.form.get("lista", type=int)
        if not c().execute("SELECT 1 FROM liste WHERE id=?", (lid,)).fetchone():
            abort(400)
        n = 0
        for v in request.form.getlist("scelta"):
            m = re.fullmatch(r"(movie|tv):(\d+)", v)
            if not m:
                continue
            tipo, tmdb_id = m.group(1), int(m.group(2))
            if not c().execute("SELECT 1 FROM titoli WHERE id=?", (v,)).fetchone():
                continue
            mio(v, crea=True)
            c().execute("INSERT OR IGNORE INTO lista_titoli VALUES (?,?,?)", (lid, v, oggi().isoformat()))
            if request.form.get("visti") == "1":
                c().execute("UPDATE miei SET visto=1 WHERE titolo_id=?", (v,))
            n += 1
        c().commit()
        log(f"importati {n} titoli nella lista {lid}")
        return redirect(url_for("lista", chi=lid))
    # primo passo: una proposta per riga, da confermare
    if not api():
        abort(503)
    righe = [r.strip(" \t•-*") for r in request.form.get("testo", "").splitlines()]
    righe = [r for r in righe if r][:150]
    proposte = []
    for r in righe:
        m = RIGA_ANNO.match(r)
        testo, anno = (m.group(1).strip(), int(m.group(2))) if m and m.group(1).strip() else (r, None)
        try:
            cand = api().cerca(testo, anno)[:4]
        except tmdb.ErroreTMDB:
            cand = []
        cand = [c().execute("SELECT * FROM titoli WHERE id=?", (logica.salva_base(c(), x["media_type"], x),)).fetchone()
                for x in cand]
        proposte.append(dict(riga=r, cand=cand))
    c().commit()
    return render_template("importa.html", liste=liste, proposte=proposte,
                           lista=request.form.get("lista", type=int), visti=request.form.get("visti") == "1")


if __name__ == "__main__":
    from waitress import serve
    db.apri().close()
    log(f"Palinsesto in ascolto su 0.0.0.0:{PORTA}" + (" (PROVA)" if PROVA else ""))
    serve(app, host="0.0.0.0", port=PORTA, threads=4, ident=None)
