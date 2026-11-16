# fog-twin

Responsable : Ines · Semaine 6

**Fog jumeau** : chaque fog sauvegarde l'état de l'autre, et reprend sa parcelle s'il tombe en panne.

| Quoi | Comment |
|---|---|
| Sauvegarde | toutes les 10 s : `GET irrigation-engine/zones` → `POST http://<voisin>:8080/backup` |
| Détection | statut du voisin `domaine/fog/<voisin>/status` (fog-exporter, via le pont) muet 30 s |
| Témoin cloud | `agrifog-enis-2026/witness/<fog>` sur test.mosquitto.org, toutes les 5 s, par les deux fogs |
| Reprise | un `irrigation-engine` pour la parcelle voisine (sa config, son état sauvegardé), sur NOTRE broker |
| Retour | le fog revenu appelle `POST /takeover {"from": fog, "action": "release"}` et récupère les litres utilisés |

Le témoin évite le **split-brain** (deux fogs qui pilotent la même parcelle) :

| Le voisin parle-t-il… | chez nous ? | au témoin ? | nous joignons le témoin ? | état | reprise ? |
|---|---|---|---|---|---|
| | oui | – | – | `normal` | non |
| | non | oui | oui | `partition` (lien coupé) | **non** |
| | non | – | non | `isole` (c'est peut-être nous) | **non** |
| | non | non | oui | `reprise` | **oui** |

Pendant la panne, les ESP32 du voisin basculent sur notre broker (`mqtt.fallback_brokers`) et publient toujours
sur `<voisin>/zone/...` : le moteur de reprise les pilote, en passant par la pompe commune (pump-scheduler).

## Lancer (PowerShell, depuis `services\fog-twin`)

```powershell
$env:FOG_ID="fog-b"; uvicorn app.main:app --host 0.0.0.0 --port 8080
```
- `--host 0.0.0.0` : le voisin doit joindre ce port via Tailscale (autoriser le port 8080 dans le pare-feu).
- `TWIN_PEER_URL` (défaut `http://<voisin>:8080`), `ENGINE_URL` (défaut `http://127.0.0.1:8001`),
  `TWIN_WITNESS` (défaut : `twin.witness_broker` de la config, vide = pas de témoin).

```powershell
python -m app.cli status        # état de notre jumeau
python -m app.cli maintenance   # maintenance prévue : le voisin reprend notre parcelle tout de suite
python -m app.cli back          # fin de maintenance : on la récupère
```
