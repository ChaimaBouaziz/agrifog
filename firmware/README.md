# Firmware ESP32 (v2)

Responsable : Ines

Même comportement que les ESP32 virtuels du simulateur, même JSON (`docs/contracts.md`) :
réveil → WiFi → MQTT → télémétrie + heartbeat → 3 s à l'écoute d'un ordre `water` (session MQTT
persistante : un ordre envoyé pendant le sommeil arrive au réveil) → relais (vanne) → deep sleep.

| Élément Wokwi | Rôle |
|---|---|
| DHT22 (GPIO 15) | température et humidité de l'air |
| potentiomètre (GPIO 34) | humidité du sol : 5 % (à gauche) → 35 % (à droite) |
| relais (GPIO 26) | la vanne de la zone `zw` |

Wokwi tourne dans le cloud : il ne voit pas le broker du fog. L'ESP32 publie sur
`test.mosquitto.org` avec le préfixe `TOPIC_PREFIX`, et le Mosquitto de fog-b fait le lien
(`deploy/compose/mosquitto/bridges/fog-b/relay-wokwi.conf`). **Le préfixe doit être le même aux
deux endroits** ; le changer pour un préfixe unique au projet (le broker est public).

Démo (`TIME_SCALE=60`) : un ordre de 3600 s ouvre le relais 60 s réelles ; le débitmètre simulé
compte le volume commandé, ce qui permet au moteur de vérifier l'arrosage.

## Démo
1. Sur le PC d'Ines : `$env:FOG_ID="fog-b"` puis `docker compose -f deploy/compose/docker-compose.yml up -d --force-recreate mosquitto`
2. Moteur : `python -m app.engine` (fog-b), depuis `services\irrigation-engine`
3. VS Code ouvert sur `firmware/` : Build ✓ puis F1 → *Wokwi: Start Simulator*
4. Tourner le potentiomètre **à gauche** (sol sec) : au réveil suivant le moteur décide « URGENCE »,
   au réveil d'après l'ESP32 reçoit l'ordre et **le relais s'allume**.
