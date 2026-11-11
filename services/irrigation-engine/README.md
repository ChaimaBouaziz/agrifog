# irrigation-engine

Responsable : Chaima · Semaine 3 (puis 4 : pluie et fuites ; 6 : prédiction fédérée)

Le cerveau du fog : écoute la télémétrie de ses zones et décide quand et combien arroser.

| Fichier | Rôle |
|---|---|
| `app/hargreaves.py` | ET0 de Hargreaves (FAO-56) à partir des seules températures des DHT22 |
| `app/decision.py` | règles de décision (fonctions pures) + vérification du débitmètre |
| `app/zone_state.py` | état de chaque zone reconstruit depuis la télémétrie (médiane des capteurs, min/max du jour) |
| `app/engine.py` | boucle MQTT : télémétrie → décision → `cmd`, `decision`, `alert` |
| `app/main.py` | API : `GET /health`, `GET /zones` (humidité + dernière décision et sa raison) |

Règles : urgence si humidité < seuil − 2 % ; sinon seulement aux heures fraîches (5–8 h, 18–21 h),
et seulement si l'humidité prévue à la prochaine fenêtre (ETc = Kc × ET0) passerait sous le seuil ;
dose = remonter à CC − 1 % (pas de drainage), limitée au temps restant dans la fenêtre ;
vérification 30 min après la fin : moins de 70 % des litres attendus → alerte.

```bash
FOG_ID=fog-a python -m app.engine                 # moteur seul
FOG_ID=fog-a uvicorn app.main:app --port 8001     # moteur + API
```
