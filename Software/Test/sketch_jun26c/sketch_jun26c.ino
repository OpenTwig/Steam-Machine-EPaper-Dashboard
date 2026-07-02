#include "Adafruit_EPD.h"

#define EPD_DC      10
#define EPD_CS       9
#define SRAM_CS      6   
#define EPD_RESET   -1   
#define EPD_BUSY    12   // Your verified software/hardware pin

// Use the direct 648x480 engine
Adafruit_UC8179 display(648, 480, EPD_DC, EPD_RESET, EPD_CS, SRAM_CS, EPD_BUSY, &SPI);

void setup() {
  Serial.begin(115200);
  
  // Safe countdown for native USB port stability
  for(int i = 5; i > 0; i--) {
    Serial.printf("Stabilizing native USB connection... Booting in %d\n", i);
    delay(1000);
  }

  Serial.println("Configuring watchdog safety overrides...");
  disableCore0WDT();
  disableCore1WDT();

  Serial.println("Initializing memory controller with inverted busy logic...");
  display.begin(true); 
  
  display.clearBuffer();
  display.setRotation(0); // True Landscape mode
  
  // Draw a frame 10 pixels inside your true 648x480 glass boundaries
  display.drawRect(10, 10, 628, 460, EPD_BLACK);
  
  // Display text strings safely within the container box
  display.setTextSize(3);
  display.setTextColor(EPD_BLACK);
  
  display.setCursor(40, 50); 
  display.print("Steam Machine Dashboard");
  
  display.setCursor(40, 120);
  display.print("System Status: Operational");
  
  display.setCursor(40, 190);
  display.print("Hardware Handshake Fixed!");

  // --- LIVE BUSY PIN CHECK ---
  Serial.println("\n=========================================");
  Serial.println("         LIVE BUSY PIN MONITOR          ");
  Serial.println("=========================================");
  
  int stateBefore = digitalRead(EPD_BUSY);
  Serial.printf("BUSY state BEFORE display update: %d\n", stateBefore);
  
  Serial.println("Pushing graphic pixel matrix array to E-Ink screen...");
  display.display(); // This triggers the physical black/white flashing
  
  int stateDuring = digitalRead(EPD_BUSY);
  Serial.printf("BUSY state DURING physical flash cycle: %d\n", stateDuring);
  
  // Give the screen 5 seconds to finish moving its physical ink particles
  delay(5000); 
  
  int stateAfter = digitalRead(EPD_BUSY);
  Serial.printf("BUSY state AFTER refresh completes: %d\n", stateAfter);
  Serial.println("=========================================\n");
  
  enableCore0WDT();
  enableCore1WDT();
  Serial.println("Refresh sequence complete!");
}

void loop() {
  delay(1); 
}