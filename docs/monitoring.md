# Supervision d'un fog (semaine 3 — Ines)

```
ESP32 / services ──MQTT──► Mosquitto ──► fog-exporter :9108 ──► Prometheus :9090 ──► Grafana :3000
                                                                   │
                                                                   └── règles d'alerte ──► Alertmanager :9093
```

Lancement (sur chaque PC, depuis `deploy/compose`) :

```powershell
$env:FOG_ID="fog-b"          # fog-a chez Chaima
docker compose --profile monitoring up -d --build
```

| Outil | Adresse | Rôle |
|---|---|---|
| Grafana | http://localhost:3000 (lecture sans compte ; admin / agrifog-dev-only) | 3 tableaux de bord |
| Prometheus | http://localhost:9090 | métriques, onglet *Alerts* |
| Alertmanager | http://localhost:9093 | alertes regroupées |
| fog-exporter | http://localhost:9108/metrics | métriques brutes |

## Tableaux de bord (dossier AgriFog)

1. **Irrigation** : humidité par zone et seuil de stress, **débit par zone (preuve de l'arrosage)**,
   eau distribuée, prédiction de l'IA fédérée, décisions par type, alertes en cours.
2. **Flotte ESP32** : nœuds vivants / muets, dernier message, batterie, signal WiFi, versions du firmware.
3. **Collaboration fog-a ↔ fog-b** : état des ponts MQTT, signe de vie de chaque fog, mode du quota,
   **accord des deux fogs sur la répartition**, besoins, attribué / utilisé, eau cédée, apprentissage fédéré.

## Alertes (deploy/compose/prometheus/alerts.yml)

| Alerte | Condition |
|---|---|
| ExporterInjoignable | plus de métriques du fog depuis 30 s |
| AucuneTelemetrie | aucune mesure depuis 1 min |
| ESP32Muet | un ESP32 silencieux depuis plus de 60 s |
| OtaRollback | une campagne OTA a été refusée par le canary (version bloquée) |
| BatterieFaible | batterie < 3,3 V |
| PontMQTTCoupe | un pont MQTT déconnecté depuis 1 min |
| FogVoisinSilencieux | pas de signe de vie d'un fog depuis 1 min |
| QuotaModeIsole | répartition du quota en mode isolé (50 %) |
| QuotaDesaccord | les deux fogs ne publient pas la même répartition |
| QuotaDepasse | eau utilisée > 105 % de la part attribuée |
| AlerteIrrigation | une alerte d'irrigation-engine est en cours (fuite, pompe…) |

Les alertes **agronomiques** partent déjà sur Telegram par le bot ; Alertmanager sert aux alertes
de **plateforme**. L'envoi Telegram d'Alertmanager est prêt dans `alertmanager.yml` (commenté, jeton jamais committé).

Les versions des images sont figées (Prometheus 2.55.1, Alertmanager 0.27.0, Grafana 11.3.0).
