/*
  AcciAlert ESP32 firmware 2.0.0  (works with the AcciAlert backend, Device API v2)

  Libraries (Library Manager):  ArduinoJson 7.x, TinyGPSPlus.   Board: "ESP32 Dev Module", ESP32 core 3.x.
  Settings (Wi-Fi, laptop address): the three #define lines just below the includes

  What it does
   - Motor, MQ-3, HC-SR04, MPU6050/6500, NEO-6M, IR speed sensor, buzzer: same pins and logic as before.
   - Every second it sends one reading to the laptop and collects commands (AUTH, STOP, RESET, ACK, LIMIT, ZONES).
   - The car starts LOCKED. It unlocks only when the laptop says the driver passed the face check.
   - It locks again if the laptop does not answer for 8 seconds (or the Wi-Fi drops).
   - The network runs in its own task, so sensors, buzzer and motor never wait for Wi-Fi.
   - No key and no device id in this file: the ESP32 pairs once with its MAC address and keeps the token in flash.
*/

#include <WiFi.h>
#include <WebServer.h>
#include <HTTPClient.h>
#include <Preferences.h>
#include <ArduinoJson.h>
#include <Wire.h>
#include <TinyGPSPlus.h>
#include <math.h>
#include <time.h>
#define FIRMWARE_VERSION "2.0.0"

// ============================================================
// EDIT THESE THREE LINES (the only settings you must change)
// ============================================================
#define WIFI_SSID "DV030"                                  // Wi-Fi name (same network as the software laptop)
#define WIFI_PASS "ENTER_WIFI_PASSWORD_HERE"               // Wi-Fi password (do not share it)
#define SERVER_BASE_URL "http://192.168.1.19:5173/api/v1"  // software laptop: run ipconfig there, IPv4 of the Wi-Fi adapter. Keep ":5173/api/v1". Never 127.0.0.1
// ============================================================

// ----------------------------
// PINS (unchanged)
// ----------------------------
#define IN1 32
#define IN2 33
#define IN3 19
#define IN4 4
#define MQ3_PIN 34
#define TRIG_PIN 5
#define ECHO_PIN 18
#define BUZZER_PIN 14            // BC547 base through 1K
#define MPU_SDA_PIN 21
#define MPU_SCL_PIN 22
#define MPU_I2C_ADDR 0x68
#define GPS_RX_PIN 16            // GPS TX -> ESP32 GPIO16
#define GPS_TX_PIN 17
#define GPS_BAUD 9600
#define IR_SPEED_PIN 27
const uint16_t IR_SLOTS_PER_REV = 20;      // slots in the encoder disc

// ----------------------------
// SETTINGS
// ----------------------------
const unsigned long COMMAND_TIMEOUT = 1000;            // phone button released -> stop
const unsigned long SENSOR_INTERVAL_MS = 100;
const unsigned long NET_INTERVAL_MS = 1000;            // one reading + one command poll per second
const unsigned long SERVER_TIMEOUT_MS = 8000;          // no answer from the laptop -> lock the car
const float WHEEL_CIRCUMFERENCE_M = 0.204f;            // same value as WHEEL_CIRCUMFERENCE_M in backend/config.py
const float DEFAULT_SPEED_LIMIT_KMH = 40.0f;           // used when the laptop sends LIMIT 0

const unsigned long ECHO_TIMEOUT_US = 30000UL;
const float ULTRASONIC_WARNING_CM = 25.0f;
const float ULTRASONIC_CRITICAL_CM = 12.0f;

// MQ-3 is a prototype ethanol sensor, not a certified breathalyser. Warm-up + clean-air baseline after boot.
const unsigned long MQ3_WARMUP_MS = 60000UL;
const unsigned long MQ3_CALIBRATION_MS = 10000UL;
const int MQ3_ALERT_DELTA = 350;

const float IMPACT_LIMIT_G = 4.0f;                     // same as "impact_acceleration" in backend/config.py

// MPU orientation. With the car standing still on its wheels, Z must read about +1.00 g on the dashboard.
// If it reads about -1.00 g (the tilt shows 150-180 degrees and the status says EMERGENCY), the sensor board
// is mounted upside down: set this to true.
const bool MPU_UPSIDE_DOWN = false;

WebServer server(80);
Preferences prefs;
SemaphoreHandle_t dataMutex;

// ----------------------------
// STATE
// ----------------------------
unsigned long lastCommandTime = 0;
bool carMoving = false;

// Lock and command state (written by the network task, read by the main loop)
volatile bool serverLocked = true;                     // start locked
volatile uint32_t lastServerOkMs = 0;                  // last good answer from the laptop
volatile bool emergencyStop = false;                   // STOP command, cleared by RESET
volatile bool reqStop = false, reqReset = false, reqAck = false;
volatile float speedLimitKmh = 0;                      // 0 = car's own default
volatile bool paired = false;
char zonesVersion[16] = "";
volatile int zonesCount = 0;

// MQ-3
int mq3Baseline = 0;
bool mq3Calibrated = false;
bool alcoholAlert = false;
int mq3Raw = 0;
uint32_t mq3BootMs = 0;
unsigned long mq3CalSum = 0, mq3CalCount = 0;

