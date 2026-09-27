"""Cuore di Palinsesto, condiviso fra il job notturno e la pagina:

- salva_scheda(): scrive una scheda TMDB nel database e CONFRONTA con quella
  di prima, producendo gli eventi (arrivi, partenze, stagioni, fine serie);
- consigli(): il verdetto per servizio, «Conviene?»;
- scadenze(): avanza i rinnovi passati e produce gli eventi degli abbonamenti.

Regola degli eventi: un titolo visto per la prima volta fa da BASE e non
produce nulla (`titoli.controllato` vuoto), altrimenti aggiungere una serie
con 5 stagioni darebbe 5 «stagione uscita» in un colpo.
"""
import calendar, datetime as dt

from db import ASSENZE_PER_CHIUDERE, riepilogo, viste, vista

ABBONAMENTO = ("flatrate", "free", "ads")      # offerte che un abbonamento copre
OFFERTE = {"flatrate": "abbonamento", "free": "gratis", "ads": "con pubblicità",
           "rent": "noleggio", "buy": "acquisto"}
STATI_FINE = {"Ended": "conclusa", "Canceled": "cancellata"}
GIORNI_ANNUNCI = 90        # stagioni in arrivo considerate dal consigliere
GIORNI_RINNOVO = 3         # avviso prima di un rinnovo
GIORNI_CRONOLOGIA = 30     # avviso prima che un servizio cancelli la cronologia


def iso(d):
    return d.isoformat() if d else None


def data(s):
    return dt.date.fromisoformat(s) if s else None


def gm(s):
    """'2026-10-03' -> '03/10/2026'"""
    return data(s).strftime("%d/%m/%Y") if s else ""


def piu_mesi(d, n):
    m = d.month - 1 + n
    a, m = d.year + m // 12, m % 12 + 1
    return d.replace(year=a, month=m, day=min(d.day, calendar.monthrange(a, m)[1]))


# --- eventi -------------------------------------------------------------------
def evento(c, chiave, tipo, testo, oggi, titolo_id=None, servizio_id=None, notificare=True):
    """Registra un fatto una volta sola: la chiave e' la deduplica."""
    cur = c.execute("INSERT OR IGNORE INTO eventi (chiave, quando, tipo, titolo_id, servizio_id, testo, notificare) "
                    "VALUES (?,?,?,?,?,?,?)", (chiave, iso(oggi), tipo, titolo_id, servizio_id, testo, int(notificare)))
    return cur.rowcount == 1


def servizi_disponibili(c, titolo_id):
    """{servizio_id: nome} dove il titolo si vede con un abbonamento, adesso."""
    q = f"""SELECT DISTINCT s.id, s.nome FROM disponibilita d
            JOIN provider p ON p.id = d.provider_id JOIN servizi s ON s.id = p.servizio_id
            WHERE d.titolo_id = ? AND d.fino IS NULL AND d.offerta IN ({",".join("?" * len(ABBONAMENTO))})
            AND s.seguito = 1"""
    return {r["id"]: r["nome"] for r in c.execute(q, (titolo_id, *ABBONAMENTO))}


# --- schede -------------------------------------------------------------------
def salva_base(c, tipo, x):
    """Titolo visto solo in una ricerca o in un elenco: dati minimi, senza
    sovrascrivere una scheda completa gia' presente."""
    from tmdb import anno_di, titolo_di, originale_di
    tid = f"{tipo}:{x['id']}"
    c.execute("""INSERT INTO titoli (id, tipo, tmdb_id, titolo, originale, anno, poster, sfondo, trama)
                 VALUES (?,?,?,?,?,?,?,?,?)
                 ON CONFLICT(id) DO UPDATE SET
                   titolo=excluded.titolo, poster=COALESCE(excluded.poster, poster),
                   trama=CASE WHEN dettagli=1 THEN trama ELSE excluded.trama END""",
              (tid, tipo, x["id"], titolo_di(x), originale_di(x), anno_di(x),
               x.get("poster_path"), x.get("backdrop_path"), x.get("overview")))
    return tid


def salva_episodi(c, tid, stagione, episodi):
    for e in episodi:
        if e.get("episode_number") is None:
            continue
        c.execute("""INSERT INTO episodi (titolo_id, stagione, numero, nome, uscita, durata) VALUES (?,?,?,?,?,?)
                     ON CONFLICT(titolo_id, stagione, numero) DO UPDATE SET
                     nome=excluded.nome, uscita=excluded.uscita, durata=excluded.durata""",
                  (tid, stagione, e["episode_number"], e.get("name"), e.get("air_date"), e.get("runtime")))


