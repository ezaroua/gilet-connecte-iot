from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
import sqlite3
import asyncio
import threading
import json
from datetime import datetime
from typing import List
import paho.mqtt.client as mqtt
import os

app = FastAPI(title="SmartPosture API", version="2.0.0", docs_url="/swagger")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

MQTT_BROKER = "broker.hivemq.com"
MQTT_PORT   = 1883

TOPICS = [
    ("smartposture/gilet_001/data",        0),
    ("smartposture/gilet_001/temperature", 0),
    ("smartposture/gilet_001/status",      0),
]

#WebSocket Manager
class ConnectionManager:
    def __init__(self):
        self.active_connections: List[WebSocket] = []

    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        self.active_connections.append(websocket)
        print(f"client connecte — total : {len(self.active_connections)}")

    def disconnect(self, websocket: WebSocket):
        if websocket in self.active_connections:
            self.active_connections.remove(websocket)

    async def broadcast(self, message: dict):
        disconnected = []
        for connection in self.active_connections:
            try:
                await connection.send_json(message)
            except Exception:
                disconnected.append(connection)
        for conn in disconnected:
            if conn in self.active_connections:
                self.active_connections.remove(conn)

manager = ConnectionManager()

#Base de données

DB_PATH = os.path.join(os.path.dirname(__file__), "smartposture.db")

