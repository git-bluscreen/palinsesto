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
                        "first_air_date": "2020-01-01", "genres": [{"id": 10765, "name": "Sci-Fi & Fantasy"}], "episode_run_time": [50],
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
        return {"episodes": [{"air_date": d, "episode_number": i + 1, "name": f"Ep {i + 1}"}
                             for i, d in enumerate(self.episodi.get((n, s), []))]}

    def consigliati(self, tipo, n):
        buono = dict(vote_count=500, vote_average=7.5)
        if (tipo, n) == ("tv", 1):
            return [dict(id=50, name="Consigliata Netflix", media_type="tv", **buono), dict(id=51, name="Senza servizio", media_type="tv", **buono),
                    dict(id=52, name="Voto basso", media_type="tv", vote_count=500, vote_average=4.0), dict(id=2, title="Film Due", media_type="movie", **buono),
                    dict(id=53, name="Nascosta", media_type="tv", **buono),
                    dict(id=60, name="Torna con la 2", media_type="tv", vote_count=3, vote_average=8.0)]   # pochi voti: non consigliata, ma somiglia
        if (tipo, n) == ("tv", 53):          # il nascosto insegna in negativo
            return [dict(id=62, name="Originale nuova", media_type="tv", **buono)]
        return [dict(id=50, name="Consigliata Netflix", media_type="tv", **buono)]   # TMDB dice sempre il tipo

    def provider_di(self, tipo, n):
        return {50: prov(NETFLIX), 53: prov(NETFLIX), 52: prov(NETFLIX)}.get(n, {})

    oggi_finto = None       # la data del giro, per gli arrivi

    def get(self, percorso, **p):
        o = self.oggi_finto
        piu = lambda n: (o + dt.timedelta(days=n)).isoformat()
        if percorso == "/discover/tv" and "with_watch_providers" in p:
            netflix = "8" in p["with_watch_providers"].split("|")
            return {"results": [dict(id=60, name="Torna con la 2", genre_ids=[10765]), dict(id=61, name="Settimanale"),
                                dict(id=63, name="Troppo lontana")] if netflix else []}
        if percorso == "/discover/tv" and "with_networks" in p:
            return {"results": [dict(id=62, name="Originale nuova", first_air_date=piu(20), genre_ids=[80])] if p["with_networks"] == 213 else []}
        prossimi = {60: dict(air_date=piu(10), season_number=2, episode_number=1),
                    61: dict(air_date=piu(3), season_number=4, episode_number=5),
                    63: dict(air_date=piu(90), season_number=1, episode_number=1)}
        if percorso.startswith("/tv/"):
            n = int(percorso.split("/")[2])
            return {"next_episode_to_air": prossimi.get(n), "genres": [{"id": 80}] if n == 53 else []}
        return None

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
c.execute("UPDATE miei SET viste_fino=? WHERE titolo_id='tv:1'", (d10.isoformat(),)); c.commit()
nf = next(v for v in logica.consigli(c, d10) if v["s"]["nome"] == "Netflix")
atteso("«viste fino a oggi» (import): aspetta la 3, non è vista", [nf["verdetto"] + " " + nf["testo"]], "aspetta")
riga = c.execute("SELECT * FROM miei WHERE titolo_id='tv:1'").fetchone()
atteso("«viste fino a oggi» copre solo le uscite",
       [str(sorted(n for n, r in db.riepilogo(c, "tv:1", riga, d10.isoformat()).items() if r["vista"]))], "[1, 2]")
c.execute("UPDATE miei SET viste_fino=NULL, stagioni_viste='1,2' WHERE titolo_id='tv:1'"); c.commit()
nf = next(v for v in logica.consigli(c, d10) if v["s"]["nome"] == "Netflix")
atteso("viste 1-2: aspetta la 3", [nf["verdetto"] + " " + nf["testo"]], "aspetta")
atteso("il giorno dell'uscita", notte(c, api, d10 + dt.timedelta(5)), "è iniziata la stagione 3 — su Netflix")
nf = next(v for v in logica.consigli(c, d10 + dt.timedelta(6)) if v["s"]["nome"] == "Netflix")
atteso("stagione in corso: aspetta la fine, non «attiva»", [nf["verdetto"] + " " + nf["testo"]],
       f"aspetta Aspetta fino al {(d10 + dt.timedelta(19)).strftime('%d/%m/%Y')}")
