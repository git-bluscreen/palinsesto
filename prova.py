#!/usr/bin/env python3
"""Collaudo della logica di Palinsesto SENZA TMDB e senza ntfy: un finto TMDB
con dati che cambio a mano, notte dopo notte, provocando ogni evento invece di
aspettarlo. Database in una cartella temporanea.

    python3 prova.py        # stampa ogni caso ed esce con 1 al primo che non torna
"""
import copy, datetime as dt, os, pathlib, sys, tempfile

os.environ["PALINSESTO_DATI"] = tempfile.mkdtemp(prefix="palinsesto-prova-")
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import db, logica, aggiorna

D = dt.date(2026, 10, 1)
NETFLIX, NETFLIX_ADS, DISNEY, NOLEGGIO = 8, 1796, 337, 35


def prov(*ids, offerta="flatrate"):
    return {offerta: [{"provider_id": i, "provider_name": f"P{i}", "logo_path": f"/l{i}.png"} for i in ids]}


class Finto:
    chiamate = 0

    def __init__(self):
        self.schede = {
            ("tv", 1): {"id": 1, "name": "Serie Uno", "status": "Returning Series", "overview": "trama",
                        "first_air_date": "2020-01-01", "genres": [], "episode_run_time": [50],
                        "seasons": [{"season_number": 0, "name": "Speciali", "air_date": "2020-01-01", "episode_count": 2},
                                    {"season_number": 1, "air_date": "2020-01-01", "episode_count": 8},
                                    {"season_number": 2, "air_date": "2022-01-01", "episode_count": 8}],
                        "watch/providers": {"results": {"IT": prov(NETFLIX)}}},
            ("movie", 2): {"id": 2, "title": "Film Due", "overview": "trama", "release_date": "2025-05-01",
                           "genres": [], "runtime": 120,
                           "watch/providers": {"results": {"IT": prov(NOLEGGIO, offerta="rent")}}},
        }
        self.episodi = {}          # (id, stagione) -> [date]
        self.catalogo = {NETFLIX: [], DISNEY: []}

    def provider(self, tipo):
        return [{"provider_id": i, "provider_name": n, "logo_path": "/x.png", "display_priority": 1}
                for i, n in [(NETFLIX, "Netflix"), (NETFLIX_ADS, "Netflix basic with Ads"), (DISNEY, "Disney Plus"),
                             (119, "Amazon Prime Video"), (39, "Now TV"), (350, "Apple TV Plus"),
                             (531, "Paramount Plus"), (NOLEGGIO, "Rakuten TV")]]

    def scheda(self, tipo, n):
        return copy.deepcopy(self.schede.get((tipo, n)))

    def stagione(self, n, s):
        return {"episodes": [{"air_date": d} for d in self.episodi.get((n, s), [])]}

    def scopri(self, tipo, ids, pagina, dal):
        r = [x for i in ids for x in self.catalogo.get(i, []) if (x.get("title") and tipo == "movie") or (x.get("name") and tipo == "tv")]
        return {"results": r if pagina == 1 else [], "total_pages": 1}


ERRORI = []

def atteso(nome, eventi, *frammenti, esatti=True):
    ok = all(any(f in e for e in eventi) for f in frammenti) and (not esatti or len(eventi) == len(frammenti))
    print(("ok   " if ok else "NO   ") + nome + ("" if ok else f"\n     attesi {frammenti}\n     avuti  {eventi}"))
    if not ok:
        ERRORI.append(nome)


def notte(c, api, oggi):
    nuovi = logica.scadenze(c, oggi)
    for (tid,) in c.execute("SELECT titolo_id FROM miei").fetchall():
        tipo, n = tid.split(":")
        nuovi += logica.salva_scheda(c, api, tipo, int(n), oggi)[1]
    c.commit()
    return nuovi


c = db.apri()
api = Finto()
aggiorna.aggiorna_provider(c, api, D)
serv = {r["nome"]: r["id"] for r in c.execute("SELECT * FROM servizi")}
mappa = {r["id"]: r["servizio_id"] for r in c.execute("SELECT * FROM provider")}
atteso("Netflix con pubblicità mappato su Netflix", ["x"] if mappa[NETFLIX_ADS] == serv["Netflix"] else [], "x")
atteso("Rakuten non mappato", ["x"] if mappa[NOLEGGIO] is None else [], "x")

