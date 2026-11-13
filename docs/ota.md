# Mises à jour OTA du firmware (semaine 4 — Ines)

```
git tag fw-v0.3.0 ─► CI firmware-release : compile, SHA-256, Release GitHub (firmware.bin + .sha256)
                                   │
ota-manager (fog)  ◄─ cli release fw-v0.3.0 : télécharge UNE fois, vérifie le SHA-256, garde une copie
       │
       ├─ cli deploy 0.3.0 esp-b-01 ─► fog-b/device/esp-b-01/ota  {id, version, url, sha256, size}
       │        ESP32 virtuel : télécharge depuis le fog, vérifie taille + SHA-256, installe, redémarre
       │        ESP32 Wokwi   : reçoit l'URL publique, contrôle et accepte l'ordre (pas de flash réel)
       │
       └─ ◄── fog-b/device/<esp>/ota/status   downloading → installed | failed | skipped | accepted
           ◄── heartbeat fw_version            la version réellement installée
```

* Le **fog sert de miroir** : les ESP32 virtuels téléchargent sur le fog
  (`http://<fog>:8003/firmware/<version>.bin`), pas sur Internet.
* Un firmware dont l'**empreinte SHA-256 ne correspond pas** est refusé deux fois : par
  ota-manager à l'ajout au catalogue, puis par l'ESP32 avant l'installation.
* Grafana, tableau **Flotte ESP32** : « Versions du firmware déployées » et « Mises à jour OTA ».
* Semaine 5 : **canary** (un ESP32, puis la flotte si ses heartbeats restent bons) et **rollback**.
