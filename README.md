# Palinsesto

Film e serie sui servizi di streaming italiani: cosa hai da vedere, dove si vede,
cosa sta per arrivare, e **quale abbonamento tenere, mettere in pausa o disdire
mese per mese** per vedere tutto pagando meno.

Un'applicazione web self-hosted per una persona (o una casa), pensata prima per il
telefono. Nasce per l'Italia: servizi, disponibilità e interfaccia sono italiani.

## Che cosa fa

- **Liste e visti** per film, stagione ed episodio; «Da vedere» si svuota e si
  riempie da sola quando esce una stagione nuova.
- **Dove lo trovi**: su quali servizi si vede un titolo in Italia, e quando arriva
  o se ne va (con una notifica).
- **«Conviene?»** per ogni servizio: abbonarsi, guardare quello che c'è,
  aspettare, disdire.
- **Piano dei prossimi mesi**: quale servizio attivare e quando, con i promemoria
  sul calendario (CalDAV). Dove un servizio concede la **pausa** (Netflix, Disney+)
  la preferisce alla disdetta, così cronologia e preferenze restano.
- **In arrivo sulle piattaforme**, con pollice su / giù che insegnano i tuoi gusti;
  **consigli** dai titoli che hai.
- **Notifiche** su [ntfy](https://ntfy.sh) (facoltative) e **aggiornamento notturno**
  automatico, con un recupero al mattino se di notte la rete manca.

## Come funziona

Python con Flask e waitress, dati in SQLite. Schede, copertine e disponibilità
vengono da [TMDB](https://www.themoviedb.org), che per le disponibilità usa
[JustWatch](https://www.justwatch.com). Le copertine passano dal server (con una
cache su disco): il browser non parla mai con TMDB.

Accesso con **un solo utente**, password (scrypt) più codice **TOTP**. Il server
accetta connessioni solo da localhost, dai reverse proxy fidati e dalle reti che
indichi: va messo dietro un reverse proxy con HTTPS.

## Installazione

Serve una **chiave API di TMDB** (gratuita, dal tuo account TMDB → Impostazioni → API).

1. Pacchetti (su Debian 13):
   ```
   apt install python3-flask python3-waitress python3-pyotp python3-requests python3-qrcode
   ```
2. Un utente dedicato e il codice in `~/palinsesto` di quell'utente (le unità
   systemd in `systemd/` presumono l'utente `palinsesto` e `/home/palinsesto`).
3. La chiave TMDB in `~/.config/palinsesto/tmdb` (permessi `0600`): la chiave v3
   o il «Read Access Token».
4. L'utente di accesso, dal terminale (la password non passa mai dal web; il segreto
   TOTP compare una volta sola, come QR):
   ```
   python3 ~/palinsesto/utente.py
   ```
5. La configurazione, facoltativa, in `~/.config/palinsesto/palinsesto.json`:
   ```json
   {
     "pagina":  "https://palinsesto.example.org",
     "proxy":   ["192.0.2.10"],
     "ammessi": ["192.0.2.0/24"]
   }
   ```
   `pagina` è l'indirizzo pubblico (link delle notifiche e del calendario), `proxy`
   i reverse proxy di cui fidarsi per l'indirizzo del client, `ammessi` le reti che
   possono collegarsi direttamente. Senza file si accetta solo localhost.
6. Le unità in `systemd/`: la pagina (`palinsesto-web.service`, porta 45090), il
   giro notturno alle 05:30 (`palinsesto-aggiorna.timer`), il recupero alle 08:30
   (`palinsesto-recupero.timer`) e una copia coerente del database per i backup
   (`palinsesto-dbcopia.timer`).

Il primo giro (`python3 aggiorna.py`, o «Aggiorna adesso» in Impostazioni) crea i
servizi e scarica i dati; poi si cercano i titoli o si importa un elenco copiato da
JustWatch (Impostazioni → Importa).

### Facoltativi

- **Notifiche**: `~/.config/palinsesto/ntfy.json` con `{"url": "https://ntfy.example.org/", "topic": "streaming", "token": "…"}`.
- **Calendario**: `~/.config/palinsesto/caldav.json` con `{"url", "utente", "password"}`
  di un utente che ha in modifica un calendario il cui nome comincia con «Palinsesto».
- **Marchio**: icone tue in `~/.config/palinsesto/marchio/` con gli stessi nomi di
  quelle in `static/icone/` (più `logo-scritta.jpg` per la pagina di accesso).

## Collaudi

`prova.py` collauda la logica contro un TMDB finto, provocando ogni evento invece di
aspettarlo; `prova_web.py` collauda la pagina contro un'istanza di prova (le
istruzioni sono in testa al file). Non toccano mai i dati veri.

## Avvertenze

Le disponibilità possono essere sbagliate o in ritardo (la fonte è una copia di
una copia): in ogni scheda «non c'è davvero» corregge un servizio sbagliato finché
la fonte si aggiorna. Le regole delle pause dei servizi cambiano: le durate si
correggono in Abbonamenti.

Questo prodotto usa l'API di TMDB ma non è approvato né certificato da TMDB.

## Licenza

[GNU Affero General Public License v3.0](LICENSE): puoi usarlo, studiarlo,
modificarlo e ridistribuirlo; se lo offri ad altri come servizio web, devi
rendere disponibile anche il codice delle tue modifiche.