for tid in ("tv:1", "movie:2"):
    c.execute("INSERT INTO titoli (id, tipo, tmdb_id) VALUES (?,?,?)", (tid, *tid.split(":")[:1], int(tid.split(":")[1])))
    c.execute("INSERT INTO miei (titolo_id, aggiunto) VALUES (?,?)", (tid, D.isoformat()))
c.commit()

atteso("prima notte = base, nessun evento", notte(c, api, D))
atteso("seconda notte senza cambiamenti", notte(c, api, D + dt.timedelta(1)))

# arrivo del film su Disney+ (abbonamento) -> evento; il noleggio non conta
api.schede[("movie", 2)]["watch/providers"]["results"]["IT"] = {**prov(DISNEY), **prov(NOLEGGIO, offerta="rent")}
atteso("arrivo su Disney+", notte(c, api, D + dt.timedelta(2)), "«Film Due» è arrivato su Disney+")
atteso("lo stesso arrivo non si ripete", notte(c, api, D + dt.timedelta(3)))

# variante Netflix con pubblicita' in piu': stesso servizio, nessun evento
api.schede[("tv", 1)]["watch/providers"]["results"]["IT"] = {**prov(NETFLIX), **prov(NETFLIX_ADS, offerta="ads")}
atteso("variante dello stesso servizio: niente", notte(c, api, D + dt.timedelta(4)))

# la serie sparisce da Netflix: una notte di assenza non basta, due si'
api.schede[("tv", 1)]["watch/providers"]["results"]["IT"] = {}
atteso("una notte di assenza: niente (sfarfallio)", notte(c, api, D + dt.timedelta(5)))
e = notte(c, api, D + dt.timedelta(6))
atteso("seconda notte di assenza: partenza", e, "«Serie Uno» non è più su Netflix")
# riapertura della stessa scheda nello stesso giorno: l'assenza non conta doppio
api.schede[("tv", 1)]["watch/providers"]["results"]["IT"] = prov(NETFLIX)
atteso("torna su Netflix", notte(c, api, D + dt.timedelta(7)), "«Serie Uno» è arrivato su Netflix")
api.schede[("tv", 1)]["watch/providers"]["results"]["IT"] = {}
oggi = D + dt.timedelta(8)
atteso("assenza contata una volta al giorno (1)", notte(c, api, oggi))
atteso("assenza contata una volta al giorno (2, stessa data)", notte(c, api, oggi))
api.schede[("tv", 1)]["watch/providers"]["results"]["IT"] = prov(NETFLIX)
atteso("presente di nuovo: niente, non era stata chiusa", notte(c, api, D + dt.timedelta(9)))

# stagione 3 annunciata con data, poi esce, poi si completa
d10 = D + dt.timedelta(10)
api.schede[("tv", 1)]["seasons"].append({"season_number": 3, "air_date": (d10 + dt.timedelta(5)).isoformat(), "episode_count": 3})
api.episodi[(1, 3)] = [(d10 + dt.timedelta(5 + 7 * i)).isoformat() for i in range(3)]
api.schede[("tv", 1)]["next_episode_to_air"] = {"air_date": (d10 + dt.timedelta(5)).isoformat(), "season_number": 3, "episode_number": 1}
atteso("stagione annunciata", notte(c, api, d10), "stagione 3 annunciata, dal")
x = logica.consigli(c, d10)
nf = next(v for v in x if v["s"]["nome"] == "Netflix")
atteso("consiglio Netflix: stagioni 1-2 da vedere", [nf["verdetto"]], "attiva")
c.execute("UPDATE miei SET stagioni_viste='1,2' WHERE titolo_id='tv:1'"); c.commit()
nf = next(v for v in logica.consigli(c, d10) if v["s"]["nome"] == "Netflix")
atteso("viste 1-2: aspetta la 3", [nf["verdetto"] + " " + nf["testo"]], "aspetta")
atteso("il giorno dell'uscita", notte(c, api, d10 + dt.timedelta(5)), "è iniziata la stagione 3 — su Netflix")
nf = next(v for v in logica.consigli(c, d10 + dt.timedelta(6)) if v["s"]["nome"] == "Netflix")
atteso("stagione in corso: aspetta la fine, non «attiva»", [nf["verdetto"] + " " + nf["testo"]],
       f"aspetta Aspetta fino al {(d10 + dt.timedelta(19)).strftime('%d/%m/%Y')}")
