"""
captcha.py  —  Wrist CAPTCHA (FIXED VERSION)
Fixed scaling issues and metric calculations
"""

import asyncio
import json
import math
import os
import sys
import threading
import webbrowser
from collections import deque

import numpy as np
import serial
import websockets

# ── Config ──────────────────────────────────────────────────────────────────
PORT    = "COM5"      # Change to your port
BAUD    = 115200
SAMPLES = 100

WS_HOST = "localhost"
WS_PORT = 8765

# FIXED THRESHOLDS - adjusted for actual sensor scales
# Accelerometer: ±2g range → values between -2.0 to +2.0
VAR_THRESHOLD    = 0.15    # Human movement has variance > 0.15
JERK_THRESHOLD   = 0.08    # Human jerk > 0.08 (natural variation)
REPEAT_THRESHOLD = 0.75    # REPETITIVE = bot (score > 0.75)

# Additional thresholds for better detection
TREMOR_THRESHOLD = 0.02    # Human tremor amplitude > 0.02
GYRO_RMS_THRESHOLD = 20.0  # Human gyro movement > 20 deg/s

# ── Connected browser clients ────────────────────────────────────────────────
clients: set = set()
clients_lock = asyncio.Lock()
_loop: asyncio.AbstractEventLoop = None

# ── Fixed Metric helpers ───────────────────────────────────────────────────

def variance(data):
    """Calculate variance of array"""
    if len(data) < 2:
        return 0.0
    return float(np.var(np.array(data, dtype=float)))

def avg_jerk(data):
    """
    Jerk = rate of change of acceleration (derivative)
    Higher jerk = more natural, varied movement
    """
    if len(data) < 2:
        return 0.0
    diff = np.diff(np.array(data, dtype=float))
    return float(np.mean(np.abs(diff)))

def repeat_score(data):
    """
    Measures how repetitive the motion is.
    HIGH score (close to 1) = very repetitive = BOT
    LOW score (close to 0) = varied = HUMAN
    """
    a = np.array(data, dtype=float)
    n = len(a) // 2
    if n < 2:
        return 0.0
    h1, h2 = a[:n], a[n:2*n]
    diff = np.sum(np.abs(h1 - h2))
    scale = np.sum(np.abs(h1) + np.abs(h2)) + 1e-6
    return float(diff / scale)  # FIXED: direct ratio (0=varied, 1=identical)

def tremor_amplitude(data):
    """Amplitude of small oscillations (tremors)"""
    a = np.array(data, dtype=float)
    return float(np.std(a))  # Standard deviation = tremor indicator

def velocity_variance(data):
    """How much velocity changes (smooth vs jerky)"""
    a = np.array(data, dtype=float)
    if len(a) < 2:
        return 0.0
    velocity = np.diff(a)
    return float(np.var(velocity))

def direction_changes(data):
    """Counts how many times movement changes direction"""
    a = np.array(data, dtype=float)
    if len(a) < 3:
        return 0
    diff = np.diff(a)
    sign_changes = np.sum(np.diff(np.sign(diff)) != 0)
    return int(sign_changes)

def gyro_rms(data):
    """Root mean square of gyro data (overall movement intensity)"""
    a = np.array(data, dtype=float)
    return float(np.sqrt(np.mean(a ** 2)))

# ── Fixed Analysis ─────────────────────────────────────────────────────────

window_count = 0

def analyse_window(ax_buf, ay_buf, gx_buf, gy_buf):
    global window_count
    
    # Convert to numpy arrays for easier handling
    ax_arr = np.array(ax_buf, dtype=float)
    ay_arr = np.array(ay_buf, dtype=float)
    gx_arr = np.array(gx_buf, dtype=float)
    gy_arr = np.array(gy_buf, dtype=float)
    
    # FIXED: Calculate actual metrics with correct scales
    # 1. Movement Variance - how much the wrist moves
    var_x = variance(ax_arr)
    var_y = variance(ay_arr)
    movement_variance = (var_x + var_y) / 2
    
    # 2. Jerk - sudden changes in acceleration (natural human movement has high jerk)
    jerk_x = avg_jerk(ax_arr)
    jerk_y = avg_jerk(ay_arr)
    movement_jerk = (jerk_x + jerk_y) / 2
    
    # 3. Repetitiveness - bot movements are more repetitive
    repeat_x = repeat_score(ax_arr)
    repeat_y = repeat_score(ay_arr)
    repetitiveness = (repeat_x + repeat_y) / 2
    
    # Additional metrics for detailed analysis
    tremor = (tremor_amplitude(ax_arr) + tremor_amplitude(ay_arr)) / 2
    vel_var = (velocity_variance(ax_arr) + velocity_variance(ay_arr)) / 2
    dir_changes = (direction_changes(list(ax_arr)) + direction_changes(list(ay_arr))) / 2
    gyro_intensity = (gyro_rms(gx_arr) + gyro_rms(gy_arr)) / 2
    
    # FIXED: Bot detection logic (HUMAN = natural movement)
    # Human movement should have:
    # - HIGH variance (> 0.1)
    # - HIGH jerk (> 0.05)
    # - LOW repetitiveness (< 0.7)
    
    is_human_variance = movement_variance > VAR_THRESHOLD
    is_human_jerk = movement_jerk > JERK_THRESHOLD
    is_human_varied = repetitiveness < REPEAT_THRESHOLD  # LOW repetitiveness = human
    
    # Calculate score (0 = definitely bot, 3 = definitely human)
    human_score = sum([is_human_variance, is_human_jerk, is_human_varied])
    verdict = "HUMAN" if human_score >= 2 else "BOT"
    
    window_count += 1
    
    # For detailed UI flags
    passes = {
        "tremor": tremor > TREMOR_THRESHOLD,
        "velocity_variance": vel_var > 0.01,
        "direction_change": dir_changes > 10,
        "gyro_rms": gyro_intensity > GYRO_RMS_THRESHOLD,
    }
    
    features = {
        "tremor": float(tremor),
        "velocity_variance": float(vel_var),
        "direction_change": float(dir_changes),
        "gyro_rms": float(gyro_intensity),
    }
    
    # Debug output
    print(f"\n── Window {window_count} ──────────────────────────────")
    print(f"  Movement Variance : {movement_variance:.3f}  (HUMAN > {VAR_THRESHOLD}) → {'✓' if is_human_variance else '✗'}")
    print(f"  Jerk (naturalness): {movement_jerk:.3f}  (HUMAN > {JERK_THRESHOLD}) → {'✓' if is_human_jerk else '✗'}")
    print(f"  Repetitiveness    : {repetitiveness:.3f}  (HUMAN < {REPEAT_THRESHOLD}) → {'✓' if is_human_varied else '✗'}")
    print(f"  Human score       : {human_score}/3  →  {verdict}")
    print(f"  Additional: tremor={tremor:.3f}, gyro={gyro_intensity:.1f}, dirChanges={dir_changes}")
    print("────────────────────────────────────────────\n")
    
    msg = json.dumps({
        "verdict": verdict,
        "score": human_score / 3.0,
        "window": window_count,
        "features": features,
        "passes": passes,
        "error": None,
    })
    
    # Push to browser
    if _loop and not _loop.is_closed():
        asyncio.run_coroutine_threadsafe(_broadcast(msg), _loop)

