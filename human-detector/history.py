"""Historique des détections : base SQLite + photos sur disque.

Un « événement » = un passage (un humain apparaît, puis disparaît).
"""
import csv
import io
import sqlite3
import threading
import time
from pathlib import Path

BASE = Path(__file__).parent
DATA_DIR = BASE / "data"
CAPTURES_DIR = DATA_DIR / "captures"
HISTORY_MAX = 2000  # au-delà, les plus anciens événements (et leurs photos) sont supprimés

FIELDS = {"ended_at", "faces", "score", "alarm", "photo", "crop", "video_time",
          "face_x", "face_y", "face_w", "face_h", "frame_w", "frame_h",
          "person", "person_id"}


def hour_start(ts):
    lt = time.localtime(ts)
    return time.mktime((lt.tm_year, lt.tm_mon, lt.tm_mday, lt.tm_hour, 0, 0, 0, 0, -1))


def day_start(ts):
    lt = time.localtime(ts)
    return time.mktime((lt.tm_year, lt.tm_mon, lt.tm_mday, 0, 0, 0, 0, 0, -1))


class History:
    def __init__(self):
        CAPTURES_DIR.mkdir(parents=True, exist_ok=True)
        self.lock = threading.Lock()
        self.db = sqlite3.connect(DATA_DIR / "history.db", check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        with self.lock, self.db:
            self.db.execute("""
                CREATE TABLE IF NOT EXISTS events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    started_at REAL NOT NULL,
                    ended_at REAL NOT NULL,
                    source TEXT NOT NULL,
                    source_type TEXT NOT NULL,
                    video_time REAL,
                    faces INTEGER NOT NULL DEFAULT 1,
                    score REAL NOT NULL DEFAULT 0,
                    alarm INTEGER NOT NULL DEFAULT 0,
                    photo TEXT, crop TEXT,
                    face_x INTEGER, face_y INTEGER, face_w INTEGER, face_h INTEGER,
                    frame_w INTEGER, frame_h INTEGER,
                    person TEXT, person_id INTEGER
                )""")
            self.db.execute("CREATE INDEX IF NOT EXISTS idx_events_started ON events(started_at DESC)")
            for col, typ in (("person", "TEXT"), ("person_id", "INTEGER")):
                try:
                    self.db.execute(f"ALTER TABLE events ADD COLUMN {col} {typ}")
                except Exception:
                    pass  # colonne déjà existante

    # --- écriture ---------------------------------------------------------
    def create(self, **f):
        cols = ["started_at", "ended_at", "source", "source_type", "video_time", "faces", "score", "alarm",
                "photo", "crop", "face_x", "face_y", "face_w", "face_h", "frame_w", "frame_h",
                "person", "person_id"]
        with self.lock, self.db:
            cur = self.db.execute(
                f"INSERT INTO events ({','.join(cols)}) VALUES ({','.join('?' * len(cols))})",
                [f.get(c) for c in cols])
            event_id = cur.lastrowid
            self._prune()
        return event_id

    def update(self, event_id, **f):
        f = {k: v for k, v in f.items() if k in FIELDS}
        if not f:
            return
        with self.lock, self.db:
            self.db.execute(f"UPDATE events SET {','.join(k + '=?' for k in f)} WHERE id=?",
                            [*f.values(), event_id])

    def _prune(self):
        rows = self.db.execute("SELECT id, photo, crop FROM events ORDER BY started_at DESC LIMIT -1 OFFSET ?",
                               (HISTORY_MAX,)).fetchall()
        for r in rows:
            self._remove_files(r)
            self.db.execute("DELETE FROM events WHERE id=?", (r["id"],))

    @staticmethod
    def _remove_files(row):
        for name in (row["photo"], row["crop"]):
            if name:
                (CAPTURES_DIR / name).unlink(missing_ok=True)

    def delete(self, event_id):
        with self.lock, self.db:
            row = self.db.execute("SELECT id, photo, crop FROM events WHERE id=?", (event_id,)).fetchone()
            if not row:
                return False
            self._remove_files(row)
            self.db.execute("DELETE FROM events WHERE id=?", (event_id,))
            return True

    def clear(self):
        with self.lock, self.db:
            for r in self.db.execute("SELECT id, photo, crop FROM events").fetchall():
                self._remove_files(r)
            self.db.execute("DELETE FROM events")

    # --- lecture ----------------------------------------------------------
    @staticmethod
    def _where(alarm_only, since, until):
        clauses, args = [], []
        if alarm_only:
            clauses.append("alarm = 1")
        if since is not None:
            clauses.append("started_at >= ?")
            args.append(since)
        if until is not None:
            clauses.append("started_at < ?")
            args.append(until)
        return (" WHERE " + " AND ".join(clauses)) if clauses else "", args

    def list(self, limit=60, offset=0, alarm_only=False, since=None, until=None):
        where, args = self._where(alarm_only, since, until)
        with self.lock:
            total = self.db.execute(f"SELECT COUNT(*) FROM events{where}", args).fetchone()[0]
            rows = self.db.execute(
                f"SELECT * FROM events{where} ORDER BY started_at DESC LIMIT ? OFFSET ?",
                [*args, limit, offset]).fetchall()
        return [dict(r) for r in rows], total

    def export_csv(self, alarm_only=False, since=None, until=None):
        where, args = self._where(alarm_only, since, until)
        with self.lock:
            rows = self.db.execute(f"SELECT * FROM events{where} ORDER BY started_at DESC", args).fetchall()
        out = io.StringIO()
        w = csv.writer(out)
        w.writerow(["id", "debut", "fin", "duree_s", "source", "type_source", "position_video_s",
                    "visages_max", "confiance", "alarme_active", "photo"])
        safe = lambda v: "'" + v if isinstance(v, str) and v[:1] in ("=", "+", "-", "@", "\t", "\r") else v  # anti injection de formule
        fmt = lambda t: time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(t))
        for r in rows:
            w.writerow([r["id"], fmt(r["started_at"]), fmt(r["ended_at"]),
                        round(r["ended_at"] - r["started_at"], 1), safe(r["source"]), r["source_type"],
                        "" if r["video_time"] is None else round(r["video_time"], 1),
                        r["faces"], round(r["score"], 3), "oui" if r["alarm"] else "non", safe(r["photo"] or "")])
        return out.getvalue()

    def stats(self):
        now = time.time()
        h0 = hour_start(now)
        with self.lock:
            q = lambda sql, *a: self.db.execute(sql, a).fetchone()[0]
            total = q("SELECT COUNT(*) FROM events")
            alarms = q("SELECT COUNT(*) FROM events WHERE alarm = 1")
            today = q("SELECT COUNT(*) FROM events WHERE started_at >= ?", day_start(now))
            last24 = q("SELECT COUNT(*) FROM events WHERE started_at >= ?", now - 86400)
            rows = self.db.execute("SELECT started_at FROM events WHERE started_at >= ?",
                                   (h0 - 23 * 3600,)).fetchall()
        hourly = [{"t": h0 - (23 - i) * 3600, "n": 0} for i in range(24)]
        for r in rows:
            i = 23 - round((h0 - hour_start(r["started_at"])) / 3600)
            if 0 <= i < 24:
                hourly[i]["n"] += 1
        return {"total": total, "alarms": alarms, "today": today, "last24h": last24, "hourly": hourly}
