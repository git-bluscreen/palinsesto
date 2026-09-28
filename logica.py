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


def in_pausa(s, oggi):
    """In pausa: attivo sulla carta, ma non si usa e non costa fino a `pausa_fino`,
    quando riparte da solo e addebita."""
    return bool(s["stato"] == "attivo" and s["pausa_fino"] and data(s["pausa_fino"]) > oggi)


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


def finito(c, t, oggi, dv):
    """Niente da vedere adesso. Film: segnato visto. Serie: almeno una stagione
    uscita, niente di uscito da vedere e nessuna stagione in corso (una serie in
    pari con episodi in arrivo non e' finita). Senza scheda completa: mai."""
    if t["tipo"] == "movie":
        return bool(t["visto"])
    if not t["dettagli"]:
        return False
    x = dv.get(t["id"])
    if x and (x["pronte"] or x["in_corso"]):
        return False
    return bool(c.execute("SELECT 1 FROM stagioni WHERE titolo_id=? AND numero>0 AND uscita<=?",
                          (t["id"], iso(oggi))).fetchone())


def sincronizza_da_vedere(c, oggi, solo=None):
    """Toglie da «Da vedere» cio' che e' finito, e ci rimette cio' che Palinsesto
    stesso aveva tolto quando torna qualcosa da vedere (stagione nuova, spunta
    tolta). Un titolo tolto A MANO non rientra: `tolto_auto` resta vuoto.
    Ritorna (tolti, rimessi) come titoli."""
    from db import meta
    lid = int(meta(c, "lista_da_vedere") or 0)
    if not lid or not c.execute("SELECT 1 FROM liste WHERE id=?", (lid,)).fetchone():
        return [], []
    dv = da_vedere(c, oggi)
    q = "SELECT t.*, m.visto, m.tolto_auto FROM miei m JOIN titoli t ON t.id=m.titolo_id" + (" WHERE t.id=?" if solo else "")
    tolti, rimessi = [], []
    for t in c.execute(q, (solo,) if solo else ()).fetchall():
        dentro = c.execute("SELECT 1 FROM lista_titoli WHERE lista_id=? AND titolo_id=?", (lid, t["id"])).fetchone()
        f = finito(c, t, oggi, dv)
        if f and dentro:
            c.execute("DELETE FROM lista_titoli WHERE lista_id=? AND titolo_id=?", (lid, t["id"]))
            c.execute("UPDATE miei SET tolto_auto=? WHERE titolo_id=?", (iso(oggi), t["id"]))
            tolti.append(t["titolo"])
        elif not f and not dentro and t["tolto_auto"]:
            c.execute("INSERT OR IGNORE INTO lista_titoli VALUES (?,?,?)", (lid, t["id"], iso(oggi)))
            c.execute("UPDATE miei SET tolto_auto=NULL WHERE titolo_id=?", (t["id"],))
            rimessi.append(t["titolo"])
    return tolti, rimessi


CONSIGLI_PESO = {"mi_piace": 3.0, "visto": 2.0, "lista": 1.0}
CONSIGLI_VOTO_MIN, CONSIGLI_VOTI_MIN = 6.3, 50     # sotto, TMDB consiglia anche cose che nessuno ha visto
CONSIGLI_CANDIDATI, CONSIGLI_TENUTI = 40, 24


