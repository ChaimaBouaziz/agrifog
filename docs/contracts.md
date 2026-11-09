# Contrats entre Fog A et Fog B

À valider par Chaima et Ines avant de coder. Toute modification passe par une PR qui touche
**ce fichier et** `libs/agrifog_common/agrifog_common/topics.py` en même temps.

## Conventions

- Noms Tailscale : `fog-a` (PC Chaima), `fog-b` (PC Ines).
- Unités : litres (L), millimètres (mm), °C, % pour l'humidité, secondes (s).
- Horodatage : UTC, ISO 8601 (`2026-11-09T05:30:00Z`), champ `ts`.
- Messages MQTT en JSON, QoS 1.

## Topics MQTT

`{fog}` = `fog-a` | `fog-b` · `{zone}` = `z1`, `z2`… · `{device}` = `esp-a-01`…

| Topic | Portée | Émetteur | Contenu |
|---|---|---|---|
| `{fog}/zone/{zone}/telemetry` | locale | ESP32 | `soil_moisture`, `air_temp`, `air_humidity`, `flow_lpm` |
| `{fog}/zone/{zone}/cmd` | locale | irrigation-engine | `{"action":"water","seconds":40}` |
| `{fog}/device/{device}/heartbeat` | locale | ESP32 | `fw_version`, `battery_v`, `rssi`, `uptime_s` |
| `{fog}/device/{device}/ota` | locale | ota-manager | `{"version":"1.1.0","url":"…","sha256":"…"}` |
| `{fog}/alert` | locale | tous | `{"type":"leak","zone":"z2","msg":"…"}` |
| `domaine/fog/{fog}/status` | partagée, retained | chaque fog | `alive`, `cpu`, `ram`, `version` |
| `domaine/air/{fog}` | partagée | chaque fog | `air_temp`, `air_humidity` |
| `domaine/quota/request/{fog}` | partagée | quota-negotiator | `need_l`, `priority` (0–1), `day` |
| `domaine/quota/allocation` | partagée, retained | quota-negotiator | `{"day":"…","fog-a":420,"fog-b":580}` |
| `domaine/pump/token` | partagée, retained | pump-scheduler | `holder`, `until` |

Seul `domaine/#` traverse le bridge Mosquitto. Le bridge est déclaré **uniquement sur fog-a**.

## API REST entre fogs (service fog-twin, port 8080)

| Méthode | Chemin | Rôle |
|---|---|---|
| GET | `/health` | état du fog |
| POST | `/backup` | reçoit un lot de données du voisin |
| GET | `/backup/{fog}` | restitue les données sauvegardées d'un fog |
| POST | `/takeover` | le voisin demande / annonce la reprise de sa parcelle |

## Valeurs communes

- Quota journalier du domaine : 1000 L (à ajuster après le modèle de sol).
- Repli hors ligne : 50 % du quota pour chaque fog.
- Heures fraîches : 05:00–08:00 et 18:00–21:00 (heure locale).
