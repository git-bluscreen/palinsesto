"""Database di Palinsesto: un solo file SQLite, facile da salvare e da spostare.

Dove sta: $PALINSESTO_DATI (predefinito ~/.local/share/palinsesto)/palinsesto.db.
Le date sono stringhe ISO 'AAAA-MM-GG' nel fuso locale (Europe/Rome): SQLite
le confronta come testo e l'ordine e' quello giusto.

Due scelte da ricordare:
- `disponibilita` tiene i provider GREZZI di TMDB (Netflix, «Netflix basic with
  Ads», «Paramount+ Amazon Channel»...), non i servizi: la mappa provider ->
  servizio sta in `provider.servizio_id` e si cambia dalle impostazioni senza
  perdere lo storico.
- Un titolo o un provider che sparisce da TMDB viene chiuso (`fino`) solo dopo
  ASSENZE_PER_CHIUDERE notti di fila: i dati di disponibilita' ogni tanto
  sfarfallano, e un «non e' piu' su Netflix» falso seguito da «arrivato su
  Netflix» il giorno dopo insegna a ignorare gli avvisi.
"""
import os, pathlib, sqlite3

DATI = pathlib.Path(os.environ.get("PALINSESTO_DATI", pathlib.Path.home() / ".local/share/palinsesto"))
FILE = DATI / "palinsesto.db"
ASSENZE_PER_CHIUDERE = 2

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (chiave TEXT PRIMARY KEY, valore TEXT);

CREATE TABLE IF NOT EXISTS servizi (
  id INTEGER PRIMARY KEY,
  nome TEXT NOT NULL UNIQUE,
  logo TEXT,
  ordine INTEGER DEFAULT 100,
  seguito INTEGER DEFAULT 1,
  -- abbonamento
  stato TEXT DEFAULT 'mai' CHECK (stato IN ('attivo','disdetto','mai')),
  prezzo REAL,
  ciclo TEXT DEFAULT 'mese' CHECK (ciclo IN ('mese','anno')),
  rinnovo TEXT,          -- prossimo addebito, se attivo
  fine TEXT,             -- ultimo giorno utile, se disdetto
  canale TEXT,           -- diretto, Prime Video Channels, TIM...
  conserva_mesi INTEGER, -- mesi di cronologia conservata dopo la fine: da verificare, mai inventati
  note TEXT
);

CREATE TABLE IF NOT EXISTS provider (
  id INTEGER PRIMARY KEY,          -- provider_id di TMDB
  nome TEXT NOT NULL,
  logo TEXT,
  priorita INTEGER,
  servizio_id INTEGER REFERENCES servizi(id) ON DELETE SET NULL
);

CREATE TABLE IF NOT EXISTS titoli (
  id TEXT PRIMARY KEY,             -- 'movie:603', 'tv:1399'
  tipo TEXT NOT NULL CHECK (tipo IN ('movie','tv')),
  tmdb_id INTEGER NOT NULL,
  titolo TEXT, originale TEXT, anno INTEGER,
  poster TEXT, sfondo TEXT, trama TEXT, generi TEXT,
  stato TEXT,                      -- tv: Returning Series / Ended / Canceled...
  durata INTEGER,
  prossimo_ep TEXT,                -- data del prossimo episodio annunciato
  prossimo_ep_sigla TEXT,          -- 'S03E04'
  dettagli INTEGER DEFAULT 0,      -- 1 = scheda completa, 0 = solo dati da una ricerca
  aggiornato TEXT,                 -- ultima scheda completa scaricata
  controllato TEXT                 -- ultima data per cui gli eventi sono stati calcolati
);

CREATE TABLE IF NOT EXISTS stagioni (
  titolo_id TEXT REFERENCES titoli(id) ON DELETE CASCADE,
  numero INTEGER, nome TEXT, episodi INTEGER,
  uscita TEXT,                     -- primo episodio
  fine TEXT,                       -- ultimo episodio, se noto
  poster TEXT,
  PRIMARY KEY (titolo_id, numero)
);

