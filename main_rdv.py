from fastapi import FastAPI, HTTPException, Header
from fastapi.responses import HTMLResponse
import secrets
import sqlite3
from datetime import datetime

app = FastAPI()

DB_PATH = "rdv.db"
import os
TECH_API_KEY = os.getenv("TECH_API_KEY", "")


# --- Init DB ---
def init_db():
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS rdv (
            token TEXT PRIMARY KEY,
            plate TEXT,
            status INTEGER,
            created_at TEXT,
            updated_at TEXT
        )
    """)
    conn.commit()
    conn.close()

init_db()

# --- Create RDV ---
@app.post("/create")
def create_rdv(plate: str, x_api_key: str = Header(None)):
    if x_api_key != TECH_API_KEY:
        raise HTTPException(status_code=401, detail="Unauthorized")

    token = secrets.token_urlsafe(32)
    now = datetime.utcnow().isoformat()

    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute(
        "INSERT INTO rdv VALUES (?, ?, ?, ?, ?)",
        (token, plate.upper(), 0, now, now)
    )
    conn.commit()
    conn.close()

    return {"token": token}

# --- Update RDV ---
@app.post("/update")
def update_rdv(token: str, status: int, x_api_key: str = Header(None)):
    if x_api_key != TECH_API_KEY:
        raise HTTPException(status_code=401, detail="Unauthorized")

    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("UPDATE rdv SET status=?, updated_at=? WHERE token=?",
                   (status, datetime.utcnow().isoformat(), token))
    conn.commit()
    conn.close()

    return {"ok": True}

# --- Public View ---
@app.get("/t/{token}", response_class=HTMLResponse)
def view_rdv(token: str):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("SELECT plate, status FROM rdv WHERE token=?", (token,))
    row = cursor.fetchone()
    conn.close()

    if not row:
        raise HTTPException(status_code=404, detail="Not found")

    plate, status = row

    steps = [
        "Réception véhicule",
        "Début des travaux",
        "Pare-brise posé",
        "Véhicule terminé"
    ]

    html = f"<h1>Suivi RDV {plate}</h1><ul>"
    for i, step in enumerate(steps):
        if i <= status:
            html += f"<li>✅ {step}</li>"
        else:
            html += f"<li>⏳ {step}</li>"
    html += "</ul>"

    return html