atteso("salto di più notti: completa arriva lo stesso", notte(c, api, d10 + dt.timedelta(25)), "stagione 3 completa")
d35 = d10 + dt.timedelta(25)
nf = next(v for v in logica.consigli(c, d35) if v["s"]["nome"] == "Netflix")
atteso("stagione completa: attiva", [nf["verdetto"]], "attiva")
atteso("episodi della stagione 3 salvati", [str(c.execute("SELECT COUNT(*) FROM episodi WHERE titolo_id='tv:1' AND stagione=3").fetchone()[0])], "3")
dv = logica.da_vedere(c, d35)["tv:1"]
atteso("3 episodi da vedere", [str(dv["episodi"])], "3")
# un episodio visto: ne restano 2, il verdetto non cambia
c.execute("INSERT INTO visti_ep VALUES ('tv:1', 3, 1, ?, 'mano')", (d35.isoformat(),)); c.commit()
atteso("visto l'episodio 1: 2 da vedere", [str(logica.da_vedere(c, d35)["tv:1"]["episodi"])], "2")
# stagione 3 spuntata intera, poi tolgo solo l'episodio 2: restano visti 1 e 3, e le stagioni 1-2
c.execute("UPDATE miei SET stagioni_viste='1,2,3' WHERE titolo_id='tv:1'"); c.commit()
atteso("stagione intera spuntata: niente da vedere", [str("tv:1" in logica.da_vedere(c, d35))], "False")
riga = c.execute("SELECT * FROM miei WHERE titolo_id='tv:1'").fetchone()
db.materializza(c, "tv:1", riga, d35.isoformat())
c.execute("DELETE FROM visti_ep WHERE titolo_id='tv:1' AND stagione=3 AND numero=2"); c.commit()
riga = c.execute("SELECT * FROM miei WHERE titolo_id='tv:1'").fetchone()
r = db.riepilogo(c, "tv:1", riga, d35.isoformat())
atteso("togliere l'ep. 2 lascia 1 e 3, e le stagioni 1-2", [f"{sorted(r[3]['numeri_visti'])} {r[1]['vista']} {r[2]['vista']}"], "[1, 3] True True")
atteso("...e l'episodio 2 torna da vedere", [str(logica.da_vedere(c, d35)["tv:1"]["episodi"])], "1")
c.execute("UPDATE miei SET stagioni_viste='1,2', viste_fino=NULL WHERE titolo_id='tv:1'")
c.execute("DELETE FROM visti_ep WHERE titolo_id='tv:1'"); c.commit()

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

# «Da vedere» si tiene in ordine da sola
lid = int(db.meta(c, "lista_da_vedere"))
for tid in ("movie:2", "tv:1"):
    c.execute("INSERT OR IGNORE INTO lista_titoli VALUES (?,?,?)", (lid, tid, d40.isoformat()))
c.execute("UPDATE miei SET visto=0 WHERE titolo_id='movie:2'"); c.commit()
sync = lambda d: logica.sincronizza_da_vedere(c, d)
atteso("niente di finito: niente da togliere", sync(d40)[0])
c.execute("UPDATE miei SET visto=1 WHERE titolo_id='movie:2'")
atteso("film visto: esce da «Da vedere»", sync(d40)[0], "Film Due")
c.execute("UPDATE miei SET visto=0 WHERE titolo_id='movie:2'")
atteso("spunta tolta: rientra", sync(d40)[1], "Film Due")
c.execute("DELETE FROM lista_titoli WHERE lista_id=? AND titolo_id='movie:2'", (lid,))
atteso("tolto a mano: non rientra", sync(d40)[1])
c.execute("INSERT INTO lista_titoli VALUES (?,?,?)", (lid, "movie:2", d40.isoformat()))
atteso("serie con la stagione 3 da vedere: resta", sync(d40)[0])
c.execute("UPDATE miei SET stagioni_viste='1,2,3' WHERE titolo_id='tv:1'")
atteso("serie tutta vista: esce", sync(d40)[0], "Serie Uno")
c.commit()
d50 = d40 + dt.timedelta(10)
api.schede[("tv", 1)]["status"] = "Returning Series"
api.schede[("tv", 1)]["seasons"].append({"season_number": 4, "air_date": (d50 - dt.timedelta(3)).isoformat(), "episode_count": 1})
api.episodi[(1, 4)] = [(d50 - dt.timedelta(3)).isoformat()]
notte(c, api, d50)
atteso("stagione 4 uscita: la serie rientra da sola", sync(d50)[1], "Serie Uno")
c.commit()

