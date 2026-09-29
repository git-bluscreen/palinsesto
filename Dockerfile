# Palinsesto in un container: la pagina e, dentro, gli orari fissi (giro 05:30,
# recupero 08:30, copia del database 00:40: vedi pianificatore.py).
#   docker build -t palinsesto .
#   docker run -d --name palinsesto -p 127.0.0.1:45090:45090 \
#     -e PALINSESTO_TMDB=... -e PALINSESTO_AMMESSI=172.16.0.0/12 \
#     -v ./dati:/dati -v ./config:/config palinsesto
#   docker exec -it palinsesto python3 utente.py
FROM python:3.13-slim

# tzdata: gli orari del pianificatore seguono TZ
RUN apt-get update && apt-get install -y --no-install-recommends tzdata \
 && rm -rf /var/lib/apt/lists/*

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    TZ=Europe/Rome \
    PALINSESTO_DATI=/dati \
    PALINSESTO_CONF=/config

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .
RUN useradd --system --uid 10001 --home-dir /app palinsesto \
 && mkdir -p /dati /config && chown palinsesto:palinsesto /dati /config && chmod 700 /config

USER palinsesto
VOLUME ["/dati", "/config"]
EXPOSE 45090

HEALTHCHECK --interval=5m --timeout=10s --start-period=30s \
  CMD python3 -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:45090/accesso', timeout=5)"

CMD ["python3", "app.py", "--pianificatore"]
