# Argus — détecteur d'humain

```bash
pip install -r requirements.txt
uvicorn app:app --reload
# puis ouvrir http://localhost:8000 et coller l'URL du flux (RTSP/HTTP/MP4)
```

Le modèle MediaPipe (`blaze_face_short_range.tflite`) est téléchargé automatiquement au premier lancement.

## Notifications

Quand l'alarme est activée et qu'un visage est détecté, une alerte (avec la photo) est envoyée.
Copie `.env.example` en `.env` et remplis :
- `NTFY_TOPIC` : nom secret de canal, puis abonne-toi au même nom dans l'app ntfy (iOS/Android) ;
- et/ou `SMTP_*` + `ALERT_EMAIL_TO` pour l'email.
Redémarre `uvicorn` après modification.

## Historique

Chaque passage détecté est enregistré dans `data/` (`history.db` + photos dans `data/captures/`) :
photo annotée, visage recadré, début/fin/durée, source, confiance, alarme active ou non.
Onglet « Historique » : statistiques, courbe des dernières 24 h, filtres (alarmes, jour), détail, export CSV.
Les 2000 événements les plus récents sont conservés. La suppression est refusée tant que l'alarme est activée.