// Ultrasonic
float distanceCm = -1.0f;
bool distanceValid = false;
bool obstacleWarning = false;
bool obstacleCritical = false;

// Buzzer
bool buzzerOutputState = false;
bool buzzerActive = false;                             // an alarm wants the buzzer (even while muted)
bool buzzerMuted = false;                              // ACK pressed on the dashboard

// MPU
bool mpuOnline = false;
uint8_t mpuWhoAmI = 0;
float accelX = 0, accelY = 0, accelZ = 9.80665f;       // m/s^2
float gyroX = 0, gyroY = 0, gyroZ = 0;                 // deg/s
float accelerationG = 1.0f;
bool accidentAlert = false;
uint32_t accidentUntilMs = 0;

// GPS
TinyGPSPlus gps;
HardwareSerial GPSSerial(2);
bool gpsFixValid = false;
double gpsLatitude = 0, gpsLongitude = 0;
uint32_t gpsSatellites = 0;

// IR speed
volatile uint32_t irPulseCount = 0;
uint32_t lastRpmPulseSnapshot = 0;
float speedRPM = 0;
float speedKmh = 0;
bool overspeedAlert = false;
unsigned long lastRpmCalcMs = 0;

unsigned long lastSensorReadMs = 0;
unsigned long lastPrintMs = 0;

// One reading, handed from the main loop to the network task
struct Telemetry {
  int alcoholAdc;
  float distance;
  float ax, ay, az, gx, gy, gz;
  float rpm;
  uint32_t pulses;
  double lat, lon;
  bool fix;
  uint32_t sats;
  bool alcohol, overspeed, accident, obstacle, buzzer;
};
Telemetry snap = {};

// ============================
// LOCK
// ============================
bool carLocked() {
  if (serverLocked) return true;
  return (uint32_t)(millis() - lastServerOkMs) > SERVER_TIMEOUT_MS;
}

bool driveAllowed() {
  return !carLocked() && !emergencyStop;
}

// ============================
// MOTOR (original logic)
// ============================
void stopCar() {
  digitalWrite(IN1, LOW);
  digitalWrite(IN2, LOW);
  digitalWrite(IN3, LOW);
  digitalWrite(IN4, LOW);
  if (carMoving) Serial.println("STOP");
  carMoving = false;
}

void forward() {
  digitalWrite(IN1, HIGH);
  digitalWrite(IN2, LOW);
  digitalWrite(IN3, LOW);      // channel B reversed
  digitalWrite(IN4, HIGH);
  carMoving = true;
  lastCommandTime = millis();
}

void backward() {
  digitalWrite(IN1, LOW);
  digitalWrite(IN2, HIGH);
  digitalWrite(IN3, HIGH);     // channel B reversed
  digitalWrite(IN4, LOW);
  carMoving = true;
  lastCommandTime = millis();
}

// ============================
// ULTRASONIC
// ============================
void readUltrasonic() {
  digitalWrite(TRIG_PIN, LOW);
  delayMicroseconds(3);
  digitalWrite(TRIG_PIN, HIGH);
  delayMicroseconds(10);
  digitalWrite(TRIG_PIN, LOW);

  unsigned long duration = pulseIn(ECHO_PIN, HIGH, ECHO_TIMEOUT_US);
  if (duration == 0) {                 // no echo: not a close obstacle
    distanceValid = false;
    distanceCm = -1.0f;
    obstacleWarning = false;
    obstacleCritical = false;
    return;
  }
  distanceCm = (duration * 0.0343f) / 2.0f;
  distanceValid = (distanceCm > 0.0f && distanceCm <= 500.0f);
  if (!distanceValid) {
    obstacleWarning = false;
    obstacleCritical = false;
    return;
  }
  obstacleCritical = (distanceCm <= ULTRASONIC_CRITICAL_CM);
  obstacleWarning = (distanceCm <= ULTRASONIC_WARNING_CM);
}

// ============================
// MQ-3 (warm-up and baseline run in the background, nothing blocks)
// ============================
void readMQ3() {
  mq3Raw = analogRead(MQ3_PIN);
  uint32_t age = millis() - mq3BootMs;

  if (!mq3Calibrated) {
    alcoholAlert = false;
    if (age >= MQ3_WARMUP_MS) {
      mq3CalSum += mq3Raw;
      mq3CalCount++;
      if (age >= MQ3_WARMUP_MS + MQ3_CALIBRATION_MS && mq3CalCount > 0) {
        mq3Baseline = (int)(mq3CalSum / mq3CalCount);
        mq3Calibrated = true;
        Serial.print("MQ-3 baseline: ");
        Serial.println(mq3Baseline);
      }
    }
    return;
  }
  alcoholAlert = (mq3Raw >= (mq3Baseline + MQ3_ALERT_DELTA));
}

