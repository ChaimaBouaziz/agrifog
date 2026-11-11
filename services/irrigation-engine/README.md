# irrigation-engine

Responsable : Chaima · Semaines 3–4 (puis 6 : prédiction fédérée)

Le cerveau du fog : écoute la télémétrie de ses zones et décide quand et combien arroser.

| Fichier | Rôle |
|---|---|
| `app/hargreaves.py` | ET0 de Hargreaves (FAO-56) à partir des seules températures des DHT22 |
| `app/decision.py` | règles de décision (fonctions pures) + vérification du débitmètre |
| `app/zone_state.py` | état de chaque zone reconstruit depuis la télémétrie (médiane des capteurs, min/max du jour) |
| `app/anomalies.py` | fuite, pompe en panne, humidité qui ne monte pas, capteur qui ne réagit pas, nœud muet |
| `app/engine.py` | boucle MQTT : télémétrie, accusés de vanne, heartbeats, prévision → `cmd`, `decision`, `alert` |
| `app/main.py` | API : `GET /health`, `GET /zones` (humidité + dernière décision et sa raison) |

Règles : urgence si humidité < seuil − 2 % ; sinon seulement aux heures fraîches (5–8 h, 18–21 h),
et seulement si l'humidité prévue à la prochaine fenêtre (ETc = Kc × ET0) passerait sous le seuil ;
dose = remonter à CC − 1 % (pas de drainage), limitée au temps restant dans la fenêtre ;
vérification 30 min après la fin : moins de 70 % des litres attendus → alerte.
Pluie : on reporte si l'humidité attendue *en comptant la pluie annoncée* reste au-dessus du seuil
jusqu'à la prochaine fenêtre ; une prévision de plus de 36 h est ignorée (offline-first).

| Situation observée | Alerte |
|---|---|
| vanne fermée mais débit > 0 | `leak` (résolue quand le débit retombe) |
| vanne ouverte mais débit nul, ou litres manquants | `pump` |
| humidité de la zone qui ne monte pas après l'arrosage | `no_rise` (tuyau bouché ?) |
| un capteur ne monte pas alors que les autres oui | `sensor` |
| plus de nouvelles d'un ESP32 depuis 3 h | `offline` (résolue à son retour) |
| la vanne ne confirme pas l'ordre en 3 h | `valve` |

```bash
FOG_ID=fog-a python -m app.engine                 # moteur seul
FOG_ID=fog-a uvicorn app.main:app --port 8001     # moteur + API
```
