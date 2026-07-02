// epaper_dashboard.ino
// ESP32-S3 Feather + Adafruit eInk Feather Friend  (5.83" 648×480 mono)
//
// The host PC renders every page as a 1-bit bitmap and streams it here over
// USB serial.  This firmware receives the frame, validates the CRC, ACKs it,
// and blits it to the panel.  When the user presses the physical button, a
// BTN byte is sent back to the PC so it can push the next page immediately.
//
// Refresh strategy:
//   - Partial refresh (~0.3 s, no flash) for same-page updates
//   - Full refresh   (~3-6 s, flashes)  every 10 partial refreshes,
//     or whenever the page changes (button press), to clear ghosting.
//
// ── Serial frame  (PC → ESP32) ──────────────────────────────────────────────
//   [MAGIC 4B = DE AD BE EF]
//   [WIDTH  2B uint16 LE ]
//   [HEIGHT 2B uint16 LE ]
//   [PAYLOAD_LEN 4B uint32 LE]
//   [PAYLOAD  PAYLOAD_LEN bytes, MSB-first 1-bit bitmap]
//   [CRC32   4B uint32 LE  of PAYLOAD only, IEEE-802.3 poly]
//
// ── Response bytes  (ESP32 → PC) ────────────────────────────────────────────
//   0x06  ACK  – frame accepted and displayed
//   0x15  NAK  – CRC mismatch; PC should retry
//   0x42  BTN  – button pressed; PC should push the next page
// ─────────────────────────────────────────────────────────────────────────────

#include <Arduino.h>
#include <Adafruit_NeoPixel.h>
#include <GxEPD2_BW.h>

// ── Upload indicator — increment FW_COLOR (0-7) each flash to confirm new code ──
//   0=Blue  1=Green  2=Red  3=Cyan  4=Magenta  5=Yellow  6=White  7=Orange
#define FW_COLOR 3

// ── Pinout (bench-tested — do NOT change) ─────────────────────────────────────
#define EPD_DC    10
#define EPD_CS     9
#define EPD_RESET -1
#define EPD_BUSY  -1

// ── NeoPixel ─────────────────────────────────────────────────────────────────
#define NEOPIXEL_PIN   33
#define NEOPIXEL_POWER 21

// ── Display ───────────────────────────────────────────────────────────────────
GxEPD2_BW<GxEPD2_583_GDEQ0583T31, GxEPD2_583_GDEQ0583T31::HEIGHT>
    display(GxEPD2_583_GDEQ0583T31(EPD_CS, EPD_DC, EPD_RESET, EPD_BUSY));

static Adafruit_NeoPixel pixel(1, NEOPIXEL_PIN, NEO_GRB + NEO_KHZ800);

static void neopixel_init() {
    pinMode(NEOPIXEL_POWER, OUTPUT);
    digitalWrite(NEOPIXEL_POWER, HIGH);
    pixel.begin();
    pixel.setBrightness(10);

    static const uint32_t COLORS[] = {
        0x0000FF,   // 0 Blue
        0x00FF00,   // 1 Green
        0xFF0000,   // 2 Red
        0x00FFFF,   // 3 Cyan
        0xFF00FF,   // 4 Magenta
        0xFFFF00,   // 5 Yellow
        0xFFFFFF,   // 6 White
        0xFF8C00,   // 7 Orange
    };
    pixel.setPixelColor(0, COLORS[FW_COLOR % 8]);
    pixel.show();
}

// ── User-configurable ─────────────────────────────────────────────────────────
#define BUTTON_PIN         8
#define SERIAL_BAUD       115200
#define COOLDOWN_MS       500      // min ms between panel refreshes
#define PARTIAL_MAX       10       // full refresh every N partial refreshes

// ── Protocol constants ────────────────────────────────────────────────────────
static const uint8_t MAGIC[4] = {0xDE, 0xAD, 0xBE, 0xEF};
#define ACK_BYTE   0x06
#define NAK_BYTE   0x15
#define BTN_BYTE   0x42
#define READY_BYTE 0x01

