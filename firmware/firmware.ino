#include <Arduino.h>
#include <WiFi.h>
#include <PubSubClient.h>
#include <Wire.h>
#include <math.h>

const char* SSID        = "Wokwi-GUEST";
const char* PASSWORD    = "";
const char* MQTT_BROKER = "broker.hivemq.com";
const int   MQTT_PORT   = 1883;

// 3 topics hierarchises
const char* TOPIC_POSTURE = "smartposture/gilet_001/data";
const char* TOPIC_TEMP    = "smartposture/gilet_001/temperature";
const char* TOPIC_STATUS  = "smartposture/gilet_001/status";

#define LED_ROUGE  26
#define LED_VERTE  27
#define BUZZER_PIN 25
#define SDA_PIN    21
#define SCL_PIN    22
#define MPU_ADDR   0x68

// Deep Sleep 10 secondes
#define SLEEP_DURATION 10

float ax, ay, az, gx, gy, gz, angle;
int scenario  = 0;
int compteur  = 0;

WiFiClient   wifiClient;
PubSubClient mqttClient(wifiClient);

// WiFi
void connectWifi() {
  Serial.print("connexion WiFi");
  WiFi.begin(SSID, PASSWORD);
  while (WiFi.status() != WL_CONNECTED) {
    delay(500);
    Serial.print(".");
  }
  Serial.println("");
  Serial.print("connecte - IP : ");
  Serial.println(WiFi.localIP());
}

//  MQTT
void connectMQTT() {
  mqttClient.setServer(MQTT_BROKER, MQTT_PORT);
  while (!mqttClient.connected()) {
    Serial.print("connexion MQTT...");
    String clientId = "gilet_001_" + String(random(1000));
    if (mqttClient.connect(clientId.c_str())) {
      Serial.println("connecte au broker MQTT");
    } else {
      Serial.print("echec rc=");
      Serial.print(mqttClient.state());
      Serial.println(" retry 3s");
      delay(3000);
    }
  }
}

// Simulation MPU 
void simulerDonnees() {
  compteur++;
  if (compteur % 20 == 0) scenario = (scenario + 1) % 3;

  if (scenario == 0) {
    ay = random(-10, 10) / 100.0;
    az = 0.98;
  } else if (scenario == 1) {
    ay = 0.49;
    az = 0.87;
  } else {
    ay = 0.87;
    az = 0.49;
  }

  ax = 0.01;
  gx = random(-50, 50) / 100.0;
  gy = random(-50, 50) / 100.0;
  gz = random(-50, 50) / 100.0;

  angle = atan2(ay, az) * (180.0 / PI);
  if (angle < 0) angle = -angle;
}

void lireMPU() {
  Wire.beginTransmission(MPU_ADDR);
  Wire.write(0x3B);
  Wire.endTransmission(false);
  Wire.requestFrom(MPU_ADDR, 14, true);

  Wire.read(); Wire.read();
  Wire.read(); Wire.read();
  Wire.read(); Wire.read();
  Wire.read(); Wire.read();
  Wire.read(); Wire.read();
  Wire.read(); Wire.read();
  Wire.read(); Wire.read();

  simulerDonnees();
}

//Simulation température
float lireTemperature() {
  // Simule entre 36.0 et 38.5 °C
  float temp = 36.0 + (random(0, 25)) / 10.0;
  return temp;
}

//LEDs + Buzzer 
void gererIndicateurs(String posture) {
  if (posture == "MAUVAISE") {
    digitalWrite(LED_ROUGE, HIGH);
    digitalWrite(LED_VERTE, LOW);
    tone(BUZZER_PIN, 1000, 500);
  } else {
    digitalWrite(LED_ROUGE, LOW);
    digitalWrite(LED_VERTE, HIGH);
    noTone(BUZZER_PIN);
  }
}

//Publication MQTT
void publierPosture() {
  String posture;
  if (angle < 15.0)      posture = "BONNE";
  else if (angle < 45.0) posture = "ATTENTION";
  else                   posture = "MAUVAISE";

  gererIndicateurs(posture);

  String payload = "{";
  payload += "\"device_id\":\"gilet_001\",";
  payload += "\"ax\":"    + String(ax, 2) + ",";
  payload += "\"ay\":"    + String(ay, 2) + ",";
  payload += "\"az\":"    + String(az, 2) + ",";
  payload += "\"gx\":"    + String(gx, 2) + ",";
  payload += "\"gy\":"    + String(gy, 2) + ",";
  payload += "\"gz\":"    + String(gz, 2) + ",";
  payload += "\"angle\":" + String(angle, 1) + ",";
  payload += "\"posture\":\"" + posture + "\"";
  payload += "}";

  bool ok = mqttClient.publish(TOPIC_POSTURE, payload.c_str());
  Serial.print("posture | angle: ");
  Serial.print(angle);
  Serial.print(" | " + posture);
  Serial.println(ok ? " | ok" : " | echec");
}

void publierTemperature() {
  float temp = lireTemperature();

  String payload = "{";
  payload += "\"device_id\":\"gilet_001\",";
  payload += "\"temperature\":" + String(temp, 1);
  payload += "}";

  bool ok = mqttClient.publish(TOPIC_TEMP, payload.c_str());
  Serial.print("temperature: ");
  Serial.print(temp);
  Serial.println(ok ? " °C | ok" : " °C | echec");
}

void publierStatus() {
  String payload = "{";
  payload += "\"device_id\":\"gilet_001\",";
  payload += "\"status\":\"up\",";
  payload += "\"uptime\":" + String(millis() / 1000);
  payload += "}";

  bool ok = mqttClient.publish(TOPIC_STATUS, payload.c_str());
  Serial.println(ok ? "status: up | ok" : "status: up | echec");
}

// Setup 
void setup() {
  Serial.begin(115200);
  pinMode(LED_ROUGE, OUTPUT);
  pinMode(LED_VERTE, OUTPUT);
  pinMode(BUZZER_PIN, OUTPUT);

  Wire.begin(SDA_PIN, SCL_PIN);
  Wire.beginTransmission(MPU_ADDR);
  Wire.write(0x6B);
  Wire.write(0);
  Wire.endTransmission(true);

  connectWifi();
  connectMQTT();

  Serial.println("SmartPosture demarre");

  // Publier les 3 topics
  lireMPU();
  publierStatus();
  delay(500);
  publierTemperature();
  delay(500);
  publierPosture();
  delay(1000);  // attendre que le broker transmette

  Serial.println("Deep Sleep 10s...");
  esp_sleep_enable_timer_wakeup(SLEEP_DURATION * 1000000ULL);
  esp_deep_sleep_start();
}

// Loop vide (Deep Sleep gere le cycle)
void loop() {}