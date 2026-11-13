# Déployer un fog avec Ansible + K3s + Helm (semaine 3 — Ines)

Une seule commande installe un nœud fog complet sur un PC :

```
ansible-playbook -i inventory.ini site.yml -l fog-b
   │
   ├─ tools     k3d, kubectl, helm (versions figées)          -> ~/.local/bin
   ├─ registry  registry Docker local  k3d-agrifog-registry    -> localhost:5001
   ├─ cluster   K3s (dans Docker, via k3d) : agrifog-fog-b     -> kubectl / helm
   ├─ images    docker build (mêmes Dockerfile que la CI) + push dans le registry
   └─ deploy    helm upgrade --install agrifog  (values-fog-b.yaml)
```

| Dans le cluster | Rôle | Depuis le PC |
|---|---|---|
| `mosquitto` | broker MQTT du fog | `localhost:21883` |
| `irrigation-engine` | décisions d'arrosage | `http://localhost:28000/zones` |
| `quota-negotiator` | partage du quota | — |
| `fog-exporter` | métriques Prometheus | — |
| `simulator` | les 10 ESP32 virtuels | — |

* **Idempotent** : rejouer le playbook ne recrée ni le registry ni le cluster ; il reconstruit les
  images et met à jour le déploiement (`helm upgrade`).
* **Ajouter une parcelle** = une ligne dans `deploy/ansible/inventory.ini` + `config/<fog>.yaml`
  + `deploy/helm/agrifog/values-<fog>.yaml`.
* **Coexistence** : le cluster utilise d'autres ports que Docker Compose (21883, 28000) ; les deux
  environnements peuvent tourner en même temps. Docker Compose reste l'environnement de la démo
  (pont entre les fogs, Wokwi, Grafana) ; le cluster montre l'installation automatisée.
* **Vérifié en CI** (job `deploy-check`) : `helm lint`, rendu du chart pour fog-a et fog-b,
  conformité au schéma Kubernetes (kubeconform), syntaxe des playbooks.

Supprimer le cluster : `ansible-playbook -i inventory.ini teardown.yml -l fog-b`.
