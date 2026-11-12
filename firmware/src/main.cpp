// AgriFog ESP32 firmware v2 — same behaviour as the virtual ESP32 of the simulator.
//
// Cycle (loop):  wake up -> WiFi -> MQTT -> publish telemetry + heartbeat
//         -> stay awake AWAKE_MS to receive a water command (persistent MQTT session:
//            commands sent while sleeping are delivered at the next wake-up)
//         -> if watering: stay awake until the valve closes -> deep sleep PERIOD_S
//
// Same JSON as docs/contracts.md. In Wokwi the board cannot reach the fog's local broker:
// it uses a public relay broker + TOPIC_PREFIX, and the fog bridges that prefix
// (deploy/compose/mosquitto/bridges/fog-b/relay-wokwi.conf).
#include <Arduino.h>
#include <ArduinoJson.h>
#include <DHT.h>
#include <PubSubClient.h>
#include <WiFi.h>
#include <esp_sleep.h>
#include <esp_sntp.h>
#include <time.h>

// ---------------------------------------------------------------- pins (diagram.json)
#define DHT_PIN 15
#define SOIL_PIN 34    // potentiomètre = capteur d'humidité du sol
#define RELAY_PIN 26   // relais = vanne de la zone

// ------------------------------------------- defaults (overridden in platformio.ini)
#ifndef FW_VERSION
#define FW_VERSION "0.2.0"
#endif
#ifndef FOG_ID
#define FOG_ID "fog-b"
#endif
#ifndef DEVICE_ID
#define DEVICE_ID "esp-b-wokwi"
#endif
#ifndef ZONE_ID
#define ZONE_ID "zw"
#endif
#ifndef WIFI_SSID
#define WIFI_SSID "Wokwi-GUEST"
#endif
#ifndef WIFI_PASS
#define WIFI_PASS ""
#endif
#ifndef MQTT_HOST
#define MQTT_HOST "test.mosquitto.org"
#endif
#ifndef MQTT_PORT
#define MQTT_PORT 1883
#endif
#ifndef MQTT_HOST_IP
#define MQTT_HOST_IP "54.36.178.49"   // IP de secours de test.mosquitto.org (si le DNS de Wokwi échoue)
#endif
#ifndef TOPIC_PREFIX
#define TOPIC_PREFIX ""
#endif
#ifndef PERIOD_S
#define PERIOD_S 10          // deep sleep entre deux réveils (s)
#endif
#ifndef FW_DEEP_SLEEP
#define FW_DEEP_SLEEP 1         // 1 = vrai deep sleep (carte réelle) ; 0 = veille simulée (Wokwi)
#endif
#ifndef TIME_SCALE
#define TIME_SCALE 60        // démo : 1 s réelle de vanne = 60 s commandées
#endif
#define AWAKE_MS 3000
#define FLOW_LPM 20.0
#define SOIL_MIN_PCT 5.0     // potentiomètre au minimum
#define SOIL_MAX_PCT 35.0    // potentiomètre au maximum

// --------------------------------------------- kept in RTC memory across deep sleep
RTC_DATA_ATTR uint32_t bootCount = 0;
RTC_DATA_ATTR float waterTotalL = 0;
RTC_DATA_ATTR float batteryV = 4.10;
RTC_DATA_ATTR char lastCmdId[64] = "";
RTC_DATA_ATTR bool timeSynced = false;

DHT dht(DHT_PIN, DHT22);
WiFiClient net;
PubSubClient mqtt(net);

String topicTelemetry, topicHeartbeat, topicCmd, topicValve;
uint32_t valveUntilMs = 0;         // millis() de fermeture de la vanne (0 = fermée)
uint32_t valveOpenedMs = 0;
float valveLitersToDeliver = 0;

String isoNow() {
  time_t now = time(nullptr);
  struct tm t;
  gmtime_r(&now, &t);
  char buf[25];
  strftime(buf, sizeof(buf), "%Y-%m-%dT%H:%M:%SZ", &t);
  return String(buf);
}

