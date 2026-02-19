from fastapi import FastAPI, HTTPException, Header
from fastapi.responses import HTMLResponse, JSONResponse
import secrets
import sqlite3
from datetime import datetime
import os
import re

app = FastAPI()

DB_PATH = "rdv.db"
TECH_API_KEY = os.getenv("TECH_API_KEY", "")

STEPS = [
    "RÉCEPTION VÉHICULE",
    "DÉBUT DES TRAVAUX",
    "PARE BRISE POSE",
    "FIN DES TRAVAUX",
]


def utc_now_iso() -> str:
    return datetime.utcnow().isoformat(timespec="seconds")


def require_api_key(x_api_key: str | None):
    if not TECH_API_KEY:
        # Si la variable n'est pas configurée, on bloque tout côté tech
        raise HTTPException(status_code=500, detail="Server not configured (TECH_API_KEY missing)")
    if x_api_key != TECH_API_KEY:
        raise HTTPException(status_code=401, detail="Unauthorized")


def normalize_plate(plate: str) -> str:
    # Nettoyage basique: uppercase, enlève espaces, garde lettres/chiffres/-,
    # et essaie de remettre des tirets si l'utilisateur met des espaces.
    p = plate.strip().upper()
    p = re.sub(r"\s+", "-", p)
    p = re.sub(r"[^A-Z0-9\-]", "", p)
    return p


def init_db():
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS rdv (
            token TEXT PRIMARY KEY,
            plate TEXT NOT NULL,
            status INTEGER NOT NULL,
            is_closed INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
    """)
    conn.commit()
    conn.close()


init_db()


# --- Create RDV (tech) ---
@app.post("/create")
def create_rdv(plate: str, x_api_key: str = Header(default=None, alias="X-API-Key")):
    require_api_key(x_api_key)

    plate_n = normalize_plate(plate)
    token = secrets.token_urlsafe(32)
    now = utc_now_iso()

    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute(
        "INSERT INTO rdv (token, plate, status, is_closed, created_at, updated_at) VALUES (?, ?, ?, 0, ?, ?)",
        (token, plate_n, 0, now, now)
    )
    conn.commit()
    conn.close()

    return {"token": token, "plate": plate_n, "status": 0, "updated_at": now}


# --- Update RDV (tech) ---
@app.post("/update")
def update_rdv(token: str, status: int, x_api_key: str = Header(default=None, alias="X-API-Key")):
    require_api_key(x_api_key)

    if status < 0 or status > 3:
        raise HTTPException(status_code=400, detail="status must be between 0 and 3")

    now = utc_now_iso()

    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    # Empêche la maj d'un RDV fermé
    cursor.execute("SELECT is_closed FROM rdv WHERE token=?", (token,))
    row = cursor.fetchone()
    if not row:
        conn.close()
        raise HTTPException(status_code=404, detail="Not found")
    if row[0] == 1:
        conn.close()
        raise HTTPException(status_code=400, detail="RDV is closed")

    cursor.execute(
        "UPDATE rdv SET status=?, updated_at=? WHERE token=?",
        (status, now, token)
    )
    conn.commit()
    conn.close()

    return {"ok": True, "token": token, "status": status, "updated_at": now}


# --- List RDV (tech) ---
@app.get("/list")
def list_rdv(x_api_key: str = Header(default=None, alias="X-API-Key")):
    require_api_key(x_api_key)

    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("""
        SELECT token, plate, status, created_at, updated_at
        FROM rdv
        WHERE is_closed = 0
        ORDER BY updated_at DESC
    """)
    rows = cursor.fetchall()
    conn.close()

    items = []
    for token, plate, status, created_at, updated_at in rows:
        items.append({
            "token": token,
            "plate": plate,
            "status": int(status),
            "created_at": created_at,
            "updated_at": updated_at,
        })
    return items


# --- Close RDV (tech) ---
@app.post("/close")
def close_rdv(token: str, x_api_key: str = Header(default=None, alias="X-API-Key")):
    require_api_key(x_api_key)

    now = utc_now_iso()
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("UPDATE rdv SET is_closed=1, updated_at=? WHERE token=?", (now, token))
    conn.commit()
    changed = cursor.rowcount
    conn.close()

    if changed == 0:
        raise HTTPException(status_code=404, detail="Not found")

    return {"ok": True, "token": token, "updated_at": now}


# --- Public status (client + flutter lecture) ---
@app.get("/status/{token}")
def get_status(token: str):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("SELECT plate, status, updated_at, is_closed FROM rdv WHERE token=?", (token,))
    row = cursor.fetchone()
    conn.close()

    if not row:
        raise HTTPException(status_code=404, detail="Not found")

    plate, status, updated_at, is_closed = row
    if is_closed == 1:
        raise HTTPException(status_code=404, detail="Not found")

    return {
        "token": token,
        "plate": plate,
        "status": int(status),
        "updated_at": updated_at
    }


# --- Public View (HTML) ---
@app.get("/t/{token}", response_class=HTMLResponse)
def view_rdv(token: str):
    # Simple HTML (tu as déjà tes visuels; on branchera après)
    data = get_status(token)
    plate = data["plate"]
    status = data["status"]
    updated_at = data["updated_at"]

    html = f"<h1>SUIVI VEHICULE : {plate}</h1>"
    html += f"<p>Dernière mise à jour : {updated_at} (UTC)</p>"
    html += "<ul>"
    for i, step in enumerate(STEPS):
        html += f"<li>{'✅' if i <= status else '⏳'} {step}</li>"
    html += "</ul>"
    return html