CREATE TABLE IF NOT EXISTS episodi (
  titolo_id TEXT REFERENCES titoli(id) ON DELETE CASCADE,
  stagione INTEGER, numero INTEGER,
  nome TEXT, uscita TEXT, durata INTEGER,
  PRIMARY KEY (titolo_id, stagione, numero)
);

-- episodi segnati visti uno per uno; `fonte` dice chi l'ha segnato:
-- 'mano' dalla pagina, 'firetv:<entita>' dal rilevamento automatico...
CREATE TABLE IF NOT EXISTS visti_ep (
  titolo_id TEXT REFERENCES titoli(id) ON DELETE CASCADE,
  stagione INTEGER, numero INTEGER,
  quando TEXT, fonte TEXT,
  PRIMARY KEY (titolo_id, stagione, numero)
);

CREATE TABLE IF NOT EXISTS disponibilita (
  titolo_id TEXT REFERENCES titoli(id) ON DELETE CASCADE,
  provider_id INTEGER,
  offerta TEXT,                    -- flatrate / ads / free / rent / buy
  dal TEXT NOT NULL,
  fino TEXT,                       -- NULL = presente
  assenze INTEGER DEFAULT 0,       -- notti di fila in cui non c'era
  assente_il TEXT,                 -- ultima notte contata: rivedere la scheda di giorno non conta doppio
  PRIMARY KEY (titolo_id, provider_id, offerta, dal)
);

CREATE TABLE IF NOT EXISTS miei (
  titolo_id TEXT PRIMARY KEY REFERENCES titoli(id) ON DELETE CASCADE,
  mi_piace INTEGER DEFAULT 0,
  visto INTEGER DEFAULT 0,         -- solo film. Una serie non e' mai «vista per sempre»: escono stagioni nuove
  stagioni_viste TEXT DEFAULT '',  -- '1,2,3': spunte una per una
  viste_fino TEXT,                 -- serie: viste tutte le stagioni uscite fino a questa data (import, «segna uscite»)
  avvisi INTEGER DEFAULT 1,
  aggiunto TEXT,
  tolto_auto TEXT                  -- data in cui Palinsesto l'ha tolto da «Da vedere» perche' finito
);

CREATE TABLE IF NOT EXISTS liste (
  id INTEGER PRIMARY KEY,
  nome TEXT NOT NULL UNIQUE,
  ordine INTEGER DEFAULT 100
);

CREATE TABLE IF NOT EXISTS lista_titoli (
  lista_id INTEGER REFERENCES liste(id) ON DELETE CASCADE,
  titolo_id TEXT REFERENCES titoli(id) ON DELETE CASCADE,
  aggiunto TEXT,
  PRIMARY KEY (lista_id, titolo_id)
);