def calcola_consigliati(c, api, oggi):
    """Somma i «consigliati» di TMDB di ogni mio titolo, pesati: mi piace > visto >
    in lista, e piu' in alto nella lista di TMDB = piu' punti. Scarta i miei, i
    nascosti, quelli con pochi voti o voto basso, e quelli che non si vedono su
    un servizio seguito; spinta del 25% a chi e' su un servizio a cui sono
    abbonato. Il motivo mostrato e' il titolo che ha contribuito di piu'.
    Sostituisce l'intera tabella: ritorna quanti consigli ha tenuto."""
    miei = {r[0] for r in c.execute("SELECT titolo_id FROM miei")}
    via = {r[0] for r in c.execute("SELECT titolo_id FROM nascosti")}
    dv = da_vedere(c, oggi)
    punti, motivi, dati = {}, {}, {}
    semi = c.execute("SELECT t.*, m.mi_piace, m.visto FROM miei m JOIN titoli t ON t.id=m.titolo_id").fetchall()
    for s in semi:
        peso = CONSIGLI_PESO["mi_piace"] if s["mi_piace"] else \
            CONSIGLI_PESO["visto"] if finito(c, s, oggi, dv) else CONSIGLI_PESO["lista"]
        for i, x in enumerate(api.consigliati(s["tipo"], s["tmdb_id"])[:20]):
            tipo = x.get("media_type") or s["tipo"]
            if tipo not in ("movie", "tv"):
                continue
            tid = f"{tipo}:{x['id']}"
            if tid in miei or tid in via or x.get("adult"):
                continue
            if (x.get("vote_count") or 0) < CONSIGLI_VOTI_MIN or (x.get("vote_average") or 0) < CONSIGLI_VOTO_MIN:
                continue
            p = peso * (1 - i / 25)
            punti[tid] = punti.get(tid, 0) + p
            motivi.setdefault(tid, {}).setdefault(s["titolo"], 0)
            motivi[tid][s["titolo"]] += p
            dati[tid] = (tipo, x)
    serv = {r["id"]: r for r in c.execute("SELECT * FROM servizi WHERE seguito=1")}
    mappa = {r["id"]: r["servizio_id"] for r in c.execute("SELECT id, servizio_id FROM provider WHERE servizio_id IS NOT NULL")}
    tenuti = []
    for tid in sorted(punti, key=punti.get, reverse=True)[:CONSIGLI_CANDIDATI]:
        tipo, x = dati[tid]
        it = api.provider_di(tipo, x["id"])
        dove = sorted({mappa[p["provider_id"]] for o in ABBONAMENTO for p in it.get(o, [])
                       if mappa.get(p["provider_id"]) in serv})
        if not dove:
            continue
        abbonato = any(serv[d]["stato"] == "attivo" for d in dove)
        primo = max(motivi[tid].items(), key=lambda y: y[1])[0]
        altri = len(motivi[tid]) - 1
        motivo = f"per «{primo}»" + (f" e altri {altri}" if altri else "")
        tenuti.append((tid, tipo, x, punti[tid] * (1.25 if abbonato else 1), motivo, dove))
    tenuti.sort(key=lambda y: y[3], reverse=True)
    c.execute("DELETE FROM consigliati")
    for tid, tipo, x, p, motivo, dove in tenuti[:CONSIGLI_TENUTI]:
        salva_base(c, tipo, x)
        c.execute("INSERT INTO consigliati VALUES (?,?,?,?,?)", (tid, round(p, 3), motivo, ",".join(map(str, dove)), iso(oggi)))
    return min(len(tenuti), CONSIGLI_TENUTI)


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
        pausa = in_pausa(s, oggi)
        attivo = (s["stato"] == "attivo" and not pausa) or (s["stato"] == "disdetto" and s["fine"] and data(s["fine"]) >= oggi)
        if pronti:
            n = len(pronti)
            verdetto = "sfrutta" if attivo else "attiva"
            testo = (f"{n} titol{'o' if n == 1 else 'i'} da vedere" +
                     (f", hai tempo fino al {gm(s['fine'])}" if s["stato"] == "disdetto" and attivo else ""))
        elif attese:
            prima = attese[0][0]
            verdetto = "aspetta"
            testo = f"Aspetta fino al {prima.strftime('%d/%m/%Y')}: {attese[0][1]['t']['titolo']}"
        elif pausa:
            verdetto = "niente"
            testo = f"In pausa fino al {gm(s['pausa_fino'])}: poi riparte da solo"
        elif attivo and s["stato"] == "attivo":
            verdetto = "disdici"
            testo = "Niente di nuovo nelle tue liste" + (f": puoi disdire prima del {gm(s['rinnovo'])}" if s["rinnovo"] else "")
        else:
            verdetto = "niente"
            testo = "Niente di nuovo nelle tue liste"
        rischio = cronologia_a_rischio(s)
        out.append(dict(s=s, verdetto=verdetto, testo=testo, pronti=pronti, attese=attese, attivo=attivo,
                        pausa=s["pausa_fino"] if pausa else None,
                        rischio=rischio, rischio_vicino=bool(rischio and rischio - dt.timedelta(days=GIORNI_CRONOLOGIA) <= oggi)))
    return out


