import base64
import hashlib
import hmac
import os
import shutil
import smtplib
import sqlite3
import ssl
import threading
import time
import urllib.request
import uuid
from email.message import EmailMessage
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

import certifi

# Doit être défini avant l'import de cv2. RTSP en TCP (fiable), sans tampon, démarrage rapide.
os.environ.setdefault(
    "OPENCV_FFMPEG_CAPTURE_OPTIONS",
    "rtsp_transport;tcp|fflags;nobuffer|flags;low_delay|analyzeduration;500000|probesize;500000",
)
import cv2  # noqa: E402
import mediapipe as mp
import numpy as np
from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from mediapipe.tasks import python as mp_python
from mediapipe.tasks.python import vision
from pydantic import BaseModel

from history import CAPTURES_DIR, History
from face_db import FaceDB

BASE = Path(__file__).parent
PEOPLE_DIR = BASE / "data" / "people"
MODEL_PATH = BASE / "models" / "blaze_face_short_range.tflite"
MODEL_URL = (
    "https://storage.googleapis.com/mediapipe-models/face_detector/"
    "blaze_face_short_range/float16/1/blaze_face_short_range.tflite"
)
UPLOAD_DIR = BASE / "uploads"
EPISODE_GAP = 3.0  # sans visage pendant 3 s, le passage est terminé (le suivant = nouvel événement)
HOLD_SECONDS = 1.0  # garde l'état « détecté » pendant 1 s après le dernier visage
MIN_SCORE = 0.75  # confiance minimale du modèle (0.5 = laxiste, 0.9 = strict)
MIN_FACE_RATIO = 0.04  # largeur minimale d'un visage, en fraction de l'image
CONFIRM_FRAMES = 3  # images consécutives nécessaires avant de valider une détection
MAX_WIDTH = 960  # les images plus larges sont réduites


class LiveReader:
    """Lit le flux en continu dans un thread et ne garde que la dernière image,
    pour analyser toujours l'image la plus récente (pas de retard qui s'accumule)."""

    def __init__(self, cap):
        self.cap = cap
        self.cond = threading.Condition()
        self.frame = None
        self.alive = True
        self.closed = False
        self.thread = threading.Thread(target=self._loop, daemon=True)
        self.thread.start()

    def _loop(self):
        while not self.closed:
            try:
                ok, frame = self.cap.read()
            except cv2.error:
                with self.cond:
                    self.alive = False
                    self.frame = None
                    self.cond.notify_all()
                return
            with self.cond:
                if not ok:
                    self.alive = False
                else:
                    self.frame = frame
                self.cond.notify_all()
            if not ok:
                return

    def read(self, timeout=10):
        with self.cond:
            if self.frame is None and self.alive:
                self.cond.wait(timeout)
            if self.frame is None:
                return False, None
            frame, self.frame = self.frame, None
            return True, frame

    def close(self):
        self.closed = True
        self.thread.join(timeout=3)  # évite de libérer la capture pendant une lecture


def ensure_model():
    if not MODEL_PATH.exists():
        MODEL_PATH.parent.mkdir(parents=True, exist_ok=True)
        ctx = ssl.create_default_context(cafile=certifi.where())
        with urllib.request.urlopen(MODEL_URL, context=ctx) as r, open(MODEL_PATH, "wb") as f:
            shutil.copyfileobj(r, f)


def load_env():
    """Charge .env (CLE=valeur par ligne) dans os.environ sans écraser l'existant."""
    path = BASE / ".env"
    if path.exists():
        for line in path.read_text().splitlines():
            if "=" in line and not line.lstrip().startswith("#"):
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip())


def notify(jpeg):
    """Envoie l'alerte (avec la photo) par ntfy et/ou email selon la config."""
    title, text = "🚨 Argus", "Humain détecté pendant que l'alarme est activée."
    ctx = ssl.create_default_context(cafile=certifi.where())
    topic = os.environ.get("NTFY_TOPIC")
    if topic:
        try:
            server = os.environ.get("NTFY_SERVER", "https://ntfy.sh").rstrip("/")
            req = urllib.request.Request(
                f"{server}/{topic}", data=jpeg, method="PUT",
                headers={"Title": title.encode("utf-8").decode("latin-1", "ignore") or "Alarme",
                         "Message": text, "Priority": "urgent", "Tags": "rotating_light",
                         "Filename": "detection.jpg"})
            urllib.request.urlopen(req, timeout=15, context=ctx).close()
        except Exception as e:
            print(f"[notif ntfy] échec : {e}")
    host, to = os.environ.get("SMTP_HOST"), os.environ.get("ALERT_EMAIL_TO")
    if host and to:
        try:
            user = os.environ.get("SMTP_USER", "")
            msg = EmailMessage()
            msg["Subject"], msg["From"], msg["To"] = title, user or to, to
            msg.set_content(text)
            msg.add_attachment(jpeg, maintype="image", subtype="jpeg", filename="detection.jpg")
            with smtplib.SMTP(host, int(os.environ.get("SMTP_PORT", 587)), timeout=15) as s:
                s.starttls(context=ctx)
                if user:
                    s.login(user, os.environ.get("SMTP_PASSWORD", ""))
                s.send_message(msg)
        except Exception as e:
            print(f"[notif email] échec : {e}")