// ============================
// BUZZER (priority order)
//  1 obstacle <= 12 cm   continuous
//  2 STOP command        continuous
//  3 alcohol             two short beeps, pause
//  4 obstacle <= 25 cm   rapid beeping
//  5 impact              long repeating beep
//  6 overspeed           one short beep per second
// The ACK command from the dashboard silences it until the alarms are gone.
// ============================
void updateBuzzer() {
  unsigned long now = millis();
  bool on = false;
  buzzerActive = true;

  if (distanceValid && obstacleCritical) {
    on = true;
  } else if (emergencyStop) {
    on = true;
  } else if (alcoholAlert) {
    unsigned long p = now % 1200UL;
    on = (p < 180UL) || (p >= 320UL && p < 500UL);
  } else if (distanceValid && obstacleWarning) {
    on = ((now % 400UL) < 180UL);
  } else if (accidentAlert) {
    on = ((now % 1000UL) < 700UL);
  } else if (overspeedAlert) {
    on = ((now % 1000UL) < 150UL);
  } else {
    buzzerActive = false;
    buzzerMuted = false;               // alarms gone: next alarm sounds again
  }

  if (buzzerMuted) on = false;
  buzzerOutputState = on;
  digitalWrite(BUZZER_PIN, on ? HIGH : LOW);
}

// ============================
// MPU6050 / MPU6500
// ============================
bool mpuWriteRegister(uint8_t reg, uint8_t value) {
  Wire.beginTransmission(MPU_I2C_ADDR);
  Wire.write(reg);
  Wire.write(value);
  return Wire.endTransmission() == 0;
}

bool mpuReadRegisters(uint8_t reg, uint8_t* buffer, uint8_t length) {
  Wire.beginTransmission(MPU_I2C_ADDR);
  Wire.write(reg);
  if (Wire.endTransmission(false) != 0) return false;
  uint8_t count = Wire.requestFrom((uint8_t)MPU_I2C_ADDR, length, (uint8_t)true);
  if (count != length) return false;
  for (uint8_t i = 0; i < length; i++) buffer[i] = Wire.read();
  return true;
}

bool initMPU() {
  Wire.begin(MPU_SDA_PIN, MPU_SCL_PIN);
  Wire.setClock(400000);
  if (!mpuReadRegisters(0x75, &mpuWhoAmI, 1)) {
    Serial.println("MPU not responding at I2C 0x68");
    return false;
  }
  if (mpuWhoAmI != 0x68 && mpuWhoAmI != 0x70 && mpuWhoAmI != 0x71 && mpuWhoAmI != 0x73) {
    Serial.print("Unknown MPU WHO_AM_I: 0x");
    Serial.println(mpuWhoAmI, HEX);
    return false;
  }
  if (!mpuWriteRegister(0x6B, 0x00)) return false;   // wake
  delay(100);
  mpuWriteRegister(0x1B, 0x00);                      // gyro +/-250 dps
  mpuWriteRegister(0x1C, 0x10);                      // accel +/-8 g (+/-2 g could never measure a 4 g impact)
  mpuWriteRegister(0x1A, 0x03);                      // low-pass filter
  delay(50);
  Serial.print("MPU detected, WHO_AM_I=0x");
  Serial.println(mpuWhoAmI, HEX);
  return true;
}

int16_t readInt16BE(const uint8_t* p) {
  return (int16_t)(((uint16_t)p[0] << 8) | p[1]);
}

void readMPU() {
  if (!mpuOnline) {
    // try again every few seconds (loose wire, late power-up)
    static uint32_t lastTry = 0;
    if (millis() - lastTry > 5000UL) {
      lastTry = millis();
      mpuOnline = initMPU();
    }
    accelX = 0; accelY = 0; accelZ = 9.80665f;       // harmless "flat and still" values
    gyroX = gyroY = gyroZ = 0;
    accelerationG = 1.0f;
    accidentAlert = false;
    return;
  }
  uint8_t d[14];
  if (!mpuReadRegisters(0x3B, d, 14)) {
    mpuOnline = false;
    Serial.println("MPU read failed");
    return;
  }
  accelX = (readInt16BE(&d[0]) / 4096.0f) * 9.80665f;      // +/-8 g range: 4096 counts per g
  accelY = (readInt16BE(&d[2]) / 4096.0f) * 9.80665f;
  accelZ = (readInt16BE(&d[4]) / 4096.0f) * 9.80665f;
  gyroX = readInt16BE(&d[8]) / 131.0f;
  gyroY = readInt16BE(&d[10]) / 131.0f;
  gyroZ = readInt16BE(&d[12]) / 131.0f;
  if (MPU_UPSIDE_DOWN) {                 // board turned over (rotated 180 deg about X): flip Y and Z
    accelY = -accelY; accelZ = -accelZ;
    gyroY = -gyroY;   gyroZ = -gyroZ;
  }
  accelerationG = sqrtf(accelX * accelX + accelY * accelY + accelZ * accelZ) / 9.80665f;

  if (accelerationG > IMPACT_LIMIT_G) accidentUntilMs = millis() + 5000UL;
  accidentAlert = ((int32_t)(accidentUntilMs - millis()) > 0);
}

