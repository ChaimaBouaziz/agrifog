# test-console

Responsable : Ines · Semaine 8

Une page web pour **provoquer chaque situation et vérifier que le système réagit**, sans taper de
commande. Chaque scénario envoie ce qu'il faut (panne du simulateur, ordre de vanne, appel à
fog-twin ou ota-manager), attend la réaction attendue sur MQTT, puis affiche **réussi / échoué**
avec le temps de réaction. Les pannes injectées sont réparées à la fin du scénario. À droite, tous les
messages MQTT du fog défilent en direct : c'est aussi la meilleure façon de voir le projet vivre.

## Lancer (PowerShell, depuis `services\test-console`)

```powershell
$env:FOG_ID="fog-a"; uvicorn app.main:app --port 8090
```
puis ouvrir **http://localhost:8090**.

- Pour les scénarios « Coopération », le voisin doit tourner ; `TWIN_PEER_URL` doit pointer vers son
  fog-twin (comme pour fog-twin : `$env:TWIN_PEER_URL="http://100.80.189.115:8080"`).
- Pour les scénarios OTA, ota-manager doit tourner sur ce PC (`OTA_URL`, défaut `http://127.0.0.1:8003`).

## Les scénarios

| Groupe | Scénario | Ce qui est vérifié |
|---|---|---|
| Décision et météo | Pluie annoncée | la nouvelle prévision est publiée |
| | Arrosage manuel | la vanne exécute l'ordre et confirme |
| | Décision expliquée | le moteur publie une décision avec sa raison |
| Anomalies | Fuite d'eau | alerte `leak` sur la bonne zone |
| | Pompe en panne | alerte `pump` |
| | Capteur défectueux | alerte `sensor` **sur le capteur figé** (non testable si le sol est presque plein) |
| | Capteur muet | alerte `offline` sur le bon ESP32 |
| | Tout réparer | plus aucune panne |
| Coopération | Le voisin est en ligne | son signe de vie arrive par le pont |
| | Accord sur le quota | les deux répartitions du jour sont identiques |
| | Jumeaux en bon état | les deux fog-twin sont « normal » |
| | Pompe commune | deux zones servies tour à tour, jamais en même temps que le voisin |
| | Maintenance | le voisin reprend notre parcelle, puis nous la rend |
| Mises à jour OTA | Firmware qui plante | campagne `rolled_back`, version bloquée |
| | Firmware qui ne mesure plus | campagne `rolled_back` (détecté par ota-manager) |

Statuts : **réussi**, **échoué** (le système a fait le contraire, ou n'a pas réagi à temps),
**non testable** (la situation du moment ne permet pas le test : la raison est affichée).