load_env()

app = FastAPI()

APP_ADMIN_USERNAME = os.environ.get("APP_ADMIN_USERNAME", os.environ.get("APP_USERNAME", "admin")).strip()
APP_ADMIN_PASSWORD = os.environ.get("APP_ADMIN_PASSWORD", os.environ.get("APP_PASSWORD", "admin123"))
APP_USER_USERNAME = os.environ.get("APP_USER_USERNAME", "user").strip()
APP_USER_PASSWORD = os.environ.get("APP_USER_PASSWORD", "user123")
APP_SESSION_SECRET = os.environ.get("APP_SESSION_SECRET", "change-me-in-production")
APP_SECURE_COOKIES = os.environ.get("APP_SECURE_COOKIES", "0").strip().lower() in {"1", "true", "yes", "on"}
SESSION_TTL_SECONDS = int(os.environ.get("APP_SESSION_TTL_SECONDS", "86400"))
MAX_LOGIN_ATTEMPTS = int(os.environ.get("APP_MAX_LOGIN_ATTEMPTS", "5"))
LOGIN_BLOCK_SECONDS = int(os.environ.get("APP_LOGIN_BLOCK_SECONDS", "300"))
USER_DB_PATH = BASE / "data" / "users.db"
LOGIN_ATTEMPTS = {}


def _hash_password(password: str) -> str:
    salt = os.urandom(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, 200_000)
    return f"pbkdf2_sha256$200000${salt.hex()}${digest.hex()}"


def _verify_password(password: str, password_hash: str) -> bool:
    if not password_hash or "$" not in password_hash:
        return False
    try:
        algorithm, iterations, salt_hex, digest_hex = password_hash.split("$", 3)
        if algorithm != "pbkdf2_sha256":
            return False
        iterations = int(iterations)
        salt = bytes.fromhex(salt_hex)
        expected = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
        return hmac.compare_digest(expected.hex(), digest_hex)
    except (TypeError, ValueError):
        return False


def ensure_user_store():
    USER_DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(USER_DB_PATH)
    conn.execute(
        "CREATE TABLE IF NOT EXISTS users ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT, "
        "username TEXT NOT NULL UNIQUE, "
        "password_hash TEXT NOT NULL, "
        "role TEXT NOT NULL DEFAULT 'user')"
    )
    columns = [row[1] for row in conn.execute("PRAGMA table_info(users)").fetchall()]
    if "role" not in columns:
        conn.execute("ALTER TABLE users ADD COLUMN role TEXT NOT NULL DEFAULT 'user'")
    desired = {}
    if APP_ADMIN_USERNAME:
        desired[APP_ADMIN_USERNAME] = ("admin", APP_ADMIN_PASSWORD)
    if APP_USER_USERNAME and APP_USER_USERNAME != APP_ADMIN_USERNAME:
        desired[APP_USER_USERNAME] = ("user", APP_USER_PASSWORD)
    for username, (role, password) in desired.items():
        row = conn.execute(
            "SELECT password_hash, role FROM users WHERE username = ?",
            (username,),
        ).fetchone()
        if row is None:
            conn.execute(
                "INSERT INTO users (username, password_hash, role) VALUES (?, ?, ?)",
                (username, _hash_password(password), role),
            )
        else:
            current_hash, current_role = row
            if current_role != role:
                conn.execute(
                    "UPDATE users SET role = ? WHERE username = ?",
                    (role, username),
                )
            if current_hash is None or current_hash == "":
                conn.execute(
                    "UPDATE users SET password_hash = ? WHERE username = ?",
                    (_hash_password(password), username),
                )
    conn.commit()
    conn.close()


def get_user_role(username: str) -> str | None:
    if not username:
        return None
    conn = sqlite3.connect(USER_DB_PATH)
    row = conn.execute(
        "SELECT role FROM users WHERE username = ?",
        (username,),
    ).fetchone()
    conn.close()
    if row is None:
        return None
    return row[0]


def user_has_role(username: str, role: str) -> bool:
    return get_user_role(username) == role


