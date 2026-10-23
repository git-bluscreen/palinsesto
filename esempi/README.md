# File di configurazione d'esempio

Vanno in `~/.config/palinsesto/` (o nella cartella di `PALINSESTO_CONF`; in Docker
il volume montato su `/config`), con permessi `0600`: contengono segreti.

| File | Serve per | Obbligatorio |
|---|---|---|
| `tmdb` | la chiave API di TMDB, su una riga (oppure la variabile `PALINSESTO_TMDB`) | sì |
| `utente.json` | l'accesso: **non si scrive a mano**, lo crea `python3 utente.py` | sì |
| `palinsesto.json` | indirizzo pubblico, reverse proxy fidati, reti ammesse (oppure le variabili `PALINSESTO_PAGINA`, `PALINSESTO_PROXY`, `PALINSESTO_AMMESSI`) | no |
| `ntfy.json` | le notifiche su ntfy | no |
| `caldav.json` | i promemoria del piano su un calendario CalDAV (Nextcloud) | no |
| `marchio/` | icone tue al posto di quelle neutre, con gli stessi nomi di `static/icone/` | no |

`compose.yaml` è un esempio per Docker Compose (o Podman).
