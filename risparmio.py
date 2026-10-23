"""Quanto si potrebbe risparmiare: il piano (greedy, spiegabile) contro la
combinazione piu' economica possibile degli stessi servizi negli stessi periodi.

Il confronto e' alla pari: il minimo deve far vedere le STESSE ore degli STESSI
titoli che il piano mette nei suoi periodi, con le stesse regole — un titolo si
guarda da quando e' pronto (almeno 7 giorni prima della fine del periodo), su un
servizio che lo ha e che in quel periodo e' gia' pagato o si paga, senza superare
le ore del periodo. Non conta invece cio' che il piano fa per comodita': far
continuare chi era acceso, non far aspettare un titolo piu' di qualche periodo,
guardare un servizio alla volta.

Ricerca esatta (ramo e limite): si accendono o no le coppie (servizio, periodo);
un ramo si taglia se costa gia' quanto il migliore trovato, o se nemmeno
accendendo tutto il resto si riesce a vedere tutto (flusso massimo). Il primo
«migliore» e' il piano stesso, quindi il minimo non costa mai di piu'. Oltre un
tetto di passi si ferma e lo dice: «il migliore trovato», non «il minimo».
"""
import collections, datetime as dt, hashlib, math, threading, time

import logica

GIORNI_PRIMA = 7            # come `pronti` nel piano: pronto almeno una settimana prima della fine
TEMPO_MAX = 3.0             # secondi di ricerca, poi si ferma (chiamata diretta)
TEMPO_SOTTOFONDO = 180      # secondi di ricerca dalla pagina, in sottofondo

_risultati, _in_corso, _lucchetto = {}, set(), threading.Lock()


def chiave(oggi, p):
    """Impronta di cio' da cui dipende il confronto: se il piano non cambia, il
    risultato si riusa invece di ricalcolarlo."""
    dati = (str(oggi), p["spesa"], [str(a) for a in p["per"]],
            sorted((k, str(v)) for k, v in p["pagato_iniziale"].items()),
            sorted((sid, s["prezzo"], s["ciclo"]) for sid, s in p["servizi"].items()),
            [(q["i"], q["disponibili"], q["ore"], [(x["s"]["id"], x["azione"], [(y["t"]["id"], y["cosa"], y["ore"], str(y["pronto"]),
               tuple(sorted(y["dove"]))) for y in x["titoli"]]) for x in q["voci"]]) for q in p["periodi"]])
    return hashlib.sha256(repr(dati).encode()).hexdigest()[:20]


def in_sottofondo(oggi, p):
    """Il confronto se e' pronto, altrimenti None e lo calcola in un filo a parte
    (la pagina si ricarica da sola): la ricerca esatta puo' chiedere decine di secondi."""
    k = chiave(oggi, p)
    with _lucchetto:
        if k in _risultati:
            return _risultati[k]
        if k in _in_corso:
            return None
        _in_corso.add(k)

    def lavora():
        try:
            r = confronto(None, oggi, p, tempo_max=TEMPO_SOTTOFONDO)
        except Exception as e:                    # si vede in pagina invece di girare per sempre
            r = dict(errore=f"{type(e).__name__}: {e}"[:200])
        with _lucchetto:
            _risultati[k] = r; _in_corso.discard(k)
            for vecchia in list(_risultati)[:-8]:
                del _risultati[vecchia]
    threading.Thread(target=lavora, daemon=True, name="risparmio").start()
    return None


