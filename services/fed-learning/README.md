# fed-learning

Responsable : Chaima · Semaine 6 — apprentissage fédéré entre les fogs

Un modèle prédit **l'humidité du sol dans 6 h** (sans arrosage) à partir de la situation actuelle
(humidité, air, heure, ET0 de Hargreaves, paramètres de la culture et du sol, pluie prévue).
Les deux fogs l'entraînent **ensemble sans échanger leurs données** : seuls les poids circulent.

- **Modèle** : réseau de neurones 12 → 16 → 1 en NumPy (900 octets de poids), recette partagée
  dans `libs/agrifog_common/agrifog_common/ml.py`.
- **FedAvg hiérarchique** (l'algorithme de la stratégie par défaut de Flower) :
  chaque zone = un client ; le fog moyenne ses zones, l'agrégateur moyenne les fogs (pondéré par
  le nombre d'échantillons).
- **Transport** : le bridge MQTT (`domaine/fl/global`, `domaine/fl/update/<fog>`), qui marche déjà
  entre les deux PC. Flower 1.43 ayant retiré l'API client simple, son déploiement réel demanderait
  SuperLink/SuperNodes sur chaque PC Windows.
- **Offline-first** : si le voisin se tait 3 tours, l'agrégateur continue seul et le reprend à son retour.
- Le modèle final est enregistré dans `data/models/moisture_model.json` ; l'irrigation-engine le
  recharge automatiquement et affiche sa prédiction dans chaque décision (à titre d'information :
  les décisions restent fondées sur le bilan hydrique).

## Utilisation

```bash
# 1. chaque fog prépare SON historique (il ne quitte pas le PC)
cd simulator && python -m simulator.history --fog fog-a --days 14 --start 2025-05-20

# 2. apprentissage fédéré réel : sur chaque PC (fog-a = agrégateur)
cd services/fed-learning && FOG_ID=fog-b python -m app.federated     # Ines d'abord
                            FOG_ID=fog-a python -m app.federated     # puis Chaima

# 3. expérience pour le rapport (sur un seul PC, avec les deux historiques)
python -m app.experiment
```

## Résultats (historique de 14 jours, 30 % pour l'entraînement)

| Erreur moyenne à +6 h (% vol) | fog-a (tomate) | fog-b (olivier) |
|---|---|---|
| modèle local (zone seule) | 0.27 | 0.26 |
| **modèle fédéré** | **0.26** | **0.22** |
| centralisé (non confidentiel) | 0.24 | 0.23 |

Le fédéré bat le modèle local (−4 % tomate, −15 % olivier) et approche le centralisé sans partager
les données. Il lui faut beaucoup de tours courts (100–200 tours × 2 époques). Limite observée :
sur les moments d'arrosage, la tomate fédérée fait un peu moins bien que la locale (0.31 contre 0.27).
Un modèle = 900 octets ; 100 tours ≈ 260 Ko échangés, contre ~53 Mo pour centraliser une saison.
