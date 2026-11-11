# Intégration continue (GitHub Actions)

Responsable : Ines

| Tâche | Quand | Ce qu'elle fait |
|---|---|---|
| `python-tests` | chaque push et PR | tests de la lib commune, du simulateur et de chaque service |
| `firmware-build` | chaque push et PR | compile le firmware ESP32 (PlatformIO), calcule son SHA-256, publie `firmware.bin` en artefact |
| `docker-images` | après les tests | construit l'image de chaque service, la scanne avec **Trivy** (rapport HIGH/CRITICAL dans le résumé de l'exécution), **bloque** si une vulnérabilité CRITICAL a un correctif ; sur `main`, publie l'image multi-architecture (amd64 + arm64 pour Raspberry Pi) sur `ghcr.io` |

Voir les résultats : onglet **Actions** du dépôt → une exécution → **Summary** (tableaux Trivy) et
**Artifacts** (firmware, rapports Trivy complets).

Construire une image en local (depuis la racine du dépôt) :
```bash
docker build -f services/irrigation-engine/Dockerfile -t agrifog/irrigation-engine .
```
