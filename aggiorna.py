#!/usr/bin/env python3
"""Giro notturno di Palinsesto (palinsesto-aggiorna.timer, 05:30).

    aggiorna.py                  giro vero: aggiorna, confronta, notifica su ntfy
    aggiorna.py --prova          su una COPIA del database, ntfy solo stampato
    aggiorna.py --oggi 2026-10-01  finge un'altra data (solo con --prova)
    aggiorna.py --senza-catalogo salta le novita' per servizio (piu' rapido)

Cosa fa, in ordine:
1. elenco dei provider TMDB per l'Italia, una volta a settimana; al primo giro
   crea anche i servizi predefiniti e la mappa provider -> servizio;
2. scadenze degli abbonamenti (rinnovi avanzati da soli, avvisi);
3. scheda completa di ogni titolo mio, con confronto e eventi (logica.py);
4. «novita' nel catalogo» per ogni servizio seguito;
5. un solo messaggio ntfy con gli eventi nuovi da notificare.

L'esito finisce nel journal (riga «giro concluso» o «giro FALLITO»): e' quella
che un domani sorvegliera' il watchdog. Esce con 1 se piu' di meta' delle
schede non si e' potuta scaricare.
"""
import argparse, datetime as dt, json, os, pathlib, shutil, sys, tempfile

import requests

QUI = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(QUI))
import calendario, db, logica, tmdb

# Servizi predefiniti e i provider TMDB che ne fanno parte. Gli id sono quelli
# verificati sull'elenco IT; le varianti (con pubblicita', canali) si
# aggiungono da sole se il nome comincia come quello del servizio.
PREDEFINITI = [
    # nome,          ordine, id principali,   prefissi dei nomi TMDB
    ("Netflix",      1, {8, 1796},            ("netflix",)),
    ("Prime Video",  2, {119, 2100},          ("amazon prime video",)),
    ("Disney+",      3, {337},                ("disney plus", "disney+")),
    ("NOW",          4, {39},                 ("now tv", "now ")),
    ("Apple TV+",    5, {350},                ("apple tv plus", "apple tv+")),
    ("Paramount+",   6, {531, 582, 1853},     ("paramount plus", "paramount+")),
]
GIORNI_PROVIDER = 7
GIORNI_CATALOGO = 540      # «recenti»: usciti negli ultimi 18 mesi
PAGINE_CATALOGO = 5        # 20 titoli a pagina, per tipo e per servizio


def log(msg):
    print(msg, flush=True)


def aggiorna_provider(c, api, oggi):
    ultimo = db.meta(c, "provider_aggiornati")
    if ultimo and (oggi - dt.date.fromisoformat(ultimo)).days < GIORNI_PROVIDER and \
            c.execute("SELECT 1 FROM provider").fetchone():
        return
    visti = {}
    for tipo in ("movie", "tv"):
        for p in api.provider(tipo):
            visti[p["provider_id"]] = p
    for p in visti.values():
        c.execute("INSERT INTO provider (id, nome, logo, priorita) VALUES (?,?,?,?) "
                  "ON CONFLICT(id) DO UPDATE SET nome=excluded.nome, logo=excluded.logo, priorita=excluded.priorita",
                  (p["provider_id"], p["provider_name"], p.get("logo_path"),
                   (p.get("display_priorities") or {}).get("IT", p.get("display_priority"))))
    if not c.execute("SELECT 1 FROM servizi").fetchone():
        for nome, ordine, ids, prefissi in PREDEFINITI:
            princ = next((visti[i] for i in sorted(ids) if i in visti), None)
            cur = c.execute("INSERT INTO servizi (nome, ordine, logo) VALUES (?,?,?)",
                            (nome, ordine, princ.get("logo_path") if princ else None))
            sid = cur.lastrowid
            for p in visti.values():
                n = p["provider_name"].lower()
                if p["provider_id"] in ids or n.startswith(prefissi):
                    c.execute("UPDATE provider SET servizio_id=? WHERE id=? AND servizio_id IS NULL",
                              (sid, p["provider_id"]))
            mappati = [r["nome"] for r in c.execute("SELECT nome FROM provider WHERE servizio_id=?", (sid,))]
            log(f"servizio {nome}: provider {', '.join(mappati) or 'NESSUNO -- da mappare a mano'}")
    db.meta(c, "provider_aggiornati", oggi.isoformat())
    log(f"provider TMDB per l'Italia: {len(visti)}")


