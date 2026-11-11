# simulator

Responsable : Chaima · Semaines 1–2, 4, 6

Remplace le matériel : ESP32 virtuels (un conteneur par device, même comportement que le
firmware : mesure, deep sleep, heartbeat, OTA double partition), modèle de sol (bilan
hydrique avec la météo réelle de Sfax) et injection de pannes (fuite, pompe HS, capteur
figé/faussé, device muet, firmware défectueux).

```bash
FOG_ID=fog-a python -m simulator.main
```

## Pannes (semaine 4)

Publier une commande JSON sur `fog-a/sim/fault` (détail dans `simulator/faults.py`) :

```powershell
docker compose -f deploy/compose/docker-compose.yml exec mosquitto mosquitto_pub -t fog-a/sim/fault -m '{\"type\":\"leak\",\"zone\":\"z2\",\"lpm\":6}'
```
Types : `leak`, `pump_fail`, `sensor_stuck`, `sensor_bias`, `offline`, `rain_forecast`, `clear`.

Les ESP32 essaient leur broker puis ceux de `fallback_brokers` (config) : si le fog tombe,
ils basculent sur le voisin, et reviennent dès que leur broker répond de nouveau.