# --- abbonamenti --------------------------------------------------------------
def scadenze(c, oggi):
    """Avanza i rinnovi passati (un abbonamento attivo si rinnova da solo) e
    registra gli eventi di rinnovo, fine e cronologia. Ritorna i testi nuovi."""
    nuovi = []
    for s in c.execute("SELECT * FROM servizi").fetchall():
        if s["stato"] == "attivo" and s["pausa_fino"] and data(s["pausa_fino"]) <= oggi:
            # la pausa e' finita: il servizio e' ripartito da solo e ha addebitato
            c.execute("UPDATE servizi SET pausa_fino=NULL, rinnovo=COALESCE(rinnovo, pausa_fino) WHERE id=?", (s["id"],))
            t = f"{s['nome']}: finita la pausa, l'abbonamento è ripartito"
            if evento(c, f"pausa-finita:{s['id']}:{s['pausa_fino']}", "rinnovo", t, oggi, servizio_id=s["id"]):
                nuovi.append(t)
            s = c.execute("SELECT * FROM servizi WHERE id=?", (s["id"],)).fetchone()
        if s["stato"] == "attivo" and s["pausa_fino"]:
            f = data(s["pausa_fino"])
            if (f - oggi).days <= GIORNI_RINNOVO:
                t = f"{s['nome']}: la pausa finisce il {f.strftime('%d/%m/%Y')} e riparte da solo. Se non ti serve, prolungala o disdici"
                if evento(c, f"pausa:{s['id']}:{iso(f)}", "rinnovo", t, oggi, servizio_id=s["id"]):
                    nuovi.append(t)
            continue
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


# --- piano di rotazione -------------------------------------------------------
# Quale servizio attivare in quale mese, per vedere tutto pagando meno mesi.
# Periodi di 30 giorni da oggi (un abbonamento mensile dura 30 giorni da quando
# lo attivi, non un mese di calendario). Le ipotesi, dette anche nella pagina:
# una stagione si guarda quando e' completa, tutta di seguito; si guardano al
# massimo `ore_mese` ore al mese; un servizio gia' pagato si usa per primo.
PIANO_PERIODI = 6
PIANO_GIORNI = 30
ORE_MESE = 25              # predefinito: si cambia dalla pagina del piano (meta «ore_mese»)
ATTESA_MAX = 3             # periodi: oltre, un titolo pronto non aspetta piu' compagnia
ATTESA_UNIONE = 2          # periodi: si aspetta se entro cosi' arriva altro sullo stesso servizio
MIN_EPISODIO = 45          # minuti di un episodio senza durata nota
MIN_FILM = 110
MESI_BREVI = ["gen", "feb", "mar", "apr", "mag", "giu", "lug", "ago", "set", "ott", "nov", "dic"]


def breve(d):
    """date(2026, 10, 3) -> '3 ott'"""
    return f"{d.day} {MESI_BREVI[d.month - 1]}"


def fine_stagione(c, tid, n):
    """(data dell'ultimo episodio, stimata?) Stimata = una alla settimana dalla
    prima uscita, quando TMDB non ha ancora tutte le date."""
    s = c.execute("SELECT * FROM stagioni WHERE titolo_id=? AND numero=?", (tid, n)).fetchone()
    if not s:
        return None, True
    # TMDB elenca solo gli episodi annunciati: una stagione di una rete TV che ne
    # ha 4 su 20 sembrerebbe completa fra un mese. Se ne ha molti meno della
    # precedente, la fine si stima sulla precedente.
    prima = c.execute("SELECT episodi FROM stagioni WHERE titolo_id=? AND numero=?", (tid, n - 1)).fetchone()
    t = c.execute("SELECT stato FROM titoli WHERE id=?", (tid,)).fetchone()
    if prima and prima["episodi"] and s["uscita"] and t and t["stato"] not in STATI_FINE \
            and (s["episodi"] or 0) < prima["episodi"] * .75:
        return data(s["uscita"]) + dt.timedelta(days=7 * (prima["episodi"] - 1)), True
    if s["fine"]:
        return data(s["fine"]), False
    date = [r[0] for r in c.execute("SELECT uscita FROM episodi WHERE titolo_id=? AND stagione=?", (tid, n))]
    if date and all(date) and len(date) >= (s["episodi"] or 0):
        return data(max(date)), False
    if not s["uscita"]:
        return None, True
    return data(s["uscita"]) + dt.timedelta(days=7 * max((s["episodi"] or 8) - 1, 0)), True


