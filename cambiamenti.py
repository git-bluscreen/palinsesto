"""Il piano e' cambiato? Confronto fra il piano di adesso e quello che l'utente ha
visto e approvato («Va bene»), per avvisarlo quando cambiano i mesi da pagare —
di solito perche' ha aggiunto titoli, o perche' di notte sono arrivate date nuove —
con il costo di ciascun titolo nuovo, cosi' puo' decidere di rimandarlo.

Si confrontano i servizi usati in ogni periodo, tolti quelli gratis per tutto il
piano (un annuale) e i titoli rimandati «solo se gratis», e solo nei periodi che
i due piani hanno in comune: col passare dei giorni un periodo esce e uno entra,
e questo non e' un cambiamento.
"""
import hashlib, json

import db, logica

META = "piano_visto"
MOSTRATO = "piano_mostrato"      # l'ultimo cambiamento mostrato in pagina: di notte non si riavvisa
MAX_COSTI = 5                    # titoli nuovi di cui si calcola il costo (un piano in piu' ciascuno)


def _sempre_gratis(p):
    per, pag = p["per"], p["pagato_iniziale"]
    return {sid for sid in p["servizi"]
            if sid in pag and all(pag[sid] >= a + logica.dt.timedelta(days=logica.PIANO_GIORNI // 2) for a, _ in per)}


def impronta(p):
    sempre = _sempre_gratis(p)
    usati = sorted({(x["s"]["id"], q["inizio"].isoformat()) for q in p["periodi"] for x in q["voci"]
                    if x["s"]["id"] not in sempre and any(not y.get("gratis") for y in x["titoli"])})
    titoli = sorted({y["t"]["id"] for q in p["periodi"] for x in q["voci"] for y in x["titoli"] if not y.get("gratis")})
    return dict(usati=[list(u) for u in usati], titoli=titoli, periodi=[a.isoformat() for a, _ in p["per"]])


def segna_visto(c, p):
    db.meta(c, META, json.dumps(impronta(p))); c.commit()


def stato(c, oggi, p):
    """None se il piano e' quello gia' visto; altrimenti dict(chiave, piu, meno,
    delta, nuovi=[dict(t, costo)]). La prima volta il piano di adesso diventa il visto."""
    try:
        visto = json.loads(db.meta(c, META) or "null")
    except ValueError:
        visto = None
    ora = impronta(p)
    if not visto:
        segna_visto(c, p); return None
    comuni = set(ora["periodi"]) & set(visto["periodi"])
    a = {tuple(u) for u in ora["usati"] if u[1] in comuni}
    b = {tuple(u) for u in visto["usati"] if u[1] in comuni}
    if a == b:
        if ora["titoli"] != visto["titoli"] or ora["periodi"] != visto["periodi"]:
            segna_visto(c, p)                     # cambiato qualcosa che non costa: si aggiorna in silenzio
        return None
    serv = p["servizi"]
    prezzo = lambda sid: logica.prezzo_mese(serv[sid], p["medio"])[0]
    inizio = {q["inizio"].isoformat(): q["inizio"] for q in p["periodi"]}
    nomi = lambda xs: [dict(s=serv[sid], dal=inizio[d]) for sid, d in sorted(xs, key=lambda y: (y[1], serv[y[0]]["nome"]))]
    titoli = {y["t"]["id"]: y["t"] for q in p["periodi"] for x in q["voci"] for y in x["titoli"]}
    nuovi = []
    for tid in [t for t in ora["titoli"] if t not in visto["titoli"]][:MAX_COSTI]:
        senza = logica.piano_attuale(c, oggi, senza={tid})
        nuovi.append(dict(t=titoli[tid], costo=round(p["spesa"] - senza["spesa"], 2)))
    return dict(chiave=hashlib.sha256(repr(sorted(a)).encode()).hexdigest()[:16], piu=nomi(a - b), meno=nomi(b - a),
                delta=round(sum(prezzo(s) for s, _ in a - b) - sum(prezzo(s) for s, _ in b - a), 2), nuovi=nuovi)


def testo(cam):
    """Una riga per ntfy."""
    pezzi = []
    if cam["piu"]:
        pezzi.append("in più " + ", ".join(f"{x['s']['nome']} dal {logica.breve(x['dal'])}" for x in cam["piu"]))
    if cam["meno"]:
        pezzi.append("in meno " + ", ".join(f"{x['s']['nome']} dal {logica.breve(x['dal'])}" for x in cam["meno"]))
    t = "Il piano è cambiato: " + "; ".join(pezzi)
    if cam["delta"]:
        t += f" ({'+' if cam['delta'] > 0 else '−'}{abs(cam['delta']):.2f} €)".replace(".", ",")
    if cam["nuovi"]:
        t += ". Titoli nuovi: " + ", ".join(f"«{n['t']['titolo']}»" for n in cam["nuovi"])
    return t + ". Apri il piano per decidere."
