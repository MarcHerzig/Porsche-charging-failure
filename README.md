# Porsche Solar Charge Guard

Behebt folgendes Problem: Beim Laden eines Porsche EV mit PV-Ueberschuss
wirft das Fahrzeug bei kurzer Unterversorgung einen Ladefehler und laedt
danach nicht mehr, bis die Easee-Wallbox neu gebootet oder das Kabel neu
gesteckt wird. Diese App ueberwacht Fahrzeug, Wallbox und PV-Anlage und:

- laedt nur dann, wenn genug Solarstrom da ist (konfigurierbarer Schwellwert
  mit Hysterese, optionaler Nacht-/Sperrzone) -- oder immer, wenn gewuenscht
- erkennt den Porsche-Ladefehler automatisch und bootet die Wallbox selbst
  neu (mit Cooldown gegen Reboot-Schleifen)
- zeigt Live-PV-Produktion, PV-Forecast der naechsten Tage und ein Log der
  letzten Aktionen in einer kleinen Web-GUI

## Voraussetzungen

- Eine **Easee**-Wallbox (Account-Email/Passwort + Charger-ID)
- Eine **Solar Manager**-Box im selben lokalen Netzwerk (Base-URL + API-Key,
  siehe Solar-Manager-App/-Portal)
- Ein **Porsche Connect**-Account (Email/Passwort)
- Docker

## Start (Docker Compose)

```bash
git clone https://github.com/MarcHerzig/Porsche-charging-failure.git
cd Porsche-charging-failure
docker compose up -d --build
```

Danach die GUI unter `http://localhost:8000` oeffnen und im Tab
**Zugangsdaten** eintragen:

- Porsche Connect Email/Passwort (optional VIN, falls mehr als ein Fahrzeug)
- Easee Email/Passwort + Charger-ID (Seriennummer der Wallbox, sichtbar in
  der Easee-App unter den Wallbox-Details oder auf dem Geraet selbst)
- Solar Manager Base-URL (z.B. `https://192.168.0.50`, IP der Solar-Manager-
  Box im lokalen Netz) + API-Key (aus dem Solar-Manager-Portal)
- Breiten-/Laengengrad des Standorts fuer den PV-Forecast

Im Tab **Dashboard** dann Modus (Smart/Immer), Schwellwert, Hysterese und
optionale Sperrzone einstellen.

## Sicherheitshinweise

- Die App hat **kein eigenes Login**. Wer sie ins Internet exposed, muss
  selbst fuer eine Absicherung sorgen (Reverse-Proxy mit Auth, VPN, o.ae.).
- Zugangsdaten werden lokal in einer SQLite-Datei im `/data`-Volume
  verschluesselt gespeichert (Fernet). Ohne explizit gesetzte
  `ENCRYPTION_KEY`-Umgebungsvariable wird beim ersten Start automatisch ein
  Schluessel erzeugt und in `/data/encryption.key` abgelegt -- fuer
  produktive/oeffentlich erreichbare Deployments sollte der Key stattdessen
  als Secret gesetzt werden (siehe `.env.example`).
- Nichts verlaesst dein Netzwerk/deinen Server ausser den Anfragen an die
  Porsche-, Easee- und Open-Meteo-APIs.

## Wie die Logik funktioniert

- **Smart-Modus**: PV-Produktion muss fuer `start_debounce_min` durchgehend
  ueber dem Schwellwert liegen, damit geladen wird; sie muss fuer
  `stop_debounce_min` durchgehend darunter liegen, damit gestoppt wird
  (asymmetrische Hysterese gegen Flattern bei Wolken). Eine optionale
  Sperrzone (z.B. nachts) stoppt sofort und unabhaengig vom Schwellwert.
- **Immer-laden-Modus** ignoriert Schwellwert und Sperrzone komplett.
- Alle 15 Minuten wird der Porsche-Ladestatus abgefragt. Bleibt ein
  Ladefehler ueber zwei aufeinanderfolgende Checks bestehen, bootet die App
  die Wallbox automatisch neu (mit `reboot_cooldown_min` Abstand zwischen
  zwei automatischen Reboots).

## Architektur

Ein Python-Container (FastAPI + Jinja2/vanilla JS, kein Build-Schritt fuer
das Frontend), SQLite im `/data`-Volume. Drei fest verdrahtete, aber intern
getrennte Integrationsmodule unter `app/integrations/`: `easee_client.py`,
`solar_client.py`, `porsche_client.py`, dazu `forecast_client.py` fuer
Open-Meteo.

## Eigenes Deployment (ArgoCD/k3s)

Marc betreibt eine Instanz via ArgoCD im eigenen Homelab unter
`porsche.maegu.be` (Manifest im separaten `argo-homelab`-Repo). Fuer eigene
Kubernetes-Deployments: Image bauen/pushen (siehe
`.github/workflows/docker-build.yml`), `ENCRYPTION_KEY` als Secret setzen,
`/data` als PVC mounten.

## Lokale Entwicklung

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
DATA_DIR=./data uvicorn app.main:app --reload
```