def minuti_episodio(c, t):
    if t["durata"]:
        return t["durata"]
    r = c.execute("SELECT AVG(durata) FROM episodi WHERE titolo_id=? AND durata>0", (t["id"],)).fetchone()[0]
    return round(r) if r else MIN_EPISODIO


def ore_stagione(c, t, n, visti):
    """Ore che restano da guardare di una stagione (episodi non visti)."""
    std = minuti_episodio(c, t)
    eps = c.execute("SELECT numero, durata FROM episodi WHERE titolo_id=? AND stagione=?", (t["id"], n)).fetchall()
    s = c.execute("SELECT episodi FROM stagioni WHERE titolo_id=? AND numero=?", (t["id"], n)).fetchone()
    attesi = max(len(eps), s["episodi"] or 0 if s else 0) or 8
    prima = c.execute("SELECT episodi FROM stagioni WHERE titolo_id=? AND numero=?", (t["id"], n - 1)).fetchone()
    if prima and prima["episodi"] and t["stato"] not in STATI_FINE and attesi < prima["episodi"] * .75:
        attesi = prima["episodi"]                 # come in fine_stagione: annunciati solo i primi
    noti = sum(e["durata"] or std for e in eps if e["numero"] not in visti)
    return (noti + max(attesi - len(eps), 0) * std) / 60


def lavori(c, oggi):
    """Cosa c'e' da guardare, come pezzi da mettere nel piano: un film, le
    stagioni pronte di una serie, ogni stagione in corso o annunciata (pronta
    quando finisce). Ritorna (pianificabili, esclusi)."""
    from db import episodi_visti
    dv = da_vedere(c, oggi)
    pezzi, esclusi = [], []
    for tid, x in dv.items():
        t = x["t"]
        dove = servizi_disponibili(c, tid)
        voci = []
        if t["tipo"] == "movie":
            voci.append(dict(cosa="Film", pronto=oggi, stimata=False, ore=(t["durata"] or MIN_FILM) / 60))
        else:
            visti = episodi_visti(c, tid, t)
            if x["pronte"]:
                p = x["pronte"]
                cosa = f"Stagione {p[0]}" if len(p) == 1 else f"Stagioni {p[0]}–{p[-1]}" \
                    if p == list(range(p[0], p[-1] + 1)) else "Stagioni " + ", ".join(map(str, p))
                voci.append(dict(cosa=cosa, pronto=oggi, stimata=False,
                                 ore=sum(ore_stagione(c, t, n, visti.get(n, set())) for n in p)))
            for n, _ in x["in_corso"] + x["in_arrivo"]:
                f, stimata = fine_stagione(c, tid, n)
                voci.append(dict(cosa=f"Stagione {n}", pronto=f, stimata=stimata,
                                 ore=ore_stagione(c, t, n, visti.get(n, set()))))
        for v in voci:
            v.update(t=t, dove=dove, ore=round(v["ore"], 1))
            if not dove:
                v["perche"] = "su nessun servizio che segui"
            elif not v["pronto"]:
                v["perche"] = "data di uscita sconosciuta"
            (esclusi if v.get("perche") else pezzi).append(v)
    return pezzi, esclusi


def prezzo_mese(s, medio):
    """(euro per un periodo di 30 giorni, stimato?) Un annuale costa l'anno intero."""
    if s["prezzo"]:
        return s["prezzo"], False
    return medio, True


def ancora_piano(c, oggi):
    """Il giorno da cui partono i periodi, fissato la prima volta. Con periodi
    contati da oggi le date scivolerebbero di un giorno ogni notte, e un
    «attiva verso il 28/10» non arriverebbe mai: sul calendario non si puo'."""
    from db import meta
    a = meta(c, "piano_ancora")
    if not a:
        a = iso(oggi); meta(c, "piano_ancora", a); c.commit()
    return data(a)