String isoIn(uint32_t seconds) {
  time_t then = time(nullptr) + seconds;
  struct tm t;
  gmtime_r(&then, &t);
  char buf[25];
  strftime(buf, sizeof(buf), "%Y-%m-%dT%H:%M:%SZ", &t);
  return String(buf);
}

bool valveOpen() { return valveUntilMs != 0 && (int32_t)(valveUntilMs - millis()) > 0; }

float soilMoisturePct() {
  int raw = analogRead(SOIL_PIN);  // 0..4095
  return SOIL_MIN_PCT + (SOIL_MAX_PCT - SOIL_MIN_PCT) * raw / 4095.0;
}

void publishJson(const String &topic, JsonDocument &doc) {
  char buf[384];
  size_t n = serializeJson(doc, buf, sizeof(buf));
  mqtt.publish(topic.c_str(), (const uint8_t *)buf, n, false);
}

void publishTelemetry() {
  JsonDocument doc;
  doc["ts"] = isoNow();
  doc["device"] = DEVICE_ID;
  doc["soil_moisture"] = roundf(soilMoisturePct() * 10) / 10;
  float t = dht.readTemperature(), h = dht.readHumidity();
  doc["air_temp"] = isnan(t) ? 0.0 : t;
  doc["air_humidity"] = isnan(h) ? 0.0 : h;
  doc["flow_lpm"] = valveOpen() ? FLOW_LPM : 0.0;
  doc["water_l_total"] = roundf(waterTotalL * 10) / 10;
  publishJson(topicTelemetry, doc);
  Serial.printf("[telemetry] sol=%.1f%% air=%.1f°C/%.0f%% vanne=%s total=%.0f L\r\n",
                soilMoisturePct(), t, h, valveOpen() ? "OUVERTE" : "fermée", waterTotalL);
}

void publishHeartbeat() {
  JsonDocument doc;
  doc["ts"] = isoNow();
  doc["fw_version"] = FW_VERSION;
  doc["battery_v"] = roundf(batteryV * 1000) / 1000;
  doc["rssi"] = WiFi.RSSI();
  doc["uptime_s"] = (uint32_t)(bootCount * PERIOD_S + millis() / 1000);
  doc["cycles"] = bootCount;
  doc["broker"] = MQTT_HOST;
  publishJson(topicHeartbeat, doc);
}

// ---------------------------------------------------------- water command -> relay
void onMessage(char *topic, byte *payload, unsigned int length) {
  JsonDocument doc;
  if (deserializeJson(doc, payload, length)) {
    Serial.println("[cmd] JSON illisible");
    return;
  }
  const char *action = doc["action"] | "";
  const char *id = doc["id"] | "";
  if (strcmp(action, "water") != 0) return;
  if (id[0] != '\0' && strcmp(id, lastCmdId) == 0) {
    Serial.printf("[cmd] %s déjà exécuté (doublon QoS 1)\r\n", id);
    return;
  }
  strncpy(lastCmdId, id, sizeof(lastCmdId) - 1);

  uint32_t seconds = doc["seconds"] | 0;
  uint32_t realMs = max<uint32_t>(1000, seconds * 1000UL / TIME_SCALE);
  valveOpenedMs = millis();
  valveUntilMs = valveOpenedMs + realMs;
  valveLitersToDeliver = FLOW_LPM * seconds / 60.0;   // ce que la commande demande
  digitalWrite(RELAY_PIN, HIGH);
  Serial.printf("[cmd] VANNE OUVERTE : %lu s commandées (%lu ms réelles en démo)\r\n",
                (unsigned long)seconds, (unsigned long)realMs);

  JsonDocument ack;                                   // accusé : le moteur suit l'ouverture réelle
  ack["id"] = id;
  ack["device"] = DEVICE_ID;
  ack["state"] = "open";
  ack["opened"] = isoNow();
  ack["until"] = isoIn((realMs + 999) / 1000);
  ack["water_l_total"] = roundf(waterTotalL * 10) / 10;
  publishJson(topicValve, ack);
}

