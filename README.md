# SmartVision — Real-Time Smart Parking

SmartVision is a local Data Communication mini project. An OpenCV service reads a webcam, video file, or RTSP source (or generates simulation data), sends compact JSON slot updates and JPEG preview frames to FastAPI over HTTP, and the browser receives live status and events over WebSocket. SQLite stores slots, sessions, events, snapshots, camera heartbeats, and communication metrics.

## Architecture

```text
 Webcam / video / RTSP / simulator
          │ OpenCV: slot polygons, occupancy and confidence
          │ HTTP POST JSON + JPEG preview + heartbeat
          ▼
   FastAPI backend ───────── SQLite
          │ JSON over WebSocket (/ws)
          │ HTTP polling fallback (/api/*)
          ▼
   Vanilla HTML / CSS / JavaScript dashboard
```

## Run locally

Requirements: Python 3.10+, a modern browser, and (for camera processing) OpenCV-compatible camera or video input.

### Backend

From the project root:

```bash
cd backend
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

The first start creates `backend/smartvision.db`, adds any newly introduced SQLite columns, and seeds LOT-01 with twelve parking slots. API docs: `http://127.0.0.1:8000/docs`.

### Dashboard

In a second terminal from the project root:

```bash
cd frontend
python3 -m http.server 8080
```

Open `http://127.0.0.1:8080`. The static frontend uses `http://127.0.0.1:8000` and `/ws` by default. Configure `window.SMARTVISION_API` and `window.SMARTVISION_WS` before `app.js` to use another host.

### OpenCV vision service or simulator

In a third terminal:

```bash
cd vision
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python main.py
```

Simulation is enabled by default. For a camera, set `SIMULATION_MODE=false` and configure `CAMERA_SOURCE`:

```bash
SIMULATION_MODE=false CAMERA_SOURCE=0 python main.py
SIMULATION_MODE=false CAMERA_SOURCE=data/parking-lot.mp4 python main.py
SIMULATION_MODE=false CAMERA_SOURCE=rtsp://user:password@camera-host/stream python main.py
```

OpenCV analyzes the configured slot polygons and posts FREE/OCCUPIED/UNKNOWN status, confidence, and a downscaled JPEG preview. It retries camera connection after failures. To configure other layouts edit `data/parking_slots.json`. This baseline uses edge density inside each polygon; lighting, shadows, and camera angle require site calibration.

## Features

- Overview, camera, analytics, vehicle-session, and communication-monitor pages in the existing vanilla frontend.
- Clickable parking slots with type, status, confidence, camera, session, and timestamps.
- Deterministic best-slot scoring using availability, entrance distance, current lot congestion, slot preferences (including EV/accessibility), and an aisle-access exit estimate.
- Historical occupancy chart and short-term trend estimates, visibly labeled as estimates.
- Slot-by-hour utilization heatmap, session history, alerts, and real-time event stream.
- Webcam/video browser preview, OpenCV JPEG preview, and configured webcam/video/RTSP analysis.
- HTTP, WebSocket, heartbeat, latency, message count, data-rate, and reconnect monitoring.
- Demo controls for vehicle entry/exit, wrong parking, fill/clear lot, camera failure/recovery, and random occupancy changes.
- SQLite persistence and HTTP polling fallback when WebSocket reconnects.

## Communication design

1. **Camera acquisition:** `vision/main.py` opens the selected webcam, local video, or RTSP source with OpenCV. If no source is configured, the same service can run deterministic simulation updates.
2. **Vision processing:** each configured parking polygon is cropped and classified using edge density. The service posts slot ID, status, confidence, and timestamp; it does not perform license-plate recognition or vehicle identity tracking.
3. **HTTP client-server messages:** the vision service sends JSON to `POST /api/parking/update` and `POST /api/vision/heartbeat`. When camera input is active, it sends a resized JPEG preview to `POST /api/vision/frame`. The backend validates payloads, updates SQLite, and records communication metrics.
4. **WebSocket server push:** FastAPI broadcasts JSON events through `/ws`. A typical event is:

   ```json
   {"type":"PARKING_UPDATE","data":{"slot_id":"P06","status":"OCCUPIED","confidence":0.94,"timestamp":"2026-10-14T17:19:31Z"}}
   ```