def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_db()

    # Table posture (existante)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS measurements (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            device_id  TEXT    NOT NULL,
            ax         REAL,
            ay         REAL,
            az         REAL,
            gx         REAL,
            gy         REAL,
            gz         REAL,
            angle      REAL,
            posture    TEXT,
            created_at TEXT    NOT NULL
        )
    """)

    # Table temperature (Time-Series)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS temperature_ts (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            device_id   TEXT    NOT NULL,
            temperature REAL    NOT NULL,
            created_at  TEXT    NOT NULL
        )
    """)

    # Table status (Time-Series)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS status_ts (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            device_id  TEXT    NOT NULL,
            status     TEXT    NOT NULL,
            uptime     INTEGER,
            created_at TEXT    NOT NULL
        )
    """)

    # Table agrégation température (5 min)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS temperature_agg (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            device_id    TEXT    NOT NULL,
            temp_avg     REAL,
            temp_min     REAL,
            temp_max     REAL,
            bucket_start TEXT    NOT NULL,
            bucket_end   TEXT    NOT NULL
        )
    """)

    # Table agrégation posture (5 min)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS posture_agg (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            device_id    TEXT    NOT NULL,
            angle_avg    REAL,
            angle_min    REAL,
            angle_max    REAL,
            nb_bonnes    INTEGER,
            nb_attention INTEGER,
            nb_mauvaises INTEGER,
            bucket_start TEXT    NOT NULL,
            bucket_end   TEXT    NOT NULL
        )
    """)

    # Index pertinents
    conn.execute("CREATE INDEX IF NOT EXISTS idx_measurements_created  ON measurements(created_at)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_measurements_device   ON measurements(device_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_measurements_posture  ON measurements(posture)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_temperature_created   ON temperature_ts(created_at)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_temperature_device    ON temperature_ts(device_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_status_created        ON status_ts(created_at)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_status_device         ON status_ts(device_id)")

    conn.commit()
    conn.close()
    print("BDD initialisee avec tables Time-Series et index")

init_db()

#Agrégation + Rétention
def aggreger_et_nettoyer():
    conn = get_db()
    now = datetime.now().isoformat()

    try:
        # Agrégation température par bucket de 5 min
        conn.execute("""
            INSERT INTO temperature_agg (device_id, temp_avg, temp_min, temp_max, bucket_start, bucket_end)
            SELECT
                device_id,
                ROUND(AVG(temperature), 2),
                ROUND(MIN(temperature), 2),
                ROUND(MAX(temperature), 2),
                MIN(created_at),
                MAX(created_at)
            FROM temperature_ts
            WHERE created_at >= datetime('now', '-5 minutes')
            GROUP BY device_id
        """)

        # Agrégation posture par bucket de 5 min
        conn.execute("""
            INSERT INTO posture_agg (device_id, angle_avg, angle_min, angle_max, nb_bonnes, nb_attention, nb_mauvaises, bucket_start, bucket_end)
            SELECT
                device_id,
                ROUND(AVG(angle), 2),
                ROUND(MIN(angle), 2),
                ROUND(MAX(angle), 2),
                SUM(CASE WHEN posture = 'BONNE'     THEN 1 ELSE 0 END),
                SUM(CASE WHEN posture = 'ATTENTION' THEN 1 ELSE 0 END),
                SUM(CASE WHEN posture = 'MAUVAISE'  THEN 1 ELSE 0 END),
                MIN(created_at),
                MAX(created_at)
            FROM measurements
            WHERE created_at >= datetime('now', '-5 minutes')
            GROUP BY device_id
        """)

        # Rétention : supprimer données brutes de plus de 10 min
        conn.execute("DELETE FROM temperature_ts WHERE created_at < datetime('now', '-10 minutes')")
        conn.execute("DELETE FROM status_ts      WHERE created_at < datetime('now', '-10 minutes')")
        conn.execute("DELETE FROM measurements   WHERE created_at < datetime('now', '-10 minutes')")

        conn.commit()
        print(f"[{now}] agregation + retention effectuees")

    except Exception as e:
        print(f"erreur agregation : {e}")
    finally:
        conn.close()

# Tache périodique toutes les 5 minutes
async def tache_agregation():
    while True:
        await asyncio.sleep(300)  # 5 minutes
        aggreger_et_nettoyer()

# Classification
def classify_posture(angle: float) -> str:
    if angle < 15:   return "BONNE"
    elif angle < 45: return "ATTENTION"
    else:            return "MAUVAISE"

#Loop asyncio global
loop = asyncio.get_event_loop()

# MQTT
def on_message(client, userdata, msg):
    try:
        payload_str = msg.payload.decode().strip()
        if not payload_str:
            return

        data  = json.loads(payload_str)
        topic = msg.topic
        now   = datetime.now().isoformat()

        if topic == "smartposture/gilet_001/data":
            posture = classify_posture(data["angle"])
            conn = get_db()
            conn.execute("""
                INSERT INTO measurements
                (device_id, ax, ay, az, gx, gy, gz, angle, posture, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                data["device_id"],
                data["ax"], data["ay"], data["az"],
                data["gx"], data["gy"], data["gz"],
                data["angle"], posture, now
            ))
            conn.commit()
            conn.close()
            message = {
                "type"      : "new_measurement",
                "device_id" : data["device_id"],
                "angle"     : data["angle"],
                "posture"   : posture,
                "ax": data["ax"], "ay": data["ay"], "az": data["az"],
                "gx": data["gx"], "gy": data["gy"], "gz": data["gz"],
                "created_at": now
            }
            print(f"posture | angle: {data['angle']} | {posture}")
            asyncio.run_coroutine_threadsafe(manager.broadcast(message), loop)

        elif topic == "smartposture/gilet_001/temperature":
            conn = get_db()
            conn.execute("""
                INSERT INTO temperature_ts (device_id, temperature, created_at)
                VALUES (?, ?, ?)
            """, (data["device_id"], data["temperature"], now))
            conn.commit()
            conn.close()
            message = {
                "type"       : "new_temperature",
                "device_id"  : data["device_id"],
                "temperature": data["temperature"],
                "created_at" : now
            }
            print(f"temperature: {data['temperature']} °C")
            asyncio.run_coroutine_threadsafe(manager.broadcast(message), loop)

        elif topic == "smartposture/gilet_001/status":
            conn = get_db()
            conn.execute("""
                INSERT INTO status_ts (device_id, status, uptime, created_at)
                VALUES (?, ?, ?, ?)
            """, (data["device_id"], data["status"], data.get("uptime", 0), now))
            conn.commit()
            conn.close()
            message = {
                "type"      : "new_status",
                "device_id" : data["device_id"],
                "status"    : data["status"],
                "uptime"    : data.get("uptime", 0),
                "created_at": now
            }
            print(f"status: {data['status']} | uptime: {data.get('uptime', 0)}s")
            asyncio.run_coroutine_threadsafe(manager.broadcast(message), loop)

    except Exception as e:
        print(f"erreur MQTT : {e}")

