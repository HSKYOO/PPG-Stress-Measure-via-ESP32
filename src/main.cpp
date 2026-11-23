#include <Arduino.h>
#include <Wire.h>
#include "MAX30105.h"

// MAX30102 -> ESP32
// VCC/VIN -> 3V3, GND -> GND, SDA -> GPIO21, SCL -> GPIO22

MAX30105 sensor;
uint32_t sampleIndex = 0;

// Diagnose initialization failure without changing the normal raw PPG protocol.
String sensorDiagnostic() {
  Wire.setTimeOut(20);
  String devices;
  bool sensorAck = false;
  uint8_t sensorProbeResult = 255;
  for (uint8_t address = 1; address < 127; ++address) {
    Wire.beginTransmission(address);
    const uint8_t result = Wire.endTransmission();
    if (address == 0x57) sensorProbeResult = result;
    if (result == 0) {
      char hex[8];
      snprintf(hex, sizeof(hex), "0x%02X", address);
      if (devices.length()) devices += ",";
      devices += hex;
      if (address == 0x57) sensorAck = true;
    }
  }
  String report = "# RETRY: MAX30102 init failed; SDA=21 SCL=22; I2C devices=";
  report += devices.length() ? devices : "none";
  report += "; SDA_level=";
  report += digitalRead(21);
  report += " SCL_level=";
  report += digitalRead(22);
  report += "; probe_0x57=";
  report += sensorProbeResult;
  if (!sensorAck) {
    report += sensorProbeResult == 2 ? "; address NACK at 0x57" : "; I2C probe failed at 0x57";
    return report;
  }
  Wire.beginTransmission(0x57);
  Wire.write(0xFF);  // Read-only part identification register.
  if (Wire.endTransmission(false) != 0 ||
      Wire.requestFrom(uint8_t(0x57), uint8_t(1)) != 1) {
    report += "; PART_ID read failed";
    return report;
  }
  char part[64];
  snprintf(part, sizeof(part), "; PART_ID=0x%02X (expected 0x15)", Wire.read());
  report += part;
  return report;
}

void setup() {
  Serial.begin(115200);
  // Recreate the bus on every attempt, so a transient startup failure does
  // not permanently stop acquisition. Retry messages are nonfatal to the app.
  while (true) {
    if (Wire.begin(21, 22)) {
      Wire.setTimeOut(20);
      if (sensor.begin(Wire, I2C_SPEED_STANDARD)) break;
      Serial.println(sensorDiagnostic());
    } else {
      Serial.println("# RETRY: I2C bus initialization failed; SDA=21 SCL=22");
    }
    Wire.end();
    // Observe released lines separately from the I2C peripheral's state.
    // INPUT_PULLUP does not actively drive a stuck line high.
    pinMode(21, INPUT_PULLUP);
    pinMode(22, INPUT_PULLUP);
    delay(1);
    Serial.printf("# RETRY: I2C controller released; SDA_level=%d SCL_level=%d\n",
                  digitalRead(21), digitalRead(22));
    delay(2000);
  }

  // LED current, averaging=1 (no FIFO averaging), Red+IR, 100 samples/s,
  // LED pulse width=411 us, ADC range=4096 nA.
  sensor.setup(0x1F, 1, 2, 100, 411, 4096);
  Serial.println("sample,red,ir");
}

void loop() {
  // Read all pending Red/IR ADC samples from the sensor FIFO.
  sensor.check();
  while (sensor.available()) {
    const uint32_t red = sensor.getFIFORed();
    const uint32_t ir = sensor.getFIFOIR();
    Serial.printf("%lu,%lu,%lu\n",
                  static_cast<unsigned long>(sampleIndex++),
                  static_cast<unsigned long>(red),
                  static_cast<unsigned long>(ir));
    sensor.nextSample();
  }
}