# in arrivo sulle piattaforme
api.oggi_finto = d50
conta = aggiorna.aggiorna_in_arrivo(c, api, d50); c.commit()
arr = {r["titolo_id"]: (r["genere"], r["cosa"]) for r in c.execute("SELECT * FROM in_arrivo WHERE servizio_id=(SELECT id FROM servizi WHERE nome='Netflix')")}
atteso("arrivi Netflix: stagione nuova e serie nuova; l'episodio di una serie non mia no", [str(sorted(arr.items()))],
       "[('tv:60', ('stagione', 'Stagione 2')), ('tv:62', ('nuova serie', 'Nuova serie'))]")
atteso("...oltre 60 giorni esclusa, altri servizi vuoti", [f"{conta['Netflix']} {conta['Disney+']}"], "2 0")
c.execute("INSERT OR IGNORE INTO titoli (id, tipo, tmdb_id, titolo) VALUES ('tv:61','tv',61,'Settimanale')")
c.execute("INSERT INTO miei (titolo_id, aggiunto) VALUES ('tv:61', ?)", (d50.isoformat(),))
conta = aggiorna.aggiorna_in_arrivo(c, api, d50); c.commit()
atteso("se la serie è mia, anche il singolo episodio", [str(c.execute("SELECT cosa FROM in_arrivo WHERE titolo_id='tv:61'").fetchone()[0])], "S04E05")

# consigliati
c.execute("INSERT INTO titoli (id, tipo, tmdb_id, titolo) VALUES ('tv:53','tv',53,'Nascosta')")
c.execute("INSERT INTO nascosti VALUES ('tv:53', ?)", (d50.isoformat(),))
c.execute("UPDATE miei SET mi_piace=1 WHERE titolo_id='tv:1'"); c.commit()
n = logica.calcola_consigliati(c, api, d50); c.commit()
k = [(r["titolo"], r["motivo"]) for r in c.execute("SELECT t.titolo, k.motivo FROM consigliati k JOIN titoli t ON t.id=k.titolo_id")]
atteso("consigliati: solo quello su un mio servizio, col motivo", [f"{t} {m}" for t, m in k], "Consigliata Netflix per «Serie Uno» e altri")   # quanti «altri» dipende dai titoli dei casi precedenti
atteso("...niente miei, nascosti, voto basso o senza servizio", [str(n)], "1")

# gusti: 👍 e 👎 insegnano cosa proporre fra gli arrivi
atteso("i generi del nascosto si completano (salvato prima dei generi)", [str(logica.completa_generi(c, api)),
       c.execute("SELECT generi_id FROM titoli WHERE id='tv:53'").fetchone()[0]], "1", "80")
logica.calcola_consigliati(c, api, d50); c.commit()
som = {r[0]: r[1] for r in c.execute("SELECT titolo_id, punti FROM somiglianze")}
atteso("somiglianze: anche senza voti (serie nuove), e il nascosto in negativo",
       [f"{som.get('tv:60', 0) > 0} {som.get('tv:62', 0) < 0}"], "True True")
g = logica.gusti(c, d50)
atteso("gusti: Sci-Fi su (Serie Uno, mi piace), Crime giù (il nascosto)", [f"{g[10765] > 0} {g[80] < 0}"], "True True")
righe = c.execute("SELECT * FROM titoli WHERE id IN ('tv:60','tv:62')").fetchall()
it = logica.interesse(c, righe, g)
atteso("interesse: quella simile ai miei «potrebbe piacerti», col motivo", [f"{it['tv:60'][0] >= logica.INTERESSE_SI} {it['tv:60'][1]}"], "True come «Serie Uno»")
atteso("...quella simile al nascosto in basso", [str(it["tv:62"][0] <= logica.INTERESSE_NO)], "True")

