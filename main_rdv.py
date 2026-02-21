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

    html = f"""
<!doctype html>
<html lang="fr">
<head>
  <meta charset="utf-8"/>
  <meta name="viewport" content="width=device-width,initial-scale=1"/>
  <title>Suivi RDV EverGlass</title>
  <style>
    html, body {{
      margin:0;
      padding:0;
      background:#000;
      height:100%;
    }}

    /* 1 image plein écran, sans “cadres” */
    .stage {{
      position: relative;
      width: 100vw;
      height: 100vh;
      overflow: hidden;
      background: #000;
    }}

    /* On garde le ratio de l’image, on remplit l’écran proprement */
    #stepImg {{
      width: 100%;
      height: 100%;
      object-fit: contain; /* 🔁 si tu préfères "cover" dis-moi */
      display:block;
      background:#000;
    }}

    /* Overlay texte */
    .overlay {{
      position: absolute;
      left: 50%;
      transform: translateX(-50%);
      bottom: 18px;
      width: min(92vw, 760px);
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 12px;
      padding: 12px 14px;
      border-radius: 16px;
      color: #fff;
      background: rgba(0,0,0,.55);
      border: 1px solid rgba(255,255,255,.18);
      backdrop-filter: blur(6px);
      box-shadow: 0 10px 22px rgba(0,0,0,.35);
      font-family: system-ui, -apple-system, Segoe UI, Roboto, Arial;
    }}

    .immat {{
      font-weight: 950;
      letter-spacing: 1.2px;
      font-size: 18px;
      white-space: nowrap;
      overflow: hidden;
      text-overflow: ellipsis;
    }}

    .meta {{
      font-weight: 800;
      font-size: 13px;
      opacity: .95;
      text-align: right;
      white-space: nowrap;
    }}

    /* Petite discrète pastille “live” */
    .dot {{
      width: 10px;
      height: 10px;
      border-radius: 999px;
      background: #38d67a;
      box-shadow: 0 0 0 6px rgba(56,214,122,.18);
      flex: 0 0 auto;
    }}

    .left {{
      display:flex;
      align-items:center;
      gap:10px;
      min-width: 0;
    }}
  </style>
</head>
<body>
  <div class="stage">
    <img id="stepImg" src="{imgs[status]}" alt="Étape RDV"/>
    <div class="overlay">
      <div class="left">
        <div class="dot" title="Mise à jour automatique"></div>
        <div class="immat" id="plate">{plate}</div>
      </div>
      <div class="meta">
        Maj: <span id="updatedAt">{updated_at}</span> UTC
      </div>
    </div>
  </div>

  <script>
    const token = {token!r};
    const imgs  = {imgs!r};

    // Anti-cache simple (utile si tu modifies les images et veux voir le changement)
    function withBust(url) {{
      const sep = url.includes("?") ? "&" : "?";
      return url + sep + "v=" + Date.now();
    }}

    function render(status, plate, updatedAt) {{
      const s = Math.min(3, Math.max(0, parseInt(status || 0, 10)));
      const img = document.getElementById("stepImg");
      const wanted = imgs[s];

      // Ne change l’image que si l’étape a changé
      if (!img.dataset.base || img.dataset.base !== wanted) {{
        img.dataset.base = wanted;
        img.src = withBust(wanted);
      }}

      document.getElementById("plate").textContent = plate || "";
      document.getElementById("updatedAt").textContent = updatedAt || "";
    }}

    async function refresh() {{
      try {{
        const r = await fetch(`/status/${{token}}`, {{ cache: "no-store" }});
        if (!r.ok) throw new Error("HTTP " + r.status);
        const j = await r.json();
        render(j.status, j.plate, j.updated_at);
      }} catch(e) {{
        document.body.innerHTML = `
          <div style="max-width:720px;margin:30px auto;padding:18px;color:#fff;font-family:system-ui;">
            <h2>RDV introuvable ou clôturé</h2>
            <p>Ce lien n’est plus actif.</p>
          </div>
        `;
      }}
    }}

    render({status}, {plate!r}, {updated_at!r});
    setInterval(refresh, 5000);
  </script>
</body>
</html>
"""
    return html