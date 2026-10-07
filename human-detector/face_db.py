"""Base de données de visages connus pour la reconnaissance faciale.

Utilise InceptionResnetV1 (facenet-pytorch) pour produire des embeddings 512-d,
comparés par distance cosinus. Le modèle est chargé une seule fois à la demande.
"""
import json
import os
import sqlite3
import threading
import uuid
from pathlib import Path

import cv2
import numpy as np

try:
    import torch
    from facenet_pytorch import InceptionResnetV1
    AVAILABLE = True
except ImportError:
    AVAILABLE = False

FACE_SIZE = 160
# Le seuil est augmenté pour éviter de matcher des visages différents sous la même identité.
# Une valeur autour de 0.5 reste un bon compromis entre précision et tolérance.
THRESHOLD = float(os.getenv("FACE_RECOGNITION_THRESHOLD", "0.5"))


def _preprocess(face_bgr: np.ndarray) -> "torch.Tensor":
    rgb = cv2.cvtColor(cv2.resize(face_bgr, (FACE_SIZE, FACE_SIZE)), cv2.COLOR_BGR2RGB)
    t = torch.from_numpy(rgb).permute(2, 0, 1).float() / 255.0
    t = (t - 0.5) / 0.5  # normalise vers [-1, 1]
    return t.unsqueeze(0)


def _cosine_dist(a: np.ndarray, b: np.ndarray) -> float:
    return float(1 - np.dot(a, b) / (np.linalg.norm(a) * np.linalg.norm(b) + 1e-9))


class FaceDB:
    def __init__(self, db_path: Path, photos_dir: Path):
        self.lock = threading.Lock()
        self.photos_dir = photos_dir
        photos_dir.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(str(db_path), check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        with self.lock, self.db:
            self.db.execute("""
                CREATE TABLE IF NOT EXISTS people (
                    id   INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL,
                    photo TEXT NOT NULL,
                    embedding TEXT NOT NULL
                )""")
        self._model = None
        self._cache = None  # (embeddings np.ndarray N×512, names list, ids list)

    @property
    def available(self) -> bool:
        return AVAILABLE

    def _get_model(self):
        if self._model is None:
            self._model = InceptionResnetV1(pretrained="vggface2").eval()
        return self._model

    def _invalidate(self):
        self._cache = None

    def _get_cache(self):
        if self._cache is not None:
            return self._cache
        with self.lock:
            rows = self.db.execute("SELECT id, name, embedding FROM people").fetchall()
        if not rows:
            self._cache = (np.empty((0, 512)), [], [])
            return self._cache
        embs = np.array([json.loads(r["embedding"]) for r in rows], dtype=np.float32)
        names = [r["name"] for r in rows]
        ids = [r["id"] for r in rows]
        self._cache = (embs, names, ids)
        return self._cache

    def _embed(self, face_bgr: np.ndarray) -> "np.ndarray | None":
        if not AVAILABLE:
            return None
        model = self._get_model()
        tensor = _preprocess(face_bgr)
        with torch.no_grad():
            emb = model(tensor).cpu().numpy()[0]
        return emb

    def add(self, name: str, image_bgr: np.ndarray) -> tuple[bool, str]:
        if not AVAILABLE:
            return False, "facenet-pytorch non disponible (pip install facenet-pytorch)"
        emb = self._embed(image_bgr)
        if emb is None:
            return False, "Impossible de calculer l'embedding"
        photo_name = f"{uuid.uuid4().hex}.jpg"
        ok, buf = cv2.imencode(".jpg", image_bgr, [cv2.IMWRITE_JPEG_QUALITY, 90])
        if ok:
            (self.photos_dir / photo_name).write_bytes(buf.tobytes())
        with self.lock, self.db:
            self.db.execute(
                "INSERT INTO people (name, photo, embedding) VALUES (?, ?, ?)",
                (name, photo_name, json.dumps(emb.tolist()))
            )
        self._invalidate()
        return True, ""

    def remove(self, person_id: int) -> bool:
        with self.lock, self.db:
            row = self.db.execute("SELECT photo FROM people WHERE id=?", (person_id,)).fetchone()
            if not row:
                return False
            (self.photos_dir / row["photo"]).unlink(missing_ok=True)
            self.db.execute("DELETE FROM people WHERE id=?", (person_id,))
        self._invalidate()
        return True

    def list_people(self) -> list[dict]:
        with self.lock:
            rows = self.db.execute("SELECT id, name, photo FROM people ORDER BY name").fetchall()
        return [dict(r) for r in rows]

    def recognize(self, face_bgr: np.ndarray) -> tuple[int | None, str | None]:
        """Retourne (person_id, name) ou (None, None) si inconnu."""
        if not AVAILABLE:
            return None, None
        known_embs, names, ids = self._get_cache()
        if len(ids) == 0:
            return None, None
        emb = self._embed(face_bgr)
        if emb is None:
            return None, None
        dists = np.array([_cosine_dist(emb, k) for k in known_embs])
        best = int(np.argmin(dists))
        best_dist = float(dists[best])
        if "FACE_RECOGNITION_DEBUG" in os.environ:
            print(f"[face-recognition] best={best_dist:.4f} threshold={THRESHOLD} candidate={names[best] if names else None}")
        if best_dist <= THRESHOLD:
            return ids[best], names[best]
        return None, None
