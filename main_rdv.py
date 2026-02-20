from fastapi import FastAPI, HTTPException, Header
from fastapi.responses import HTMLResponse
from pathlib import Path
from fastapi.staticfiles import StaticFiles
import secrets
import sqlite3
from datetime import datetime
import os
import re

app = FastAPI()


@app.get("/health")
def health():
    return {"status": "ok"}


BASE_DIR = Path(__file__).parent
app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")


@app.get("/debug/static")
def debug_static():
    base = BASE_DIR / "static" / "rdv"
    if not base.exists():
        return {"exists": False, "base_dir": str(BASE_DIR), "path": str(base)}
    files = []
    for p in base.iterdir():
        if p.is_file():
            files.append({"name": p.name, "size": p.stat().st_size})
    return {"exists": True, "base_dir": str(BASE_DIR), "path": str(base), "files": files}


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
        raise HTTPException(status_code=500, detail="Server not configured (TECH_API_KEY missing)")
    if x_api_key != TECH_API_KEY:
        raise HTTPException(status_code=401, detail="Unauthorized")


def normalize_plate(plate: str) -> str:
    p = plate.strip().upper()
    p = re.sub(r"\s+", "-", p)
    p = re.sub(r"[^A-Z0-9\-]", "", p)
    return p


def init_db():
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS rdv (
            token TEXT PRIMARY KEY,
            plate TEXT NOT NULL,
            status INTEGER NOT NULL,
            is_closed INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
    """
    )
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
        (token, plate_n, 0, now, now),
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
        (status, now, token),
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
    cursor.execute(
        """
        SELECT token, plate, status, created_at, updated_at
        FROM rdv
        WHERE is_closed = 0
        ORDER BY updated_at DESC
    """
    )
    rows = cursor.fetchall()
    conn.close()

    items = []
    for token, plate, status, created_at, updated_at in rows:
        items.append(
            {
                "token": token,
                "plate": plate,
                "status": int(status),
                "created_at": created_at,
                "updated_at": updated_at,
            }
        )
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
        "updated_at": updated_at,
    }


# --- Public View (HTML) ---
@app.get("/t/{token}", response_class=HTMLResponse)
def view_rdv(token: str):
    data = get_status(token)
    plate = data["plate"]
    status = int(data["status"])
    updated_at = data["updated_at"]

    # Assets
    logo = "/static/rdv/everglass_logo.png"
    imgs = [
        "/static/rdv/step0_reception.png",
        "/static/rdv/step1_debut.png",
        "/static/rdv/step2_pose.png",
        "/static/rdv/step3_fin.png",
    ]

    if status < 0:
        status = 0
    if status > 3:
        status = 3

    # ✅ MODIF MINIMALE: page client "mono image" (une seule image = étape en cours)
    html = f"""
<!doctype html>
<html lang="fr">
<head>
  <meta charset="utf-8"/>
  <meta name="viewport" content="width=device-width,initial-scale=1"/>
  <title>Suivi RDV EverGlass</title>
  <style>
    :root {{
      --bg: #0f3a2a;
    }}
    html, body {{
      margin: 0;
      padding: 0;
      background: radial-gradient(1200px 800px at 20% 0%, #1c6a4b 0%, var(--bg) 55%, #0b241a 100%);
    }}
    .wrap {{
      max-width: 980px;
      margin: 0 auto;
      padding: 12px;
    }}
    .img {{
      width: 100%;
      height: auto;
      display: block;
      border-radius: 14px;
      box-shadow: 0 14px 28px rgba(0,0,0,.25);
      background: rgba(255,255,255,.06);
    }}

    /* (on garde un footer discret, sans “cadres” supplémentaires) */
    .footer {{
      margin-top: 10px;
      color: rgba(255,255,255,.78);
      font-family: system-ui, -apple-system, Segoe UI, Roboto, Arial;
      font-size: 12px;
      text-align: center;
      opacity: .9;
    }}
  </style>
</head>
<body>
  <div class="wrap">
    <img id="stepImage" class="img" src="{imgs[status]}" alt="Suivi RDV" />
    <div class="footer">Mise à jour automatique</div>
  </div>

  <script>
    const token = {token!r};
    const imgs  = {imgs!r};

    function clampStatus(s) {{
      s = parseInt(s || 0, 10);
      if (isNaN(s)) s = 0;
      if (s < 0) s = 0;
      if (s > 3) s = 3;
      return s;
    }}

    function setImg(status) {{
      const img = document.getElementById("stepImage");
      const nextSrc = imgs[status];
      if (img && img.getAttribute("src") !== nextSrc) {{
        img.setAttribute("src", nextSrc);
      }}
    }}

    async function refresh() {{
      try {{
        const r = await fetch(`/status/${{token}}`, {{ cache: "no-store" }});
        if (!r.ok) throw new Error("HTTP " + r.status);
        const j = await r.json();
        setImg(clampStatus(j.status));
      }} catch(e) {{
        document.body.innerHTML = `
          <div style="max-width:720px;margin:30px auto;padding:18px;color:#fff;font-family:system-ui;">
            <h2>RDV introuvable ou clôturé</h2>
            <p>Ce lien n’est plus actif.</p>
          </div>
        `;
      }}
    }}

    setImg({status});
    setInterval(refresh, 5000);
  </script>
</body>
</html>
"""
    return html