def on_connect(client, userdata, flags, rc, properties=None):
    print(f"connecte au broker MQTT — rc: {rc}")
    client.subscribe(TOPICS)
    for topic, _ in TOPICS:
        print(f"abonne au topic : {topic}")

def start_mqtt():
    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
    client.on_connect = on_connect
    client.on_message = on_message
    client.connect(MQTT_BROKER, MQTT_PORT, 60)
    client.loop_forever()

@app.on_event("startup")
async def startup():
    global loop
    loop = asyncio.get_event_loop()
    thread = threading.Thread(target=start_mqtt, daemon=True)
    thread.start()
    asyncio.create_task(tache_agregation())
    print("client MQTT demarre")

# Routes HTTP 
@app.get("/")
def read_root():
    return {"status": "SmartPosture API v2.0 en ligne"}

@app.get("/api/data")
def get_data():
    conn = get_db()
    rows = conn.execute(
        "SELECT * FROM measurements ORDER BY id DESC LIMIT 50"
    ).fetchall()
    conn.close()
    return [dict(row) for row in rows]

@app.get("/api/live")
def get_live():
    conn = get_db()
    row = conn.execute(
        "SELECT * FROM measurements ORDER BY id DESC LIMIT 1"
    ).fetchone()
    conn.close()
    return dict(row) if row else {"posture": "BONNE", "angle": 0}

@app.get("/api/stats")
def get_stats():
    conn = get_db()
    total     = conn.execute("SELECT COUNT(*) as c FROM measurements").fetchone()["c"]
    bonnes    = conn.execute("SELECT COUNT(*) as c FROM measurements WHERE posture='BONNE'").fetchone()["c"]
    attention = conn.execute("SELECT COUNT(*) as c FROM measurements WHERE posture='ATTENTION'").fetchone()["c"]
    mauvaises = conn.execute("SELECT COUNT(*) as c FROM measurements WHERE posture='MAUVAISE'").fetchone()["c"]
    angle_moy = conn.execute("SELECT AVG(angle) as a FROM measurements").fetchone()["a"]
    conn.close()
    return {
        "total"       : total,
        "bonnes"      : bonnes,
        "attention"   : attention,
        "mauvaises"   : mauvaises,
        "angle_moyen" : round(angle_moy or 0, 1),
        "pct_bonne"   : round((bonnes / total * 100) if total > 0 else 0, 1)
    }

@app.get("/api/alerts")
def get_alerts():
    conn = get_db()
    rows = conn.execute("""
        SELECT angle, posture, created_at FROM measurements
        WHERE posture = 'MAUVAISE'
        ORDER BY id DESC LIMIT 20
    """).fetchall()
    conn.close()
    return [dict(row) for row in rows]

@app.get("/api/temperature")
def get_temperature():
    conn = get_db()
    rows = conn.execute(
        "SELECT * FROM temperature_ts ORDER BY id DESC LIMIT 50"
    ).fetchall()
    conn.close()
    return [dict(row) for row in rows]

@app.get("/api/temperature/agg")
def get_temperature_agg():
    conn = get_db()
    rows = conn.execute(
        "SELECT * FROM temperature_agg ORDER BY id DESC LIMIT 20"
    ).fetchall()
    conn.close()
    return [dict(row) for row in rows]

@app.get("/api/status")
def get_status():
    conn = get_db()
    row = conn.execute(
        "SELECT * FROM status_ts ORDER BY id DESC LIMIT 1"
    ).fetchone()
    conn.close()
    return dict(row) if row else {"status": "unknown"}

@app.get("/api/posture/agg")
def get_posture_agg():
    conn = get_db()
    rows = conn.execute(
        "SELECT * FROM posture_agg ORDER BY id DESC LIMIT 20"
    ).fetchall()
    conn.close()
    return [dict(row) for row in rows]

#WebSocket 
@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await manager.connect(websocket)
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        manager.disconnect(websocket)