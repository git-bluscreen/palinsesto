#!/usr/bin/env python3
"""Collaudo della pagina contro un'istanza di PROVA (mai quella vera):

    PALINSESTO_DATI=<db di prova.py> PALINSESTO_CONF=<conf con utente di prova> \
    PALINSESTO_PROVA=1 PALINSESTO_PORTA=45091 python3 app.py &
    python3 prova_web.py [indirizzo-esterno-per-il-403]

L'utente di prova e' «prova» / «prova-prova-123» / TOTP JBSWY3DPEHPK3PXP:
va creato solo nella conf di prova, mai in ~/.config/palinsesto.
"""
import re, sys, pyotp, requests

B = "http://127.0.0.1:45091"
s = requests.Session()
ERR = []
def ok(nome, cond, extra=""):
    print(("ok   " if cond else "NO   ") + nome + ("" if cond else f"  {extra}"))
    if not cond: ERR.append(nome)

r = s.get(B + "/", allow_redirects=False); ok("senza sessione -> accesso", r.status_code == 302 and "/accesso" in r.headers["Location"], r.status_code)
r = s.post(B + "/accesso", data=dict(nome="prova", password="sbagliata", codice="000000")); ok("password sbagliata -> 401", r.status_code == 401)
r = s.post(B + "/accesso", data=dict(nome="prova", password="prova-prova-123", codice=pyotp.TOTP("JBSWY3DPEHPK3PXP").now(), dopo="/novita"), allow_redirects=False)
ok("accesso giusto -> torna a /novita", r.status_code == 302 and r.headers["Location"].endswith("/novita"), (r.status_code, r.headers.get("Location")))
r = s.post(B + "/accesso", data=dict(nome="prova", password="prova-prova-123", codice="1", dopo="//esterno.example"), allow_redirects=False)
for p in ["/", "/cerca", "/liste", "/liste/1", "/liste/tutti", "/liste/piaciuti", "/liste/avvisi", "/liste/1?da_vedere=1&miei_abb=1&tipo=tv&servizio=1",
          "/novita", "/novita?servizio=1", "/servizio/1", "/servizio/3", "/abbonamenti", "/impostazioni", "/importa", "/t/tv/1", "/t/movie/2", "/static/stile.css"]:
    r = s.get(B + p); ok(f"GET {p} -> {r.status_code}", r.status_code == 200, r.text[-300:])
csrf = re.search(r'name="csrf" value="([^"]+)"', s.get(B + "/t/tv/1").text).group(1)
ok("«Dove lo trovi» porta alla pagina del servizio", 'href="/servizio/' in s.get(B + "/t/tv/1").text)
ok("servizio inesistente -> 404", s.get(B + "/servizio/9999").status_code == 404)
r = s.post(B + "/t/tv/1", data=dict(azione="mi_piace")); ok("POST senza CSRF -> 400", r.status_code == 400)
if "♥ Mi piace" in s.get(B + "/t/tv/1").text:          # prova.py puo' averlo lasciato gia' acceso
    s.post(B + "/t/tv/1", data=dict(azione="mi_piace", csrf=csrf))
r = s.post(B + "/t/tv/1", data=dict(azione="mi_piace", csrf=csrf)); ok("mi piace", r.status_code == 200 and "♥ Mi piace" in r.text)
r = s.post(B + "/t/tv/1", data=dict(azione="stagione", n=3, csrf=csrf)); ok("stagione 3 vista", r.status_code == 200 and r.text.count('aria-pressed="true" title="Vista"') >= 1)
for n in (1, 2, 3):   # nessuna spunta: la prova parte pulita
    r = s.post(B + "/t/tv/1", data=dict(azione="stagione", n=n, csrf=csrf))
ok("nessuna stagione vista", r.text.count('title="Vista"') == 0, r.text.count('title="Vista"'))
r = s.post(B + "/t/tv/1", data=dict(azione="tutte_viste", csrf=csrf))
ok("segna uscite come viste", r.text.count('title="Vista"') == 2, r.text.count('title="Vista"'))
r = s.post(B + "/t/tv/1", data=dict(azione="stagione", n=1, csrf=csrf))
ok("togli la 1: la 2 resta vista", r.text.count('title="Vista"') == 1, r.text.count('title="Vista"'))
ok("la stagione con episodi si apre (details)", 'class="stagione-dettagli"' in s.get(B + "/t/tv/1").text)
r = s.post(B + "/t/tv/1", data=dict(azione="avvisi", csrf=csrf)); ok("avvisi spenti", "Avvisi spenti" in r.text)
r = s.post(B + "/t/tv/1", data=dict(azione="avvisi", csrf=csrf)); ok("avvisi riaccesi", "Avvisi attivi" in r.text)
r = s.post(B + "/t/tv/1", data=dict(azione="boh", csrf=csrf)); ok("azione sconosciuta -> 400", r.status_code == 400)
r = s.post(B + "/liste", data=dict(azione="nuova", nome="Horror", csrf=csrf)); ok("nuova lista", "Horror" in r.text)
if "✓ Da vedere" in s.get(B + "/t/movie/2").text:      # il collaudo non dipende dallo stato lasciato da prova.py
    s.post(B + "/t/movie/2", data=dict(azione="lista", lista=1, csrf=csrf))
