#!/usr/bin/env python3
"""Crea o rinnova l'utente di Palinsesto. Si lancia A MANO, dal
terminale, come l'utente che fa girare il servizio:

    python3 ~/palinsesto/utente.py            # utente nuovo, o nuova password + nuovo 2FA
    python3 ~/palinsesto/utente.py --password # solo la password, il 2FA resta

La password si digita senza eco; il segreto TOTP compare UNA volta come QR nel
terminale, da inquadrare con l'app di autenticazione. Nessuno dei due passa
da una pagina web o da una chat. Ogni esecuzione fa uscire tutti i dispositivi.
"""
import getpass, hashlib, json, os, pathlib, secrets, sys

import pyotp, qrcode

CONF   = pathlib.Path(os.environ.get("PALINSESTO_CONF", pathlib.Path.home() / ".config/palinsesto"))
UTENTE = CONF / "utente.json"
N, R, P = 2**15, 8, 1          # scrypt: ~32 MiB e ~0,1 s per tentativo

solo_pw = "--password" in sys.argv
prec = json.loads(UTENTE.read_text()) if UTENTE.exists() else None
if solo_pw and not prec:
    sys.exit("nessun utente esistente: lancia senza --password")

nome = (prec or {}).get("nome") if solo_pw else (input(f"nome utente [{(prec or {}).get('nome', 'palinsesto')}]: ").strip()
                                                 or (prec or {}).get("nome", "palinsesto"))
while True:
    pw = getpass.getpass("password (almeno 12 caratteri): ")
    if len(pw) < 12:
        print("troppo corta"); continue
    if pw != getpass.getpass("ripeti: "):
        print("non coincidono"); continue
    break

sale = secrets.token_bytes(16)
h = hashlib.scrypt(pw.encode(), salt=sale, n=N, r=R, p=P, maxmem=128 * 1024 * 1024, dklen=32)
totp = prec["totp"] if solo_pw else pyotp.random_base32()

if not solo_pw:
    uri = pyotp.TOTP(totp).provisioning_uri(name=nome, issuer_name="Palinsesto")
    q = qrcode.QRCode(border=1)
    q.add_data(uri); q.make()
    print("\nInquadra questo QR con l'app di autenticazione (Aegis, 2FAS, Google Authenticator...):\n")
    q.print_ascii(invert=True)
    print("Se il QR non si legge, «inserisci chiave» nell'app e usa il segreto che compare con --mostra-segreto.")
    if "--mostra-segreto" in sys.argv:
        print(f"\n⚠️  SEGRETO TOTP (non incollarlo da nessuna parte): {totp}\n")
    while True:
        c = input("codice a 6 cifre mostrato dall'app, per conferma: ").strip()
        if pyotp.TOTP(totp).verify(c, valid_window=1):
            break
        print("codice non valido, riprova")

CONF.mkdir(mode=0o700, parents=True, exist_ok=True)
tmp = UTENTE.with_suffix(".tmp")
fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
os.write(fd, json.dumps({
    "nome": nome, "sale": sale.hex(), "hash": h.hex(), "n": N, "r": R, "p": P,
    "totp": totp,
    "gen": secrets.token_hex(8),       # cambia a ogni esecuzione: i token vecchi non valgono piu'
}, indent=1).encode())
os.close(fd)
tmp.replace(UTENTE)
print(f"\nfatto: {UTENTE} (0600). Tutti i dispositivi sono usciti.")