def valid_credentials(username: str, password: str) -> bool:
    if not username or not password:
        return False
    conn = sqlite3.connect(USER_DB_PATH)
    row = conn.execute(
        "SELECT password_hash FROM users WHERE username = ?",
        (username,),
    ).fetchone()
    conn.close()
    if row is None:
        return False
    return _verify_password(password, row[0])


def get_session_user(request: Request) -> dict | None:
    session = request.cookies.get("argus_session")
    if not session:
        return None
    username = decode_session(session)
    if not username:
        return None
    role = get_user_role(username)
    if role is None:
        role = "admin" if username == APP_ADMIN_USERNAME else "user"
    return {"username": username, "role": role}


def require_admin(request: Request) -> dict:
    user = get_session_user(request)
    if user is None:
        raise HTTPException(status_code=401, detail="Authentification requise")
    if user["role"] != "admin":
        raise HTTPException(status_code=403, detail="Accès réservé à l'administrateur")
    return user


def encode_session(username: str) -> str:
    now = int(time.time())
    payload = f"{username}:{now}".encode("utf-8")
    signature = hmac.new(APP_SESSION_SECRET.encode("utf-8"), payload, hashlib.sha256).hexdigest()
    token = f"{username}:{now}:{signature}"
    return base64.b64encode(token.encode("utf-8")).decode("ascii")