r = s.post(B + "/t/movie/2", data=dict(azione="lista", lista=1, csrf=csrf)); ok("film in «Da vedere»", r.status_code == 200 and "✓ Da vedere" in r.text)
r = s.post(B + "/t/movie/2", data=dict(azione="visto", csrf=csrf)); ok("film visto: esce da «Da vedere»", "+ Da vedere" in r.text and "✓ Visto" in r.text)
r = s.get(B + "/liste/visti"); ok("...e compare fra i Visti", "Film Due" in r.text)
r = s.post(B + "/t/movie/2", data=dict(azione="visto", csrf=csrf)); ok("spunta tolta: rientra", "✓ Da vedere" in r.text)
r = s.post(B + "/t/movie/2", data=dict(azione="lista", lista=1, csrf=csrf, torna="//esterno.example"), allow_redirects=False)
ok("«torna» esterno ignorato", r.status_code == 302 and "esterno" not in r.headers["Location"], r.headers.get("Location"))
r = s.post(B + "/abbonamenti/1", data=dict(stato="attivo", ciclo="mese", prezzo="13,99", rinnovo="2026-10-15", csrf=csrf))
ok("salva abbonamento", r.status_code == 200 and "13,99" in r.text)
ok("spesa mensile", "Spesa attuale: <b>13,99 €</b>" in r.text, re.search(r"Spesa attuale.*", r.text))
r = s.post(B + "/abbonamenti/1", data=dict(stato="boh", ciclo="mese", csrf=csrf)); ok("stato non valido -> 400", r.status_code == 400)
r = s.post(B + "/abbonamenti/1", data=dict(stato="attivo", ciclo="mese", rinnovo="ieri", csrf=csrf)); ok("data non valida -> 400", r.status_code == 400)
r = s.post(B + "/impostazioni", data=dict(azione="seguito", servizio=6, csrf=csrf)); ok("smetti di seguire un servizio", r.status_code == 200 and "Non seguito" in r.text)
r = s.post(B + "/impostazioni", data=dict(azione="seguito", servizio=6, csrf=csrf))
r = s.get(B + "/"); ok("home: Conviene? presente", "Conviene?" in r.text and "Netflix" in r.text)
ok("home: consigliati col motivo", "Consigliati per te" in r.text and "Consigliata Netflix" in r.text and "per «Serie Uno»" in r.text)
r = s.get(B + "/"); ok("home: in arrivo sulle piattaforme", "In arrivo sulle piattaforme" in r.text and "Torna con la 2" in r.text and "Netflix · 3" in r.text and "S04E05" in r.text)
r = s.get(B + "/in-arrivo?servizio=1"); ok("pagina in arrivo per Netflix", r.status_code == 200 and "Stagione 2" in r.text and "Nuova serie" in r.text)
ok("pagina servizio: in arrivo su Netflix", "In arrivo su Netflix · 3" in s.get(B + "/servizio/1").text)
ok("pagina in arrivo, tutti", s.get(B + "/in-arrivo").status_code == 200)
ok("pagina servizio: consigliati", "Consigliati per te su Netflix" in s.get(B + "/servizio/1").text)
r = s.post(B + "/consigliati", data=dict(azione="nascondi", titolo="tv:50", csrf=csrf))
ok("«Non mi interessa»: sparisce", r.status_code == 200 and "Consigliata Netflix" not in r.text)
r = s.post(B + "/consigliati", data=dict(azione="nascondi", titolo="tv:999", csrf=csrf)); ok("consiglio inesistente -> 400", r.status_code == 400)
h = s.get(B + "/").headers; ok("CSP e no-store", "default-src 'none'" in h["Content-Security-Policy"] and h["Cache-Control"] == "no-store")
if len(sys.argv) > 1:
    r = requests.get(f"http://{sys.argv[1]}:45091/accesso"); ok("IP non ammesso -> 403", r.status_code == 403, r.status_code)
r = s.get(B + "/img/w92/..%2f..%2fetc%2fpasswd"); ok("percorso immagine malformato -> 404", r.status_code == 404, r.status_code)
r = s.get(B + "/img/w9999/abcdef.jpg"); ok("misura immagine non ammessa -> 404", r.status_code == 404, r.status_code)
r = s.post(B + "/esci", data=dict(csrf=csrf), allow_redirects=False); ok("uscita", r.status_code == 302)
r = s.get(B + "/", allow_redirects=False); ok("dopo l'uscita -> accesso", r.status_code == 302)
print("\nTUTTO OK" if not ERR else f"\n{len(ERR)} CASI SBAGLIATI")
sys.exit(1 if ERR else 0)