def salva_scheda(c, api, tipo, tmdb_id, oggi, con_stagioni=True):
    """Scarica e salva la scheda completa; ritorna (titolo_id, [testi degli eventi nuovi])."""
    from tmdb import anno_di, titolo_di, originale_di
    d = api.scheda(tipo, tmdb_id)
    if d is None:
        return None, []
    tid = f"{tipo}:{tmdb_id}"
    prima = c.execute("SELECT * FROM titoli WHERE id=?", (tid,)).fetchone()
    base = not prima or not prima["controllato"]
    mio = c.execute("SELECT * FROM miei WHERE titolo_id=?", (tid,)).fetchone()
    notif = bool(mio and mio["avvisi"])
    nome = titolo_di(d)
    nuovi = []

    def ev(chiave, tipo_ev, testo, servizio_id=None):
        if not base and evento(c, chiave, tipo_ev, testo, oggi, tid, servizio_id, notif):
            nuovi.append(testo)

    prossimo = d.get("next_episode_to_air") or {}
    c.execute("""INSERT INTO titoli (id, tipo, tmdb_id) VALUES (?,?,?) ON CONFLICT(id) DO NOTHING""",
              (tid, tipo, tmdb_id))
    c.execute("""UPDATE titoli SET titolo=?, originale=?, anno=?, poster=?, sfondo=?, trama=?, generi=?,
                 stato=?, durata=?, prossimo_ep=?, prossimo_ep_sigla=?, dettagli=1, aggiornato=?
                 WHERE id=?""",
              (nome, originale_di(d), anno_di(d), d.get("poster_path"), d.get("backdrop_path"),
               d.get("overview"), ", ".join(g["name"] for g in d.get("genres", [])),
               d.get("status"), d.get("runtime") or (d.get("episode_run_time") or [None])[0],
               prossimo.get("air_date"),
               f"S{prossimo['season_number']:02d}E{prossimo['episode_number']:02d}" if prossimo else None,
               dt.datetime.now().isoformat(timespec="seconds"), tid))

    # --- disponibilita' -------------------------------------------------------
    prima_serv = servizi_disponibili(c, tid)
    it = (d.get("watch/providers") or {}).get("results", {}).get("IT", {})
    ora = set()
    for offerta in OFFERTE:
        for p in it.get(offerta, []):
            c.execute("INSERT INTO provider (id, nome, logo, priorita) VALUES (?,?,?,?) "
                      "ON CONFLICT(id) DO UPDATE SET nome=excluded.nome, logo=excluded.logo",
                      (p["provider_id"], p["provider_name"], p.get("logo_path"), p.get("display_priority")))
            ora.add((p["provider_id"], offerta))
    aperte = {(r["provider_id"], r["offerta"]): r for r in
              c.execute("SELECT * FROM disponibilita WHERE titolo_id=? AND fino IS NULL", (tid,))}
    for k in ora - aperte.keys():
        c.execute("INSERT OR IGNORE INTO disponibilita (titolo_id, provider_id, offerta, dal) VALUES (?,?,?,?)",
                  (tid, *k, iso(oggi)))
    for k, r in aperte.items():
        if k in ora:
            if r["assenze"]:
                c.execute("UPDATE disponibilita SET assenze=0, assente_il=NULL WHERE titolo_id=? AND provider_id=? "
                          "AND offerta=? AND fino IS NULL", (tid, *k))
        elif r["assente_il"] != iso(oggi):
            n = r["assenze"] + 1
            c.execute("UPDATE disponibilita SET assenze=?, assente_il=?, fino=? WHERE titolo_id=? AND provider_id=? "
                      "AND offerta=? AND fino IS NULL",
                      (n, iso(oggi), iso(oggi) if n >= ASSENZE_PER_CHIUDERE else None, tid, *k))
    dopo_serv = servizi_disponibili(c, tid)
    for sid in dopo_serv.keys() - prima_serv.keys():
        ev(f"arrivo:{tid}:{sid}:{iso(oggi)}", "arrivo", f"«{nome}» è arrivato su {dopo_serv[sid]}", sid)
    for sid in prima_serv.keys() - dopo_serv.keys():
        ev(f"partenza:{tid}:{sid}:{iso(oggi)}", "partenza", f"«{nome}» non è più su {prima_serv[sid]}", sid)
    dove = " — su " + ", ".join(sorted(dopo_serv.values())) if dopo_serv else ""

    # --- stagioni -------------------------------------------------------------
    if tipo == "tv":
        vecchie = {r["numero"]: r for r in c.execute("SELECT * FROM stagioni WHERE titolo_id=?", (tid,))}
        controllato = data(prima["controllato"]) if prima and prima["controllato"] else oggi
        for s in d.get("seasons", []):
            n = s["season_number"]
            if n == 0:
                continue        # «Speciali»: non sono una stagione da aspettare
            uscita = s.get("air_date")
            fine = vecchie[n]["fine"] if n in vecchie else None
            # la fine di una stagione (ultimo episodio) chiede una chiamata in piu':
            # solo per quelle in corso o in arrivo, e finche' non e' nota
            corrente = not uscita or data(uscita) >= oggi - dt.timedelta(days=150)
            senza_ep = not c.execute("SELECT 1 FROM episodi WHERE titolo_id=? AND stagione=?", (tid, n)).fetchone()
            if con_stagioni and ((corrente and (not fine or data(fine) >= oggi)) or (senza_ep and mio)):
                st = api.stagione(tmdb_id, n)
                eps = (st or {}).get("episodes", [])
                salva_episodi(c, tid, n, eps)
                date = [e.get("air_date") for e in eps]
                if date and all(date):
                    fine = max(date)
            c.execute("""INSERT INTO stagioni (titolo_id, numero, nome, episodi, uscita, fine, poster)
                         VALUES (?,?,?,?,?,?,?) ON CONFLICT(titolo_id, numero) DO UPDATE SET
                         nome=excluded.nome, episodi=excluded.episodi, uscita=excluded.uscita,
                         fine=excluded.fine, poster=excluded.poster""",
                      (tid, n, s.get("name"), s.get("episode_count"), uscita, fine, s.get("poster_path")))
            if n not in vecchie:
                quando = f", dal {gm(uscita)}" if uscita and data(uscita) > oggi else ""
                ev(f"annuncio:{tid}:S{n}", "annuncio", f"«{nome}»: stagione {n} annunciata{quando}")
            elif uscita and vecchie[n]["uscita"] != uscita and data(uscita) > oggi:
                ev(f"data:{tid}:S{n}:{uscita}", "annuncio", f"«{nome}»: la stagione {n} esce il {gm(uscita)}")
            if uscita and controllato < data(uscita) <= oggi:
                ev(f"uscita:{tid}:S{n}", "uscita", f"«{nome}»: è iniziata la stagione {n}{dove}")
            if fine and controllato < data(fine) <= oggi:
                ev(f"completa:{tid}:S{n}", "completa", f"«{nome}»: stagione {n} completa, tutti gli episodi usciti{dove}")
        if prima and prima["stato"] and prima["stato"] != d.get("status") and d.get("status") in STATI_FINE:
            ev(f"fine:{tid}", "fine", f"«{nome}»: serie {STATI_FINE[d['status']]}, non ci saranno altre stagioni")

    c.execute("UPDATE titoli SET controllato=? WHERE id=?", (iso(oggi), tid))
    return tid, nuovi