def decode_session(raw: str | None) -> str | None:
    if not raw:
        return None
    try:
        decoded = base64.b64decode(raw.encode("ascii")).decode("utf-8")
    except Exception:
        return None
    parts = decoded.split(":")
    if len(parts) != 3:
        return None
    username, ts_raw, signature = parts
    if not username or not ts_raw.isdigit():
        return None
    ts = int(ts_raw)
    now = int(time.time())
    if now - ts > SESSION_TTL_SECONDS:
        return None
    expected = hmac.new(
        APP_SESSION_SECRET.encode("utf-8"),
        f"{username}:{ts_raw}".encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    if not hmac.compare_digest(signature, expected):
        return None
    return username


def _login_key(username: str, client_ip: str) -> str:
    return f"{client_ip}:{username.lower()}"


def _record_failed_login(username: str, client_ip: str) -> None:
    key = _login_key(username, client_ip)
    now = time.time()
    attempts = LOGIN_ATTEMPTS.get(key, [])
    attempts = [ts for ts in attempts if now - ts < LOGIN_BLOCK_SECONDS]
    attempts.append(now)
    LOGIN_ATTEMPTS[key] = attempts


def _clear_failed_login(username: str, client_ip: str) -> None:
    LOGIN_ATTEMPTS.pop(_login_key(username, client_ip), None)


def _login_blocked(username: str, client_ip: str) -> bool:
    key = _login_key(username, client_ip)
    attempts = LOGIN_ATTEMPTS.get(key, [])
    now = time.time()
    attempts = [ts for ts in attempts if now - ts < LOGIN_BLOCK_SECONDS]
    LOGIN_ATTEMPTS[key] = attempts
    return len(attempts) >= MAX_LOGIN_ATTEMPTS


ensure_user_store()


@app.middleware("http")
async def security_middleware(request: Request, call_next):
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
    response.headers["Content-Security-Policy"] = "default-src 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline'; script-src 'self' 'unsafe-inline'; connect-src 'self'"
    return response


@app.middleware("http")
async def auth_middleware(request: Request, call_next):
    path = request.url.path
    public_paths = {"/login", "/api/login"}
    if path in public_paths or path.startswith("/static/"):
        return await call_next(request)
    if path.startswith("/api/") or path in {"/", "/captures", "/people-photos"}:
        user = get_session_user(request)
        if user is None:
            if path.startswith("/api/"):
                return JSONResponse({"detail": "Authentification requise"}, status_code=401)
            return RedirectResponse(url="/login", status_code=303)
        request.state.user = user
    return await call_next(request)


@app.get("/login")
def login_page():
    return HTMLResponse("""
    <!doctype html>
    <html lang="fr">
    <head>
      <meta charset="utf-8">
      <meta name="viewport" content="width=device-width, initial-scale=1">
      <title>Argus — connexion</title>
      <style>
        :root { color-scheme: dark; --bg:#0f1115; --panel:#181b21; --line:#2a2f38; --text:#e8eaed; --muted:#9aa0aa; --accent:#60a5fa; }
        *{box-sizing:border-box} body{margin:0;background:var(--bg);color:var(--text);font-family:system-ui,sans-serif;display:grid;place-items:center;min-height:100vh} .box{width:min(420px,90vw);background:var(--panel);border:1px solid var(--line);border-radius:16px;padding:28px 24px;box-shadow:0 18px 40px rgba(0,0,0,.3)} .brand{display:flex;align-items:center;gap:14px;margin-bottom:22px} .logo{width:52px;height:52px;border-radius:14px;background:linear-gradient(135deg,#60a5fa,#8b5cf6);display:grid;place-items:center;box-shadow:0 10px 20px rgba(96,165,250,.32)} .logo svg{width:28px;height:28px;display:block} .title-wrap{display:flex;flex-direction:column;line-height:1.1} .project{margin:0;font-size:1.8rem;font-weight:800;letter-spacing:.02em} .subtitle{margin:4px 0 0;color:var(--muted);font-size:.9rem} h1{margin:0 0 20px;font-size:1.25rem;font-weight:700;color:var(--text)} label{display:block;margin-bottom:8px;color:var(--muted)} input{width:100%;padding:12px 14px;border:1px solid var(--line);border-radius:10px;background:#0d1117;color:var(--text);margin-bottom:16px} button{width:100%;padding:12px;border:0;border-radius:10px;background:var(--accent);color:#0b1220;font-weight:700;cursor:pointer}.hint{color:var(--muted);font-size:.9rem;margin-top:12px}
      </style>
    </head>
    <body>
      <div class="box">
        <div class="brand">
          <div class="logo" aria-label="Logo Argus">
            <svg viewBox="0 0 64 64" fill="none" xmlns="http://www.w3.org/2000/svg" aria-hidden="true">
              <path d="M32 10C20.954 10 12 18.954 12 30C12 41.046 20.954 50 32 50C43.046 50 52 41.046 52 30C52 18.954 43.046 10 32 10ZM32 15.5C40.008 15.5 46.5 21.992 46.5 30C46.5 38.008 40.008 44.5 32 44.5C23.992 44.5 17.5 38.008 17.5 30C17.5 21.992 23.992 15.5 32 15.5Z" fill="white" opacity="0.92"/>
              <path d="M32 24C27.582 24 24 27.582 24 32C24 36.418 27.582 40 32 40C36.418 40 40 36.418 40 32C40 27.582 36.418 24 32 24ZM32 27.5C34.485 27.5 36.5 29.515 36.5 32C36.5 34.485 34.485 36.5 32 36.5C29.515 36.5 27.5 34.485 27.5 32C27.5 29.515 29.515 27.5 32 27.5Z" fill="white"/>
              <path d="M19 18L14 12L24 12L19 18ZM45 18L50 12L40 12L45 18Z" fill="white" opacity="0.9"/>
            </svg>
          </div>
          <div class="title-wrap">
            <p class="project">Argus</p>
            <span class="subtitle">Détecteur d'humain</span>
          </div>
        </div>
        <h1>Connexion</h1>
        <form method="post" action="/api/login">
          <label for="username">Nom d'utilisateur</label>
          <input id="username" name="username" type="text" placeholder="admin" autocomplete="username" required>
          <label for="password">Mot de passe</label>
          <input id="password" name="password" type="password" placeholder="••••••••" autocomplete="current-password" required>
          <button type="submit">Se connecter</button>
          <div class="hint">Identifiants configurés dans le fichier .env.</div>
        </form>
      </div>
    </body>
    </html>
    """)


@app.post("/api/login")
def login(request: Request, username: str = Form(...), password: str = Form(...), response: Response = None):
    client_ip = request.client.host if request.client else "unknown"
    if _login_blocked(username, client_ip):
        raise HTTPException(status_code=429, detail=f"Trop de tentatives. Réessayez dans {LOGIN_BLOCK_SECONDS // 60} minutes.")
    if not valid_credentials(username, password):
        _record_failed_login(username, client_ip)
        raise HTTPException(status_code=401, detail="Identifiants invalides")
    _clear_failed_login(username, client_ip)
    session_value = encode_session(username)
    cookie_kwargs = {
        "httponly": True,
        "samesite": "lax",
        "secure": APP_SECURE_COOKIES,
        "max_age": SESSION_TTL_SECONDS,
    }
    if response is not None:
        response.set_cookie(key="argus_session", value=session_value, **cookie_kwargs)
    redirect = RedirectResponse(url="/", status_code=303)
    redirect.set_cookie(key="argus_session", value=session_value, **cookie_kwargs)
    return redirect


@app.post("/api/logout")
def logout(response: Response):
    response.delete_cookie(
        key="argus_session",
        path="/",
        httponly=True,
        samesite="lax",
        secure=APP_SECURE_COOKIES,
    )
    redirect = RedirectResponse(url="/login", status_code=303)
    redirect.set_cookie(
        key="argus_session",
        value="",
        path="/",
        httponly=True,
        samesite="lax",
        secure=APP_SECURE_COOKIES,
        max_age=0,
    )
    return redirect


def open_capture(url: str):
    """Ouvre un flux vidéo avec un backend OpenCV compatible.

    Les flux RTSP peuvent être accessibles via différents backends selon l'installation
    de OpenCV et la plateforme. On essaye d'abord le backend par défaut puis le backend
    FFMPEG, ce qui évite de bloquer sur un flux valide mais non ouvert par un backend trop strict.
    """
    if not url or not url.strip():
        return None, "URL du flux vide"

    stream = url.strip()
    for backend in (cv2.CAP_ANY, cv2.CAP_FFMPEG):
        cap = cv2.VideoCapture(stream, backend)
        if cap is not None and cap.isOpened():
            cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
            return cap, None
        if cap is not None:
            cap.release()

    return None, "Impossible d'ouvrir le flux. Vérifie l'URL, le Raspberry Pi, et la disponibilité du flux RTSP."


class Detector:
    def __init__(self):
        self.lock = threading.Lock()
        self.thread = None
        self.stop_event = threading.Event()
        self.frame_jpeg = None
        self.paused = False
        self.seek_to = None
        self.armed = False
        self.alarm = False
        self.code = None
        self.source_label = ""
        self.state = self._blank_state()

    def arm(self, code):
        self.code = code
        self.alarm = False
        self.armed = True

    def disarm(self, code):
        if self.code is None or not hmac.compare_digest(code, self.code):
            return False
        self.armed = False
        self.alarm = False
        self.code = None
        return True

    @staticmethod
    def _blank_state():
        return {"running": False, "connected": False, "human_detected": False, "faces": 0, "error": None,
                "is_file": False, "paused": False, "position": 0, "duration": 0, "person": None,
                "detection_start": None}

    def pause(self):
        self.paused = True

    def resume(self):
        self.paused = False
        self._set(paused=False)

    def seek(self, t):
        self.seek_to = max(0.0, t)

    def start(self, url, label=None):
        self.stop()
        self.source_label = label or safe_label(url)
        self.stop_event = threading.Event()
        self.paused = False
        self.seek_to = None
        self.state = self._blank_state()
        self.state["running"] = True
        self.thread = threading.Thread(target=self._run, args=(url, self.stop_event), daemon=True)
        self.thread.start()

    def stop(self):
        self.stop_event.set()
        if self.thread:
            self.thread.join(timeout=5)
        self.thread = None
        with self.lock:
            self.state = self._blank_state()
            self.frame_jpeg = None

    def _set(self, **kw):
        with self.lock:
            self.state.update(kw)

    def _run(self, url, stop):
        try:
            ensure_model()
        except Exception as e:
            self._set(running=False, error=f"Téléchargement du modèle impossible : {e}")
            return
        options = vision.FaceDetectorOptions(
            base_options=mp_python.BaseOptions(
                model_asset_path=str(MODEL_PATH),
                delegate=mp_python.BaseOptions.Delegate.CPU,  # évite le crash Metal/GPU sur macOS
            ),
            min_detection_confidence=MIN_SCORE,
        )
        last_seen = 0.0
        ep_id = ep_photo = ep_crop = None  # épisode (passage) en cours dans l'historique
        ep_score = ep_saved = ep_touched = 0.0
        ep_faces, ep_alarm = 0, False
        ep_person_id = ep_person_name = None  # personne reconnue pour cet épisode
        last_recog = 0.0  # dernière tentative de reconnaissance
        detection_start = None  # horodatage du premier visage inconnu détecté (alarme différée)
        with vision.FaceDetector.create_from_options(options) as detector:
            while not stop.is_set():
                cap, open_error = open_capture(url)
                if cap is None:
                    self._set(connected=False, human_detected=False, faces=0,
                              error=open_error or "Impossible d'ouvrir le flux, nouvelle tentative…")
                    stop.wait(2)
                    continue
                is_file = Path(url).is_file()
                fps = cap.get(cv2.CAP_PROP_FPS) if is_file else 0
                fps = fps if fps and fps > 1 else 25
                delay = 1 / fps
                duration = (cap.get(cv2.CAP_PROP_FRAME_COUNT) / fps) if is_file else 0
                self._set(connected=True, error=None, is_file=is_file, duration=duration)
                live = None if is_file else LiveReader(cap)
                streak = 0
                while not stop.is_set():
                    t0 = time.time()
                    step = False
                    if is_file and self.seek_to is not None:
                        cap.set(cv2.CAP_PROP_POS_MSEC, self.seek_to * 1000)
                        self.seek_to = None
                        step = True  # affiche l'image à la nouvelle position même en pause
                    if is_file and self.paused and not step:
                        stop.wait(0.05)
                        continue
                    try:
                        ok, frame = cap.read() if is_file else live.read()
                    except cv2.error:
                        self._set(connected=False, error="Flux corrompu ou non lisible, reconnexion…")
                        break
                    if not ok:
                        self._set(connected=False, error="Flux interrompu, reconnexion…")
                        break
                    h, w = frame.shape[:2]
                    if w > MAX_WIDTH:  # image plus petite = analyse, encodage et réseau plus rapides
                        frame = cv2.resize(frame, (MAX_WIDTH, int(h * MAX_WIDTH / w)), interpolation=cv2.INTER_AREA)
                        h, w = frame.shape[:2]
                    rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
                    result = detector.detect(mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb))
                    # filtre anti faux positifs : score minimum + taille minimum
                    dets = [d for d in result.detections
                            if d.categories and d.categories[0].score >= MIN_SCORE
                            and d.bounding_box.width >= MIN_FACE_RATIO * w]
                    streak = streak + 1 if dets else 0
                    if streak < CONFIRM_FRAMES:  # il faut plusieurs images de suite pour valider
                        dets = []
                    n = len(dets)
                    now = time.time()
                    triggered = False
                    position = cap.get(cv2.CAP_PROP_POS_MSEC) / 1000 if is_file else 0
                    best, crop_jpeg = None, None
                    if n:
                        last_seen = now
                        best = max(dets, key=lambda d: d.categories[0].score)
                        score = best.categories[0].score
                        # nouvel épisode, ou meilleure prise de vue du même passage : on garde le visage net
                        if ep_id is None or (score > ep_score + 0.05 and now - ep_saved > 1):
                            crop_jpeg = encode_crop(frame, best.bounding_box)  # avant de dessiner dessus
                        # reconnaissance : max 1 fois toutes les 2 s, uniquement sur le meilleur visage
                        if now - last_recog > 2.0 and face_db.available:
                            last_recog = now
                            b = best.bounding_box
                            face_crop = encode_crop_raw(frame, b)
                            if face_crop is not None:
                                pid, pname = face_db.recognize(face_crop)
                                if pid is not None:
                                    ep_person_id, ep_person_name = pid, pname
                        # alarme différée : 4 s pour reconnaître la personne avant de déclencher
                        if self.armed and not self.alarm:
                            if ep_person_id is not None:
                                detection_start = None  # personne autorisée → on ne déclenche pas
                            else:
                                if detection_start is None:
                                    detection_start = now
                                elif now - detection_start >= 4.0:
                                    self.alarm = True
                                    triggered = True
                                    detection_start = None
                        else:
                            detection_start = None
                    else:
                        detection_start = None
                        if ep_id is not None and now - last_seen > EPISODE_GAP:
                            ep_person_id = ep_person_name = None  # réinitialise pour le prochain épisode
                    for d in dets:
                        b = d.bounding_box
                        is_best = d is best
                        color = (0, 200, 0) if (is_best and ep_person_name) else (0, 0, 255)
                        label = ep_person_name if (is_best and ep_person_name) else "Humain"
                        cv2.rectangle(frame, (b.origin_x, b.origin_y),
                                      (b.origin_x + b.width, b.origin_y + b.height), color, 4)
                        cv2.putText(frame, label, (b.origin_x, b.origin_y + b.height + 28),
                                    cv2.FONT_HERSHEY_SIMPLEX, 0.9, color, 2, cv2.LINE_AA)
                    ok, buf = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 75])
                    if triggered and ok:  # envoi en arrière-plan pour ne pas ralentir l'analyse
                        threading.Thread(target=notify, args=(buf.tobytes(),), daemon=True).start()
                    if ok:
                        if n:
                            b = best.bounding_box
                            box = dict(face_x=b.origin_x, face_y=b.origin_y, face_w=b.width, face_h=b.height,
                                       frame_w=w, frame_h=h)
                            if ep_id is None:
                                fname = f"{int(now)}_{uuid.uuid4().hex[:8]}"
                                ep_photo, ep_crop = f"{fname}.jpg", f"{fname}_visage.jpg"
                                (CAPTURES_DIR / ep_photo).write_bytes(buf.tobytes())
                                if crop_jpeg:
                                    (CAPTURES_DIR / ep_crop).write_bytes(crop_jpeg)
                                ep_id = events.create(
                                    started_at=now, ended_at=now, source=self.source_label,
                                    source_type="fichier" if is_file else "flux",
                                    video_time=position if is_file else None, faces=n, score=score,
                                    alarm=int(self.armed), photo=ep_photo, crop=ep_crop if crop_jpeg else None,
                                    person=ep_person_name, person_id=ep_person_id,
                                    **box)
                                ep_score, ep_saved, ep_touched, ep_faces, ep_alarm = score, now, now, n, self.armed
                            else:
                                changes = {}
                                if crop_jpeg:  # meilleure image du passage : remplace la photo
                                    (CAPTURES_DIR / ep_photo).write_bytes(buf.tobytes())
                                    (CAPTURES_DIR / ep_crop).write_bytes(crop_jpeg)
                                    ep_score, ep_saved = score, now
                                    changes.update(score=score, crop=ep_crop, photo=ep_photo,
                                                   video_time=position if is_file else None, **box)
                                if n > ep_faces or (self.armed and not ep_alarm):
                                    ep_faces, ep_alarm = max(ep_faces, n), ep_alarm or self.armed
                                    changes.update(faces=ep_faces, alarm=int(ep_alarm))
                                if ep_person_id is not None and changes.get("person_id") != ep_person_id:
                                    changes.update(person=ep_person_name, person_id=ep_person_id)
                                if changes or now - ep_touched >= 1:
                                    events.update(ep_id, ended_at=now, **changes)
                                    ep_touched = now
                        elif ep_id is not None and now - last_seen > EPISODE_GAP:
                            events.update(ep_id, ended_at=last_seen)  # le passage est terminé
                            ep_id = None
                    with self.lock:
                        if ok:
                            self.frame_jpeg = buf.tobytes()
                        self.state.update(faces=n, human_detected=(now - last_seen) < HOLD_SECONDS,
                                          paused=self.paused, position=position,
                                          person=ep_person_name if n else None,
                                          detection_start=detection_start)
                    if is_file:  # cadence de lecture normale pour un fichier (la vidéo boucle)
                        stop.wait(max(0, delay - (time.time() - t0)))
                if live:
                    live.close()
                cap.release()
                stop.wait(1)
        if ep_id is not None:
            events.update(ep_id, ended_at=last_seen)
        self._set(running=False, connected=False, human_detected=False, faces=0, person=None)