def _flusso(domanda, archi, capacita):
    """Flusso massimo intero: sorgente -> pezzo (domanda) -> periodo (se l'arco
    c'e') -> pozzo (capacita'). Ritorna (flusso, assegnazione {(pezzo, periodo): decimi})."""
    n_p, n_i = len(domanda), len(capacita)
    S, T = n_p + n_i, n_p + n_i + 1
    adj = [[] for _ in range(T + 1)]                # arco = [verso, capacita', indice del gemello]

    def arco(a, b, c):
        adj[a].append([b, c, len(adj[b])]); adj[b].append([a, 0, len(adj[a]) - 1])
    for k, d in enumerate(domanda):
        arco(S, k, d)
        for i in archi[k]:
            arco(k, n_p + i, d)
    for i, c in enumerate(capacita):
        arco(n_p + i, T, c)
    tot = 0
    while True:                                   # Edmonds-Karp: il grafo ha poche decine di nodi
        prima = [None] * (T + 1); prima[S] = (S, -1)
        coda = collections.deque([S])
        while coda and prima[T] is None:
            u = coda.popleft()
            for j, (v, c, _) in enumerate(adj[u]):
                if c > 0 and prima[v] is None:
                    prima[v] = (u, j); coda.append(v)
        if prima[T] is None:
            break
        f, v = math.inf, T
        while v != S:
            u, j = prima[v]; f = min(f, adj[u][j][1]); v = u
        v = T
        while v != S:
            u, j = prima[v]; e = adj[u][j]; e[1] -= f; adj[v][e[2]][1] += f; v = u
        tot += f
    usato = {}
    for k in range(n_p):
        for v, c, r in adj[k]:
            if n_p <= v < n_p + n_i and adj[v][r][1] > 0:
                usato[(k, v - n_p)] = adj[v][r][1]
    return tot, usato


