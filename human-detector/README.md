# ATLAS — Détecteur d'humain

Système de détection et de reconnaissance faciale en temps réel sur flux vidéo (RTSP, HTTP, fichier MP4).  
Interface web avec alarme à code, historique des passages et base de visages connus.
La vue **En direct** affiche aussi des courbes CPU, mémoire, température et stockage sur cinq minutes. Ces valeurs sont mesurées sur la machine qui exécute le backend ATLAS (par exemple le Raspberry Pi une fois le backend déployé dessus), pas sur la caméra.

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
| `ATLAS_AGENT_TOKEN` | Jeton secret partagé avec l'agent de métriques du Pi | *(désactivé)* |
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

## Métriques Raspberry Pi sans installer ATLAS sur le Pi

Le petit agent `atlas_pi_agent.py` n'utilise que la bibliothèque standard Python de Linux. Il envoie CPU, mémoire, température et espace disque à ATLAS toutes les cinq secondes. Le backend conserve le dernier relevé en mémoire; si l'agent n'envoie plus rien pendant 20 secondes, l'interface l'indique comme déconnecté et affiche les mesures locales en secours.

1. Sur le PC, génère un jeton aléatoire et conserve-le localement :

   ```powershell
py -3 -c "import secrets; print(secrets.token_urlsafe(32))"
   ```

   Ajoute `ATLAS_AGENT_TOKEN=<jeton>` au fichier `.env` du backend et redémarre ATLAS. Ne mets pas le jeton dans le dépôt.

2. Assure-toi que le Pi et le PC sont sur un réseau local de confiance. Réserve une adresse IP au PC dans le routeur. Si ATLAS tourne directement sous Uvicorn, l'agent utilisera le port `8000`; avec Docker Compose, utilise le port frontend `8080`. Sous Windows, ajoute une règle entrante pour ce port sur le profil **Privé** (PowerShell lancé en administrateur) :

   ```powershell
   New-NetFirewallRule -DisplayName "ATLAS Raspberry Pi metrics" -Direction Inbound -Protocol TCP -LocalPort 8000 -Action Allow -Profile Private
   ```

   Avec Docker Compose, remplace `8000` par `8080`.

3. Depuis PowerShell, dans le dossier `human-detector`, copie les deux fichiers vers le Pi :

   ```powershell
   scp .\atlas_pi_agent.py .\atlas-pi-agent.service pi@<IP_DU_PI>:/tmp/
   ```

4. Connecte-toi au Pi en SSH, crée le compte du service et installe l'agent :

   ```bash
   sudo useradd --system --home-dir /opt/atlas-agent --create-home --shell /usr/sbin/nologin atlas-agent
   sudo install -o atlas-agent -g atlas-agent -m 0755 /tmp/atlas_pi_agent.py /opt/atlas-agent/atlas_pi_agent.py
   sudo install -o root -g root -m 0644 /tmp/atlas-pi-agent.service /etc/systemd/system/atlas-pi-agent.service
   sudo nano /etc/atlas-agent.env
   ```

   Dans `/etc/atlas-agent.env`, renseigne les mêmes valeurs que ci-dessous, en remplaçant les exemples par l'adresse du PC et le même jeton qu'à l'étape 1 :

   ```ini
   ATLAS_URL=http://<IP_DU_PC>:8080
   ATLAS_AGENT_TOKEN=<meme-jeton-secret>
   ```

   Si ATLAS tourne directement avec Uvicorn, remplace `8080` par `8000`. Protège ensuite le fichier et démarre le service :

   ```bash
   sudo chown root:atlas-agent /etc/atlas-agent.env
   sudo chmod 640 /etc/atlas-agent.env
   sudo systemctl daemon-reload
   sudo systemctl enable --now atlas-pi-agent
   sudo systemctl status atlas-pi-agent --no-pager
   ```

   L'agent tourne alors en arrière-plan au démarrage. Pour consulter ses erreurs : `sudo journalctl -u atlas-pi-agent -f`.

La route de réception des métriques est protégée par le jeton secret configuré dans `.env`. Ne transfère pas le port ATLAS sur Internet; pour un réseau non fiable, utilise un VPN ou HTTPS.

---

## Structure

```
human-detector/
├── app.py          # Backend FastAPI + détection + alarme
├── face_db.py      # Base de visages (facenet-pytorch)
├── history.py      # Historique SQLite des passages
├── seed_demo.py    # Seed des utilisateurs depuis .env
├── atlas_pi_agent.py       # Agent Linux léger pour les métriques Raspberry Pi
├── atlas-pi-agent.service  # Service systemd de l'agent
├── static/
│   └── index.html  # Interface web (SPA vanilla JS)
├── models/         # Modèle MediaPipe (téléchargé au premier lancement)
├── data/           # BDD SQLite + captures photos
└── .env            # Configuration locale (ne pas commiter)
```

Les métriques système sont relevées toutes les cinq secondes. Sans agent Pi actif, ATLAS affiche les métriques locales (mémoire de `psutil.virtual_memory()` et disque du volume contenant `data/`). Avec l'agent, les courbes basculent vers les mesures du Pi et n'affichent jamais une série mélangée. La température reste indisponible si aucun capteur n'est exposé.

---

## Sécurité

- Mots de passe hashés en **PBKDF2-SHA256** (200 000 itérations)
- Sessions signées **HMAC-SHA256** stockées en cookie `httponly`
- Brute-force login bloqué après 5 tentatives (5 min)
- Headers de sécurité HTTP sur toutes les réponses
- Suppression de l'historique bloquée si l'alarme est active