void updateValve() {
  static uint32_t lastMs = 0;
  uint32_t now = millis();
  if (valveUntilMs != 0) {
    uint32_t total = valveUntilMs - valveOpenedMs;
    uint32_t from = max(lastMs, valveOpenedMs), to = min(now, valveUntilMs);
    if (to > from && total > 0)   // débitmètre simulé : le volume commandé coule pendant l'ouverture
      waterTotalL += valveLitersToDeliver * (to - from) / total;
    if (!valveOpen()) {
      digitalWrite(RELAY_PIN, LOW);
      valveUntilMs = 0;
      Serial.printf("[cmd] vanne fermée, compteur %.0f L\r\n", waterTotalL);
      publishTelemetry();
    }
  }
  lastMs = now;
}

// ------------------------------------------------------------------ connections
// WiFi.begin() une seule fois : le relancer pendant une connexion en cours l'annule.
// Ensuite on attend (jusqu'à 20 s par réveil) ; la simulation Wokwi peut être plus lente que le temps réel.
bool connectWiFi() {
  static bool begun = false;
  if (!begun) {
    WiFi.mode(WIFI_STA);
    WiFi.begin(WIFI_SSID, WIFI_PASS, 6);   // canal 6 = démarrage rapide dans Wokwi
    begun = true;
  }
  for (int i = 0; i < 200 && WiFi.status() != WL_CONNECTED; i++) delay(100);
  if (WiFi.status() != WL_CONNECTED) {
    Serial.printf("[wifi] pas encore connecté (état %d)\r\n", (int)WiFi.status());
    return false;
  }
  return true;
}

// Heure réelle par NTP. On donne des ADRESSES IP (pas de noms) : avec le core 3.3, quand le
// serveur ne répond pas tout de suite, SNTP relance une résolution DNS depuis son timer et
// lwIP s'arrête sur "assert failed" (udp_new, dns.c). Puis on arrête SNTP : plus de timer en fond.
void syncTime() {
  if (timeSynced && time(nullptr) > 1700000000) return;  // l'horloge RTC survit au deep sleep
  configTime(0, 0, "162.159.200.1", "216.239.35.0");     // time.cloudflare.com, time.google.com
  for (int i = 0; i < 50 && time(nullptr) < 1700000000; i++) delay(100);
  timeSynced = time(nullptr) > 1700000000;
  esp_sntp_stop();
  Serial.printf("[ntp] %s\r\n", timeSynced ? isoNow().c_str() : "pas d'heure (on continue)");
}

// Le DNS du réseau Wokwi est parfois en panne (c'est lui qui faisait planter SNTP) :
// on résout le nom nous-mêmes et, en cas d'échec, on prend l'IP de secours.
void setBroker() {
  static IPAddress ip;
  static bool resolved = false;
  if (!resolved) {
    if (WiFi.hostByName(MQTT_HOST, ip) == 1 && ip != IPAddress(0, 0, 0, 0)) {
      Serial.printf("[dns] %s -> %s\r\n", MQTT_HOST, ip.toString().c_str());
    } else {
      ip.fromString(MQTT_HOST_IP);
      Serial.printf("[dns] échec pour %s, IP de secours %s\r\n", MQTT_HOST, MQTT_HOST_IP);
    }
    resolved = true;
  }
  mqtt.setServer(ip, MQTT_PORT);
}

bool connectMqtt() {
  setBroker();
  mqtt.setCallback(onMessage);
  mqtt.setBufferSize(512);
  // session persistante (cleanSession = false) : les ordres envoyés pendant le sommeil attendent
  if (!mqtt.connect(DEVICE_ID, nullptr, nullptr, nullptr, 0, false, nullptr, false)) {
    Serial.printf("[mqtt] échec de connexion à %s (état %d)\r\n", MQTT_HOST, mqtt.state());
    return false;
  }
  mqtt.subscribe(topicCmd.c_str(), 1);
  return true;
}

// Vrai deep sleep : la RAM est perdue, seules les variables RTC_DATA_ATTR survivent.
void deepSleep() {
  Serial.printf("[sleep] deep sleep %d s\r\n\r\n", PERIOD_S);
  Serial.flush();
  mqtt.disconnect();
  delay(100);
  WiFi.disconnect(true);   // esp_deep_sleep_start() coupe la radio lui-même : pas de WIFI_OFF ici
  esp_sleep_enable_timer_wakeup((uint64_t)PERIOD_S * 1000000ULL);
  esp_deep_sleep_start();
}