def encode_crop(frame, b, margin=0.35):
    """JPEG du visage seul, avec une marge autour pour garder du contexte."""
    h, w = frame.shape[:2]
    mx, my = int(b.width * margin), int(b.height * margin)
    x0, y0 = max(0, b.origin_x - mx), max(0, b.origin_y - my)
    x1, y1 = min(w, b.origin_x + b.width + mx), min(h, b.origin_y + b.height + my)
    if x1 <= x0 or y1 <= y0:
        return None
    ok, buf = cv2.imencode(".jpg", frame[y0:y1, x0:x1], [cv2.IMWRITE_JPEG_QUALITY, 90])
    return buf.tobytes() if ok else None


def encode_crop_raw(frame, b, margin=0.15):
    """Retourne le tableau numpy BGR du visage rogné (pour la reconnaissance)."""
    h, w = frame.shape[:2]
    mx, my = int(b.width * margin), int(b.height * margin)
    x0, y0 = max(0, b.origin_x - mx), max(0, b.origin_y - my)
    x1, y1 = min(w, b.origin_x + b.width + mx), min(h, b.origin_y + b.height + my)
    if x1 <= x0 or y1 <= y0:
        return None
    return frame[y0:y1, x0:x1]


def safe_label(url):
    """Nom de source affichable, sans identifiants (rtsp://user:mdp@hôte → rtsp://hôte)."""
    parts = urlsplit(url)
    if "@" in parts.netloc:
        parts = parts._replace(netloc=parts.netloc.rsplit("@", 1)[1])
    return urlunsplit(parts)


