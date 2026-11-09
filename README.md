# AgriFog

Irrigation intelligente sur deux nœuds fog collaboratifs (Fog A = tomate, Fog B = olivier),
avec une chaîne DevOps complète. 100 % simulé, aucun matériel.

Équipe : Chaima (Fog A — simulation, décision, IA) · Ines (Fog B — firmware, plateforme, DevOps).

## Arborescence

```
agrifog/
  docs/contracts.md        # contrat MQTT + API entre les fogs (à lire en premier)
  config/                  # configuration propre à chaque fog (culture, zones, voisins)
  libs/agrifog_common/     # code partagé : topics MQTT, chargement de config
  firmware/                # ESP32 PlatformIO + projet Wokwi               (Ines)
  simulator/               # ESP32 virtuels, modèle de sol, pannes           (Chaima)
  services/
    irrigation-engine/     # décision d'arrosage, fuites                     (Chaima)
    quota-negotiator/      # partage du quota d'eau entre fogs               (Chaima)
    fed-learning/          # apprentissage fédéré Flower                     (Chaima)
    pump-scheduler/        # jeton de pompe partagée                          (Ines)
    fog-twin/              # API REST entre fogs : backup, reprise           (Ines)
    ota-manager/           # OTA canary + rollback                           (Ines)
  deploy/
    compose/               # environnement de dev (Mosquitto, InfluxDB)
    ansible/               # installation d'un fog (K3s, registry…)
    helm/agrifog/          # chart des services
    flux/fog-a|fog-b/      # GitOps par fog
  data/                    # météo Sfax (non versionnée)
  experiments/             # scripts et résultats des expériences
```

## Démarrage rapide (dev)

```bash
# 1. Broker + base de données
docker compose -f deploy/compose/docker-compose.yml up -d

# 2. Lib commune (une fois, dans un venv)
python -m venv .venv && source .venv/bin/activate
pip install -e libs/agrifog_common

# 3. Lancer un service (exemple)
FOG_ID=fog-a uvicorn app.main:app --app-dir services/irrigation-engine --port 8001

# 4. Écouter le bus
mosquitto_sub -h localhost -t 'fog-a/#' -t 'domaine/#' -v
```

Chaque fog se lance avec `FOG_ID=fog-a` ou `FOG_ID=fog-b` : le code est identique, seule `config/<fog>.yaml` change.

## Règles Git

- `main` protégée : on passe toujours par une PR relue par l'autre.
- Branches `feature/<sujet>`, commits courts au présent (`add soil model`).
- Un tag par fin de semaine : `v0.1`, `v0.2`…
