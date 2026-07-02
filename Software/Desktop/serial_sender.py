"""
serial_sender.py — USB serial transport for the e-paper dashboard.

Implements the framed protocol:
  PC → ESP32 :  [MAGIC 4B][W 2B][H 2B][LEN 4B][PAYLOAD][CRC32 4B]
  ESP32 → PC :  0x06 ACK | 0x15 NAK | 0x42 BTN

Only one object should be created per session; it owns the serial port for
the life of the program and runs a tiny background thread that drains
incoming bytes so BTN presses are never lost while the main thread sleeps.
"""

import binascii
import logging
import queue
import struct
import threading
import time

import serial
import serial.tools.list_ports

log = logging.getLogger(__name__)

# ── Protocol constants (must match epaper_dashboard.ino) ─────────────────────
MAGIC        = bytes([0xDE, 0xAD, 0xBE, 0xEF])
ACK_BYTE     = 0x06
NAK_BYTE     = 0x15
BTN_BYTE     = 0x42

# USB Vendor IDs used for auto-detection.
# 0x239A = Adafruit (bootloader / some boards)
# 0x303A = Espressif native USB CDC (ESP32-S3 with ARDUINO_USB_CDC_ON_BOOT=1)
_KNOWN_VIDS = {0x239A, 0x303A}

# Sent by the ESP32 every second while idle (S_SYNC), so the PC knows when
# setup() / display.begin() is done and it's safe to send a frame.
READY_BYTE = 0x01

# How long to wait for ACK/NAK after sending one full frame.
# Must comfortably exceed the panel's physical refresh time (~3 s) because
# the ESP32 sends ACK *before* calling display.display(), then blocks;
# any retry frame we send during that window will be buffered.
ACK_TIMEOUT_S = 30.0

MAX_RETRIES   = 3
PANEL_W       = 648
PANEL_H       = 480
SERIAL_BAUD   = 115200   # match SERIAL_BAUD in epaper_dashboard.ino


def find_port(override: str = "") -> str | None:
    """
    Return the serial port for the ESP32.
    If `override` is set (from config serial_port:) use it directly.
    Otherwise scan for any Adafruit device by USB Vendor ID.
    """
    if override:
        log.info("Using configured serial port: %s", override)
        return override
    for info in serial.tools.list_ports.comports():
        if info.vid in _KNOWN_VIDS:
            log.info("Auto-detected ESP32 on %s (VID=%04X PID=%04X)",
                     info.device, info.vid, info.pid or 0)
            return info.device
    return None


def _build_frame(payload: bytes) -> bytes:
    crc  = binascii.crc32(payload) & 0xFFFFFFFF
    hdr  = MAGIC + struct.pack("<HHI", PANEL_W, PANEL_H, len(payload))
    return hdr + payload + struct.pack("<I", crc)