// ============================
// GPS
// ============================
void readGPS() {
  while (GPSSerial.available()) gps.encode(GPSSerial.read());
  gpsFixValid = gps.location.isValid() && gps.location.age() < 3000;
  if (gpsFixValid) {
    gpsLatitude = gps.location.lat();
    gpsLongitude = gps.location.lng();
  }
  gpsSatellites = gps.satellites.isValid() ? gps.satellites.value() : 0;
}

// ============================
// IR speed
// ============================
void IRAM_ATTR onIrPulse() {
  irPulseCount++;
}

void calculateRPM() {
  unsigned long now = millis();
  unsigned long elapsed = now - lastRpmCalcMs;
  if (elapsed < 1000UL) return;

  noInterrupts();
  uint32_t count = irPulseCount;
  interrupts();

  uint32_t pulses = count - lastRpmPulseSnapshot;
  speedRPM = (pulses * 60000.0f) / (IR_SLOTS_PER_REV * (float)elapsed);
  lastRpmPulseSnapshot = count;
  lastRpmCalcMs = now;

  speedKmh = speedRPM * WHEEL_CIRCUMFERENCE_M * 60.0f / 1000.0f;
  float limit = (speedLimitKmh > 0) ? speedLimitKmh : DEFAULT_SPEED_LIMIT_KMH;
  overspeedAlert = (speedKmh > limit);
}

// ============================
// Hand the newest reading to the network task
// ============================
void publishSnapshot() {
  if (xSemaphoreTake(dataMutex, pdMS_TO_TICKS(20)) != pdTRUE) return;
  snap.alcoholAdc = mq3Raw;
  snap.distance = distanceValid ? distanceCm : 500.0f;    // 500 = nothing in range
  snap.ax = accelX; snap.ay = accelY; snap.az = accelZ;
  snap.gx = gyroX;  snap.gy = gyroY;  snap.gz = gyroZ;
  snap.rpm = speedRPM;
  snap.pulses = irPulseCount;
  snap.fix = gpsFixValid;
  snap.lat = gpsFixValid ? gpsLatitude : 0.0;             // 0,0 with fix=false: dashboard uses the laptop position
  snap.lon = gpsFixValid ? gpsLongitude : 0.0;
  snap.sats = gpsSatellites;
  snap.alcohol = alcoholAlert;
  snap.overspeed = overspeedAlert;
  snap.accident = accidentAlert;
  snap.obstacle = distanceValid && obstacleWarning;
  snap.buzzer = buzzerActive && !buzzerMuted;
  xSemaphoreGive(dataMutex);
}

// ============================
// NETWORK TASK (core 0)
// ============================
String token;
uint32_t doneIds[16];
uint8_t doneNext = 0;
uint32_t pendingCommands = 0;

time_t timeBase = 0;
uint32_t timeBaseMs = 0;
bool timeValid = false;
String tzSuffix = "+05:30";

bool wasDone(uint32_t id) {
  for (uint8_t i = 0; i < 16; i++) if (doneIds[i] == id) return true;
  return false;
}
void markDone(uint32_t id) {
  doneIds[doneNext % 16] = id;
  doneNext++;
}

// The laptop sends its clock with every command poll; the ESP32 needs no NTP / internet.
void syncTime(const char* st) {
  int Y, M, D, h, m, s;
  if (!st || sscanf(st, "%d-%d-%dT%d:%d:%d", &Y, &M, &D, &h, &m, &s) != 6) return;
  struct tm t;
  memset(&t, 0, sizeof t);
  t.tm_year = Y - 1900; t.tm_mon = M - 1; t.tm_mday = D;
  t.tm_hour = h; t.tm_min = m; t.tm_sec = s;
  timeBase = mktime(&t);                  // ESP32 default zone is UTC, so this is a plain calendar conversion
  timeBaseMs = millis();
  timeValid = true;
  size_t n = strlen(st);
  for (size_t i = 19; i < n; i++) {       // zone part after the seconds: +05:30, -03:00 or Z
    if (st[i] == '+' || st[i] == '-' || st[i] == 'Z') { tzSuffix = String(st + i); break; }
  }
}

String isoNow() {
  time_t t = timeBase + (time_t)((uint32_t)(millis() - timeBaseMs) / 1000UL);
  struct tm tmv;
  gmtime_r(&t, &tmv);
  char b[32];
  strftime(b, sizeof b, "%Y-%m-%dT%H:%M:%S", &tmv);
  return String(b) + tzSuffix;
}

int httpCall(const char* method, const String& path, const String& body, String& reply, bool useAuth) {
  HTTPClient h;
  h.setConnectTimeout(3000);
  h.setTimeout(3000);
  if (!h.begin(String(SERVER_BASE_URL) + path)) return -1;
  h.addHeader("Content-Type", "application/json");
  if (useAuth) h.addHeader("Authorization", "Bearer " + token);
  int code = (strcmp(method, "GET") == 0) ? h.GET() : h.POST(body);
  reply = (code > 0) ? h.getString() : String("");
  h.end();
  return code;
}

void clearToken() {
  prefs.remove("token");
  token = "";
  paired = false;
  serverLocked = true;
  Serial.println("Token rejected by the laptop: pairing again.");
}