# «non c'e' davvero»: Netflix ignorato per Serie Uno finche' TMDB lo elenca
netflix = c.execute("SELECT id FROM servizi WHERE nome='Netflix'").fetchone()[0]
api.schede[("tv", 1)]["watch/providers"]["results"]["IT"] = prov(NETFLIX, DISNEY)
notte(c, api, d50 + dt.timedelta(1))
c.execute("INSERT INTO correzioni VALUES ('tv:1', ?, ?)", (netflix, d50.isoformat())); c.commit()
atteso("correzione: Netflix non conta piu' per Serie Uno", [str(sorted(logica.servizi_disponibili(c, "tv:1").values()))], "['Disney+']")
atteso("...ma TMDB lo elenca ancora (grezzi)", [str(sorted(logica.servizi_disponibili(c, "tv:1", grezzi=True).values()))], "['Disney+', 'Netflix']")
notte(c, api, d50 + dt.timedelta(2))
atteso("...finche' TMDB lo dice, la correzione resta", [str(c.execute("SELECT COUNT(*) FROM correzioni").fetchone()[0])], "1")
api.schede[("tv", 1)]["watch/providers"]["results"]["IT"] = prov(DISNEY)
ev_via = notte(c, api, d50 + dt.timedelta(3)) + notte(c, api, d50 + dt.timedelta(4))
atteso("...TMDB smette di elencarlo: nessun «non e' piu' su Netflix» (per te non c'era)", [e for e in ev_via if "Netflix" in e])
atteso("...e la correzione si toglie da sola", [str(c.execute("SELECT COUNT(*) FROM correzioni").fetchone()[0])], "0")
api.schede[("tv", 1)]["watch/providers"]["results"]["IT"] = prov(NETFLIX, DISNEY)
atteso("...se poi arriva davvero, e' un arrivo vero", notte(c, api, d50 + dt.timedelta(5)), "«Serie Uno» è arrivato su Netflix", esatti=False)

# notifica: un solo messaggio, poi niente
aggiorna.notifica(c, {"Netflix": 1}, prova=True)
resto = c.execute("SELECT COUNT(*) FROM eventi WHERE notificare=1 AND notificato=0").fetchone()[0]
atteso("dopo la notifica non resta nulla da notificare", [str(resto)] if resto == 0 else [], "0")

# piano di rotazione: database a parte, scenario costruito a mano
pc = db.apri(pathlib.Path(os.environ["PALINSESTO_DATI"]) / "piano.db")
iso = lambda n: (D + dt.timedelta(n)).isoformat()
for sid, nome, stato, prezzo, ciclo, rinnovo in [(1, "Mensile", "attivo", 9.99, "mese", iso(20)), (2, "Spento", "mai", 5.99, "mese", None),
                                                 (3, "Annuale", "attivo", 50, "anno", iso(300)), (4, "Senza prezzo", "mai", None, "mese", None)]:
    pc.execute("INSERT INTO servizi (id, nome, stato, prezzo, ciclo, rinnovo) VALUES (?,?,?,?,?,?)", (sid, nome, stato, prezzo, ciclo, rinnovo))
    pc.execute("INSERT INTO provider (id, nome, servizio_id) VALUES (?,?,?)", (sid, nome, sid))

def serie(n, nome, dove, date, durata=50):
    tid = f"tv:{n}"
    pc.execute("INSERT INTO titoli (id, tipo, tmdb_id, titolo, dettagli) VALUES (?,?,?,?,1)", (tid, "tv", n, nome))
    pc.execute("INSERT INTO stagioni (titolo_id, numero, episodi, uscita) VALUES (?,1,?,?)", (tid, len(date), date[0]))
    for i, d in enumerate(date):
        pc.execute("INSERT INTO episodi VALUES (?,1,?,?,?,?)", (tid, i + 1, f"Ep {i + 1}", d, durata))
    pc.execute("INSERT INTO miei (titolo_id, aggiunto) VALUES (?,?)", (tid, iso(0)))
    for p in dove:
        pc.execute("INSERT INTO disponibilita (titolo_id, provider_id, offerta, dal) VALUES (?,?,'flatrate',?)", (tid, p, iso(-100)))