def aggiorna_catalogo(c, api, oggi):
    """Titoli recenti di ogni servizio. Un titolo mai visto prima su quel
    servizio e' una novita'. Il PRIMO giro di un servizio fa solo da base.
    Limite dichiarato: TMDB non dice quando un titolo e' entrato nel catalogo,
    e un titolo recente che entra fra i piu' popolari per la prima volta sembra
    nuovo anche se c'era da prima."""
    dal = (oggi - dt.timedelta(days=GIORNI_CATALOGO)).isoformat()
    novita = {}
    for s in c.execute("SELECT * FROM servizi WHERE seguito=1").fetchall():
        ids = [r["id"] for r in c.execute("SELECT id FROM provider WHERE servizio_id=?", (s["id"],))]
        if not ids:
            continue
        base = not c.execute("SELECT 1 FROM catalogo WHERE servizio_id=?", (s["id"],)).fetchone()
        for tipo in ("movie", "tv"):
            for pagina in range(1, PAGINE_CATALOGO + 1):
                r = api.scopri(tipo, ids, pagina, dal)
                for x in r.get("results", []):
                    tid = logica.salva_base(c, tipo, x)
                    cur = c.execute("INSERT OR IGNORE INTO catalogo (servizio_id, titolo_id, dal) "
                                    "SELECT ?,?,? WHERE NOT EXISTS (SELECT 1 FROM catalogo WHERE servizio_id=? AND titolo_id=?)",
                                    (s["id"], tid, oggi.isoformat(), s["id"], tid))
                    if cur.rowcount and not base:
                        logica.evento(c, f"catalogo:{s['id']}:{tid}", "catalogo",
                                      f"«{tmdb.titolo_di(x)}» è nuovo su {s['nome']}", oggi, tid, s["id"], notificare=False)
                        novita[s["nome"]] = novita.get(s["nome"], 0) + 1
                if pagina >= r.get("total_pages", 0):
                    break
        if base:
            log(f"catalogo {s['nome']}: primo giro, fa da base")
    return novita


# Reti TMDB delle produzioni originali di ciascun servizio (verificate il 28/09
# con /network/<id>). NOW non ne ha una: trasmette HBO e Sky, registrati altrove,
# quindi per NOW si vedono solo le serie gia' in catalogo con episodi in arrivo.
RETI = {"Netflix": 213, "Prime Video": 1024, "Disney+": 2739, "Apple TV+": 2552, "Paramount+": 4330}
# talk show, notiziari, soap, bambini, reality: il 28/09 riempivano l'elenco (WWE, X Factor, Hell's Kitchen)
SENZA_GENERI = "10767|10763|10766|10762|10764"
GIORNI_ARRIVO, TENUTI_ARRIVO = 60, 20


