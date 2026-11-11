# Contrats entre Fog A et Fog B

À valider par Chaima et Ines avant de coder. Toute modification passe par une PR qui touche
**ce fichier et** `libs/agrifog_common/agrifog_common/topics.py` en même temps.

## Conventions

- Noms Tailscale : `fog-a` (PC Chaima), `fog-b` (PC Ines).
- Unités : litres (L), millimètres (mm), °C, % pour l'humidité, secondes (s).
- Horodatage : UTC, ISO 8601 (`2025-07-01T05:30:00Z`), champ `ts`. **C'est le temps SIMULÉ** (horloge accélérée du simulateur) : les services doivent raisonner sur `ts`, jamais sur l'horloge du PC.
- Durées de commande en secondes simulées.
- Messages MQTT en JSON, QoS 1.

## Topics MQTT

`{fog}` = `fog-a` | `fog-b` · `{zone}` = `z1`, `z2`… · `{device}` = `esp-a-01`…

| Topic | Portée | Émetteur | Contenu |
|---|---|---|---|
| `{fog}/zone/{zone}/telemetry` | locale | ESP32 | `device`, `soil_moisture`, `air_temp`, `air_humidity`, `flow_lpm`, `water_l_total` (compteur cumulé du débitmètre) |
| `{fog}/zone/{zone}/cmd` | locale | irrigation-engine | `{"id":"<unique>","action":"water","seconds":600}` — exécutée par l'ESP32 qui porte la vanne (1er de la zone) ; `id` évite les doublons QoS 1 |
| `{fog}/zone/{zone}/decision` | locale | irrigation-engine | `action` (water/wait), `kind`, `reason` (explication lisible), `moisture`, `et0` — publié quand la situation change |
| `{fog}/device/{device}/heartbeat` | locale | ESP32 | `fw_version`, `battery_v`, `rssi`, `uptime_s`, `broker` (broker utilisé) |
| `{fog}/device/{device}/ota` | locale | ota-manager | `{"version":"1.1.0","url":"…","sha256":"…"}` |
| `{fog}/zone/{zone}/valve` | locale | ESP32 vanne | accusé d'exécution : `id`, `device`, `state`, `opened`, `until`, `water_l_total` |
| `{fog}/weather/forecast` | locale, retained | simulateur (puis passerelle météo) | `ts`, `rain_next_24h_mm`, `source` |
| `{fog}/sim/fault` | locale | humain / bot Telegram | injection de pannes (voir `simulator/simulator/faults.py`) |
| `{fog}/alert` | locale | irrigation-engine | `ts`, `type` (leak, pump, no_rise, sensor, offline, valve), `zone`, `device`, `msg`, `resolved` |
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