serie(101, "Sul mensile", [1], [iso(-60)] * 10)                        # 8,3 h, pronta
serie(102, "Sullo spento", [2], [iso(-60)] * 6)                        # 5 h, pronta
serie(103, "Sullo spento, finisce dopo", [2], [iso(-10 + 7 * i) for i in range(8)])   # completa a +39
serie(105, "Senza prezzo", [4], [iso(-30)] * 7)                        # nient'altro in arrivo
serie(106, "Da nessuna parte", [], [iso(-30)] * 3)
pc.execute("INSERT INTO titoli (id, tipo, tmdb_id, titolo, durata, dettagli) VALUES ('movie:104','movie',104,'Film ovunque',120,1)")
pc.execute("INSERT INTO miei (titolo_id, aggiunto) VALUES ('movie:104', ?)", (iso(0),))
for p in (2, 3):
    pc.execute("INSERT INTO disponibilita (titolo_id, provider_id, offerta, dal) VALUES ('movie:104',?,'flatrate',?)", (p, iso(-100)))
pc.commit()
pp = logica.piano(pc, D, ore_mese=25)
dove_in = lambda i: {x["s"]["nome"]: (x["azione"], sorted(y["t"]["titolo"] for y in x["titoli"])) for x in pp["periodi"][i]["voci"]}
az = {a["s"]["nome"]: (a["tipo"], a["testo"]) for a in pp["azioni"]}
p0, p1 = dove_in(0), dove_in(1)
atteso("piano: il gia' pagato si usa subito", [str(p0.get("Mensile"))], "('pagato', ['Sul mensile'])")
atteso("...il film va sull'annuale, non su quello da attivare", [str(p0.get("Annuale"))], "('pagato', ['Film ovunque'])")
atteso("...lo spento aspetta la serie che finisce dopo", [r["motivo"] for r in pp["periodi"][0]["rinvii"]], "aspetta: verso il 9 nov è pronto anche «Sullo spento, finisce dopo»")
atteso("...e le prende insieme nel periodo dopo", [str(p1.get("Spento"))], "('attiva', ['Sullo spento', 'Sullo spento, finisce dopo'])")
atteso("...senza niente da unire si attiva subito, prezzo stimato", [f"{p0.get('Senza prezzo')} {pp['periodi'][0]['voci'][-1]['stimato']}"], "('attiva', ['Senza prezzo']) True")
atteso("...il mensile senza altro da vedere: disdici prima del rinnovo", [az["Mensile"][1]], "Disdici prima del 21 ott")
atteso("...lo spento: attivalo al periodo giusto", [az["Spento"][1]], "Attivalo verso il 31 ott")
atteso("...fuori piano chi non e' su nessun servizio", [v["t"]["titolo"] + " " + v["perche"] for v in pp["esclusi"]], "Da nessuna parte su nessun servizio che segui")
atteso("...spesa: uno stimato (mediana 9,99) + lo spento", [f"{pp['spesa']:.2f} {pp['oggi_attivi']:.2f}"], "15.98 59.94")
pp = logica.piano(pc, D, ore_mese=6)
atteso("piano con poche ore: il troppo si divide e continua", [str(dove_in(0).get("Mensile")), str(dove_in(1).get("Mensile"))],
       "('pagato', ['Sul mensile'])", "('rinnova', ['Sul mensile'])")
parti = [y["parte"] for q in pp["periodi"] for x in q["voci"] if x["s"]["nome"] == "Mensile" for y in x["titoli"]]
atteso("...prima «una parte», alla fine «il resto»", [" / ".join(parti)], "una parte / il resto")
# stagione di una rete TV: 4 episodi annunciati su una precedente di 20
serie(107, "Rete TV", [3], [iso(-5 + 7 * i) for i in range(4)])
pc.execute("INSERT INTO stagioni (titolo_id, numero, episodi, uscita) VALUES ('tv:107', 0, 0, NULL)")
pc.execute("UPDATE stagioni SET numero=2 WHERE titolo_id='tv:107' AND numero=1"); pc.execute("UPDATE episodi SET stagione=2 WHERE titolo_id='tv:107'")
pc.execute("INSERT INTO stagioni (titolo_id, numero, episodi, uscita) VALUES ('tv:107', 1, 20, ?)", (iso(-400),))
pc.execute("UPDATE miei SET stagioni_viste='1' WHERE titolo_id='tv:107'")     # la precedente e' vista: non entra nel piano
pc.commit()
f, stim = logica.fine_stagione(pc, "tv:107", 2)
atteso("stagione con pochi episodi annunciati: fine stimata sulla precedente", [f"{f} {stim}"], f"{D + dt.timedelta(-5 + 7 * 19)} True")