def aggiorna_in_arrivo(c, api, oggi):
    """Per ogni servizio seguito, due fonti:
    - serie gia' su quel servizio in Italia con episodi in uscita nei prossimi
      GIORNI_ARRIVO giorni: la scheda dice se e' una stagione nuova (episodio 1)
      o la prosecuzione di una in corso;
    - serie originali della sua rete che debuttano nello stesso periodo.
    I film non ci sono: nessuno annuncia con una data quando un film arriva su
    un servizio in Italia. Sostituisce la tabella: ritorna {servizio: quanti}."""
    fino = (oggi + dt.timedelta(days=GIORNI_ARRIVO)).isoformat()
    miei = {r[0] for r in c.execute("SELECT titolo_id FROM miei")}
    conta = {}
    for s in c.execute("SELECT * FROM servizi WHERE seguito=1").fetchall():
        ids = [r["id"] for r in c.execute("SELECT id FROM provider WHERE servizio_id=?", (s["id"],))]
        righe = {}
        if ids:
            r = api.get("/discover/tv", language=tmdb.LINGUA, watch_region=tmdb.REGIONE,
                        with_watch_providers="|".join(map(str, ids)), with_watch_monetization_types="flatrate|free|ads",
                        without_genres=SENZA_GENERI, sort_by="popularity.desc",
                        **{"air_date.gte": oggi.isoformat(), "air_date.lte": fino}) or {}
            for x in r.get("results", [])[:TENUTI_ARRIVO]:
                d = api.get(f"/tv/{x['id']}", language=tmdb.LINGUA) or {}
                p = d.get("next_episode_to_air") or {}
                if not p.get("air_date") or not (oggi.isoformat() <= p["air_date"] <= fino):
                    continue
                nuova = p.get("episode_number") == 1
                if not nuova and f"tv:{x['id']}" not in miei:
                    continue      # il singolo episodio settimanale conta solo per le serie che seguo
                righe[f"tv:{x['id']}"] = (x, p["air_date"], "stagione" if nuova else "episodi",
                                          f"Stagione {p['season_number']}" if nuova else
                                          f"S{p['season_number']:02d}E{p['episode_number']:02d}")
        if s["nome"] in RETI:
            r = api.get("/discover/tv", language=tmdb.LINGUA, with_networks=RETI[s["nome"]], without_genres=SENZA_GENERI,
                        sort_by="popularity.desc", **{"first_air_date.gte": oggi.isoformat(), "first_air_date.lte": fino}) or {}
            for x in r.get("results", [])[:TENUTI_ARRIVO]:
                if x.get("first_air_date"):
                    righe.setdefault(f"tv:{x['id']}", (x, x["first_air_date"], "nuova serie", "Nuova serie"))
        c.execute("DELETE FROM in_arrivo WHERE servizio_id=?", (s["id"],))
        for tid, (x, data, genere, cosa) in righe.items():
            logica.salva_base(c, "tv", x)
            c.execute("INSERT INTO in_arrivo VALUES (?,?,?,?,?,?)", (s["id"], tid, data, genere, cosa, oggi.isoformat()))
        conta[s["nome"]] = len(righe)
    return conta


def conf_ntfy():
    try:
        return json.loads((tmdb.CONF / "ntfy.json").read_text())
    except FileNotFoundError:
        return None


def notifica(c, novita, prova):
    righe = [(r["id"], r["testo"], r["titolo_id"]) for r in
             c.execute("SELECT * FROM eventi WHERE notificare=1 AND notificato=0 ORDER BY id")]
    extra = [f"{n} novità nel catalogo di {s}" for s, n in sorted(novita.items())]
    if not righe and not extra:
        return
    corpo = [t for _, t, _ in righe] + extra
    testo = ""
    for i, t in enumerate(corpo):
        if len((testo + "• " + t + "\n").encode()) > 3500:
            testo += f"…e altri {len(corpo) - i}"
            break
        testo += "• " + t + "\n"
    cfg = conf_ntfy() or {}
    pagina = cfg.get("pagina", "https://palinsesto.example.org")
    # con un solo titolo il tocco porta alla sua scheda, altrimenti alle novita'
    unico = {t for _, _, t in righe if t}
    click = f"{pagina}/t/{unico.pop().replace(':', '/')}" if len(unico) == 1 and not extra else f"{pagina}/novita"
    titolo = f"Palinsesto: {len(corpo)} novità" if len(corpo) > 1 else "Palinsesto"
    msg = {"topic": cfg.get("topic", "streaming"), "title": titolo, "message": testo.strip(),
           "click": click, "tags": ["tv"], "priority": 3}
    if prova or not cfg:
        log(("ntfy (PROVA, non inviato): " if prova else "ntfy NON configurato, messaggio non inviato: ")
            + json.dumps(msg, ensure_ascii=False))
        if not prova:
            return          # restano da notificare: partiranno quando ntfy sara' configurato
    else:
        r = requests.post(cfg["url"], json=msg, timeout=20,
                          headers={"Authorization": f"Bearer {cfg['token']}"})
        if r.status_code != 200:
            raise RuntimeError(f"ntfy ha risposto HTTP {r.status_code}")
        log(f"ntfy: inviato ({len(corpo)} righe)")
    c.executemany("UPDATE eventi SET notificato=1 WHERE id=?", [(i,) for i, _, _ in righe])
    c.execute("UPDATE eventi SET notificato=1 WHERE tipo='catalogo' AND notificato=0")