5. **Heartbeat and failure handling:** the vision sender sends an ONLINE heartbeat every five seconds. If heartbeats exceed `HEARTBEAT_TIMEOUT` (default 20 seconds), the backend marks the camera OFFLINE and flags occupancy as stale. Last-known slot values remain visible as last-known data.
6. **Polling fallback:** the dashboard reconnects to WebSocket with exponential backoff. While disconnected it refreshes status, events, and analytics over HTTP every 15 seconds.
7. **Latency and monitoring:** the Communication Monitor measures backend HTTP request latency, counts HTTP/WebSocket messages, estimates bits per second over the most recent minute, and shows browser reconnect attempts and live connection uptime. Values are approximate and intended for a local classroom demonstration.
8. **Error handling:** invalid slot IDs or payloads return HTTP 422, an invalid configured vision key returns HTTP 401, camera read failures report OFFLINE and retry, and browser requests report backend connectivity state.

## API and WebSocket

| Method | Endpoint | Purpose |
|---|---|---|
| GET | `/api/health` | Backend health |
| GET | `/api/parking/status` | Summary, slots, and camera status |
| GET | `/api/parking/slots` | Slot detail data |
| POST | `/api/parking/update` | Vision slot states |
| GET | `/api/parking/analytics` | Occupancy history, session duration, utilization heatmap, usage counts |
| GET | `/api/parking/forecast` | Current occupancy and +30/+60/+90 minute estimates |
| GET | `/api/parking/events` | Recent slot/session events |
| GET | `/api/parking/sessions` | Anonymous parking sessions |
| GET | `/api/parking/recommendation` | Deterministic best available slot; optional `slot_type`, `ev_required`, `accessible` |
| GET | `/api/camera/status` | Camera source, heartbeat, stale-data state, demo mode |
| GET | `/api/camera/frame` | Latest OpenCV JPEG preview |
| GET | `/api/communication/status` | HTTP/WS state and communication metrics |
| POST | `/api/demo/vehicle-enter` | Simulated vehicle entry |
| POST | `/api/demo/vehicle-leave` | Simulated vehicle exit |
| POST | `/api/demo/wrong-parking` | Explicitly simulated wrong-parking event |
| POST | `/api/demo/fill-lot` / `/api/demo/clear-lot` | Fill or clear free/occupied slots |
| POST | `/api/demo/camera-failure` / `/api/demo/camera-recovery` | Simulate camera fault and recovery |
| POST | `/api/vision/heartbeat` | Vision-service heartbeat |
| POST | `/api/vision/frame` | JPEG preview upload |
| WS | `/ws` | Live JSON event channel (`/ws/parking` remains supported) |

If `VISION_API_KEY` is set on the backend, set the same value in the vision process; vision write endpoints require `X-Vision-API-Key`. Keep the demo API on a trusted local network.

## Demo sequence

1. Start the backend, dashboard server, and simulator in separate terminals.
2. Open the dashboard and show the DEMO MODE badge, parking layout, recommendation, forecast, and event stream.
3. Use Vehicle enters / Vehicle leaves and open Vehicle Sessions to show anonymous IDs and durations.
4. Use Wrong parking to demonstrate a simulated alert, then Camera failure and Camera recovery to show stale/online states.
5. Use Random occupancy simulation or Fill lot / Clear lot to populate the live stream, history, and heatmap.
6. Open Communication Monitor to show HTTP, WebSocket, latency, message, and data-rate counters.

## Limitations

- Edge-density classification is a simple OpenCV baseline and can misclassify scenes until its thresholds and polygons are calibrated. No YOLO model is included.
- The browser can preview a local webcam or video directly. RTSP is handled by the OpenCV vision service; browser live RTSP preview is not supported directly, so the service posts a downscaled JPEG preview to FastAPI.
- Vehicle counts are inferred from occupied slots. Session IDs are temporary anonymous identifiers; there is no plate recognition or persistent real-world vehicle identity.
- Wrong-parking events are represented as simulated alerts; automatic geometric wrong-parking detection is not implemented.
- Forecasts use a simple recent-trend estimate. Communication rate and latency are approximate local-demo measurements.
