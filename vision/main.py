"""Edge vision sender: simulation by default, or OpenCV slot-region analysis."""
import json
import base64
import os
import random
import time
from pathlib import Path
from datetime import datetime, timezone

import cv2
import httpx
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
BACKEND = os.getenv("BACKEND_URL", "http://127.0.0.1:8000").rstrip("/")
FPS = max(1, int(os.getenv("VISION_FPS", "5")))
THRESHOLD = float(os.getenv("OCCUPANCY_THRESHOLD", "0.16"))
SIMULATION = os.getenv("SIMULATION_MODE", "true").lower() in ("1", "true", "yes")
SOURCE = os.getenv("CAMERA_SOURCE", "sample.mp4")
KEY = os.getenv("VISION_API_KEY", "")
CONFIG = json.loads((ROOT / "data" / "parking_slots.json").read_text())
HEADERS = {"X-Vision-API-Key": KEY} if KEY else {}


def post(path, payload):
    try:
        response = httpx.post(f"{BACKEND}{path}", json=payload, headers=HEADERS, timeout=3)
        response.raise_for_status()
        return True
    except httpx.HTTPError as exc:
        print(f"Backend communication failed ({path}): {exc}")
        return False


def send_heartbeat(status, source):
    return post("/api/vision/heartbeat", {"camera_id": CONFIG["camera_id"], "status": status,
                                           "source": source, "timestamp": datetime.now(timezone.utc).isoformat()})


def simulate():
    states = {slot["slot_id"]: "FREE" for slot in CONFIG["slots"]}
    states.update({"P02": "OCCUPIED", "P06": "OCCUPIED", "P09": "OCCUPIED", "P11": "OCCUPIED"})
    source = "simulation"
    print(f"Simulation publishing to {BACKEND}; Ctrl+C to stop")
    last_heartbeat = 0.0
    last_change = time.time()
    while True:
        current = time.time()
        if current - last_heartbeat >= 5:
            send_heartbeat("ONLINE", source)
            last_heartbeat = current
        if current - last_change >= 20:
            free = [key for key, value in states.items() if value == "FREE"]
            occupied = [key for key, value in states.items() if value == "OCCUPIED"]
            if free and occupied:
                states[random.choice(free)] = "OCCUPIED"
                states[random.choice(occupied)] = "FREE"
                last_change = current
        slots = [{"slot_id": sid, "status": status, "confidence": round(random.uniform(.86, .99), 2)} for sid, status in states.items()]
        post("/api/parking/update", {"parking_lot_id": CONFIG["parking_lot_id"], "camera_id": CONFIG["camera_id"], "source": source, "slots": slots})
        time.sleep(max(1 / FPS, 1))


def detect_frame(frame, definitions):
    height, width = frame.shape[:2]
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    results = []
    for slot in definitions:
        points = np.array([[int(x * width / 100), int(y * height / 100)] for x, y in slot["coordinates"]], dtype=np.int32)
        mask = np.zeros((height, width), dtype=np.uint8)
        cv2.fillPoly(mask, [points], 255)
        region = cv2.bitwise_and(gray, gray, mask=mask)
        pixels = gray[mask > 0]
        if pixels.size == 0:
            status, confidence = "UNKNOWN", 0.0
        else:
            # Edge density approximates texture/vehicle presence; tune per camera in config.
            edges = cv2.Canny(region, 55, 130)
            density = float(np.count_nonzero(edges[mask > 0]) / pixels.size)
            status = "OCCUPIED" if density >= THRESHOLD else "FREE"
            confidence = min(.99, max(.5, .5 + abs(density - THRESHOLD) * 2.2))
        results.append({"slot_id": slot["slot_id"], "status": status, "confidence": round(confidence, 2)})
    return results


def run_camera():
    source = int(SOURCE) if SOURCE.isdigit() else SOURCE
    if isinstance(source, str) and not Path(source).is_absolute():
        source = str((ROOT / "data" / source).resolve())
    delay = 1 / FPS
    last_heartbeat = 0.0
    last_frame_sent = 0.0
    while True:
        capture = cv2.VideoCapture(source)
        if not capture.isOpened():
            print(f"Camera/video unavailable: {SOURCE}. Retrying in 5 seconds")
            send_heartbeat("OFFLINE", str(SOURCE))
            capture.release()
            time.sleep(5)
            continue
        print(f"OpenCV camera active: {SOURCE}; JPEG preview is sent to {BACKEND}")
        retry = False
        try:
            while True:
                started = time.time()
                ok, frame = capture.read()
                if not ok:
                    if isinstance(source, str) and not source.lower().startswith("rtsp://"):
                        capture.set(cv2.CAP_PROP_POS_FRAMES, 0)
                        continue
                    send_heartbeat("OFFLINE", str(SOURCE))
                    retry = True
                    break
                current = time.time()
                if current - last_heartbeat >= 5:
                    send_heartbeat("ONLINE", str(SOURCE))
                    last_heartbeat = current
                slots = detect_frame(frame, CONFIG["slots"])
                post("/api/parking/update", {"parking_lot_id": CONFIG["parking_lot_id"], "camera_id": CONFIG["camera_id"], "source": str(SOURCE), "slots": slots})
                if current - last_frame_sent >= 1:
                    preview = frame
                    h, w = frame.shape[:2]
                    if w > 640:
                        preview = cv2.resize(frame, (640, round(h * 640 / w)))
                    ok, encoded = cv2.imencode(".jpg", preview, [cv2.IMWRITE_JPEG_QUALITY, 65])
                    if ok:
                        post("/api/vision/frame", {"camera_id": CONFIG["camera_id"], "timestamp": datetime.now(timezone.utc).isoformat(), "jpeg_base64": base64.b64encode(encoded).decode("ascii")})
                    last_frame_sent = current
                time.sleep(max(0, delay - (time.time() - started)))
        finally:
            capture.release()
        if retry:
            time.sleep(3)


if __name__ == "__main__":
    try:
        simulate() if SIMULATION else run_camera()
    except KeyboardInterrupt:
        print("Vision service stopped")
