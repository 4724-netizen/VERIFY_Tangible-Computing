// ============================================================
//  Wrist CAPTCHA — MPU6050 Sensor Streamer
//  Board  : Arduino Uno
//  Sensor : MPU6050 via I2C  (SDA → A4, SCL → A5)
//  Output : CSV over Serial at 115200 baud, 50 Hz
// ============================================================
//
//  Wiring:
//    MPU6050 VCC  → Arduino 3.3V  (or 5V — most breakouts accept both)
//    MPU6050 GND  → Arduino GND
//    MPU6050 SDA  → Arduino A4
//    MPU6050 SCL  → Arduino A5
//    MPU6050 AD0  → GND           (sets I2C address to 0x68)
//    MPU6050 INT  → not used here
//
//  Serial output format (one line per sample):
//    timestamp_ms,ax,ay,az,gx,gy,gz
//    all values are raw 16-bit signed integers
//    Divide accel by 16384.0  → g  (±2g range)
//    Divide gyro  by 131.0    → °/s (±250 °/s range)
// ============================================================

#include <Wire.h>

// ---- MPU6050 register map (only what we need) ---------------
#define MPU_ADDR         0x68
#define REG_PWR_MGMT_1   0x6B
#define REG_ACCEL_XOUT   0x3B
#define REG_GYRO_XOUT    0x43
#define REG_CONFIG       0x1A
#define REG_GYRO_CONFIG  0x1B
#define REG_ACCEL_CONFIG 0x1C

// ---- Sampling -----------------------------------------------
#define SAMPLE_HZ        50
#define SAMPLE_INTERVAL  (1000UL / SAMPLE_HZ)

// ---- Window marker ------------------------------------------
// Every 2 seconds the sketch emits "#WINDOW" so the PC-side
// Python script knows where each analysis window starts.
#define WINDOW_SAMPLES   (SAMPLE_HZ * 2)
static uint16_t sampleCount = 0;

// ---- Raw sensor data struct ---------------------------------
struct SensorData {          //storing the reading
  int16_t ax, ay, az;
  int16_t gx, gy, gz;
};

// ================================================================
//  I2C helpers
// ================================================================

static void writeByte(uint8_t reg, uint8_t value) {   //sends command to sensor
  Wire.beginTransmission(MPU_ADDR);
  Wire.write(reg);
  Wire.write(value);
  Wire.endTransmission();
}

static bool readBytes(uint8_t reg, uint8_t* buf, uint8_t len) {  //fetching data from sensor
  Wire.beginTransmission(MPU_ADDR);
  Wire.write(reg);
  if (Wire.endTransmission(false) != 0) return false;
  Wire.requestFrom((uint8_t)MPU_ADDR, len);
  for (uint8_t i = 0; i < len; i++) {
    if (!Wire.available()) return false;
    buf[i] = Wire.read();
  }
  return true;
}

static int16_t toInt16(uint8_t hi, uint8_t lo) {
  return (int16_t)((hi << 8) | lo);
}

// ================================================================
//  MPU6050 initialisation
// ================================================================

static bool mpuInit() {
  // Wake device — clear sleep bit
  writeByte(REG_PWR_MGMT_1, 0x00);
  delay(100);

  // DLPF = 3: ~44 Hz bandwidth
  // Keeps human hand tremor (8–12 Hz) intact, cuts high-freq noise
  writeByte(REG_CONFIG, 0x03);

  // Gyro  ±250 °/s  → 131.0 LSB/(°/s)
  writeByte(REG_GYRO_CONFIG, 0x00);

  // Accel ±2 g      → 16384.0 LSB/g
  writeByte(REG_ACCEL_CONFIG, 0x00);

  // Confirm via WHO_AM_I (register 0x75, should return 0x68)
  uint8_t id;
  Wire.beginTransmission(MPU_ADDR);
  Wire.write(0x75);
  Wire.endTransmission(false);
  Wire.requestFrom((uint8_t)MPU_ADDR, (uint8_t)1);
  if (!Wire.available()) return false;
  id = Wire.read();
  Serial.print("#DEBUG WHO_AM_I = 0x");
  Serial.println(id, HEX);
  return (id == 0x68 || id == 0x70);
}

// ================================================================
//  Read one sample from sensor
// ================================================================

static bool readSensor(SensorData& d) {
  uint8_t buf[6];

  if (!readBytes(REG_ACCEL_XOUT, buf, 6)) return false;
  d.ax = toInt16(buf[0], buf[1]);
  d.ay = toInt16(buf[2], buf[3]);
  d.az = toInt16(buf[4], buf[5]);

  if (!readBytes(REG_GYRO_XOUT, buf, 6)) return false;
  d.gx = toInt16(buf[0], buf[1]);
  d.gy = toInt16(buf[2], buf[3]);
  d.gz = toInt16(buf[4], buf[5]);

  return true;
}

// ================================================================
//  Serial output
// ================================================================

static void printHeader() {
  Serial.println(F("#timestamp_ms,ax,ay,az,gx,gy,gz"));
}

static void printSample(unsigned long ts, const SensorData& d) {
  Serial.print(ts);   Serial.print(',');
  Serial.print(d.ax); Serial.print(',');
  Serial.print(d.ay); Serial.print(',');
  Serial.print(d.az); Serial.print(',');
  Serial.print(d.gx); Serial.print(',');
  Serial.print(d.gy); Serial.print(',');
  Serial.println(d.gz);
}

// ================================================================
//  setup()
// ================================================================

void setup() {
  Serial.begin(115200);
  Wire.begin();
  Wire.setClock(100000);  // 400 kHz fast-mode I2C
  delay(200);

  if (!mpuInit()) {
    while (true) {
      Serial.println(F("#ERROR: MPU6050 not found — check wiring and AD0 pin"));
      delay(2000);
    }
  }

  Serial.println(F("#INFO: MPU6050 ready"));
  Serial.print(F("#INFO: Sample rate = "));
  Serial.print(SAMPLE_HZ);
  Serial.println(F(" Hz"));
  Serial.print(F("#INFO: Window size = "));
  Serial.print(WINDOW_SAMPLES);
  Serial.println(F(" samples (2 s)"));
  printHeader();
}

// ================================================================
//  loop()
// ================================================================

void loop() {
  static unsigned long nextSample = 0;
  unsigned long now = millis();

  if (now < nextSample) return;
  nextSample = now + SAMPLE_INTERVAL;

  SensorData d;
  if (!readSensor(d)) {
    Serial.println(F("#WARN: read failed, skipping"));
    return;
  }

  printSample(now, d);

  sampleCount++;
  if (sampleCount >= WINDOW_SAMPLES) {
    sampleCount = 0;
    Serial.println(F("#WINDOW"));
  }
}