atteso("salto di più notti: completa arriva lo stesso", notte(c, api, d10 + dt.timedelta(25)), "stagione 3 completa")
nf = next(v for v in logica.consigli(c, d10 + dt.timedelta(25)) if v["s"]["nome"] == "Netflix")
atteso("stagione completa: attiva", [nf["verdetto"]], "attiva")

# fine serie
api.schede[("tv", 1)]["status"] = "Ended"
atteso("serie conclusa", notte(c, api, d10 + dt.timedelta(26)), "serie conclusa")

# avvisi spenti: l'evento c'e' ma non si notifica
c.execute("UPDATE miei SET avvisi=0 WHERE titolo_id='movie:2'"); c.commit()
api.schede[("movie", 2)]["watch/providers"]["results"]["IT"] = {**prov(DISNEY), **prov(NETFLIX)}
d40 = d10 + dt.timedelta(30)
atteso("arrivo con avvisi spenti: registrato", notte(c, api, d40), "«Film Due» è arrivato su Netflix")
r = c.execute("SELECT notificare FROM eventi WHERE chiave LIKE 'arrivo:movie:2:%' ORDER BY id DESC").fetchone()
atteso("...ma non da notificare", ["x"] if r["notificare"] == 0 else [], "x")

# abbonamenti: rinnovo passato che avanza, avviso a 3 giorni, cronologia
c.execute("UPDATE servizi SET stato='attivo', rinnovo=?, ciclo='mese', prezzo=13.99 WHERE nome='Netflix'",
          ((d40 - dt.timedelta(days=40)).isoformat(),))
c.commit()
e = logica.scadenze(c, d40)
nuovo = c.execute("SELECT rinnovo FROM servizi WHERE nome='Netflix'").fetchone()[0]
atteso("rinnovo passato avanzato al futuro", [nuovo] if nuovo >= d40.isoformat() else [], nuovo)
c.execute("UPDATE servizi SET rinnovo=? WHERE nome='Netflix'", ((d40 + dt.timedelta(2)).isoformat(),)); c.commit()
atteso("rinnovo fra 2 giorni", logica.scadenze(c, d40), "Netflix: rinnovo il", )
atteso("...una volta sola", logica.scadenze(c, d40 + dt.timedelta(1)))
c.execute("UPDATE servizi SET stato='disdetto', fine=?, conserva_mesi=10 WHERE nome='Disney+'",
          ((d40 - dt.timedelta(days=290)).isoformat(),)); c.commit()
atteso("cronologia a rischio (10 mesi - 30 giorni)", logica.scadenze(c, d40), "Disney+: dal")
atteso("31 marzo + 1 mese = 30 aprile", [str(logica.piu_mesi(dt.date(2026, 3, 31), 1))], "2026-04-30")

# catalogo: prima notte base, poi solo i nuovi
api.catalogo[NETFLIX] = [{"id": 10, "title": "Vecchio"}]
aggiorna.aggiorna_catalogo(c, api, d40)
api.catalogo[NETFLIX] = [{"id": 10, "title": "Vecchio"}, {"id": 11, "name": "Nuova serie"}]
n = aggiorna.aggiorna_catalogo(c, api, d40 + dt.timedelta(1))
atteso("catalogo: base poi una novità", [f"{k}={v}" for k, v in n.items()], "Netflix=1")
api.catalogo[NETFLIX] = [{"id": 11, "name": "Nuova serie"}]
n = aggiorna.aggiorna_catalogo(c, api, d40 + dt.timedelta(2))
api.catalogo[NETFLIX] = [{"id": 10, "title": "Vecchio"}]
n = aggiorna.aggiorna_catalogo(c, api, d40 + dt.timedelta(3))
atteso("catalogo: un titolo che rientra non è nuovo", [f"{k}={v}" for k, v in n.items()])

# notifica: un solo messaggio, poi niente
aggiorna.notifica(c, {"Netflix": 1}, prova=True)
resto = c.execute("SELECT COUNT(*) FROM eventi WHERE notificare=1 AND notificato=0").fetchone()[0]
atteso("dopo la notifica non resta nulla da notificare", [str(resto)] if resto == 0 else [], "0")

print(f"\n{'TUTTO OK' if not ERRORI else f'{len(ERRORI)} CASI SBAGLIATI'} — database in {db.DATI}")
sys.exit(1 if ERRORI else 0)
