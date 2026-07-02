#include <Arduino.h>
#include <GxEPD2_BW.h>

#define EPD_DC     10
#define EPD_CS      9
#define EPD_RESET  -1
#define EPD_BUSY    8   // BUSY pin under test (GPIO 12 conflicts with ESP32-S3 flash)

GxEPD2_BW<GxEPD2_583_GDEQ0583T31, GxEPD2_583_GDEQ0583T31::HEIGHT>
    display(GxEPD2_583_GDEQ0583T31(EPD_CS, EPD_DC, EPD_RESET, EPD_BUSY));

// Interrupt flags — set in ISR when BUSY transitions
volatile bool     busy_fell = false;   // HIGH→LOW  (display went busy)
volatile bool     busy_rose = false;   // LOW→HIGH  (display became ready)
volatile uint32_t fell_ms   = 0;
volatile uint32_t rose_ms   = 0;

void IRAM_ATTR onBusyFall() { busy_fell = true; fell_ms = millis(); }
void IRAM_ATTR onBusyRise() { busy_rose = true; rose_ms = millis(); }

void setup() {
    Serial.begin(115200);
    delay(3000);

    Serial.println("\n========================================");
    Serial.println("       BUSY PIN TEST  (GPIO 12)        ");
    Serial.println("  using GxEPD2_583_GDEQ0583T31         ");
    Serial.println("========================================");

    pinMode(EPD_BUSY, INPUT);
    attachInterrupt(digitalPinToInterrupt(EPD_BUSY), onBusyFall, FALLING);
    attachInterrupt(digitalPinToInterrupt(EPD_BUSY), onBusyRise, RISING);

    Serial.printf("BUSY state at startup: %s\n",
                  digitalRead(EPD_BUSY) ? "HIGH (idle — correct)" : "LOW (unexpected)");

    Serial.println("\nInitialising display (GxEPD2)...");
    display.init(115200);
    display.setRotation(0);

    Serial.println("Triggering full refresh with white screen...");
    busy_fell = false;
    busy_rose = false;
    uint32_t t_start = millis();

    display.setFullWindow();
    display.firstPage();
    do { display.fillScreen(GxEPD_WHITE); } while (display.nextPage());

    uint32_t t_end = millis();

    Serial.printf("BUSY after refresh: %s\n",
                  digitalRead(EPD_BUSY) ? "HIGH (idle)" : "LOW (still busy?)");
    Serial.printf("Total refresh duration: %lu ms\n", t_end - t_start);

    Serial.println("\n--- Interrupt report ---");
    if (busy_fell) {
        Serial.printf("BUSY went LOW  at t+%lu ms  (display started refreshing)\n",
                      fell_ms - t_start);
    } else {
        Serial.println("BUSY never went LOW — pin not connected or wired incorrectly");
    }
    if (busy_rose) {
        Serial.printf("BUSY went HIGH at t+%lu ms  (display finished refreshing)\n",
                      rose_ms - t_start);
    } else {
        Serial.println("BUSY never went HIGH during refresh window");
    }

    if (busy_fell && busy_rose) {
        Serial.printf("\nSUCCESS: BUSY pin is working. Actual refresh time: %lu ms\n",
                      rose_ms - fell_ms);
        Serial.println("Set EPD_BUSY 12 in the dashboard firmware.");
    } else if (!busy_fell) {
        Serial.println("\nFAIL: BUSY pin did not go LOW. Check solder joint on GPIO 8.");
    } else {
        Serial.println("\nPARTIAL: BUSY went LOW but never HIGH — refresh may have timed out.");
    }

    Serial.println("========================================");
    display.hibernate();
}

void loop() {
    delay(1);
}
