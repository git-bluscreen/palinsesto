"""Gli orari fissi di Palinsesto senza systemd ne' cron (per Docker): un thread
dentro la pagina (`python3 app.py --pianificatore`) che lancia

    05:30  il giro notturno            (aggiorna.py)
    08:30  il recupero, se serve        (aggiorna.py --recupero)
    00:40  la copia coerente del db     (db.copia_coerente, per i backup del volume)

Gli orari si cambiano con PALINSESTO_ORARI, per esempio "giro=05:30,recupero=08:30,copia=00:40"
(una voce vuota, come "copia=", la spegne). Il giro e il recupero girano in un
processo a parte, come dal tasto «Aggiorna adesso»: il lucchetto in aggiorna.py
impedisce due giri insieme. Con systemd (unita' in systemd/) questo non serve.
"""
import datetime as dt, os, pathlib, subprocess, sys, threading, time

import db

QUI = pathlib.Path(__file__).resolve().parent
PREDEFINITI = {"giro": "05:30", "recupero": "08:30", "copia": "00:40"}


def orari():
    voci = dict(PREDEFINITI)
    for pezzo in os.environ.get("PALINSESTO_ORARI", "").split(","):
        if "=" in pezzo:
            k, v = (x.strip() for x in pezzo.split("=", 1))
            if k in voci:
                voci[k] = v
    out = {}
    for k, v in voci.items():
        if v:
            h, m = v.split(":")
            out[k] = dt.time(int(h), int(m))
    return out


def prossimo(adesso, quando):
    d = dt.datetime.combine(adesso.date(), quando)
    return d if d > adesso else d + dt.timedelta(days=1)


def esegui(lavoro, log):
    try:
        if lavoro == "copia":
            log(f"copia del database: {db.copia_coerente()}")
            return
        argomenti = [sys.executable, "-u", str(QUI / "aggiorna.py")] + (["--recupero"] if lavoro == "recupero" else [])
        esito = subprocess.run(argomenti, cwd=str(QUI), stdin=subprocess.DEVNULL)
        if esito.returncode not in (0, 75):          # 75 = rete giu': lo dice gia' la riga «giro FALLITO»
            log(f"{lavoro}: uscito con {esito.returncode}")
    except Exception as e:                          # un lavoro fallito non deve fermare gli altri
        log(f"{lavoro}: {type(e).__name__}: {e}")


def ciclo(log):
    tabella = orari()
    log("pianificatore: " + ", ".join(f"{k} alle {v.strftime('%H:%M')}" for k, v in sorted(tabella.items(), key=lambda x: x[1])))
    while True:
        adesso = dt.datetime.now()
        lavoro, quando = min(((k, prossimo(adesso, v)) for k, v in tabella.items()), key=lambda x: x[1])
        # si dorme a pezzi: un orologio che salta (ora legale, sospensione) non fa perdere il turno
        while dt.datetime.now() < quando:
            time.sleep(min(60, max(1, (quando - dt.datetime.now()).total_seconds())))
        esegui(lavoro, log)


def avvia(log):
    if not orari():
        log("pianificatore: nessun orario, spento")
        return
    threading.Thread(target=ciclo, args=(log,), name="pianificatore", daemon=True).start()
