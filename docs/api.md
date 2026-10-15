# SmartVision API reference

Base URL: `http://127.0.0.1:8000`; interactive schema: `/docs`. HTTP uses JSON except `GET /api/camera/frame`, which returns `image/jpeg`.

## Read APIs

- `GET /api/health` — backend health and timestamp.
- `GET /api/parking/status` — occupancy summary, slots, camera state, and timestamp.
- `GET /api/parking/slots` — all slot details including type, polygon, confidence, occupied-since, vehicle-detected, entrance distance, camera, and active session.
- `GET /api/parking/analytics` — historical occupancy, average/peak occupancy, duration, entries/exits today, most-used slots, and slot-by-hour heatmap.
- `GET /api/parking/forecast` (`/api/parking/prediction` remains available) — current and +30/+60/+90 minute occupancy estimates.
- `GET /api/parking/events` — recent slot, vehicle, and wrong-parking events.
- `GET /api/parking/sessions` — anonymous session IDs, slot, entry, exit, duration, and active state.
- `GET /api/parking/recommendation` — deterministic best available slot. Optional query parameters: `slot_type=EV|ACCESSIBLE|STANDARD|RESERVED|BIKE`, `ev_required=true`, `accessible=true`.
- `GET /api/camera/status`, `GET /api/camera/frame` — source/heartbeat/stale state and latest OpenCV JPEG frame.
- `GET /api/communication/status` — HTTP/WebSocket status and persisted communication counters.
- `GET /api/parking/alerts`, `GET /api/cameras` — active alerts and configured camera list.

## Vision write APIs

Set `VISION_API_KEY` on the backend and vision client to require `X-Vision-API-Key` on vision writes.

`POST /api/parking/update`

```json
{
  "parking_lot_id": "LOT-01",
  "camera_id": "CAM-01",
  "slots": [{"slot_id":"P03","status":"OCCUPIED","confidence":0.94,"timestamp":"2026-10-14T10:00:00Z"}]
}
```

Status is `FREE`, `OCCUPIED`, or `UNKNOWN`; confidence is from 0 to 1. Unknown IDs return 422. A confirmed transition opens/closes an anonymous session and writes events and an occupancy snapshot.

- `POST /api/vision/heartbeat` accepts camera ID, `ONLINE`/`OFFLINE`, source, and optional timestamp.
- `POST /api/vision/frame` accepts camera ID, timestamp, and a base64 JPEG (`jpeg_base64`). Frames are limited to 2 MB encoded text.
- `POST /api/parking/alerts` accepts `WRONG_PARKING`, `DOUBLE_PARKING`, or `CAMERA_OFFLINE`, a message, and optional slot ID.

## Demo APIs

- `POST /api/demo/vehicle-enter`, `/api/demo/vehicle-leave`
- `POST /api/demo/wrong-parking`
- `POST /api/demo/fill-lot`, `/api/demo/clear-lot`
- `POST /api/demo/camera-failure`, `/api/demo/camera-recovery`

Wrong-parking demo alerts are labeled simulated; the project does not automatically classify them from vehicle geometry.

## WebSocket

Connect to `ws://127.0.0.1:8000/ws`; the legacy `/ws/parking` path remains supported. On connect, the server sends `CONNECTED`. State changes, sessions, camera status, alerts, heartbeat, and statistics are sent as `{ "type": "...", "data": { ... } }`. The dashboard reconnects with exponential backoff and polls REST state as fallback.