def main():
    a = argparse.ArgumentParser()
    a.add_argument("--prova", action="store_true")
    a.add_argument("--oggi")
    a.add_argument("--senza-catalogo", action="store_true")
    a.add_argument("--db")
    o = a.parse_args()
    if o.oggi and not o.prova:
        sys.exit("--oggi solo insieme a --prova")
    oggi = dt.date.fromisoformat(o.oggi) if o.oggi else dt.date.today()

    file = pathlib.Path(o.db) if o.db else db.FILE
    if o.prova and not o.db:
        copia = pathlib.Path(tempfile.mkdtemp(prefix="palinsesto-prova-")) / "palinsesto.db"
        if file.exists():
            src = db.apri(file); dst = __import__("sqlite3").connect(copia)
            src.backup(dst); dst.close(); src.close()
        file = copia
        log(f"PROVA su {copia}")
    try:
        api = tmdb.TMDB()
    except FileNotFoundError:
        log(f"giro FALLITO: manca la chiave TMDB in {tmdb.CONF / 'tmdb'}")
        sys.exit(1)
    c = db.apri(file)

    aggiorna_provider(c, api, oggi); c.commit()
    nuovi = logica.scadenze(c, oggi); c.commit()

    miei = [r["titolo_id"] for r in c.execute("SELECT titolo_id FROM miei")]
    errori = 0
    for tid in miei:
        tipo, n = tid.split(":")
        try:
            _, ev = logica.salva_scheda(c, api, tipo, int(n), oggi)
            nuovi += ev
            c.commit()
        except tmdb.ErroreTMDB as e:
            c.rollback()
            errori += 1
            log(f"scheda {tid}: {e}")

    tolti, rimessi = logica.sincronizza_da_vedere(c, oggi); c.commit()
    if tolti or rimessi:
        log(f"«Da vedere»: tolti {len(tolti)} finiti, rimessi {len(rimessi)} con novità ({', '.join(rimessi)})")

    novita = {}
    if not o.senza_catalogo:
        try:
            novita = aggiorna_catalogo(c, api, oggi); c.commit()
        except tmdb.ErroreTMDB as e:
            c.rollback(); errori += 1
            log(f"catalogo: {e}")

    try:
        conta = aggiorna_in_arrivo(c, api, oggi); c.commit()
        log("in arrivo: " + ", ".join(f"{k} {v}" for k, v in conta.items()))
    except tmdb.ErroreTMDB as e:
        c.rollback()
        log(f"in arrivo: {e} (restano quelli di ieri)")

    try:
        n = logica.completa_generi(c, api); c.commit()
        if n:
            log(f"generi completati: {n}")
        n = logica.calcola_consigliati(c, api, oggi); c.commit()
        log(f"consigliati: {n}")
    except tmdb.ErroreTMDB as e:
        c.rollback()
        log(f"consigliati: {e} (restano quelli di ieri)")

    try:
        esito = calendario.aggiorna(c, oggi) if not o.prova else None
        if esito:
            log(f"calendario: {esito[0]} eventi scritti, {esito[1]} tolti")
    except Exception as e:          # il calendario non deve fermare gli avvisi; l'esito resta in meta e in pagina
        log(f"calendario: {e}")

    for t in nuovi:
        log(f"evento: {t}")
    notifica(c, novita, o.prova); c.commit()
    db.meta(c, "ultimo_giro", dt.datetime.now().isoformat(timespec="seconds")); c.commit()

    fallito = miei and errori > len(miei) / 2
    log(f"giro {'FALLITO' if fallito else 'concluso'}: {len(miei)} titoli, {errori} errori, "
        f"{len(nuovi)} eventi, novità catalogo {sum(novita.values())}, {api.chiamate} chiamate TMDB")
    sys.exit(1 if fallito else 0)


if __name__ == "__main__":
    main()
