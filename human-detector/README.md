# Argus — Détecteur d'humain

Système de détection et de reconnaissance faciale en temps réel sur flux vidéo (RTSP, HTTP, fichier MP4).  
Interface web avec alarme à code, historique des passages et base de visages connus.

---

## Prérequis

- Python 3.10+
- `pip`
- Une caméra IP / flux RTSP ou un fichier vidéo

---

## Installation

```bash
# 1. Cloner le dépôt
git clone https://github.com/Damiendm50540/Argus.git
cd Argus/human-detector

# 2. Créer un environnement virtuel (recommandé)
python3 -m venv .venv
source .venv/bin/activate      # Windows : .venv\Scripts\activate

# 3. Installer les dépendances
pip install -r requirements.txt
```

Sous Windows, le fichier installe MediaPipe 0.10.35, nécessaire au détecteur avec les versions récentes de Python.

> **Note :** `torch` et `facenet-pytorch` sont nécessaires pour la reconnaissance faciale.  
> Si tu veux uniquement la détection (sans reconnaissance), tu peux les retirer de `requirements.txt`.

---

## Configuration

Copie le fichier d'exemple et remplis les valeurs :

```bash
cp .env.example .env
```

Variables disponibles dans `.env` :

| Variable | Description | Défaut |
|---|---|---|
| `APP_ADMIN_USERNAME` | Identifiant administrateur | `admin` |
| `APP_ADMIN_PASSWORD` | Mot de passe administrateur | `admin123` |
| `APP_USER_USERNAME` | Identifiant utilisateur lecture | `user` |
| `APP_USER_PASSWORD` | Mot de passe utilisateur lecture | `user123` |
| `APP_SESSION_SECRET` | Clé secrète de signature des sessions | `argus-local-secret-change-me` |
| `NTFY_TOPIC` | Canal ntfy pour les alertes téléphone | *(désactivé)* |
| `SMTP_HOST` | Serveur SMTP pour les alertes email | *(désactivé)* |

> **En production**, change impérativement `APP_SESSION_SECRET` par une valeur longue et aléatoire.

---

## Lancement

```bash
uvicorn app:app --reload
```

Ouvre ensuite [http://localhost:8000](http://localhost:8000) dans ton navigateur.

---

## Utilisation

### En direct
1. Colle l'URL de ton flux dans le champ (`rtsp://…`, `http://…`, ou chemin vers un `.mp4`)
2. Clique sur **Démarrer**
3. Le système détecte les humains en temps réel et affiche les visages encadrés

### Alarme
- Saisis un code à 4 chiffres sur le digicode pour **armer** l'alarme
- Si un visage inconnu est détecté, l'alarme se déclenche après **4 secondes**
- Les personnes enregistrées dans la base sont reconnues et **ne déclenchent pas** l'alarme
- Entre le même code pour **désarmer**

### Reconnaissance faciale
- Va dans l'onglet **Personnes** (compte admin requis)
- Ajoute un nom + une photo nette du visage
- L'embedding est calculé automatiquement côté serveur

### Historique
- L'onglet **Historique** affiche tous les passages détectés avec photos, durée, confiance
- Filtrage par date ou par alarmes uniquement
- Export CSV disponible

---

## Seed des utilisateurs

Pour synchroniser les comptes depuis le `.env` (mots de passe hashés automatiquement) :

```bash
python3 seed_demo.py
```

---

## Docker

```bash
docker compose up --build
```

L'application sera accessible sur [http://localhost:8080](http://localhost:8080).

---

## Structure

```
human-detector/
├── app.py          # Backend FastAPI + détection + alarme
├── face_db.py      # Base de visages (facenet-pytorch)
├── history.py      # Historique SQLite des passages
├── seed_demo.py    # Seed des utilisateurs depuis .env
├── static/
│   └── index.html  # Interface web (SPA vanilla JS)
├── models/         # Modèle MediaPipe (téléchargé au premier lancement)
├── data/           # BDD SQLite + captures photos
└── .env            # Configuration locale (ne pas commiter)
```

---

## Sécurité

- Mots de passe hashés en **PBKDF2-SHA256** (200 000 itérations)
- Sessions signées **HMAC-SHA256** stockées en cookie `httponly`
- Brute-force login bloqué après 5 tentatives (5 min)
- Headers de sécurité HTTP sur toutes les réponses
- Suppression de l'historique bloquée si l'alarme est active