// ── Panel geometry ────────────────────────────────────────────────────────────
#define PANEL_W    648u
#define PANEL_H    480u
#define PAGE_BYTES (PANEL_W * PANEL_H / 8u)   // 38 880 bytes

// ── Frame buffer ──────────────────────────────────────────────────────────────
static uint8_t* frame_buf = nullptr;

// ── State ─────────────────────────────────────────────────────────────────────
static uint32_t last_display_ms = 0;
static uint32_t last_ready_ms   = 0;
static uint8_t  partial_count   = 0;   // resets to 0 on full refresh
static bool     page_changed    = false; // set by button press, cleared after next frame

// ─────────────────────────────────────────────────────────────────────────────
// CRC32  — IEEE 802.3 polynomial (matches Python's binascii.crc32)
// ─────────────────────────────────────────────────────────────────────────────
static uint32_t crc32_compute(const uint8_t* data, size_t len) {
    uint32_t c = 0xFFFFFFFFu;
    while (len--) {
        c ^= *data++;
        for (int k = 0; k < 8; k++)
            c = (c >> 1) ^ (0xEDB88320u & -(c & 1u));
    }
    return c ^ 0xFFFFFFFFu;
}

// ─────────────────────────────────────────────────────────────────────────────
// Display a frame — full or partial based on refresh strategy
// ─────────────────────────────────────────────────────────────────────────────
static void show_frame() {
    bool do_full = page_changed || (partial_count >= PARTIAL_MAX);
    page_changed = false;

    if (do_full) {
        partial_count = 0;
        display.setFullWindow();
    } else {
        partial_count++;
        display.setPartialWindow(0, 0, PANEL_W, PANEL_H);
    }

    // GxEPD2 page-by-page rendering — frame_buf holds the full bitmap
    // We use drawImage to blit the pre-rendered 1-bit buffer directly.
    display.firstPage();
    do {
        // GxEPD2 stores 1=white 0=black; our bitmap is 1=black 0=white (EPD convention).
        // drawBitmap with GxEPD_WHITE bg and GxEPD_BLACK fg handles the inversion.
        display.fillScreen(GxEPD_BLACK);
        display.drawBitmap(0, 0, frame_buf, PANEL_W, PANEL_H, GxEPD_WHITE);
    } while (display.nextPage());

    last_display_ms = millis();
    display.hibernate();
}

// ─────────────────────────────────────────────────────────────────────────────
// Serial receive state machine
// ─────────────────────────────────────────────────────────────────────────────
enum RxState : uint8_t { S_SYNC, S_HEADER, S_PAYLOAD, S_CHKSUM };

static RxState  rx_state  = S_SYNC;
static uint8_t  sync_pos  = 0;
static uint8_t  hdr_buf[8];
static uint8_t  hdr_pos   = 0;
static uint32_t rx_len    = 0;
static uint32_t rx_pos    = 0;
static uint8_t  crc_buf[4];
static uint8_t  crc_pos   = 0;

static void reset_rx() {
    rx_state = S_SYNC;
    sync_pos = 0;
}