class Modello:
    """Il problema del piano come insiemi di coppie (servizio, periodo) da pagare:
    cio' che il piano fa vedere (pezzi e ore), quando e' pronto, dove si vede, le ore
    di ogni periodo, cio' che e' gia' pagato. Lo usano il confronto (minimo esatto)
    e il piano stesso (`migliora`: ricerca locale veloce)."""

    def __init__(self, p, oggi=None):
        self.p = p
        self.per, self.serv, self.pagato = p["per"], p["servizi"], p["pagato_iniziale"]
        self.n = n = len(self.per)
        self.medio = p["medio"]
        pezzi = {}                                # un pezzo diviso fra piu' periodi si somma
        for q in p["periodi"]:
            for x in q["voci"]:
                for y in x["titoli"]:
                    if y.get("gratis"):           # rimandato «solo se gratis»: non chiede mesi
                        continue
                    k = (y["t"]["id"], y["cosa"])
                    z = pezzi.setdefault(k, dict(t=y["t"], cosa=y["cosa"], pronto=y["pronto"], dove=list(y["dove"]), ore=0.0, orig=y))
                    z["ore"] += y["ore"]
        self.pezzi = [z for z in pezzi.values() if z["ore"] > 0]
        self.domanda = [max(1, round(z["ore"] * 10)) for z in self.pezzi]       # in decimi d'ora: flusso intero
        self.capacita = [math.ceil(max(q["disponibili"], q["ore"]) * 10 + 1) for q in p["periodi"]]   # il piano tollera un'ora in piu'
        self.pronto_da = [next((i for i in range(n) if z["pronto"] <= self.per[i][1] - dt.timedelta(days=GIORNI_PRIMA)), n)
                          for z in self.pezzi]
        self.utili = {sid for z in self.pezzi for sid in z["dove"]}
        self.annuale = {sid for sid in self.utili if self.serv[sid]["ciclo"] == "anno"}
        # le coppie che si possono pagare: non gia' coperte, con qualcosa di pronto
        self.coppie = [(sid, i) for i in range(n) for sid in sorted(self.utili)
                       if not self.coperto(sid, i) and any(sid in z["dove"] and self.pronto_da[k] <= i for k, z in enumerate(self.pezzi))]
        # come nel piano: un mensile attivato nel periodo in corso vale 30 giorni da oggi,
        # e copre anche il periodo dopo se ne prende almeno meta'
        o = p.get("oggi") or oggi or self.per[0][0]
        self.copre_dopo = n > 1 and o + dt.timedelta(days=logica.PIANO_GIORNI - 1) >= self.per[1][0] + dt.timedelta(days=logica.PIANO_GIORNI // 2)
        self.del_piano = sorted({(x["s"]["id"], q["i"]) for q in p["periodi"] for x in q["voci"] if x["azione"] != "pagato"},
                                key=lambda y: (y[1], y[0]))

    def coperto(self, sid, i):
        return sid in self.pagato and self.pagato[sid] >= self.per[i][0] + dt.timedelta(days=logica.PIANO_GIORNI // 2)

    def prezzo(self, sid):
        return logica.prezzo_mese(self.serv[sid], self.medio)[0]

    def periodi_di(self, sid, i):
        """I periodi che una coppia pagata rende usabili."""
        if sid in self.annuale:
            return range(i, self.n)
        return (0, 1) if i == 0 and self.copre_dopo else (i,)

    def attivi(self, scelte):
        """{periodo: servizi usabili}: coperti, pagati quel mese, annuali comprati prima."""
        su = {i: {sid for sid in self.utili if self.coperto(sid, i)} for i in range(self.n)}
        for sid, i in scelte:
            for j in self.periodi_di(sid, i):
                su[j].add(sid)
        return su

    def costo(self, scelte):
        return round(sum(self.prezzo(sid) for sid, _ in scelte), 2)

    def fattibile(self, scelte):
        su = self.attivi(scelte)
        archi = [[i for i in range(self.pronto_da[k], self.n) if su[i] & set(z["dove"])] for k, z in enumerate(self.pezzi)]
        f, usato = _flusso(self.domanda, archi, self.capacita)
        return f >= sum(self.domanda), usato


def migliora(p, oggi=None):
    """Ricerca locale sul piano: togliere un mese pagato, sostituirlo con uno piu'
    economico, o sostituirne due con uno, finche' si riesce a vedere tutto. Veloce
    (centinaia di flussi su un grafo minuscolo), quindi gira a ogni calcolo del piano.
    Ritorna (modello, coppie scelte, assegnazione) o None se non migliora."""
    m = Modello(p, oggi)
    if not m.pezzi or not m.del_piano:
        return None
    scelte = list(m.del_piano)
    if not m.fattibile(scelte)[0]:
        return None
    candidate = sorted(set(m.coppie) | set(scelte), key=lambda y: (m.prezzo(y[0]), y[1]))
    migliorato = True
    while migliorato:
        migliorato = False
        for a in sorted(scelte, key=lambda y: -m.prezzo(y[0])):            # 1) togliere
            prova = [x for x in scelte if x != a]
            if m.fattibile(prova)[0]:
                scelte, migliorato = prova, True
                break
        if migliorato:
            continue
        for a in sorted(scelte, key=lambda y: -m.prezzo(y[0])):            # 2) uno con uno piu' economico
            for b in candidate:
                if b in scelte or m.prezzo(b[0]) >= m.prezzo(a[0]) - 0.005:
                    continue
                prova = [x for x in scelte if x != a] + [b]
                if m.fattibile(prova)[0]:
                    scelte, migliorato = prova, True
                    break
            if migliorato:
                break
        if migliorato:
            continue
        for j, a in enumerate(scelte):                                     # 3) due con uno
            for a2 in scelte[j + 1:]:
                for b in candidate:
                    if b in scelte or m.prezzo(b[0]) >= m.prezzo(a[0]) + m.prezzo(a2[0]) - 0.005:
                        continue
                    prova = [x for x in scelte if x not in (a, a2)] + [b]
                    if m.fattibile(prova)[0]:
                        scelte, migliorato = prova, True
                        break
                if migliorato:
                    break
            if migliorato:
                break
    if m.costo(scelte) >= m.costo(m.del_piano) - 0.005:
        return None
    return m, sorted(scelte, key=lambda y: (y[1], y[0])), m.fattibile(scelte)[1]


def confronto(c, oggi, p=None, tempo_max=TEMPO_MAX):
    p = p or logica.piano_attuale(c, oggi)
    m = Modello(p, oggi)
    per, serv, n = m.per, m.serv, m.n
    pezzi, domanda, capacita, pronto_da, coppie, annuale = m.pezzi, m.domanda, m.capacita, m.pronto_da, m.coppie, m.annuale
    coperto, prezzo, costo, fattibile, copre_dopo = m.coperto, m.prezzo, m.costo, m.fattibile, m.copre_dopo

    # il piano, come insieme di coppie pagate
    del_piano = m.del_piano
    piano_ok, _ = fattibile(del_piano)
    migliore, migliore_costo = list(del_piano), costo(del_piano)
    if not piano_ok:                              # non dovrebbe: allora il confronto non vale
        migliore_costo = math.inf

    inizio, passi, interrotto = time.monotonic(), 0, False

    # limite inferiore: i pezzi che si vedono su un servizio solo (che non sara' mai
    # gratis quando sono pronti) chiedono a quel servizio almeno tante ore; ogni mese
    # ne da' al massimo la capacita' di un periodo. Mesi che mancano x prezzo.
    solo = collections.defaultdict(int)          # servizio -> decimi che solo lui puo' dare
    for k, z in enumerate(pezzi):
        if len(z["dove"]) == 1 and not any(coperto(z["dove"][0], i) for i in range(pronto_da[k], n)):
            solo[z["dove"][0]] += domanda[k]
    cap_max = max(capacita) if capacita else 1

    def limite(scelte):
        lb = 0.0
        for sid, d in solo.items():
            if sid in annuale:
                if not any(s == sid for s, _ in scelte):
                    lb += prezzo(sid)
                continue
            mesi_presi = sum(2 if i == 0 and copre_dopo else 1 for s, i in scelte if s == sid)
            lb += max(0, math.ceil(d / cap_max) - mesi_presi) * prezzo(sid)
        return lb

    def cerca(k, scelte, spesa):
        nonlocal migliore, migliore_costo, passi, interrotto
        if interrotto:
            return
        passi += 1
        if passi % 200 == 0 and time.monotonic() - inizio > tempo_max:
            interrotto = True; return
        if spesa + limite(scelte) >= migliore_costo - 0.005:
            return
        if fattibile(scelte)[0]:                  # basta cosi': aggiungere costa solo di piu'
            migliore, migliore_costo = list(scelte), round(spesa, 2); return
        if k == len(coppie) or not fattibile(scelte + coppie[k:])[0]:
            return
        sid, i = coppie[k]
        if sid in annuale and any(s == sid for s, _ in scelte):
            cerca(k + 1, scelte, spesa); return   # un annuale si compra una volta
        cerca(k + 1, scelte + [coppie[k]], spesa + prezzo(sid))
        cerca(k + 1, scelte, spesa)

    if pezzi:
        cerca(0, [], 0.0)
    elif not del_piano:
        migliore, migliore_costo = [], 0.0

    def mesi(scelte):
        return [dict(i=i, inizio=per[i][0], fine=per[i][1],
                     servizi=[serv[sid] for sid, j in sorted(scelte, key=lambda y: serv[y[0]]["nome"]) if j == i])
                for i in range(n)]

    _, usato = fattibile(migliore) if migliore_costo != math.inf else (False, {})
    dove_guardare = collections.defaultdict(list)          # periodo -> [(titolo, cosa, ore)]
    for (k, i), d in sorted(usato.items()):
        dove_guardare[i].append(dict(t=pezzi[k]["t"], cosa=pezzi[k]["cosa"], ore=round(d / 10, 1)))
    righe = mesi(migliore)
    for r in righe:
        r["titoli"] = dove_guardare.get(r["i"], [])
    return dict(piano=p["spesa"], piano_coerente=piano_ok and abs(costo(del_piano) - p["spesa"]) < 0.01,
                minimo=None if migliore_costo == math.inf else migliore_costo, esatto=not interrotto,
                risparmio=None if migliore_costo == math.inf else round(p["spesa"] - migliore_costo, 2),
                oggi_attivi=p["oggi_attivi"], piano_mesi=mesi(del_piano), minimo_mesi=righe,
                ore=round(sum(domanda) / 10, 1), titoli=len(pezzi), passi=passi,
                secondi=round(time.monotonic() - inizio, 2))