void registerDevice() {
  JsonDocument d;
  d["mac"] = WiFi.macAddress();
  d["firmware"] = FIRMWARE_VERSION;
  String body, reply;
  serializeJson(d, body);
  int code = httpCall("POST", "/device/register", body, reply, false);
  uint32_t waitMs = 5000;

  if (code == 201) {
    JsonDocument r;
    if (!deserializeJson(r, reply)) {
      String t = r["token"].as<String>();
      if (t.length() > 10) {
        token = t;
        prefs.putString("token", token);
        paired = true;
        Serial.println("PAIRED as " + r["device_id"].as<String>());
        return;
      }
    }
    Serial.println("Register answer had no token");
  } else if (code == 403) {
    Serial.println("Not paired yet: on the laptop run  python -m backend.tools.devices pair --mac " + WiFi.macAddress());
    waitMs = 10000;
  } else if (code == 409) {
    Serial.println("Already registered: on the laptop run  python -m backend.tools.devices reset  then  pair");
    waitMs = 60000;
  } else if (code == 429) {
    Serial.println("Too many attempts: waiting 5 minutes");
    waitMs = 300000;
  } else {
    Serial.printf("Register failed: %d %s  (check SERVER_BASE_URL, Wi-Fi and the laptop firewall)\n", code, reply.c_str());
  }
  vTaskDelay(pdMS_TO_TICKS(waitMs));
}

// Returns false when the command was refused.
bool runCommand(const char* type, JsonVariantConst value, const char*& detail) {
  if (strcmp(type, "AUTH") == 0) {
    serverLocked = (value.as<int>() == 0);
    Serial.println(serverLocked ? "AUTH 0: LOCKED" : "AUTH 1: UNLOCKED");
    return true;
  }
  if (strcmp(type, "STOP") == 0)  { reqStop = true;  return true; }
  if (strcmp(type, "RESET") == 0) { reqReset = true; return true; }
  if (strcmp(type, "ACK") == 0)   { reqAck = true;   return true; }
  if (strcmp(type, "LIMIT") == 0) {
    float v = value.as<float>();
    if (!(v >= 0.0f && v <= 200.0f)) { detail = "limit out of range"; return false; }
    speedLimitKmh = v;
    Serial.printf("LIMIT %.0f km/h\n", v);
    return true;
  }
  if (strcmp(type, "ZONES") == 0) {
    const char* ver = value["version"] | "";
    strlcpy(zonesVersion, ver, sizeof zonesVersion);
    zonesCount = value["count"] | 0;
    Serial.printf("ZONES %d (version %s)\n", (int)zonesCount, zonesVersion);
    return true;
  }
  detail = "unknown command";
  return false;
}

void pollCommands() {
  String reply;
  int code = httpCall("GET", "/device/commands?limit=10", "", reply, true);
  if (code == 401) { clearToken(); return; }
  if (code != 200) return;

  JsonDocument r;
  if (deserializeJson(r, reply)) return;

  syncTime(r["server_time"] | "");
  serverLocked = ((r["state"]["auth"] | 0) == 0);     // missing = locked
  lastServerOkMs = millis();

  JsonDocument a;
  JsonArray acks = a["acks"].to<JsonArray>();
  for (JsonObject c : r["commands"].as<JsonArray>()) {
    uint32_t id = c["id"] | 0;
    if (id == 0) continue;
    const char* result = "executed";
    const char* detail = nullptr;
    if (!wasDone(id)) {                                // a repeated id is only acknowledged again
      if (!runCommand(c["type"] | "", c["value"], detail)) result = "rejected";
      markDone(id);
    }
    JsonObject o = acks.add<JsonObject>();
    o["id"] = id;
    o["result"] = result;
    if (detail) o["detail"] = detail;
  }
  if (acks.size() > 0) {
    String body, rep;
    serializeJson(a, body);
    httpCall("POST", "/device/commands/ack", body, rep, true);
  }
}

float clampf(float v, float lo, float hi) {
  if (!isfinite(v)) return 0;
  return v < lo ? lo : (v > hi ? hi : v);
}
double round6(double v) { return round(v * 1e6) / 1e6; }