# --- consigliere --------------------------------------------------------------
def da_vedere(c, oggi):
    """Per ogni titolo mio: cosa c'e' da vedere e cosa sta arrivando.
    Ritorna {titolo_id: dict(pronte=[n..], in_corso=[(n, fine)], in_arrivo=[(n, uscita)])}."""
    out = {}
    for t in c.execute("SELECT t.*, m.visto, m.stagioni_viste, m.viste_fino FROM miei m JOIN titoli t ON t.id=m.titolo_id"):
        if t["tipo"] == "movie":
            if t["visto"]:
                continue
            out[t["id"]] = dict(t=t, pronte=[0], in_corso=[], in_arrivo=[], episodi=0)
            continue
        riep = riepilogo(c, t["id"], t, iso(oggi))
        pronte, in_corso, in_arrivo, episodi = [], [], [], 0
        sigla_n = int(t["prossimo_ep_sigla"][1:3]) if t["prossimo_ep_sigla"] else None
        for s in c.execute("SELECT * FROM stagioni WHERE titolo_id=? ORDER BY numero", (t["id"],)):
            n, u, f = s["numero"], data(s["uscita"]), data(s["fine"])
            r = riep.get(n) or {}
            if r.get("vista"):
                continue
            if u and u <= oggi:
                if r.get("noti"):
                    # tutti gli episodi usciti sono visti, ne mancano di futuri: in pari, si aspetta
                    completa = r["usciti"] == r["totale"] or (f and f <= oggi)
                    if completa and r["da_vedere"]:
                        pronte.append(n); episodi += r["da_vedere"]
                    elif not completa:
                        in_corso.append((n, f or data(t["prossimo_ep"])))
                elif (f and f > oggi) or (not f and sigla_n == n):
                    in_corso.append((n, f or data(t["prossimo_ep"])))
                else:
                    pronte.append(n)
            elif u and u <= oggi + dt.timedelta(days=GIORNI_ANNUNCI):
                in_arrivo.append((n, u))
        if pronte or in_corso or in_arrivo:
            out[t["id"]] = dict(t=t, pronte=pronte, in_corso=in_corso, in_arrivo=in_arrivo, episodi=episodi)
    return out


