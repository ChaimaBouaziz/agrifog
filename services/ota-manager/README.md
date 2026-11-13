# ota-manager

Responsable : Ines · Semaines 4–5

Mises à jour du firmware des ESP32 du fog : catalogue des versions (SHA-256 vérifié, copie locale
servie aux ESP32), envoi des ordres OTA, suivi des statuts et des versions installées.
Voir `docs/ota.md`.

```powershell
$env:FOG_ID="fog-b"; uvicorn app.main:app --port 8003     # le service
python -m app.cli release fw-v0.3.0                        # ajouter la release GitHub au catalogue
python -m app.cli deploy 0.3.0 esp-b-01                    # mettre à jour un ESP32 (ou: all)
python -m app.cli devices                                  # versions installées, statuts OTA
```


Semaine 5 — canary + rollback :

```powershell
python -m app.cli campaign 0.3.1                    # canary sur 1 ESP32 (60 s), puis la flotte, ou rollback
python -m app.cli campaign-status                   # canary -> rollout -> done | rolled_back
python -m app.cli defect 0.3.0 0.3.1 crash          # DÉMO : version défectueuse (crash | sensor)
python -m app.cli releases                          # une version refusée apparaît BLOQUÉE
```
