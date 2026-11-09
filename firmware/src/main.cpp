// AgriFog ESP32 firmware — stub (semaine 1 : lecture capteurs + Serial).
// Prochaines étapes (Ines) : MQTT, heartbeat, relais, deep sleep, liste de brokers, OTA.
#include <Arduino.h>
#include <DHT.h>

#define DHT_PIN 15
#define SOIL_PIN 34   // potentiomètre dans Wokwi = capteur d'humidité du sol
#define RELAY_PIN 26

DHT dht(DHT_PIN, DHT22);

void setup() {
  Serial.begin(115200);
  dht.begin();
  pinMode(RELAY_PIN, OUTPUT);
  digitalWrite(RELAY_PIN, LOW);
  Serial.printf("AgriFog fw %s — %s/%s/%s\n", FW_VERSION, FOG_ID, ZONE_ID, DEVICE_ID);
}

void loop() {
  float t = dht.readTemperature();
  float h = dht.readHumidity();
  int raw = analogRead(SOIL_PIN);                 // 0..4095
  float soil = raw * 100.0f / 4095.0f;            // % approx.
  Serial.printf("air_temp=%.1f air_humidity=%.1f soil_moisture=%.1f\n", t, h, soil);
  delay(5000);
}