# periodi ancorati: domani le date del piano non si spostano
a1 = {a["s"]["nome"]: a["testo"] for a in logica.piano(pc, D + dt.timedelta(1), 25, ancora=D)["azioni"]}
atteso("piano ancorato: il giorno dopo la data di attivazione resta la stessa", [a1["Spento"]], "Attivalo verso il 31 ott")
q0 = logica.piano(pc, D + dt.timedelta(10), 25, ancora=D)["periodi"][0]
atteso("...il primo periodo gia' iniziato ha meno ore", [f"{q0['inizio']} {q0['disponibili']}"], f"{D} 16.7")

# promemoria per il calendario
pp = logica.piano(pc, D, 25, ancora=D)
ev = {e["uid"]: e for e in logica.promemoria(pc, D, pp)}
atteso("promemoria: disdici il mensile il giorno prima del rinnovo", [f"{ev['disdici-1']['giorno']} {ev['disdici-1']['avviso']}"], f"{D + dt.timedelta(19)} 2")
atteso("...attiva lo spento al suo periodo, con i titoli", [f"{ev['attiva-2-0']['giorno']} {ev['attiva-2-0']['testo'][:60]}"],
       f"{D + dt.timedelta(30)} Da guardare: Sullo spento, Sullo spento, finisce dopo.")
atteso("...e disdicilo prima dei 30 giorni pagati", [str(ev["disdici-2-0"]["giorno"])], str(D + dt.timedelta(58)))
atteso("...niente per l'annuale", [u for u in ev if u.endswith("-3") or "-3-" in u])

import calendario
class FintoCal:
    def __init__(self): self.file = {}; self.chiamate = []
    def assicura(self): return False
    def elenco(self): return set(self.file)
    def metti(self, n, t): self.file[n] = t; self.chiamate.append("PUT " + n)
    def togli(self, n): self.file.pop(n, None); self.chiamate.append("DELETE " + n)
fc = FintoCal(); fc.file["altro-evento.ics"] = "non nostro"
lista = logica.promemoria(pc, D, pp)
atteso("calendario: prima scrittura", [str(calendario.sincronizza(pc, lista, fc))], f"({len(lista)}, 0)")
atteso("...ics con allarme due giorni prima alle 9", [fc.file["palinsesto-disdici-1.ics"]], "TRIGGER:-P1DT15H", "DTSTART;VALUE=DATE:20261020", esatti=False)
fc.chiamate.clear()
atteso("...seconda volta, niente cambiato: nessuna scrittura", [str(calendario.sincronizza(pc, lista, fc)) + " " + str(fc.chiamate)], "(0, 0) []")
atteso("...un promemoria che non serve piu' si toglie, l'evento altrui resta",
       [str(calendario.sincronizza(pc, lista[1:], fc)), str(sorted(fc.file))], "(0, 1)", "altro-evento.ics")
atteso("...testo con virgole e a capo in formato iCalendar", [calendario.testo_ics("a, b; c\nd")], "a\\, b\; c\\nd")

pc.execute("INSERT INTO servizi (id, nome, stato, prezzo, ciclo, fine) VALUES (9, 'Disdetto in offerta', 'disdetto', 2.99, 'mese', ?)", (iso(90),)); pc.commit()
az = {a["s"]["nome"]: (a["tipo"], a["testo"]) for a in logica.piano(pc, D, 25, ancora=D)["azioni"]}
atteso("disdetto ma ancora pagato: lo dice, non «non ti serve»", [str(az["Disdetto in offerta"])], "('pagato', 'Già disdetto, attivo fino al 30 dic')")
atteso("...e nessun promemoria per lui", [e["uid"] for e in logica.promemoria(pc, D, logica.piano(pc, D, 25, ancora=D)) if e["uid"].endswith("-9") or "-9-" in e["uid"]])

# pausa: non costa e non si usa fino al giorno in cui riparte da solo
pc.execute("INSERT INTO servizi (id, nome, stato, prezzo, ciclo, rinnovo, pausa_fino, pausa_proroghe) VALUES (10, 'In pausa', 'attivo', 7.99, 'mese', ?, ?, 1)", (iso(50), iso(50)))
pc.execute("INSERT INTO provider (id, nome, servizio_id) VALUES (10, 'In pausa', 10)"); pc.commit()
def pausa_az():
    pp = logica.piano(pc, D, 25, ancora=D)
    return pp, next(a for a in pp["azioni"] if a["s"]["nome"] == "In pausa"), {e["uid"]: e for e in logica.promemoria(pc, D, pp)}