CREATE TABLE IF NOT EXISTS eventi (
  id INTEGER PRIMARY KEY,
  chiave TEXT NOT NULL UNIQUE,     -- deduplica: lo stesso fatto non si notifica due volte
  quando TEXT NOT NULL,
  tipo TEXT NOT NULL,
  titolo_id TEXT,
  servizio_id INTEGER,
  testo TEXT NOT NULL,
  notificare INTEGER DEFAULT 1,
  notificato INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS catalogo (
  servizio_id INTEGER REFERENCES servizi(id) ON DELETE CASCADE,
  titolo_id TEXT REFERENCES titoli(id) ON DELETE CASCADE,
  dal TEXT NOT NULL,
  fino TEXT,
  assenze INTEGER DEFAULT 0,
  assente_il TEXT,
  PRIMARY KEY (servizio_id, titolo_id, dal)
);

-- consigli ricalcolati ogni notte dai «consigliati» di TMDB dei miei titoli
CREATE TABLE IF NOT EXISTS consigliati (
  titolo_id TEXT PRIMARY KEY REFERENCES titoli(id) ON DELETE CASCADE,
  punteggio REAL, motivo TEXT,
  servizi TEXT,                    -- id dei servizi seguiti dove si vede, separati da virgola
  calcolato TEXT
);

-- cosa arriva su ogni servizio (non solo i miei titoli), ricalcolato ogni notte
CREATE TABLE IF NOT EXISTS in_arrivo (
  servizio_id INTEGER REFERENCES servizi(id) ON DELETE CASCADE,
  titolo_id TEXT REFERENCES titoli(id) ON DELETE CASCADE,
  data TEXT NOT NULL,
  genere TEXT,                     -- 'nuova serie' / 'stagione' / 'episodi'
  cosa TEXT,                       -- «Stagione 3», «S02E05»...
  calcolato TEXT,
  PRIMARY KEY (servizio_id, titolo_id)
);

-- «Non mi interessa»: mai piu' fra i consigliati
CREATE TABLE IF NOT EXISTS nascosti (
  titolo_id TEXT PRIMARY KEY,
  quando TEXT
);

CREATE INDEX IF NOT EXISTS disp_aperte ON disponibilita(titolo_id) WHERE fino IS NULL;
CREATE INDEX IF NOT EXISTS eventi_quando ON eventi(quando);
CREATE INDEX IF NOT EXISTS catalogo_aperti ON catalogo(servizio_id) WHERE fino IS NULL;
"""


def apri(file=None):
    f = pathlib.Path(file or FILE)
    f.parent.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(f, timeout=30)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA foreign_keys = ON")
    c.execute("PRAGMA journal_mode = WAL")     # la pagina legge mentre il job notturno scrive
    c.executescript(SCHEMA)
    migra(c)
    if not c.execute("SELECT 1 FROM liste").fetchone():
        cur = c.execute("INSERT INTO liste (nome, ordine) VALUES ('Da vedere', 1)")
        meta(c, "lista_da_vedere", cur.lastrowid)
        c.commit()
    return c


def migra(c):
    colonne = {r[1] for r in c.execute("PRAGMA table_info(miei)")}
    if "viste_fino" not in colonne:
        # fino al 27/09 l'import segnava le serie «viste» con visto=1, che le
        # escludeva per sempre anche dalle stagioni future: diventano «viste
        # fino a oggi», il significato che l'utente intendeva
        c.execute("ALTER TABLE miei ADD COLUMN viste_fino TEXT")
        c.execute("UPDATE miei SET viste_fino=date('now','localtime'), visto=0 WHERE visto=1 AND titolo_id LIKE 'tv:%'")
        c.commit()
    if "tolto_auto" not in colonne:
        c.execute("ALTER TABLE miei ADD COLUMN tolto_auto TEXT")
        c.commit()
    if not meta(c, "lista_da_vedere"):
        r = c.execute("SELECT id FROM liste WHERE nome='Da vedere'").fetchone() or \
            c.execute("SELECT id FROM liste ORDER BY ordine, id LIMIT 1").fetchone()
        if r:
            meta(c, "lista_da_vedere", r[0]); c.commit()


def meta(c, chiave, valore=None):
    if valore is None:
        r = c.execute("SELECT valore FROM meta WHERE chiave=?", (chiave,)).fetchone()
        return r[0] if r else None
    c.execute("INSERT INTO meta VALUES (?,?) ON CONFLICT(chiave) DO UPDATE SET valore=excluded.valore",
              (chiave, str(valore)))


def viste(riga):
    """Stagioni spuntate una per una in una riga di `miei`."""
    s = (riga["stagioni_viste"] if riga else "") or ""
    return {int(x) for x in s.split(",") if x.strip().isdigit()}


def vista(numero, uscita, spuntate, viste_fino):
    """Una stagione e' vista se spuntata, o se uscita entro «viste fino al»."""
    return numero in spuntate or bool(viste_fino and uscita and uscita <= viste_fino)


# Tre modi di dire «visto», dal piu' fine al piu' grosso: l'episodio spuntato
# (visti_ep), la stagione spuntata intera (miei.stagioni_viste) e «tutto quello
# uscito fino al» (miei.viste_fino, dall'import). Un episodio e' visto se lo dice
# uno qualunque dei tre. Le due scorciatoie valgono anche per episodi che non
# conosciamo ancora: per questo non si convertono subito in spunte.

def episodi_visti(c, titolo_id, riga):
    """{stagione: {numeri}} degli episodi visti, per qualunque delle tre vie."""
    sv = viste(riga)
    vf = riga["viste_fino"] if riga else None
    out = {}
    for r in c.execute("SELECT stagione, numero FROM visti_ep WHERE titolo_id=?", (titolo_id,)):
        out.setdefault(r[0], set()).add(r[1])
    for e in c.execute("SELECT stagione, numero, uscita FROM episodi WHERE titolo_id=?", (titolo_id,)):
        if vista(e["stagione"], e["uscita"], sv, vf):
            out.setdefault(e["stagione"], set()).add(e["numero"])
    return out


def riepilogo(c, titolo_id, riga, oggi):
    """{stagione: dict} con totale, usciti, visti, da_vedere (episodi usciti non visti),
    noti (episodi conosciuti) e vista (niente da vedere e niente ancora da uscire).
    Senza episodi noti vale la stagione intera."""
    sv = viste(riga)
    vf = riga["viste_fino"] if riga else None
    visti = episodi_visti(c, titolo_id, riga)
    eps = {}
    for e in c.execute("SELECT stagione, numero, uscita FROM episodi WHERE titolo_id=? ORDER BY stagione, numero",
                       (titolo_id,)):
        eps.setdefault(e["stagione"], []).append(e)
    out = {}
    for s in c.execute("SELECT numero, uscita FROM stagioni WHERE titolo_id=?", (titolo_id,)):
        n = s["numero"]
        if eps.get(n):
            tutti = [e["numero"] for e in eps[n]]
            usciti = [e["numero"] for e in eps[n] if e["uscita"] and e["uscita"] <= oggi]
            v = visti.get(n, set()) & set(tutti)
            da = [x for x in usciti if x not in v]
            out[n] = dict(noti=True, totale=len(tutti), usciti=len(usciti), visti=len(v), da_vedere=len(da),
                          vista=bool(usciti) and not da and len(usciti) == len(tutti), numeri_visti=v)
        else:
            vv = vista(n, s["uscita"], sv, vf)
            out[n] = dict(noti=False, totale=None, usciti=None, visti=None, da_vedere=None, vista=vv, numeri_visti=set())
    return out


def materializza(c, titolo_id, riga, oggi):
    """Trasforma le due scorciatoie in spunte per episodio, dove gli episodi sono
    noti, prima di togliere una spunta: togliere l'episodio 3 non deve togliere
    tutta la stagione, ne' tutto cio' che era «visto fino al»."""
    sv = viste(riga)
    vf = riga["viste_fino"] if riga else None
    noti = {r[0] for r in c.execute("SELECT DISTINCT stagione FROM episodi WHERE titolo_id=?", (titolo_id,))}
    for n, numeri in episodi_visti(c, titolo_id, riga).items():
        c.executemany("INSERT OR IGNORE INTO visti_ep VALUES (?,?,?,?,'mano')",
                      [(titolo_id, n, e, oggi) for e in numeri])
    # le stagioni di cui non conosciamo gli episodi restano intere
    resto = {s["numero"] for s in c.execute("SELECT numero, uscita FROM stagioni WHERE titolo_id=?", (titolo_id,))
             if s["numero"] not in noti and vista(s["numero"], s["uscita"], sv, vf)}
    c.execute("UPDATE miei SET stagioni_viste=?, viste_fino=NULL WHERE titolo_id=?",
              (",".join(map(str, sorted(resto))), titolo_id))