events = History()
detector = Detector()
face_db = FaceDB(BASE / "data" / "people.db", PEOPLE_DIR)
app.mount("/captures", StaticFiles(directory=CAPTURES_DIR), name="captures")
PEOPLE_DIR.mkdir(parents=True, exist_ok=True)
app.mount("/people-photos", StaticFiles(directory=PEOPLE_DIR), name="people-photos")


class StartRequest(BaseModel):
    url: str


@app.post("/api/start")
def start(req: StartRequest):
    detector.start(req.url.strip())
    return {"ok": True}


@app.post("/api/upload")
def upload(file: UploadFile = File(...)):
    UPLOAD_DIR.mkdir(exist_ok=True)
    dest = UPLOAD_DIR / f"{uuid.uuid4().hex}{Path(file.filename or '').suffix}"
    with open(dest, "wb") as f:
        shutil.copyfileobj(file.file, f)
    detector.start(str(dest), label=file.filename or dest.name)
    return {"ok": True}


class SeekRequest(BaseModel):
    t: float


@app.post("/api/pause")
def pause():
    detector.pause()
    return {"ok": True}


@app.post("/api/resume")
def resume():
    detector.resume()
    return {"ok": True}


@app.post("/api/seek")
def seek(req: SeekRequest):
    detector.seek(req.t)
    return {"ok": True}