void sendTelemetry() {
  if (!timeValid) return;                              // clock comes from the first command poll
  Telemetry t;
  if (xSemaphoreTake(dataMutex, pdMS_TO_TICKS(50)) != pdTRUE) return;
  t = snap;
  xSemaphoreGive(dataMutex);

  JsonDocument d;
  d["timestamp"] = isoNow();
  JsonObject s = d["sensors"].to<JsonObject>();
  s["alcohol_adc"] = constrain(t.alcoholAdc, 0, 4095);
  s["distance_cm"] = clampf(t.distance, 0, 500);
  JsonObject a = s["accelerometer"].to<JsonObject>();
  a["x"] = clampf(t.ax, -160, 160); a["y"] = clampf(t.ay, -160, 160); a["z"] = clampf(t.az, -160, 160);
  JsonObject g = s["gyroscope"].to<JsonObject>();
  g["x"] = clampf(t.gx, -2000, 2000); g["y"] = clampf(t.gy, -2000, 2000); g["z"] = clampf(t.gz, -2000, 2000);
  s["speed_rpm"] = clampf(t.rpm, 0, 20000);
  s["ir_pulse_count"] = t.pulses;
  JsonObject p = s["gps"].to<JsonObject>();
  p["latitude"] = round6(t.lat);
  p["longitude"] = round6(t.lon);
  p["fix_valid"] = t.fix;
  p["satellites"] = (t.sats > 64) ? 64 : t.sats;
  JsonObject al = d["alerts"].to<JsonObject>();
  al["alcohol"] = t.alcohol;
  al["overspeed"] = t.overspeed;
  al["accident"] = t.accident;
  al["obstacle"] = t.obstacle;
  al["buzzer"] = t.buzzer;

  String body, reply;
  serializeJson(d, body);
  int code = httpCall("POST", "/telemetry", body, reply, true);
  if (code == 201) {
    JsonDocument r;
    if (!deserializeJson(r, reply)) pendingCommands = r["commands_pending"] | 0;
  } else if (code == 401) {
    clearToken();
  } else {
    Serial.printf("Telemetry failed: %d %s\n", code, reply.c_str());
  }
}

void netTask(void*) {
  for (;;) {
    if (WiFi.status() != WL_CONNECTED) {
      serverLocked = true;
      vTaskDelay(pdMS_TO_TICKS(500));
      continue;
    }
    if (token.isEmpty()) {
      registerDevice();
      continue;
    }
    uint32_t started = millis();
    pollCommands();
    sendTelemetry();
    uint32_t spent = millis() - started;
    uint32_t interval = (pendingCommands > 0) ? 200 : NET_INTERVAL_MS;   // commands waiting: come back sooner
    pendingCommands = 0;
    vTaskDelay(pdMS_TO_TICKS(spent >= interval ? 10 : interval - spent));
  }
}

