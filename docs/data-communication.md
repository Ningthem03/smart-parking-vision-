# Data communication in SmartVision

## Architecture

```text
Camera / video / RTSP / simulator
  │ OpenCV classification
  │ HTTP + JSON slot states, heartbeat, and JPEG preview
  ▼
FastAPI backend ─── SQLite persistence
  │ WebSocket JSON events (/ws)
  │ HTTP REST state polling (/api/*) when WS is reconnecting
  ▼
Vanilla JavaScript dashboard
```

## Client-server flow

1. `vision/main.py` is the camera-side client. OpenCV reads a webcam, video file, or RTSP stream and evaluates the configured parking polygons. The simulator sends the same slot-state payload when no camera is available.
2. The vision client posts slot ID, status, confidence, and timestamp to `POST /api/parking/update`. It posts an ONLINE heartbeat every five seconds to `/api/vision/heartbeat`. For camera mode, it also sends a resized JPEG preview about once per second to `/api/vision/frame`.
3. HTTP runs over TCP/IP. FastAPI validates payloads, updates SQLite slot status, session/event history, occupancy snapshots, and communication metrics, then returns JSON and an HTTP status code.
4. FastAPI broadcasts changes to the dashboard over the persistent `/ws` WebSocket connection. A JSON event contains a type and data object, for example:

   ```json
   {"type":"PARKING_UPDATE","data":{"slot_id":"P06","status":"OCCUPIED","confidence":0.94,"timestamp":"2026-10-14T17:19:31Z"}}
   ```

5. WebSocket delivery is immediate after the database commit. When a connection drops, the dashboard reconnects with backoff and uses HTTP polling every 15 seconds to recover the latest state while disconnected.
6. The vision service retries failed camera opens. Heartbeats stop during a prolonged input or process failure; after `HEARTBEAT_TIMEOUT` (default 20 seconds), the backend marks the camera OFFLINE, raises an alert, and the UI labels the last-known occupancy stale. Recovery begins when an ONLINE heartbeat arrives.

## Protocol monitoring

The Communication Monitor reports HTTP status, last backend request latency, WebSocket connectivity, message counts, approximate bytes transferred in the last minute, last heartbeat, browser reconnect attempts, and browser connection uptime. HTTP and WebSocket message metrics are retained in SQLite. The byte rate and client-side reconnect count are approximate demo measurements.

Latency includes camera sampling, local OpenCV work, HTTP round trip, database writes, and WebSocket delivery. For a LAN demonstration, set the dashboard API URL and vision `BACKEND_URL` to the backend host IP, allow TCP port 8000, and keep the optional `VISION_API_KEY` shared only with the trusted client. A deployed system should use TLS (`https`/`wss`), stronger client authentication, and network access controls.
