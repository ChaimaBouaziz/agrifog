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

## GitOps avec Flux (semaine 5)

```
ansible-playbook -i inventory.ini site.yml -l fog-b -e gitops=true
   └─ flux     installe Flux dans le cluster et le branche sur GitHub (dépôt public : pas de jeton)

GitHub (branche main) ──► source-controller : relit le dépôt chaque minute
                           └► kustomize-controller : applique deploy/flux/fog-b/apps
                                └► helm-controller : HelmRelease agrifog = chart deploy/helm/agrifog
                                                     + values.yaml + values-fog-b.yaml
```

* **Git est la référence** : on ne lance plus `helm` à la main. Modifier le fog = modifier
  `deploy/helm/agrifog/values-fog-b.yaml` (ou le chart) dans une PR ; une fois mergée sur `main`,
  Flux l'applique en ~1 min, sur le fog concerné uniquement.
* Exemple : `simulatorDevices: 3` dans `values-fog-b.yaml` -> après le merge, le simulateur du cluster
  redémarre avec 3 ESP32 (`kubectl logs deploy/simulator`).
* Une mise à jour qui échoue est **annulée par Flux** (`remediateLastFailure` : helm rollback).
* Suivre : `flux get all -A` ; forcer : `flux reconcile kustomization agrifog-apps --with-source`.
* Les images restent celles du registry local (construites par Ansible, tag `dev`).