// ============================
// PHONE PAGE (local controller, original UI + lock/server status)
// ============================
const char webpage[] PROGMEM = R"rawliteral(
<!DOCTYPE html>
<html>
<head>
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>AcciAlert Car</title>
<style>
body { background:#101827; color:white; font-family:Arial; text-align:center; padding:20px; }
h2 { color:#38bdf8; }
.controls { display:flex; flex-direction:column; align-items:center; gap:25px; margin-top:40px; }
button { width:180px; height:110px; border:none; border-radius:20px; font-size:25px; font-weight:bold;
         color:white; background:#2563eb; touch-action:none; user-select:none; }
button:active { background:#1d4ed8; }
.stop { background:#dc2626; }
p { color:#a5b4fc; }
.lock { margin:14px auto; max-width:360px; padding:10px; border-radius:12px; font-weight:bold; background:#7f1d1d; }
.lock.open { background:#14532d; }
.sensor { margin:14px auto; max-width:360px; padding:12px; border-radius:12px; background:#1e293b; color:#e2e8f0; text-align:left; }
</style>
</head>
<body>
<h2>ACCIALERT</h2>
<p>Wi-Fi Car Controller</p>
<div class="lock" id="lockBox">Checking...</div>
<p>Press and hold to move</p>

<div class="controls">
<button onpointerdown="start('forward')" onpointerup="stop()" onpointerleave="stop()" onpointercancel="stop()">FORWARD &#9650;</button>
<button class="stop" onclick="stop()">STOP</button>
<button onpointerdown="start('backward')" onpointerup="stop()" onpointerleave="stop()" onpointercancel="stop()">BACKWARD &#9660;</button>
</div>

<div class="sensor" id="sensorStatus">Reading sensors...</div>

<script>
let timer = null;
function start(direction) {
  clearInterval(timer);
  fetch('/' + direction).catch(console.error);
  timer = setInterval(() => { fetch('/' + direction).catch(console.error); }, 300);
}
function stop() {
  clearInterval(timer);
  timer = null;
  fetch('/stop').catch(console.error);
}
document.addEventListener('visibilitychange', () => { if (document.hidden) stop(); });
window.addEventListener('blur', stop);

async function refreshSensors() {
  try {
    const response = await fetch('/status', {cache: 'no-store'});
    const d = await response.json();
    const lb = document.getElementById('lockBox');
    if (!d.paired) { lb.textContent = 'NOT PAIRED with the laptop'; lb.className = 'lock'; }
    else if (d.emergency) { lb.textContent = 'EMERGENCY STOP (waiting for RESET)'; lb.className = 'lock'; }
    else if (d.locked) { lb.textContent = 'LOCKED: face login needed on the laptop'; lb.className = 'lock'; }
    else { lb.textContent = 'UNLOCKED: driver verified'; lb.className = 'lock open'; }
    document.getElementById('sensorStatus').innerHTML =
      'Laptop link: ' + (d.serverOnline ? 'ONLINE' : 'OFFLINE') +
      '<br>Speed: ' + d.speedKmh.toFixed(1) + ' km/h (limit ' + d.limitKmh.toFixed(0) + ')' +
      '<br>Overspeed: ' + (d.overspeed ? 'YES' : 'NO') +
      '<hr>MQ-3: ' + d.mq3 + ' (baseline ' + (d.mq3Calibrated ? d.mq3Baseline : 'warming up') + ')' +
      '<br>Alcohol alert: ' + (d.alcoholAlert ? 'YES' : 'NO') +
      '<br>Distance: ' + (d.distanceValid ? d.distanceCm.toFixed(1) + ' cm' : 'NO ECHO') +
      '<br>Obstacle: ' + (d.obstacleCritical ? 'CRITICAL' : (d.obstacleWarning ? 'WARNING' : 'NO')) +
      '<br>Buzzer: ' + (d.buzzer ? 'ON' : 'OFF') +
      '<hr>MPU: ' + (d.mpuOnline ? 'ONLINE' : 'OFFLINE') +
      '<br>Acceleration: ' + d.accelerationG.toFixed(2) + ' g' +
      '<br>Gyroscope: ' + d.gyroX.toFixed(1) + ', ' + d.gyroY.toFixed(1) + ', ' + d.gyroZ.toFixed(1) + ' dps' +
      '<br>Accident alert: ' + (d.accidentAlert ? 'YES' : 'NO') +
      '<br>IR RPM: ' + d.rpm.toFixed(1) + ' (pulses ' + d.irPulses + ')' +
      '<br>GPS fix: ' + (d.gpsFix ? 'YES' : 'NO') + ' (satellites ' + d.satellites + ')' +
      '<br>Latitude: ' + d.latitude.toFixed(6) +
      '<br>Longitude: ' + d.longitude.toFixed(6);
  } catch (e) {
    document.getElementById('sensorStatus').textContent = 'ESP32 status unavailable';
  }
}
setInterval(refreshSensors, 500);
refreshSensors();
</script>
</body>
</html>
)rawliteral";

String tf(bool v) { return v ? "true" : "false"; }

void handleStatus() {
  String j = "{";
  j += "\"paired\":" + tf(paired) + ",";
  j += "\"locked\":" + tf(carLocked()) + ",";
  j += "\"emergency\":" + tf(emergencyStop) + ",";
  j += "\"serverOnline\":" + tf((uint32_t)(millis() - lastServerOkMs) <= SERVER_TIMEOUT_MS && lastServerOkMs != 0) + ",";
  j += "\"speedKmh\":" + String(speedKmh, 2) + ",";
  j += "\"limitKmh\":" + String(speedLimitKmh > 0 ? speedLimitKmh : DEFAULT_SPEED_LIMIT_KMH, 0) + ",";
  j += "\"overspeed\":" + tf(overspeedAlert) + ",";
  j += "\"mq3\":" + String(mq3Raw) + ",";
  j += "\"mq3Baseline\":" + String(mq3Baseline) + ",";
  j += "\"mq3Calibrated\":" + tf(mq3Calibrated) + ",";
  j += "\"alcoholAlert\":" + tf(alcoholAlert) + ",";
  j += "\"distanceValid\":" + tf(distanceValid) + ",";
  j += "\"distanceCm\":" + String(distanceCm < 0 ? 0 : distanceCm, 1) + ",";
  j += "\"obstacleWarning\":" + tf(obstacleWarning) + ",";
  j += "\"obstacleCritical\":" + tf(obstacleCritical) + ",";
  j += "\"buzzer\":" + tf(buzzerOutputState) + ",";
  j += "\"mpuOnline\":" + tf(mpuOnline) + ",";
  j += "\"accelerationG\":" + String(accelerationG, 2) + ",";
  j += "\"gyroX\":" + String(gyroX, 1) + ",";
  j += "\"gyroY\":" + String(gyroY, 1) + ",";
  j += "\"gyroZ\":" + String(gyroZ, 1) + ",";
  j += "\"accidentAlert\":" + tf(accidentAlert) + ",";
  j += "\"rpm\":" + String(speedRPM, 1) + ",";
  j += "\"irPulses\":" + String((uint32_t)irPulseCount) + ",";
  j += "\"gpsFix\":" + tf(gpsFixValid) + ",";
  j += "\"satellites\":" + String(gpsSatellites) + ",";
  j += "\"latitude\":" + String(gpsFixValid ? gpsLatitude : 0.0, 6) + ",";
  j += "\"longitude\":" + String(gpsFixValid ? gpsLongitude : 0.0, 6);
  j += "}";
  server.send(200, "application/json", j);
}

void handleDrive(bool fwd) {
  if (!driveAllowed()) {
    stopCar();
    server.send(403, "text/plain", emergencyStop ? "EMERGENCY STOP" : "LOCKED");
    return;
  }
  if (fwd) forward(); else backward();
  server.send(200, "text/plain", fwd ? "FORWARD" : "BACKWARD");
}

// ============================
// SETUP
// ============================
void setup() {
  Serial.begin(115200);
  delay(200);

  pinMode(IN1, OUTPUT);
  pinMode(IN2, OUTPUT);
  pinMode(IN3, OUTPUT);
  pinMode(IN4, OUTPUT);
  pinMode(MQ3_PIN, INPUT);
  pinMode(TRIG_PIN, OUTPUT);
  pinMode(ECHO_PIN, INPUT);
  pinMode(BUZZER_PIN, OUTPUT);
  digitalWrite(TRIG_PIN, LOW);
  digitalWrite(BUZZER_PIN, LOW);
  stopCar();

  analogReadResolution(12);
  analogSetPinAttenuation(MQ3_PIN, ADC_11db);

  dataMutex = xSemaphoreCreateMutex();
  mq3BootMs = millis();

  mpuOnline = initMPU();
  GPSSerial.begin(GPS_BAUD, SERIAL_8N1, GPS_RX_PIN, GPS_TX_PIN);
  pinMode(IR_SPEED_PIN, INPUT_PULLUP);
  attachInterrupt(digitalPinToInterrupt(IR_SPEED_PIN), onIrPulse, FALLING);
  lastRpmCalcMs = millis();

  WiFi.mode(WIFI_STA);
  WiFi.setAutoReconnect(true);
  Serial.println();
  Serial.println("AcciAlert firmware " FIRMWARE_VERSION);
  WiFi.begin(WIFI_SSID, WIFI_PASS);
  Serial.print("Connecting to Wi-Fi");
  unsigned long t0 = millis();
  while (WiFi.status() != WL_CONNECTED && millis() - t0 < 20000) {
    delay(500);
    Serial.print(".");
  }
  if (WiFi.status() == WL_CONNECTED) {
    Serial.println("\nWi-Fi connected. ESP32 address: " + WiFi.localIP().toString());
  } else {
    Serial.println("\nWi-Fi not connected yet (will keep trying). Check WIFI_SSID / WIFI_PASS at the top.");
  }
  // The MAC is only valid after the Wi-Fi radio has started (before that it reads 00:00:00:00:00:00)
  Serial.println("MAC for pairing: " + WiFi.macAddress());

  prefs.begin("accialert", false);
  token = prefs.getString("token", "");
  paired = !token.isEmpty();
  Serial.println(paired ? "Token found in flash: already paired." : "No token: waiting for the pairing window on the laptop.");

  server.on("/", HTTP_GET, []() { server.send(200, "text/html", webpage); });
  server.on("/forward", HTTP_GET, []() { handleDrive(true); });
  server.on("/backward", HTTP_GET, []() { handleDrive(false); });
  server.on("/stop", HTTP_GET, []() {
    stopCar();
    server.send(200, "text/plain", "STOP");
  });
  server.on("/status", HTTP_GET, handleStatus);
  server.begin();

  xTaskCreatePinnedToCore(netTask, "net", 16384, nullptr, 1, nullptr, 0);
  Serial.println("Ready. The car is LOCKED until the driver passes the face check.");
}

// ============================
// LOOP (core 1): sensors, buzzer, motor safety, phone page
// ============================
void loop() {
  server.handleClient();

  // Commands from the laptop (set by the network task)
  if (reqStop)  { reqStop = false;  emergencyStop = true; stopCar(); Serial.println("STOP command: motor off"); }
  if (reqReset) { reqReset = false; emergencyStop = false; accidentUntilMs = 0; accidentAlert = false; buzzerMuted = false; Serial.println("RESET command"); }
  if (reqAck)   { reqAck = false;   buzzerMuted = true; }

  // Locked or stopped: the motor never runs
  if (carMoving && !driveAllowed()) stopCar();

  // Phone button released / page closed
  if (carMoving && millis() - lastCommandTime > COMMAND_TIMEOUT) {
    stopCar();
    Serial.println("AUTO STOP");
  }

  // Wi-Fi watchdog (auto-reconnect is on; this is the backup)
  static unsigned long lastWifiTry = 0;
  if (WiFi.status() != WL_CONNECTED && millis() - lastWifiTry > 15000UL) {
    lastWifiTry = millis();
    WiFi.reconnect();
  }

  if (millis() - lastSensorReadMs >= SENSOR_INTERVAL_MS) {
    lastSensorReadMs = millis();

    readMQ3();
    readUltrasonic();
    readMPU();
    readGPS();
    calculateRPM();
    updateBuzzer();
    publishSnapshot();

    if (millis() - lastPrintMs >= 500UL) {
      lastPrintMs = millis();
      Serial.printf("%s | MQ3:%d%s | Dist:", carLocked() ? "LOCKED" : "UNLOCKED", mq3Raw, alcoholAlert ? " ALCOHOL" : "");
      if (distanceValid) Serial.printf("%.1fcm", distanceCm); else Serial.print("none");
      Serial.printf(" | Buzz:%s | Acc:%.2fg%s | RPM:%.1f (%.1f km/h)%s | GPS:%s sats:%u",
                    buzzerOutputState ? "ON" : "off", accelerationG, accidentAlert ? " IMPACT" : "",
                    speedRPM, speedKmh, overspeedAlert ? " OVERSPEED" : "",
                    gpsFixValid ? "FIX" : "no fix", (unsigned)gpsSatellites);
      if (gpsFixValid) Serial.printf(" %.6f,%.6f", gpsLatitude, gpsLongitude);
      Serial.println();
    }
  }
}