// Veille simulée (Wokwi) : le deep sleep de Wokwi + core 3.x redémarre par le chien de garde
// (TG1WDT_SYS_RESET) et efface la mémoire RTC. On garde le même cycle, mais on attend
// PERIOD_S en restant connecté : un ordre reçu pendant la "veille" est exécuté tout de suite.
void simulatedSleep() {
  Serial.printf("[sleep] veille simulée %d s (Wokwi)\r\n\r\n", PERIOD_S);
  uint32_t start = millis();
  while (millis() - start < (uint32_t)PERIOD_S * 1000UL && !valveOpen()) {
    static uint32_t lastTry = 0;                 // une tentative toutes les 5 s au plus
    if (WiFi.status() == WL_CONNECTED && !mqtt.connected() && millis() - lastTry > 5000) {
      lastTry = millis();
      connectMqtt();
    }
    mqtt.loop();
    delay(50);
  }
}

void goToSleep() {
  batteryV = max(3.0f, batteryV - 0.0004f);   // éveillé ~80 mA, deep sleep ~10 µA
#if FW_DEEP_SLEEP
  deepSleep();
#else
  simulatedSleep();
#endif
}

// Un cycle = un réveil : mesures, publication, attente d'un ordre, arrosage éventuel.
void cycle() {
  bootCount++;
  Serial.printf("\r\nAgriFog fw %s — %s/%s/%s — réveil n°%lu\r\n", FW_VERSION, FOG_ID, ZONE_ID, DEVICE_ID,
                (unsigned long)bootCount);
  // 1er cycle : exactement la séquence de la v2 qui marchait (WiFi -> heure -> MQTT),
  // sans interroger WiFi/MQTT avant qu'ils soient démarrés (core 3.x : assert failed).
  static bool online = false;
  if (!online || WiFi.status() != WL_CONNECTED) {
    if (!connectWiFi()) {
      Serial.println("[wifi] pas de réseau, on réessaie au prochain réveil");
      return;
    }
    Serial.println("[wifi] connecté");
    syncTime();
    if (!connectMqtt()) return;
    Serial.println("[mqtt] connecté");
    online = true;
  } else if (!mqtt.connected()) {
    if (!connectMqtt()) return;
    Serial.println("[mqtt] reconnecté");
  }
  delay(500);                       // laisser arriver un ordre en attente
  mqtt.loop();
  publishTelemetry();
  publishHeartbeat();

  uint32_t start = millis(), lastReport = 0;
  while (millis() - start < AWAKE_MS || valveOpen()) {   // on ne dort pas vanne ouverte
    mqtt.loop();
    updateValve();
    if (valveOpen() && millis() - lastReport >= 10000) {  // la vanne reste ouverte : on le montre
      lastReport = millis();
      Serial.printf("[vanne] ouverte, encore %lu s, %.0f L comptés\r\n",
                    (unsigned long)((valveUntilMs - millis()) / 1000), waterTotalL);
      publishTelemetry();                                 // le moteur voit le débit pendant l'arrosage
    }
    delay(50);
  }
  updateValve();
}

void setup() {
  Serial.begin(115200);
  pinMode(RELAY_PIN, OUTPUT);
  digitalWrite(RELAY_PIN, LOW);
  dht.begin();
  String base = String(TOPIC_PREFIX) + FOG_ID;
  topicTelemetry = base + "/zone/" + ZONE_ID + "/telemetry";
  topicCmd = base + "/zone/" + ZONE_ID + "/cmd";
  topicValve = base + "/zone/" + ZONE_ID + "/valve";
  topicHeartbeat = base + "/device/" + DEVICE_ID + "/heartbeat";
}

// Avec FW_DEEP_SLEEP=1, goToSleep() ne revient jamais : la carte redémarre dans setup() au réveil.
void loop() {
  cycle();
  goToSleep();
}