class CodeRequest(BaseModel):
    code: str


def check_code(code):
    if not (len(code) == 4 and code.isdigit()):
        raise HTTPException(400, "Le code doit contenir 4 chiffres")


@app.post("/api/alarm/arm")
def alarm_arm(req: CodeRequest):
    check_code(req.code)
    detector.arm(req.code)
    return {"ok": True}


@app.post("/api/alarm/disarm")
def alarm_disarm(req: CodeRequest):
    check_code(req.code)
    if not detector.disarm(req.code):
        raise HTTPException(403, "Code incorrect")
    return {"ok": True}


@app.post("/api/stop")
def stop():
    detector.stop()
    return {"ok": True}


@app.get("/api/status")
def status():
    with detector.lock:
        state = {**detector.state, "armed": detector.armed, "alarm": detector.alarm}
    ds = state.pop("detection_start", None)
    state["countdown"] = round(max(0.0, 4.0 - (time.time() - ds)), 1) if ds is not None else None
    return state


def mjpeg():
    while True:
        with detector.lock:
            frame = detector.frame_jpeg
        if frame:
            yield b"--frame\r\nContent-Type: image/jpeg\r\n\r\n" + frame + b"\r\n"
        time.sleep(0.04)


@app.get("/api/video")
def video():
    return StreamingResponse(mjpeg(), media_type="multipart/x-mixed-replace; boundary=frame")


