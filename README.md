# gilet-connecte-iot

Projet IoT de surveillance de posture avec un ESP32 et un capteur MPU-6050.

## Comment lancer

**Backend**
```bash
cd backend/src
pip install fastapi uvicorn paho-mqtt
python -m uvicorn main:app --reload --host 0.0.0.0
```

**Firmware**
Ouvrir le dossier `firmware/` dans VS Code avec l'extension Wokwi et lancer la simulation.

## Topics MQTT

```
smartposture/gilet_001/data         → posture + angle
smartposture/gilet_001/temperature  → température
smartposture/gilet_001/status       → statut ESP32
```

## Base de données SQLite

- `measurements` — données posture brutes
- `temperature_ts` — données température brutes  
- `status_ts` — statut ESP32
- `posture_agg` — agrégation toutes les 5 min
- `temperature_agg` — agrégation toutes les 5 min

Les données brutes sont supprimées après 10 minutes.

## Routes API

```
GET /api/data            → dernières mesures
GET /api/live            → mesure en cours
GET /api/stats           → statistiques
GET /api/temperature     → températures
GET /api/status          → statut ESP32
WS  /ws                  → websocket temps réel
```

## Broker MQTT

broker.hivemq.com port 1883