def piano(c, oggi, ore_mese=ORE_MESE, periodi=PIANO_PERIODI, ancora=None):
    """Il piano: per ogni periodo, quali servizi tenere o attivare e cosa guardarci.
    Greedy e spiegabile, non ottimo: prima i servizi gia' pagati, poi chi deve
    continuare, poi i nuovi dal piu' carico. Un servizio si attiva se ha
    abbastanza da guardare, se un titolo aspetta da troppo, o se nei prossimi
    periodi non arriva altro da unire; altrimenti si rimanda, col motivo."""
    serv = {s["id"]: s for s in c.execute("SELECT * FROM servizi WHERE seguito=1 ORDER BY ordine, nome")}
    noti = sorted(s["prezzo"] for s in serv.values() if s["prezzo"] and s["ciclo"] == "mese")
    medio = noti[len(noti) // 2] if noti else 0.0
    ancora = min(ancora or oggi, oggi)
    primo = (oggi - ancora).days // PIANO_GIORNI
    per = [(ancora + dt.timedelta(days=PIANO_GIORNI * (primo + i)), ancora + dt.timedelta(days=PIANO_GIORNI * (primo + i + 1) - 1))
           for i in range(periodi)]
    pagato = {}                                    # servizio -> ultimo giorno gia' pagato
    for sid, s in serv.items():
        if s["stato"] == "attivo" and in_pausa(s, oggi):
            continue                               # in pausa: non si usa finche' non riparte o si riprende
        if s["stato"] == "attivo":
            pagato[sid] = data(s["rinnovo"]) - dt.timedelta(days=1) if s["rinnovo"] else per[0][1]
        elif s["stato"] == "disdetto" and s["fine"] and data(s["fine"]) >= oggi:
            pagato[sid] = data(s["fine"])

    def coperto(sid, i):
        """Pagato per almeno meta' del periodo: si usa senza spendere."""
        return sid in pagato and pagato[sid] >= per[i][0] + dt.timedelta(days=PIANO_GIORNI // 2)

    def periodo_di(d):
        return min(max((d - per[0][0]).days, 0) // PIANO_GIORNI, periodi - 1)

    pezzi, esclusi = lavori(c, oggi)
    resto = sorted(pezzi, key=lambda v: (v["pronto"], v["t"]["titolo"] or ""))
    for v in resto:
        v["dal"] = periodo_di(v["pronto"])
    out, acceso_prima = [], set()
    for i, (ini, fin) in enumerate(per):
        # il primo periodo e' gia' in parte passato: restano meno ore
        disponibili = ore_mese * ((fin - oggi).days + 1) / PIANO_GIORNI if i == 0 else ore_mese
        cap = float(disponibili)
        pronti = lambda sid: [v for v in resto if sid in v["dove"] and v["pronto"] <= fin - dt.timedelta(days=7)]
        voci, rinvii = {}, []

        def assegna(sid, azione):
            nonlocal cap
            s = serv[sid]
            if azione == "pagato":
                costo, stimato = 0.0, False
            else:
                costo, stimato = prezzo_mese(s, medio)
                if s["ciclo"] == "anno":
                    pagato[sid] = ini + dt.timedelta(days=364)
            x = dict(s=s, azione=azione, titoli=[], ore=0.0, costo=costo, stimato=stimato)
            for v in pronti(sid):
                if cap <= 0.5:
                    break
                if v["ore"] <= cap + 1:            # un'ora di tolleranza: un episodio in piu' non sposta il mese
                    x["titoli"].append(dict(v, parte="il resto" if v.get("iniziato") else ""))
                    x["ore"] += v["ore"]; cap -= v["ore"]; resto.remove(v)
                elif cap >= 2:                     # troppo per un mese: se ne guarda una parte, il resto continua
                    x["titoli"].append(dict(v, ore=round(cap, 1), parte="un'altra parte" if v.get("iniziato") else "una parte"))
                    x["ore"] += cap; v["ore"] = round(v["ore"] - cap, 1); v["iniziato"] = True; cap = 0
                else:
                    break
            if x["titoli"]:
                voci[sid] = x
            return x

        # 1) cio' che e' gia' pagato, a costo zero
        for sid in serv:
            if coperto(sid, i):
                assegna(sid, "pagato")
        # 2) il resto, chi era acceso prima per primo, poi dal piu' carico
        ore_su = {sid: sum(v["ore"] for v in pronti(sid)) for sid in serv if sid not in voci}
        for sid in sorted((s for s in ore_su if ore_su[s] > 0), key=lambda s: (s not in acceso_prima, -ore_su[s])):
            mie = pronti(sid)
            if not mie:
                continue                            # presi da un servizio scelto prima in questo periodo
            h = sum(v["ore"] for v in mie)
            s = serv[sid]
            if cap < min(h, disponibili / 2):
                rinvii.append(dict(s=s, motivo="prima finisci quello che hai già in questo periodo"))
                continue
            limite = per[min(i + ATTESA_UNIONE, periodi - 1)][1] - dt.timedelta(days=7)
            unire = [v for v in resto if sid in v["dove"] and v not in mie and v["pronto"] <= limite
                     and not any(coperto(o, periodo_di(v["pronto"])) for o in v["dove"])]
            continua = sid in acceso_prima
            aspettato = max(i - v["dal"] for v in mie) >= ATTESA_MAX
            if continua or aspettato or h >= disponibili * .8 or not unire or i == periodi - 1:
                if in_pausa(s, oggi):
                    assegna(sid, "riprendi" if ini < data(s["pausa_fino"]) else "rinnova")
                else:
                    gia = continua or (s["stato"] == "attivo" and i == 0)
                    assegna(sid, "rinnova" if gia else "attiva")
            else:
                u = unire[0]
                rinvii.append(dict(s=s, motivo=f"aspetta: verso il {breve(u['pronto'])} è pronto anche «{u['t']['titolo']}» ({u['cosa'].lower()})"))
        # chi e' acceso e non ha finito continua nel periodo dopo, prima dei nuovi
        acceso_prima = {sid for sid, x in voci.items() if x["titoli"] and
                        (x["azione"] != "pagato" or (i + 1 < periodi and not coperto(sid, i + 1)))}
        out.append(dict(i=i, inizio=ini, fine=fin, voci=list(voci.values()), rinvii=rinvii,
                        ore=round(disponibili - cap, 1), disponibili=round(disponibili, 1), costo=sum(x["costo"] for x in voci.values())))

    # cosa fare adesso, servizio per servizio
    azioni = []
    for sid, s in serv.items():
        usi = [p["i"] for p in out for x in p["voci"] if x["s"]["id"] == sid and x["titoli"]]
        pagati = [p["i"] for p in out for x in p["voci"] if x["s"]["id"] == sid and x["azione"] != "pagato"]
        if in_pausa(s, oggi):
            pf = data(s["pausa_fino"])
            prima = [i for i in pagati if per[i][0] < pf]
            subito = [i for i in pagati if per[i][1] >= pf and i <= periodo_di(pf) + 1]
            dopo = [i for i in pagati if per[i][1] >= pf]
            n = s["pausa_proroghe"]            # None = non si sa (i servizi lo propongono a ridosso, e cambiano)
            if prima:
                q = max(per[prima[0]][0], oggi)
                azioni.append(dict(s=s, tipo="riprendi", quando=q, pausa=pf,
                                   testo="Riprendilo dalla pausa adesso" if q == oggi else f"Riprendilo dalla pausa verso il {breve(q)}"))
            elif subito:
                azioni.append(dict(s=s, tipo="tieni", quando=pf, pausa=pf, testo=f"In pausa fino al {breve(pf)}: poi riparte, e ti serve"))
            elif n:
                azioni.append(dict(s=s, tipo="proroga", quando=pf, pausa=pf,
                                   testo=f"La pausa finisce il {breve(pf)}: prolungala (ne rest{'a' if n == 1 else 'ano'} {n})"
                                         + (f"; ti servirà verso il {breve(per[dopo[0]][0])}" if dopo else "")))
            elif n is None:
                azioni.append(dict(s=s, tipo="disdici", quando=pf, pausa=pf, forse=True,
                                   testo=f"La pausa finisce il {breve(pf)}: prolungala se te lo propone, altrimenti disdici prima che riparta"
                                         + (f"; ti servirà verso il {breve(per[dopo[0]][0])}" if dopo else "")))
            else:
                azioni.append(dict(s=s, tipo="disdici", quando=pf, pausa=pf,
                                   testo=f"La pausa finisce il {breve(pf)}: disdici prima, o riparte e addebita"
                                         + (f"; ti servirà verso il {breve(per[dopo[0]][0])}" if dopo else "")))
            continue
        if s["stato"] == "attivo" and s["ciclo"] == "mese":
            r = data(s["rinnovo"]) if s["rinnovo"] else None
            k = next((i for i in range(periodi) if not coperto(sid, i)), None)
            if k is not None and k in pagati:
                azioni.append(dict(s=s, tipo="tieni", quando=r, testo="Tienilo" + (f": al rinnovo del {breve(r)} ti serve ancora" if r else "")))
            else:
                dopo = next((i for i in pagati if k is not None and i > k), None)
                testo = (f"Disdici prima del {breve(r)}" if r else "Disdici") + ": fino ad allora resta attivo"
                if dopo is not None:
                    testo += f"; riattivalo verso il {breve(per[dopo][0])}"
                azioni.append(dict(s=s, tipo="disdici", quando=r or oggi, testo=testo))
        elif s["stato"] == "disdetto" and sid in pagato and not pagati:
            testo = f"Già disdetto, attivo fino al {breve(pagato[sid])}"
            azioni.append(dict(s=s, tipo="pagato", quando=pagato[sid],
                               testo=testo + (": usalo per quello che il piano gli assegna" if usi else "")))
        elif s["stato"] == "attivo":
            azioni.append(dict(s=s, tipo="pagato", quando=pagato.get(sid), testo=f"Annuale, pagato fino al {breve(data(s['rinnovo']))}" if s["rinnovo"] else "Annuale"))
        elif pagati:
            j = pagati[0]
            azioni.append(dict(s=s, tipo="attiva", quando=per[j][0],
                               testo="Attivalo adesso" if j == 0 else f"Attivalo verso il {breve(per[j][0])}"))
        elif not usi:
            azioni.append(dict(s=s, tipo="niente", quando=None, testo="Non ti serve nei prossimi sei mesi"))
    ordine = {"disdici": 0, "proroga": 0, "riprendi": 1, "attiva": 1, "tieni": 2, "pagato": 3, "niente": 4}
    azioni.sort(key=lambda a: (ordine[a["tipo"]], a["quando"] or dt.date.max))

    oltre = list(resto)
    spesa = round(sum(p["costo"] for p in out), 2)
    # confronto: tenere attivi, per tutti i periodi, i mensili attivi oggi
    oggi_attivi = round(sum((s["prezzo"] or medio) * periodi for s in serv.values()
                            if s["stato"] == "attivo" and s["ciclo"] == "mese" and not in_pausa(s, oggi)), 2)
    tutti = round(sum((s["prezzo"] or medio) * (periodi if s["ciclo"] == "mese" else 0) for s in serv.values()), 2)
    return dict(periodi=out, azioni=azioni, oltre=oltre, esclusi=esclusi, spesa=spesa, oggi_attivi=oggi_attivi,
                tutti=tutti, stimati=[s["nome"] for s in serv.values() if not s["prezzo"]], medio=medio, ore_mese=ore_mese)


def promemoria(c, oggi, p):
    """Gli eventi del calendario dal piano, con UID stabili: lo stesso promemoria
    si sposta invece di duplicarsi quando il piano cambia.
    - servizio mensile attivo: «Disdici» il giorno prima del rinnovo in cui non
      serve piu' (dopo i periodi a pagamento che il piano gli assegna ancora);
    - servizio non attivo: per ogni tratto di periodi consecutivi, «Attiva» al
      primo giorno e «Disdici» due giorni prima dei 30 giorni pagati.
    Quando l'utente segna l'attivazione in Abbonamenti, il servizio diventa
    attivo e i due eventi del tratto lasciano il posto al «Disdici» del rinnovo."""
    serv = {s["id"]: s for s in c.execute("SELECT * FROM servizi WHERE seguito=1")}
    pagati = {}
    for q in p["periodi"]:
        for x in q["voci"]:
            if x["azione"] != "pagato":
                pagati.setdefault(x["s"]["id"], {})[q["i"]] = (q, x)
    ev = []

    def titoli(voci):
        t = []
        for q, x in voci:
            for y in x["titoli"]:
                if y["t"]["titolo"] not in t:
                    t.append(y["t"]["titolo"])
        return t

    azioni = {a["s"]["id"]: a for a in p["azioni"]}
    for sid, s in serv.items():
        mie = pagati.get(sid, {})
        if in_pausa(s, oggi):
            a, pf = azioni[sid], data(s["pausa_fino"])
            if a["tipo"] == "riprendi":
                ev.append(dict(uid=f"riprendi-{sid}", giorno=a["quando"], avviso=0, titolo=f"Riprendi {s['nome']} dalla pausa",
                               testo=f"Da guardare: {', '.join(titoli(mie.values()))}. Poi segnalo in Palinsesto: «L'ho ripreso oggi»."))
            elif a["tipo"] == "proroga":
                ev.append(dict(uid=f"pausa-{sid}", giorno=max(pf - dt.timedelta(days=1), oggi), avviso=2,
                               titolo=f"{s['nome']}: prolunga la pausa",
                               testo=f"La pausa finisce il {pf.strftime('%d/%m/%Y')} e poi riparte e addebita. Prolungala "
                                     f"(proroghe rimaste: {s['pausa_proroghe']}) o disdici, poi segnalo in Palinsesto."))
            elif a["tipo"] == "disdici":
                forse = a.get("forse")
                ev.append(dict(uid=f"disdici-{sid}", giorno=max(pf - dt.timedelta(days=1), oggi), avviso=2,
                               titolo=f"{s['nome']}: prolunga la pausa o disdici" if forse else f"Disdici {s['nome']}",
                               testo=f"La pausa finisce il {pf.strftime('%d/%m/%Y')}: se non fai niente, riparte e addebita. "
                                     + ("Se il servizio ti propone di prolungarla, prolungala; altrimenti disdici. " if forse else "")
                                     + "Poi segnalo in Palinsesto."))
            else:
                ev.append(dict(uid=f"riparte-{sid}", giorno=pf, avviso=0, titolo=f"{s['nome']} riparte dalla pausa",
                               testo=f"Finisce la pausa e riparte l'abbonamento: ti serve per {', '.join(titoli(mie.values()))}."))
            continue
        if s["stato"] == "attivo" and s["ciclo"] == "mese" and s["rinnovo"]:
            r = data(s["rinnovo"])
            # periodi pagati consecutivi subito dopo la copertura di oggi: tanti rinnovi quanti servono
            k = next((i for i in range(len(p["periodi"])) if i not in mie and p["periodi"][i]["inizio"] >= r), None)
            n = len([i for i in mie if k is None or i < k])
            fine = r + dt.timedelta(days=PIANO_GIORNI * n)
            ev.append(dict(uid=f"disdici-{sid}", giorno=max(fine - dt.timedelta(days=1), oggi), avviso=2,
                           titolo=f"Disdici {s['nome']}",
                           testo=f"Il rinnovo del {fine.strftime('%d/%m/%Y')} non ti serve: disdici prima, resti abbonato fino ad allora."
                                 + (f"\nDa finire prima: {', '.join(titoli(mie.values()))}." if mie else "")))
            continue
        if s["stato"] == "attivo":
            continue                                   # annuale: niente da ricordare nei sei mesi
        tratti = []
        for i in sorted(mie):
            if tratti and tratti[-1][-1] == i - 1:
                tratti[-1].append(i)
            else:
                tratti.append([i])
        for n, t in enumerate(tratti):
            voci = [mie[i] for i in t]
            inizio = max(voci[0][0]["inizio"], oggi)
            fine = inizio + dt.timedelta(days=PIANO_GIORNI * len(t))
            costo = sum(x["costo"] for _, x in voci)
            ev.append(dict(uid=f"attiva-{sid}-{n}", giorno=inizio, avviso=0, titolo=f"Attiva {s['nome']}",
                           testo=f"Da guardare: {', '.join(titoli(voci))}.\n{len(t)} mes{'e' if len(t) == 1 else 'i'}, "
                                 f"{costo:.2f} euro. Segna l'attivazione in Palinsesto, Abbonamenti, con la data del rinnovo."))
            ev.append(dict(uid=f"disdici-{sid}-{n}", giorno=fine - dt.timedelta(days=2), avviso=2,
                           titolo=f"Disdici {s['nome']}",
                           testo=f"Se l'hai attivato il {inizio.strftime('%d/%m/%Y')}, si rinnova il {fine.strftime('%d/%m/%Y')}: "
                                 "disdici prima. La data esatta e' quella che segni in Abbonamenti."))
    return ev
