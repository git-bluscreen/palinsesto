"""Client minimo per l'API di TMDB (v3), in italiano e per la regione IT.

La chiave sta in ~/.config/palinsesto/tmdb (0600): o il «Read Access Token»
(v4, lungo, comincia con eyJ) o la «API Key» (v3, 32 caratteri). Non compare
mai nei log: gli errori riportano solo il percorso chiamato.

La disponibilita' sui servizi di streaming che TMDB restituisce viene da
JustWatch, e le condizioni d'uso chiedono di citarlo: lo fa il pie' di pagina.
"""
import os, pathlib, time

import requests

CONF   = pathlib.Path(os.environ.get("PALINSESTO_CONF", pathlib.Path.home() / ".config/palinsesto"))
BASE   = "https://api.themoviedb.org/3"
IMG    = "https://image.tmdb.org/t/p"
LINGUA = "it-IT"
REGIONE = "IT"
PAUSA  = 0.03          # fra una chiamata e l'altra: TMDB tollera ~50/s, qui ne facciamo ~30


class ErroreTMDB(Exception):
    pass


class TMDB:
    def __init__(self, chiave=None):
        k = chiave or (CONF / "tmdb").read_text().strip()
        self.s = requests.Session()
        self.s.headers["Accept"] = "application/json"
        self.params = {}
        if k.startswith("eyJ"):
            self.s.headers["Authorization"] = f"Bearer {k}"
        else:
            self.params["api_key"] = k
        self.chiamate = 0
        self._ultima = 0.0

    def get(self, percorso, **params):
        p = dict(self.params, **params)
        for tentativo in range(4):
            attesa = PAUSA - (time.monotonic() - self._ultima)
            if attesa > 0:
                time.sleep(attesa)
            self._ultima = time.monotonic()
            self.chiamate += 1
            try:
                r = self.s.get(BASE + percorso, params=p, timeout=20)
            except requests.RequestException as e:
                if tentativo == 3:
                    raise ErroreTMDB(f"{percorso}: {type(e).__name__}") from None
                time.sleep(2 ** tentativo)
                continue
            if r.status_code == 429 or r.status_code >= 500:
                time.sleep(float(r.headers.get("Retry-After", 2 ** tentativo)))
                continue
            if r.status_code == 404:
                return None
            if r.status_code != 200:
                raise ErroreTMDB(f"{percorso}: HTTP {r.status_code}")
            return r.json()
        raise ErroreTMDB(f"{percorso}: troppi tentativi")

    # --- chiamate usate -----------------------------------------------------
    def provider(self, tipo):
        return (self.get(f"/watch/providers/{tipo}", language=LINGUA, watch_region=REGIONE) or {}).get("results", [])

    def scheda(self, tipo, tmdb_id):
        d = self.get(f"/{tipo}/{tmdb_id}", language=LINGUA, append_to_response="watch/providers")
        if d and not d.get("overview"):
            # molte schede non hanno la trama in italiano: meglio l'inglese che niente
            en = self.get(f"/{tipo}/{tmdb_id}", language="en-US")
            if en:
                d["overview"] = en.get("overview") or ""
                d["_trama_en"] = True
        return d

    def stagione(self, tmdb_id, numero):
        return self.get(f"/tv/{tmdb_id}/season/{numero}", language=LINGUA)

    def cerca(self, testo, anno=None):
        r = (self.get("/search/multi", query=testo, language=LINGUA, include_adult="false") or {}).get("results", [])
        r = [x for x in r if x.get("media_type") in ("movie", "tv")]
        if anno:
            # a parita' di risultati, prima quelli dell'anno indicato
            r.sort(key=lambda x: (anno_di(x) != anno, ))
        return r

    def scopri(self, tipo, provider_ids, pagina, dal):
        """Titoli di un servizio (abbonamento, gratis o con pubblicita') usciti da `dal`
        in poi, i piu' popolari prima."""
        campo = "primary_release_date" if tipo == "movie" else "first_air_date"
        return self.get(f"/discover/{tipo}", language=LINGUA, watch_region=REGIONE,
                        with_watch_providers="|".join(map(str, provider_ids)),
                        with_watch_monetization_types="flatrate|free|ads",
                        sort_by="popularity.desc", page=pagina, **{f"{campo}.gte": dal}) or {}


def anno_di(x):
    d = x.get("release_date") or x.get("first_air_date") or ""
    return int(d[:4]) if d[:4].isdigit() else None


def titolo_di(x):
    return x.get("title") or x.get("name") or "?"


def originale_di(x):
    return x.get("original_title") or x.get("original_name")
