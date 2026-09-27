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
  visto INTEGER DEFAULT 0,         -- film: visto; serie: vista tutta (anche le stagioni future no)
  stagioni_viste TEXT DEFAULT '',  -- '1,2,3'
  avvisi INTEGER DEFAULT 1,
  aggiunto TEXT
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
    if not c.execute("SELECT 1 FROM liste").fetchone():
        c.execute("INSERT INTO liste (nome, ordine) VALUES ('Da vedere', 1)")
        c.commit()
    return c


def meta(c, chiave, valore=None):
    if valore is None:
        r = c.execute("SELECT valore FROM meta WHERE chiave=?", (chiave,)).fetchone()
        return r[0] if r else None
    c.execute("INSERT INTO meta VALUES (?,?) ON CONFLICT(chiave) DO UPDATE SET valore=excluded.valore",
              (chiave, str(valore)))


def viste(riga):
    """Insieme delle stagioni viste da una riga di `miei`."""
    s = (riga["stagioni_viste"] if riga else "") or ""
    return {int(x) for x in s.split(",") if x.strip().isdigit()}
