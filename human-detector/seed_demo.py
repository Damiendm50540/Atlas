#!/usr/bin/env python3
"""Synchronise users.db avec les identifiants définis dans .env (mots de passe hashés)."""

import hashlib
import os
import sqlite3
from pathlib import Path

BASE = Path(__file__).parent
DATA_DIR = BASE / "data"
DATA_DIR.mkdir(exist_ok=True)


def load_env():
    path = BASE / ".env"
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        if "=" in line and not line.lstrip().startswith("#"):
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip())


def hash_pw(password: str) -> str:
    salt = os.urandom(16)
    d = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, 200_000)
    return f"pbkdf2_sha256$200000${salt.hex()}${d.hex()}"


def seed_users():
    load_env()

    users = {}
    admin_u = os.environ.get("APP_ADMIN_USERNAME", "admin").strip()
    admin_p = os.environ.get("APP_ADMIN_PASSWORD", "")
    user_u  = os.environ.get("APP_USER_USERNAME",  "user").strip()
    user_p  = os.environ.get("APP_USER_PASSWORD",  "")

    if admin_u and admin_p:
        users[admin_u] = ("admin", admin_p)
    if user_u and user_p and user_u != admin_u:
        users[user_u] = ("user", user_p)

    conn = sqlite3.connect(DATA_DIR / "users.db")
    conn.execute("""CREATE TABLE IF NOT EXISTS users (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        username TEXT NOT NULL UNIQUE,
        password_hash TEXT NOT NULL,
        role TEXT NOT NULL DEFAULT 'user')""")

    # supprime les comptes qui ne sont plus dans .env
    db_users = {r[0] for r in conn.execute("SELECT username FROM users").fetchall()}
    for u in db_users - set(users):
        conn.execute("DELETE FROM users WHERE username=?", (u,))
        print(f"  supprimé    : {u}")

    for username, (role, password) in users.items():
        row = conn.execute("SELECT id FROM users WHERE username=?", (username,)).fetchone()
        if row:
            conn.execute("UPDATE users SET password_hash=?, role=? WHERE username=?",
                         (hash_pw(password), role, username))
            print(f"  mis à jour  : {username}  [{role}]")
        else:
            conn.execute("INSERT INTO users (username, password_hash, role) VALUES (?,?,?)",
                         (username, hash_pw(password), role))
            print(f"  créé        : {username}  [{role}]")

    conn.commit()
    conn.close()


if __name__ == "__main__":
    print("Synchronisation users.db depuis .env…")
    seed_users()
    print("Done.")