pp, a, ev = pausa_az()
atteso("pausa senza niente da vedere: prolungala (ne resta 1)", [f"{a['tipo']} {a['testo']}"], "proroga La pausa finisce il 20 nov: prolungala (ne resta 1)")
atteso("...promemoria il giorno prima della fine, avviso 2 giorni prima", [f"{ev['pausa-10']['giorno']} {ev['pausa-10']['avviso']} {ev['pausa-10']['titolo']}"], f"{D + dt.timedelta(49)} 2 In pausa: prolunga la pausa")
atteso("...non conta nella spesa di oggi", [f"{pp['oggi_attivi']:.2f}"], "59.94")
pc.execute("UPDATE servizi SET pausa_proroghe=0 WHERE id=10"); pc.commit()
pp, a, ev = pausa_az()
atteso("pausa senza proroghe: disdici prima che riparta", [f"{a['tipo']} {ev['disdici-10']['titolo']}"], "disdici Disdici In pausa")
pc.execute("UPDATE servizi SET pausa_proroghe=NULL WHERE id=10"); pc.commit()
pp, a, ev = pausa_az()
atteso("proroghe non note: «prolungala se te lo propone, altrimenti disdici»", [f"{a['tipo']} {a['testo']} | {ev['disdici-10']['titolo']}"],
       "disdici La pausa finisce il 20 nov: prolungala se te lo propone, altrimenti disdici prima che riparta | In pausa: prolunga la pausa o disdici")
serie(110, "Esce durante la pausa", [10], [iso(-20)] * 6)
pp, a, ev = pausa_az()
atteso("esce qualcosa durante la pausa: riprendilo, con promemoria", [f"{a['tipo']} {a['testo']} {ev['riprendi-10']['titolo']}"], "riprendi Riprendilo dalla pausa adesso Riprendi In pausa dalla pausa")
atteso("...nel piano il periodo dice «riprendi», non «attiva»", [str([x["azione"] for x in pp["periodi"][0]["voci"] if x["s"]["nome"] == "In pausa"])], "['riprendi']")
atteso("il giorno in cui finisce: la pausa si chiude da sola, con avviso", logica.scadenze(pc, D + dt.timedelta(50)), "In pausa: finita la pausa, l'abbonamento è ripartito", esatti=False)
atteso("...e torna un abbonamento attivo normale", [str(pc.execute("SELECT pausa_fino, rinnovo FROM servizi WHERE id=10").fetchone()[:])], f"(None, '{iso(50)}')")

# pausa invece della disdetta: il servizio serve di nuovo fra qualche mese
pc.execute("INSERT INTO servizi (id, nome, stato, prezzo, ciclo, rinnovo, pausa_durate) VALUES (11, 'Con pausa', 'attivo', 7.99, 'mese', ?, '30,60,90')", (iso(20),))
pc.execute("INSERT INTO provider (id, nome, servizio_id) VALUES (11, 'Con pausa', 11)")
serie(111, "Adesso sul pausabile", [11], [iso(-40)] * 2)                                 # 1,7 h, si guarda nel pagato
serie(112, "Dopo sul pausabile", [11], [iso(-20 + 10 * i) for i in range(10)])          # completa a +70
pc.commit()
pp = logica.piano(pc, D, 25, ancora=D)
a = next(x for x in pp["azioni"] if x["s"]["nome"] == "Con pausa")
atteso("pausa: serve di nuovo quando «Dopo» e' completa (+70): pausa dal rinnovo (+20), 60 giorni, riparte a +80",
       [f"{a['tipo']} {a['testo']}"], f"pausa Mettilo in pausa prima del {logica.breve(D + dt.timedelta(20))}: per 60 giorni; "
                                        f"riparte il {logica.breve(D + dt.timedelta(80))}, quando ti serve di nuovo")
ev = {e["uid"]: e for e in logica.promemoria(pc, D, pp)}
atteso("...promemoria «Metti in pausa» il giorno prima del rinnovo, niente «Disdici»",
       [f"{ev['pausa-11']['giorno']} {ev['pausa-11']['titolo']} {'disdici-11' in ev}"], f"{D + dt.timedelta(19)} Metti in pausa Con pausa False")