def event_json(r):
    return {**r, "duration": max(0.0, r["ended_at"] - r["started_at"]),
            "photo_url": f"/captures/{r['photo']}" if r["photo"] else None,
            "crop_url": f"/captures/{r['crop']}" if r["crop"] else None}


def require_disarmed():
    if detector.armed:  # un intrus ne doit pas pouvoir effacer ses traces
        raise HTTPException(403, "Désactive l'alarme avant de supprimer l'historique")


@app.get("/api/me")
def me(request: Request):
    user = get_session_user(request)
    if user is None:
        raise HTTPException(status_code=401, detail="Authentification requise")
    return user


@app.get("/api/events")
def list_events(limit: int = 60, offset: int = 0, alarm: bool = False,
                since: float | None = None, until: float | None = None):
    items, total = events.list(min(max(limit, 1), 200), max(offset, 0), alarm, since, until)
    return {"items": [event_json(r) for r in items], "total": total}


@app.get("/api/events/stats")
def events_stats():
    return events.stats()


@app.get("/api/events/export.csv")
def events_export(alarm: bool = False, since: float | None = None, until: float | None = None):
    return Response(events.export_csv(alarm, since, until), media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition": 'attachment; filename="historique-argus.csv"'})


@app.delete("/api/events/{event_id}")
def delete_event(request: Request, event_id: int):
    require_admin(request)
    require_disarmed()
    if not events.delete(event_id):
        raise HTTPException(404, "Événement introuvable")
    return {"ok": True}


@app.delete("/api/events")
def clear_events(request: Request):
    require_admin(request)
    require_disarmed()
    events.clear()
    return {"ok": True}


@app.get("/api/people")
def list_people(request: Request):
    require_admin(request)
    people = face_db.list_people()
    return {"items": [
        {**p, "photo_url": f"/people-photos/{p['photo']}"} for p in people
    ], "available": face_db.available}


@app.post("/api/people")
async def add_person(request: Request, name: str, file: UploadFile = File(...)):
    require_admin(request)
    if not name.strip():
        raise HTTPException(400, "Le nom est requis")
    data = await file.read()
    arr = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_COLOR)
    if arr is None:
        raise HTTPException(400, "Image invalide")
    ok, err = face_db.add(name.strip(), arr)
    if not ok:
        raise HTTPException(400, err)
    return {"ok": True}


@app.delete("/api/people/{person_id}")
def delete_person(request: Request, person_id: int):
    require_admin(request)
    if not face_db.remove(person_id):
        raise HTTPException(404, "Personne introuvable")
    return {"ok": True}


@app.get("/")
def index():
    page = BASE / "static" / "index.html"
    if not page.exists():  # image Docker : la page est servie par le conteneur frontend
        raise HTTPException(404, "Page servie par le frontend")
    return FileResponse(page)
