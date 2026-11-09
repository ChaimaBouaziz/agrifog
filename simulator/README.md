# simulator

Responsable : Chaima · Semaines 1–2, 4, 6

Remplace le matériel : ESP32 virtuels (un conteneur par device, même comportement que le
firmware : mesure, deep sleep, heartbeat, OTA double partition), modèle de sol (bilan
hydrique avec la météo réelle de Sfax) et injection de pannes (fuite, pompe HS, capteur
figé/faussé, device muet, firmware défectueux).

```bash
FOG_ID=fog-a python -m simulator.main
```