class SerialSender:
    """
    Thread-safe serial transport.

    A background reader thread continuously drains the port's incoming buffer.
    BTN bytes are pushed onto _btn_queue; ACK/NAK bytes onto _resp_queue.
    The main thread calls send() (blocking) and poll_button() (non-blocking).
    """

    def __init__(self, port: str, baud: int = SERIAL_BAUD):
        self._ser = serial.Serial(port, baud, timeout=0.05)
        self._lock       = threading.Lock()   # serialises write() calls
        self._resp_queue = queue.Queue()      # ACK / NAK bytes
        self._btn_queue  = queue.Queue()      # BTN bytes
        self._rdy_queue  = queue.Queue()      # READY_BYTE idle beacons
        self._stop       = threading.Event()

        self._reader = threading.Thread(
            target=self._read_loop, daemon=True, name="serial-reader"
        )
        self._reader.start()

        # Wait for the ESP32 idle beacon (sent every 1 s while in S_SYNC).
        # This fires as soon as display.begin() finishes, regardless of how
        # long that takes, and also works if Python restarts after ESP32 is up.
        log.info("Waiting for ESP32 idle beacon (up to 30 s)…")
        try:
            self._rdy_queue.get(timeout=30.0)
            log.info("ESP32 ready")
        except queue.Empty:
            log.warning("No idle beacon received — ESP32 may not be running dashboard firmware")

        log.info("SerialSender connected on %s @ %d baud", port, baud)

    # ── Public API ────────────────────────────────────────────────────────────

    def send(self, img_bytes: bytes) -> bool:
        """
        Send a rendered 1-bit page image to the ESP32.

        Builds the framed protocol, writes it, then waits for ACK.
        Returns True on success, False after MAX_RETRIES failures.
        """
        if len(img_bytes) != PANEL_W * PANEL_H // 8:
            log.error("send(): wrong payload size %d (expected %d)",
                      len(img_bytes), PANEL_W * PANEL_H // 8)
            return False

        frame = _build_frame(img_bytes)

        for attempt in range(1, MAX_RETRIES + 1):
            # Wait for an idle beacon before sending — confirms ESP32 is in S_SYNC
            # and prevents interleaving frames when previous bytes are still in transit.
            try:
                beacon = self._rdy_queue.get(timeout=60.0)
                if beacon is None:
                    raise serial.SerialException("port disconnected")
            except queue.Empty:
                log.warning("Attempt %d/%d: no idle beacon from ESP32 (display still refreshing?)",
                            attempt, MAX_RETRIES)
            # Drain any extra accumulated beacons and stale ACK/NAK responses.
            while not self._rdy_queue.empty():
                self._rdy_queue.get_nowait()
            time.sleep(0.05)   # let any in-flight response arrive before draining
            self._drain_resp_queue()

            with self._lock:
                try:
                    self._ser.reset_output_buffer()  # discard any unsent bytes from prior attempt
                except Exception:
                    pass
                try:
                    self._ser.write(frame)
                    self._ser.flush()
                except serial.SerialException as exc:
                    log.warning("ESP32 disconnected during write: %s", exc)
                    raise

            try:
                resp = self._resp_queue.get(timeout=ACK_TIMEOUT_S)
                if resp is None:
                    raise serial.SerialException("port disconnected")
            except queue.Empty:
                beacons = 0
                while not self._rdy_queue.empty():
                    self._rdy_queue.get_nowait()
                    beacons += 1
                if beacons:
                    log.warning("Attempt %d/%d: timeout — ESP32 was idle the whole time (%d beacons); frame not reaching device",
                                attempt, MAX_RETRIES, beacons)
                else:
                    log.warning("Attempt %d/%d: timeout — ESP32 received frame but sent no response",
                                attempt, MAX_RETRIES)
                continue

            if resp == ACK_BYTE:
                log.debug("ACK received (attempt %d)", attempt)
                return True
            if resp == NAK_BYTE:
                log.warning("NAK received (attempt %d/%d), retrying", attempt, MAX_RETRIES)
                continue
            # BTN during a send is unexpected — the ESP32 only sends BTN in
            # SYNC state, but queue it anyway and keep waiting for ACK/NAK.
            if resp == BTN_BYTE:
                self._btn_queue.put(BTN_BYTE)
                attempt -= 1   # don't count this as a retry
                continue

        log.error("send(): max retries exhausted")
        return False

    def poll_button(self) -> bool:
        """Return True (and consume the event) if a BTN press is waiting."""
        try:
            self._btn_queue.get_nowait()
            return True
        except queue.Empty:
            return False

    def close(self):
        self._stop.set()
        self._reader.join(timeout=2)
        self._ser.close()

    # ── Background reader ─────────────────────────────────────────────────────

    def _read_loop(self):
        while not self._stop.is_set():
            try:
                data = self._ser.read(64)
            except serial.SerialException as exc:
                log.error("Serial read error: %s", exc)
                self._stop.set()
                # Unblock any send() waiting on these queues
                self._rdy_queue.put(None)
                self._resp_queue.put(None)
                return

            for b in data:
                if b == BTN_BYTE:
                    self._btn_queue.put(b)
                elif b in (ACK_BYTE, NAK_BYTE):
                    self._resp_queue.put(b)
                elif b == READY_BYTE:
                    self._rdy_queue.put(b)
                # else: ignore (debug output from the ESP32, etc.)

    def _drain_resp_queue(self):
        while True:
            try:
                self._resp_queue.get_nowait()
            except queue.Empty:
                break