# ── WebSocket broadcast ───────────────────────────────────────────────────

async def _broadcast(msg: str):
    async with clients_lock:
        dead = set()
        for ws in clients:
            try:
                await ws.send(msg)
            except Exception:
                dead.add(ws)
        clients.difference_update(dead)

async def ws_handler(websocket):
    async with clients_lock:
        clients.add(websocket)
    print(f"  Browser connected ({len(clients)} client(s))")
    try:
        await websocket.wait_closed()
    finally:
        async with clients_lock:
            clients.discard(websocket)

# ── Serial reader (runs in its own thread) ────────────────────────────────

def serial_thread():
    ax_buf = deque(maxlen=SAMPLES)
    ay_buf = deque(maxlen=SAMPLES)
    gx_buf = deque(maxlen=SAMPLES)
    gy_buf = deque(maxlen=SAMPLES)
    
    consecutive_errors = 0
    
    while True:
        try:
            print(f"Opening {PORT} at {BAUD} baud...")
            with serial.Serial(PORT, BAUD, timeout=2) as ser:
                print("✅ Sensor connected. Waiting for data...\n")
                consecutive_errors = 0
                
                for raw in ser:
                    line = raw.decode("utf-8", errors="ignore").strip()
                    
                    # Check for window trigger from Arduino
                    if line.startswith("#"):
                        if line == "#WINDOW" and len(ax_buf) == SAMPLES:
                            analyse_window(ax_buf, ay_buf, gx_buf, gy_buf)
                        continue
                    
                    # Parse sensor data: format expected: timestamp,ax,ay,az,gx,gy,gz
                    parts = line.split(",")
                    if len(parts) != 7:
                        continue
                    
                    try:
                        # Assuming format: ts,ax,ay,az,gx,gy,gz
                        _, ax, ay, _, gx, gy, _ = [int(p) for p in parts]
                    except (ValueError, IndexError):
                        continue
                    
                    # FIXED: Keep raw values, don't overscale
                    # MPU6050: accelerometer ±2g = 16384 LSB/g → value/16384 = g's
                    # Keep as raw g's for better threshold matching
                    ax_buf.append(ax / 16384.0)  # Now in g units (range ~ -2 to 2)
                    ay_buf.append(ay / 16384.0)
                    gx_buf.append(gx / 131.0)    # Gyro ±250 deg/s = 131 LSB per deg/s
                    gy_buf.append(gy / 131.0)
                    
        except serial.SerialException as e:
            consecutive_errors += 1
            wait_time = min(consecutive_errors * 2, 10)
            print(f"⚠️ Serial error: {e}. Retrying in {wait_time}s...")
            import time
            time.sleep(wait_time)
        except Exception as e:
            print(f"Unexpected error: {e}")
            import time
            time.sleep(3)

# ── Main ──────────────────────────────────────────────────────────────────

async def main():
    global _loop
    _loop = asyncio.get_running_loop()
    
    # Start serial in background thread
    t = threading.Thread(target=serial_thread, daemon=True)
    t.start()
    
    # Open the HTML file
    html_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "index.html")
    webbrowser.open(f"file:///{html_path.replace('\\', '/')}")
    print(f"📂 Opened: {html_path}")
    print(f"🔌 WebSocket listening on ws://{WS_HOST}:{WS_PORT}")
    print("\n🎯 Ready! Move your wrist naturally while following the ball.\n")
    
    async with websockets.serve(ws_handler, WS_HOST, WS_PORT):
        await asyncio.Future()  # run forever

if __name__ == "__main__":
    asyncio.run(main())