# fog-exporter

Responsable : Ines · Semaine 3 — supervision

Écoute le broker du fog (sans rien modifier) et expose l'état du fog au format **Prometheus**
sur `http://<fog>:9108/metrics`. Publie aussi le **signe de vie du fog** sur
`domaine/fog/<fog>/status` toutes les 10 s (il traverse le pont : chaque fog voit si son voisin est vivant).

| Source MQTT | Métriques (préfixe `agrifog_`) |
|---|---|
| `<fog>/zone/+/telemetry` | `soil_moisture_pct`, `air_temp_celsius`, `air_humidity_pct`, `flow_lpm`, `water_total_liters` |
| `<fog>/zone/+/decision`, `cmd`, `valve` | `decisions_total`, `ai_moisture_6h_pct`, `et0_mm_per_day`, `water_commands_total`, `valve_acks_total` |
| `<fog>/device/+/heartbeat` | `device_battery_volts`, `device_rssi_dbm`, `device_firmware_info`, `device_last_seen_timestamp_seconds` |
| `<fog>/alert` | `alert_active`, `alerts_total` |
| `<fog>/quota/budget`, `usage` | `quota_allocated_liters`, `quota_used_liters`, `quota_given_liters`, `quota_received_liters`, `quota_negotiated` |
| `domaine/quota/allocation/+`, `request/+` | `quota_allocation_liters{computed_by, fog}` (accord sans chef), `quota_request_liters` |
| `domaine/fog/+/status`, `domaine/fl/update/+` | `fog_alive`, `fog_last_seen_timestamp_seconds`, `fl_round`, `fl_mae_pct` |
| `$SYS/broker/connection/+/state` | `bridge_up{connection}` |

Le temps `ts` des messages est simulé ; les « dernier vu » utilisent l'heure réelle de réception.

```powershell
$env:FOG_ID="fog-b"; python -m app.main     # depuis services\fog-exporter (ou via Docker Compose, profil monitoring)
python -m pytest -q
```
