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
| `{fog}/device/{device}/ota` | locale | ota-manager | `id`, `version`, `url`, `sha256`, `size` (ordre de mise à jour) |
| `{fog}/device/{device}/ota/status` | locale | ESP32 | `id`, `device`, `version`, `from`, `state` (`downloading`, `installed`, `skipped`, `failed`, `accepted`, `rolled_back` + `running`), `detail` |
| `{fog}/ota/campaign` | locale | ota-manager | `version`, `canary`, `state` (`canary`, `rollout`, `done`, `rolled_back`, `aborted`), `detail` |
| `{fog}/zone/{zone}/valve` | locale | ESP32 vanne | accusé d'exécution : `id`, `device`, `state`, `opened`, `until`, `water_l_total` |
| `{fog}/weather/forecast` | locale, retained | simulateur (puis passerelle météo) | `ts`, `rain_next_24h_mm`, `source` |
| `{fog}/sim/fault` | locale | humain / bot Telegram | injection de pannes (voir `simulator/simulator/faults.py`) |
| `{fog}/alert` | locale | irrigation-engine | `ts`, `type` (leak, pump, no_rise, sensor, offline, valve), `zone`, `device`, `msg`, `resolved` |
| `domaine/fog/{fog}/status` | partagée, retained | fog-exporter (toutes les 10 s ; dernière volonté `alive:false`) | `fog`, `alive`, `cpu`, `ram`, `version`, `unix` (heure réelle d'envoi) |
| `domaine/air/{fog}` | partagée | chaque fog | `air_temp`, `air_humidity` |
| `domaine/quota/request/{fog}` | partagée | quota-negotiator | `day`, `fog`, `need_l`, `priority`, `ts` |
| `domaine/quota/allocation/{fog}` | partagée, retained | quota-negotiator | `day`, `quota_l`, `allocation` `{fog: litres}`, `mode` (négocié / isolé), `computed_by` |
| `domaine/quota/release/{fog}` | partagée | quota-negotiator | `day`, `fog`, `liters` cédés au voisin |
| `{fog}/quota/budget` | locale, retained | quota-negotiator | `day`, `allocated_l`, `mode`, `given_l`, `received_l` |
| `{fog}/quota/usage` | locale (retained quand fog-twin le restaure) | irrigation-engine, fog-twin | `day`, `used_l` |
| `{fog}/pump/request` | locale | irrigation-engine (et le moteur de reprise de fog-twin) | `id`, `fog`, `zone`, `seconds`, `ts` — l'arrosage attend la pompe commune |
| `domaine/fl/global` | partagée, retained | fed-learning (agrégateur) | `run`, `round`, `weights` (listes), `final`, `fogs` |
| `domaine/fl/update/{fog}` | partagée | fed-learning | `run`, `round`, `fog`, `n` (échantillons), `weights`, `mae` |
| `domaine/pump/claim/{fog}` | partagée, retained | pump-scheduler | `fog`, `want`, `since` (unix d'entrée dans la file), `using`, `zone`, `remaining_s`, `unix` — rafraîchi toutes les 2 s |
| `domaine/pump/token` | partagée, retained | pump-scheduler | `holder` (null = libre), `zone`, `seconds`, `until_unix`, `ts` — informatif (Grafana, humains) |
| `domaine/twin/{fog}` | partagée, retained | fog-twin | `fog`, `state` (normal, voisin-inconnu, partition, isole, reprise), `reason`, `takeover` (fog repris ou null), `unix` |

Seul `domaine/#` traverse le bridge Mosquitto. Le bridge est déclaré **uniquement sur fog-a**.

## Pompe commune (service pump-scheduler)

Les deux parcelles sont alimentées par **un seul forage** : une seule zone (des deux fogs) arrosée à la fois.
irrigation-engine ne publie plus `cmd` directement (si `pump.shared: true`) : il demande la pompe sur
`{fog}/pump/request`, et pump-scheduler envoie `cmd` quand la pompe est à lui.

Exclusion mutuelle sans coordinateur : chaque fog publie **sa propre** demande (`domaine/pump/claim/{fog}`),
personne n'écrit chez l'autre. Règle appliquée à l'identique des deux côtés :
1. la pompe est occupée tant que l'autre fog a `using: true` (bail = durée de l'arrosage + 1 s) ;
2. si les deux attendent, passe le premier entré dans la file (`since`), égalité → ordre des noms ;
3. après chaque arrosage, un fog qui a encore des zones à arroser se remet **en fin de file** → alternance ;
4. une demande non rafraîchie depuis 20 s = fog mort : on l'ignore (le bail expire tout seul).
Avant d'utiliser la pompe, on attend 1,5 s que la demande de l'autre fog ait traversé le pont.

## Fog jumeau (service fog-twin, port 8080)

- **Sauvegarde** : toutes les 10 s, chaque fog envoie l'état de son irrigation-engine (`/zones` : humidité,
  décisions, budget, litres utilisés) au voisin (`POST /backup`).
- **Panne** : si le statut du voisin (`domaine/fog/{voisin}/status`) se tait 30 s, on consulte le **témoin
  cloud** (`agrifog-enis-2026/witness/{fog}` sur test.mosquitto.org, un message toutes les 5 s) :
  - le voisin parle encore au témoin → **partition** (le lien entre fogs est coupé, le voisin vit) : pas de reprise ;
  - on ne joint pas le témoin nous-mêmes → **isolé** (c'est peut-être nous qui sommes coupés) : pas de reprise ;
  - sinon → **reprise** : on lance un irrigation-engine pour la parcelle voisine, restauré depuis la sauvegarde.
    Les ESP32 du voisin basculent sur notre broker (`fallback_brokers`), le moteur de reprise les pilote.
- **Retour** : le fog revenu demande sa parcelle (`POST /takeover` action `release`), récupère les litres
  utilisés pendant la reprise et les publie (retained) sur `{fog}/quota/usage` : le quota du jour est respecté.

| Méthode | Chemin | Rôle |
|---|---|---|
| GET | `/health` | état du jumeau (normal, partition, reprise…) |
| POST | `/backup` | reçoit un lot de données du voisin : `fog`, `unix`, `data` |
| GET | `/backup/{fog}` | restitue les données sauvegardées d'un fog |
| POST | `/takeover` | `{"from": fog, "action": "request"}` (maintenance : reprends ma parcelle) ou `"release"` (je suis revenu : rends-la) |

## Valeurs communes

- Quota journalier du domaine : `domain.daily_quota_l` dans la config (6000 L, identique sur les deux fogs).
- Repli hors ligne : 50 % du quota pour chaque fog.
- Heures fraîches : 05:00–08:00 et 18:00–21:00 (heure locale).