pc.execute("UPDATE servizi SET pausa_durate='14,28' WHERE id=11"); pc.commit()
a = next(x for x in logica.piano(pc, D, 25, ancora=D)["azioni"] if x["s"]["nome"] == "Con pausa")
atteso("...pause troppo corte per arrivarci: la piu' lunga, e alla fine disdici (mai prima)", [f"{a['tipo']} {a['testo']}"],
       f"pausa Mettilo in pausa prima del {logica.breve(D + dt.timedelta(20))}: per 28 giorni; alla fine ({logica.breve(D + dt.timedelta(48))}), "
       "se non ti serve ancora, disdici prima che riparta")
pc.execute("UPDATE servizi SET pausa_durate='30x3' WHERE id=11"); pc.commit()
a = next(x for x in logica.piano(pc, D, 25, ancora=D)["azioni"] if x["s"]["nome"] == "Con pausa")
atteso("...a passi come Netflix: un mese, poi una proroga", [f"{a['testo']} | primo {a['primo']} proroghe {a['proroghe']}"],
       f"Mettilo in pausa prima del {logica.breve(D + dt.timedelta(20))}: un mese, poi prorogala 1 volta quando te lo propone; "
       f"riparte il {logica.breve(D + dt.timedelta(80))}, quando ti serve di nuovo. Fino ad allora si guarda | primo {D + dt.timedelta(50)} proroghe 2")
pc.execute("INSERT INTO servizi (id, nome, stato, prezzo, ciclo, rinnovo, pausa_durate) VALUES (12, 'Niente dopo', 'attivo', 5.99, 'mese', ?, '30x3')", (iso(20),))
pc.commit()
a = next(x for x in logica.piano(pc, D, 25, ancora=D)["azioni"] if x["s"]["nome"] == "Niente dopo")
atteso("...niente da vedere: pausa lunga lo stesso, la disdetta solo alla fine", [a["testo"]],
       f"Mettilo in pausa prima del {logica.breve(D + dt.timedelta(20))}: un mese, poi prorogala 2 volte quando te lo propone; "
       f"alla fine ({logica.breve(D + dt.timedelta(110))}), se non ti serve ancora, disdici prima che riparta. Fino ad allora si guarda")
pc.execute("DELETE FROM servizi WHERE id=12")
pc.execute("UPDATE servizi SET pausa_durate='30,60,90' WHERE id=11"); pc.commit()
# segnata la pausa: comincia al rinnovo, fino ad allora si guarda
pc.execute("UPDATE servizi SET pausa_dal=?, pausa_fino=?, rinnovo=? WHERE id=11", (iso(20), iso(80), iso(80))); pc.commit()
s11 = pc.execute("SELECT * FROM servizi WHERE id=11").fetchone()
atteso("pausa chiesta: non ancora in pausa, ma programmata", [f"{logica.in_pausa(s11, D)} {logica.pausa_programmata(s11, D)} {logica.in_pausa(s11, D + dt.timedelta(25))}"], "False True True")
pp = logica.piano(pc, D, 25, ancora=D)
p0 = {x["s"]["nome"]: (x["azione"], [y["t"]["titolo"] for y in x["titoli"]]) for x in pp["periodi"][0]["voci"]}
atteso("...i giorni pagati prima della pausa si usano", [str(p0.get("Con pausa"))], "('pagato', ['Adesso sul pausabile'])")
a = next(x for x in pp["azioni"] if x["s"]["nome"] == "Con pausa")
atteso("...e il piano lo racconta", [f"{a['tipo']} {a['testo']}"], f"pagato Si guarda fino al {logica.breve(D + dt.timedelta(19))}, poi in pausa fino al {logica.breve(D + dt.timedelta(80))}")
atteso("...la pausa finita si chiude da sola, anche il suo inizio", logica.scadenze(pc, D + dt.timedelta(80)), "Con pausa: finita la pausa", esatti=False)
atteso("...niente resta di lei", [str(pc.execute("SELECT pausa_dal, pausa_fino FROM servizi WHERE id=11").fetchone()[:])], "(None, None)")

print(f"\n{'TUTTO OK' if not ERRORI else f'{len(ERRORI)} CASI SBAGLIATI'} — database in {db.DATI}")
sys.exit(1 if ERRORI else 0)