def cronologia_a_rischio(s):
    """Data da cui il servizio puo' cancellare la cronologia, se si puo' calcolare."""
    if s["stato"] != "disdetto" or not s["fine"] or not s["conserva_mesi"]:
        return None
    return piu_mesi(data(s["fine"]), s["conserva_mesi"])


def consigli(c, oggi):
    """[dict] un verdetto per servizio seguito, con il motivo e i titoli che lo determinano."""
    dv = da_vedere(c, oggi)
    per_serv = {}
    for tid, x in dv.items():
        x["dove"] = servizi_disponibili(c, tid)
        for sid in x["dove"]:
            per_serv.setdefault(sid, []).append(x)
    out = []
    for s in c.execute("SELECT * FROM servizi WHERE seguito=1 ORDER BY ordine, nome"):
        voci = per_serv.get(s["id"], [])
        pronti = [x for x in voci if x["pronte"]]
        attese = sorted([(f, x) for x in voci for _, f in x["in_corso"] if f] +
                        [(u, x) for x in voci for _, u in x["in_arrivo"]], key=lambda y: y[0])
        attivo = s["stato"] == "attivo" or (s["stato"] == "disdetto" and s["fine"] and data(s["fine"]) >= oggi)
        if pronti:
            n = len(pronti)
            verdetto = "sfrutta" if attivo else "attiva"
            testo = (f"{n} titol{'o' if n == 1 else 'i'} da vedere" +
                     (f", hai tempo fino al {gm(s['fine'])}" if s["stato"] == "disdetto" and attivo else ""))
        elif attese:
            prima = attese[0][0]
            verdetto = "aspetta"
            testo = f"Aspetta fino al {prima.strftime('%d/%m/%Y')}: {attese[0][1]['t']['titolo']}"
        elif attivo and s["stato"] == "attivo":
            verdetto = "disdici"
            testo = "Niente di nuovo nelle tue liste" + (f": puoi disdire prima del {gm(s['rinnovo'])}" if s["rinnovo"] else "")
        else:
            verdetto = "niente"
            testo = "Niente di nuovo nelle tue liste"
        rischio = cronologia_a_rischio(s)
        out.append(dict(s=s, verdetto=verdetto, testo=testo, pronti=pronti, attese=attese, attivo=attivo,
                        rischio=rischio, rischio_vicino=bool(rischio and rischio - dt.timedelta(days=GIORNI_CRONOLOGIA) <= oggi)))
    return out


# --- abbonamenti --------------------------------------------------------------
def scadenze(c, oggi):
    """Avanza i rinnovi passati (un abbonamento attivo si rinnova da solo) e
    registra gli eventi di rinnovo, fine e cronologia. Ritorna i testi nuovi."""
    nuovi = []
    for s in c.execute("SELECT * FROM servizi").fetchall():
        if s["stato"] == "attivo" and s["rinnovo"]:
            r = data(s["rinnovo"])
            while r < oggi:
                r = piu_mesi(r, 12 if s["ciclo"] == "anno" else 1)
            if iso(r) != s["rinnovo"]:
                c.execute("UPDATE servizi SET rinnovo=? WHERE id=?", (iso(r), s["id"]))
            if (r - oggi).days <= GIORNI_RINNOVO:
                prezzo = f" ({s['prezzo']:.2f} €)".replace(".", ",") if s["prezzo"] else ""
                t = f"{s['nome']}: rinnovo il {r.strftime('%d/%m/%Y')}{prezzo}. Se non ti serve, disdici ora"
                if evento(c, f"rinnovo:{s['id']}:{iso(r)}", "rinnovo", t, oggi, servizio_id=s["id"]):
                    nuovi.append(t)
        if s["stato"] == "disdetto" and s["fine"]:
            f = data(s["fine"])
            if 0 <= (f - oggi).days <= GIORNI_RINNOVO:
                t = f"{s['nome']}: l'abbonamento finisce il {f.strftime('%d/%m/%Y')}"
                if evento(c, f"fine:{s['id']}:{iso(f)}", "scadenza", t, oggi, servizio_id=s["id"]):
                    nuovi.append(t)
            rischio = cronologia_a_rischio(s)
            if rischio and rischio - dt.timedelta(days=GIORNI_CRONOLOGIA) <= oggi:
                t = (f"{s['nome']}: dal {rischio.strftime('%d/%m/%Y')} la cronologia e i preferiti possono essere "
                     "cancellati. Riattiva almeno un mese prima di allora se vuoi conservarli")
                if evento(c, f"cronologia:{s['id']}:{iso(rischio)}", "cronologia", t, oggi, servizio_id=s["id"]):
                    nuovi.append(t)
    return nuovi
