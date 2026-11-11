# quota-negotiator

Responsable : Chaima · Semaine 5 — **cœur de la coopération entre les deux fogs**

Les deux parcelles partagent un même forage : `domain.daily_quota_l` litres par jour (même valeur
dans `config/fog-a.yaml` et `config/fog-b.yaml`). Chaque fog négocie sa part chaque nuit.

| Heure (simulée) | Action | Topic |
|---|---|---|
| 03:00 | annoncer son besoin du jour et sa priorité | `domaine/quota/request/<fog>` |
| 04:00 | calculer la répartition (dès que la demande du voisin est là) | `domaine/quota/allocation/<fog>` + `<fog>/quota/budget` |
| 10:00 | céder ce dont on n'aura pas besoin ce soir | `domaine/quota/release/<fog>` |

- **Besoin** d'une zone = remonter à CC − 1 % + ETc du jour − pluie prévue.
- **Priorité** = sensibilité de la culture (tomate 1.0, olivier 0.5) × (1 + 2 × stress).
- **Répartition** : si tout le monde rentre dans le quota, chacun a son besoin + la marge partagée ;
  sinon partage proportionnel à besoin × priorité, sans jamais donner plus que le besoin (« water-filling »).
- **Accord sans chef** : les deux fogs exécutent le même algorithme déterministe sur les mêmes
  demandes et trouvent la même répartition (publiée par chacun : on peut vérifier l'accord).
- **Mode isolé** : pas de demande du voisin 1 h après la sienne (Tailscale coupé) → 50 % du quota.
- Le moteur d'irrigation reçoit `<fog>/quota/budget`, limite ses arrosages et publie `<fog>/quota/usage`.

Les deux simulateurs doivent partager la même horloge : lancer avec `--sync`.

```bash
FOG_ID=fog-a python -m app.negotiator               # négociateur seul
FOG_ID=fog-a uvicorn app.main:app --port 8002       # + API : GET /quota
```