static void process_byte(uint8_t b) {
    switch (rx_state) {

    case S_SYNC:
        if (b == MAGIC[sync_pos]) {
            if (++sync_pos == 4) {
                sync_pos = 0;
                hdr_pos  = 0;
                rx_state = S_HEADER;
            }
        } else {
            sync_pos = (b == MAGIC[0]) ? 1 : 0;
        }
        break;

    case S_HEADER:
        hdr_buf[hdr_pos++] = b;
        if (hdr_pos < 8) break;
        {
            uint16_t w  = (uint16_t)hdr_buf[0] | ((uint16_t)hdr_buf[1] << 8);
            uint16_t h  = (uint16_t)hdr_buf[2] | ((uint16_t)hdr_buf[3] << 8);
            uint32_t pl = (uint32_t)hdr_buf[4]
                        | ((uint32_t)hdr_buf[5] << 8)
                        | ((uint32_t)hdr_buf[6] << 16)
                        | ((uint32_t)hdr_buf[7] << 24);

            if (w != PANEL_W || h != PANEL_H || pl != PAGE_BYTES) {
                Serial.write(NAK_BYTE);
                reset_rx();
                break;
            }
            rx_len   = pl;
            rx_pos   = 0;
            rx_state = S_PAYLOAD;
        }
        break;

    case S_CHKSUM:
        crc_buf[crc_pos++] = b;
        if (crc_pos < 4) break;
        {
            uint32_t rx_crc = (uint32_t)crc_buf[0]
                            | ((uint32_t)crc_buf[1] << 8)
                            | ((uint32_t)crc_buf[2] << 16)
                            | ((uint32_t)crc_buf[3] << 24);
            uint32_t calc   = crc32_compute(frame_buf, rx_len);

            if (calc == rx_crc) {
                Serial.write(ACK_BYTE);
                Serial.flush();
                show_frame();
            } else {
                Serial.write(NAK_BYTE);
            }
        }
        reset_rx();
        break;

    default:
        reset_rx();
        break;
    }
}

// ─────────────────────────────────────────────────────────────────────────────
// Button handling
// ─────────────────────────────────────────────────────────────────────────────
static bool     btn_last    = HIGH;
static uint32_t btn_edge_ms = 0;
static bool     btn_held    = false;
static bool     btn_pending = false;

static void handle_button() {
    bool now = digitalRead(BUTTON_PIN);

    if (now != btn_last) {
        btn_edge_ms = millis();
        btn_last    = now;
        return;
    }
    if (millis() - btn_edge_ms < 50) return;

    if (now == LOW && !btn_held) {
        btn_held    = true;
        btn_pending = true;
    }
    if (now == HIGH) {
        btn_held = false;
    }

    if (btn_pending
        && rx_state == S_SYNC
        && millis() - last_display_ms >= COOLDOWN_MS) {
        page_changed = true;   // next frame will full-refresh
        Serial.write(BTN_BYTE);
        btn_pending = false;
    }
}

// ─────────────────────────────────────────────────────────────────────────────
// setup / loop
// ─────────────────────────────────────────────────────────────────────────────
void setup() {
    neopixel_init();
    Serial.setRxBufferSize(4096);
    Serial.begin(SERIAL_BAUD);
    Serial.setTimeout(0);

    pinMode(BUTTON_PIN, INPUT_PULLUP);

    frame_buf = (uint8_t*)malloc(PAGE_BYTES);
    if (frame_buf) {
        memset(frame_buf, 0xFF, PAGE_BYTES);
    }

    display.init(SERIAL_BAUD);
    display.setRotation(0);

    // Initial full refresh to clear the panel
    display.setFullWindow();
    display.firstPage();
    do { display.fillScreen(GxEPD_WHITE); } while (display.nextPage());

    while (Serial.available()) Serial.read();
}

void loop() {
    while (Serial.available()) {
        if (rx_state == S_PAYLOAD) {
            int      avail = Serial.available();
            uint32_t need  = rx_len - rx_pos;
            uint32_t take  = ((uint32_t)avail < need) ? (uint32_t)avail : need;
            Serial.readBytes(frame_buf + rx_pos, (size_t)take);
            rx_pos += take;
            if (rx_pos == rx_len) {
                crc_pos  = 0;
                rx_state = S_CHKSUM;
            }
        } else {
            process_byte((uint8_t)Serial.read());
        }
    }

    if (rx_state == S_SYNC && millis() - last_ready_ms >= 1000) {
        Serial.write(READY_BYTE);
        last_ready_ms = millis();
    }

    handle_button();
    delay(1);
}
