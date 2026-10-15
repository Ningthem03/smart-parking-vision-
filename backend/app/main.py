import asyncio
import base64
import binascii
import json
import uuid
import time
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from fastapi import Depends, FastAPI, Header, HTTPException, Response, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import select, desc, func
from sqlalchemy.orm import Session
from .config import settings
from .database import Base, SessionLocal, engine, get_db
from .hub import hub
from .models import Alert, Camera, CommunicationMetric, OccupancySnapshot, ParkingEvent, ParkingLot, ParkingSession, ParkingSlot, now
from .schemas import AlertInput, CameraFrame, Heartbeat, ParkingUpdate


def seed():
    db = SessionLocal()
    try:
        if not db.get(ParkingLot, "LOT-01"):
            db.add(ParkingLot(id="LOT-01", name="Campus Parking", location="Main campus"))
            layout_path = Path(__file__).resolve().parents[2] / "data" / "parking_slots.json"
            definitions = json.loads(layout_path.read_text())["slots"] if layout_path.exists() else []
            for i, definition in enumerate(definitions or [{} for _ in range(12)], 1):
                col, row = (i - 1) % 6, (i - 1) // 6
                x, y = 7 + col * 15.5, 16 + row * 47
                slot_id = definition.get("slot_id", f"P{i:02}")
                slot_type = definition.get("type", "EV" if i == 12 else "STANDARD")
                coordinates = definition.get("coordinates", [[x, y], [x + 12, y], [x + 12, y + 34], [x, y + 34]])
                db.add(ParkingSlot(id=slot_id, parking_lot_id="LOT-01", slot_number=i, type="STANDARD" if slot_type == "NORMAL" else slot_type,
                                   distance_from_entrance=round(float(definition.get("distance_from_entrance", 4 + (i - 1) * 2.5)), 1),
                                   coordinates=coordinates))
        if not db.get(Camera, "CAM-01"):
            db.add(Camera(id="CAM-01", name="Demo camera", source="simulation", status="OFFLINE"))
        db.commit()
    finally:
        db.close()


def migrate_columns():
    # create_all handles fresh installs; add new optional model fields for existing SQLite demos.
    if not settings.database_url.startswith("sqlite"):
        return
    from sqlalchemy import inspect, text
    inspector = inspect(engine)
    if "parking_slots" not in inspector.get_table_names():
        return
    existing = {c["name"] for c in inspector.get_columns("parking_slots")}
    additions = {"occupied_since": "DATETIME", "vehicle_detected": "BOOLEAN DEFAULT 0", "vehicle_type": "VARCHAR",
                 "distance_from_entrance": "FLOAT DEFAULT 0", "camera_id": "VARCHAR DEFAULT 'CAM-01'"}
    with engine.begin() as conn:
        for name, sql_type in additions.items():
            if name not in existing:
                conn.execute(text(f"ALTER TABLE parking_slots ADD COLUMN {name} {sql_type}"))
        existing_sessions = {c["name"] for c in inspector.get_columns("parking_sessions")}
        if "session_code" not in existing_sessions:
            conn.execute(text("ALTER TABLE parking_sessions ADD COLUMN session_code VARCHAR"))
        existing_events = {c["name"] for c in inspector.get_columns("parking_events")}
        if "origin" not in existing_events:
            conn.execute(text("ALTER TABLE parking_events ADD COLUMN origin VARCHAR DEFAULT 'LEGACY EVENT'"))
        if "communication_metrics" in inspector.get_table_names():
            existing_metrics = {c["name"] for c in inspector.get_columns("communication_metrics")}
            if "latency_ms" not in existing_metrics:
                conn.execute(text("ALTER TABLE communication_metrics ADD COLUMN latency_ms FLOAT"))
        conn.execute(text("UPDATE parking_slots SET type='STANDARD' WHERE type='NORMAL'"))
        conn.execute(text("UPDATE parking_slots SET distance_from_entrance=4+(slot_number-1)*2.5 WHERE distance_from_entrance=0"))
        conn.execute(text("UPDATE parking_slots SET vehicle_detected=1, occupied_since=last_updated WHERE status='OCCUPIED' AND occupied_since IS NULL"))


async def monitor_camera():
    while True:
        await asyncio.sleep(2)
        db = SessionLocal()
        try:
            camera = db.get(Camera, "CAM-01")
            if camera and camera.last_heartbeat:
                stamp = camera.last_heartbeat
                if stamp.tzinfo is None:
                    stamp = stamp.replace(tzinfo=timezone.utc)
                # A fresh explicit OFFLINE report stays offline until an ONLINE heartbeat arrives.
                timed_out = (datetime.now(timezone.utc) - stamp).total_seconds() > settings.heartbeat_timeout
                status = "OFFLINE" if timed_out else camera.status
                if camera.status != status:
                    camera.status = status
                    db.commit()
                    await emit("CAMERA_STATUS", {"camera_id": camera.id, "status": status, "timestamp": now().isoformat()})
                    await emit("CAMERA_ONLINE" if status == "ONLINE" else "CAMERA_OFFLINE", {"camera_id": camera.id, "status": status, "timestamp": now().isoformat(), "message": f"Camera {status.lower()} after heartbeat check"})
                    if status == "OFFLINE":
                        alert = Alert(type="CAMERA_OFFLINE", message="Camera heartbeat timed out; occupancy is stale.")
                        db.add(alert); db.commit()
                        await emit("ALERT", alert_json(alert))
        finally:
            db.close()


@asynccontextmanager
async def lifespan(app: FastAPI):
    Base.metadata.create_all(bind=engine)
    migrate_columns()
    seed()
    task = asyncio.create_task(monitor_camera())
    yield
    task.cancel()


app = FastAPI(title="SmartVision Parking API", version="1.0.0", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=settings.origins or ["*"], allow_credentials=True, allow_methods=["*"], allow_headers=["*"])

COMM = {"messages_received": 0, "messages_sent": 0, "bytes_sent": 0, "reconnect_attempts": 0, "connected_since": None, "last_latency_ms": None, "http_status": 200}
LAST_CAMERA_FRAME = {"jpeg": None, "timestamp": None, "camera_id": "CAM-01"}


def emit(event_type, data):
    COMM["messages_sent"] += 1
    COMM["bytes_sent"] += len(str(data).encode())
    db = SessionLocal()
    try:
        db.add(CommunicationMetric(direction="OUT", event_type=f"WS {event_type}", bytes_count=len(str(data).encode())))
        db.commit()
    finally:
        db.close()
    return hub.broadcast({"type": event_type, "data": data})


@app.middleware("http")
async def track_http_communication(request, call_next):
    started = time.perf_counter()
    response = await call_next(request)
    elapsed = round((time.perf_counter() - started) * 1000, 2)
    COMM["last_latency_ms"], COMM["http_status"] = elapsed, response.status_code
    db = SessionLocal()
    try:
        db.add(CommunicationMetric(direction="IN", event_type=f"HTTP {request.method} {request.url.path}", bytes_count=int(request.headers.get("content-length", 0))))
        db.add(CommunicationMetric(direction="OUT", event_type=f"HTTP {response.status_code} {request.url.path}", bytes_count=int(response.headers.get("content-length", 0)), latency_ms=elapsed))
        db.commit()
    finally:
        db.close()
    return response


def require_key(x_vision_api_key: str | None):
    if settings.vision_api_key and x_vision_api_key != settings.vision_api_key:
        raise HTTPException(status_code=401, detail="Invalid vision API key")


def slot_json(s, active_session=None):
    duration = None
    if active_session:
        duration = max(0, int((now() - active_session.entry_time.replace(tzinfo=timezone.utc)).total_seconds()))
    return {"id": s.id, "slot_number": s.slot_number, "type": "STANDARD" if s.type == "NORMAL" else s.type, "slot_type": "STANDARD" if s.type == "NORMAL" else s.type, "coordinates": s.coordinates,
            "status": s.status, "confidence": s.confidence, "last_updated": s.last_updated.isoformat(), "duration_seconds": duration,
            "occupied_since": s.occupied_since.isoformat() if s.occupied_since else None, "vehicle_detected": s.vehicle_detected,
            "vehicle_type": s.vehicle_type, "distance_from_entrance": s.distance_from_entrance, "camera_id": s.camera_id,
            "session_id": active_session.session_code if active_session else None}


def alert_json(a):
    origin = "SIMULATED EVENT" if a.message.lower().startswith("simulated") else "REPORTED EVENT"
    return {"id": a.id, "type": a.type, "message": a.message, "slot_id": a.slot_id, "timestamp": a.timestamp.isoformat(), "active": a.active, "origin": origin}


def snapshot(db):
    slots = db.scalars(select(ParkingSlot).where(ParkingSlot.parking_lot_id == "LOT-01")).all()
    occupied = sum(s.status == "OCCUPIED" for s in slots)
    free = sum(s.status == "FREE" for s in slots)
    pct = round(100 * occupied / len(slots), 1) if slots else 0
    snap = OccupancySnapshot(parking_lot_id="LOT-01", occupied_count=occupied, free_count=free, occupancy_percentage=pct)
    db.add(snap); db.commit()
    return {"occupied": occupied, "free": free, "total": len(slots), "occupancy_percentage": pct}


@app.get("/api/health")
def health():
    return {"status": "ok", "service": "smartvision-api", "timestamp": now().isoformat()}


@app.get("/api/parking/status")
def parking_status(db: Session = Depends(get_db)):
    slots = db.scalars(select(ParkingSlot).order_by(ParkingSlot.slot_number)).all()
    sessions = {s.slot_id: s for s in db.scalars(select(ParkingSession).where(ParkingSession.exit_time.is_(None))).all()}
    cam = db.get(Camera, "CAM-01")
    camera = {"status": cam.status if cam else "OFFLINE", "source": cam.source if cam else "simulation",
              "last_heartbeat": cam.last_heartbeat.isoformat() if cam and cam.last_heartbeat else None,
              "last_frame": LAST_CAMERA_FRAME["timestamp"],
              "demo_mode": bool(not cam or cam.source in ("simulation", "dashboard demo"))}
    return {**summary(slots), "slots": [slot_json(s, sessions.get(s.id)) for s in slots], "camera": camera, "timestamp": now().isoformat()}


def summary(slots):
    total = len(slots); occupied = sum(s.status == "OCCUPIED" for s in slots); free = sum(s.status == "FREE" for s in slots)
    return {"total": total, "occupied": occupied, "free": free, "unknown": total - occupied - free, "occupancy_percentage": round(occupied * 100 / total, 1) if total else 0}


@app.get("/api/parking/slots")
def parking_slots(db: Session = Depends(get_db)):
    return parking_status(db)["slots"]


@app.post("/api/parking/update")
async def parking_update(payload: ParkingUpdate, x_vision_api_key: str | None = Header(default=None), db: Session = Depends(get_db)):
    require_key(x_vision_api_key)
    COMM["messages_received"] += 1
    updates = []
    origin = "SIMULATED EVENT" if payload.source.lower() == "simulation" else "REAL VISION EVENT"
    for item in payload.slots:
        slot = db.get(ParkingSlot, item.slot_id)
        if not slot or slot.parking_lot_id != payload.parking_lot_id:
            raise HTTPException(status_code=422, detail=f"Unknown slot: {item.slot_id}")
        old = slot.status
        slot.status, slot.confidence, slot.last_updated = item.status, item.confidence, item.timestamp or now()
        slot.vehicle_detected = item.status == "OCCUPIED"
        if old != item.status and item.status != "UNKNOWN":
            event_type = "OCCUPIED" if item.status == "OCCUPIED" else "FREED"
            db.add(ParkingEvent(slot_id=slot.id, event_type=event_type, confidence=item.confidence, origin=origin))
            vehicle_event = "VEHICLE_ENTERED" if item.status == "OCCUPIED" else "VEHICLE_EXITED"
            db.add(ParkingEvent(slot_id=slot.id, event_type=vehicle_event, confidence=item.confidence, origin=origin))
            session_code = None
            if item.status == "OCCUPIED":
                slot.occupied_since = slot.last_updated
                session_code = f"V-{uuid.uuid4().hex[:6].upper()}"
                db.add(ParkingSession(slot_id=slot.id, session_code=session_code))
            else:
                slot.occupied_since = None
                session = db.scalar(select(ParkingSession).where(ParkingSession.slot_id == slot.id, ParkingSession.exit_time.is_(None)).order_by(desc(ParkingSession.entry_time)))
                if session:
                    session.exit_time = now()
                    entry = session.entry_time.replace(tzinfo=timezone.utc) if session.entry_time.tzinfo is None else session.entry_time
                    session.duration_seconds = max(0, int((session.exit_time - entry).total_seconds()))
            updates.append({"slot_id": slot.id, "status": item.status, "previous_status": old, "confidence": item.confidence, "timestamp": slot.last_updated.isoformat(), "session_id": session_code, "origin": origin, "event_type": "SLOT_OCCUPIED" if item.status == "OCCUPIED" else "SLOT_FREED", "vehicle_event": vehicle_event})
    db.commit()
    stats = snapshot(db) if updates else summary(db.scalars(select(ParkingSlot).where(ParkingSlot.parking_lot_id == payload.parking_lot_id)).all())
    for item in updates:
        await emit("PARKING_UPDATE", item)
        await emit(item["event_type"], {**item, "message": f"{item['slot_id']} → {item['status']}"})
        await emit(item["vehicle_event"], {**item, "message": f"Anonymous session {item.get('session_id') or 'closed'} · {item['slot_id']}"})
    if updates:
        await emit("STATISTICS_UPDATE", stats)
    return {"accepted": len(payload.slots), "changes": len(updates), "statistics": stats}


@app.post("/api/vision/heartbeat")
async def heartbeat(payload: Heartbeat, x_vision_api_key: str | None = Header(default=None), db: Session = Depends(get_db)):
    require_key(x_vision_api_key)
    COMM["messages_received"] += 1
    cam = db.get(Camera, payload.camera_id)
    if not cam:
        cam = Camera(id=payload.camera_id, name=payload.camera_id)
        db.add(cam)
    previous = cam.status
    cam.status, cam.source, cam.last_heartbeat = payload.status, payload.source, payload.timestamp or now()
    db.commit()
    data = {"camera_id": cam.id, "status": cam.status, "last_heartbeat": cam.last_heartbeat.isoformat()}
    await emit("HEARTBEAT_RECEIVED", {**data, "message": f"Camera heartbeat: {cam.status}"})
    if previous != cam.status:
        await emit("CAMERA_STATUS", data)
        await emit("CAMERA_ONLINE" if cam.status == "ONLINE" else "CAMERA_OFFLINE", {**data, "message": f"Camera {cam.status.lower()}"})
        if cam.status == "OFFLINE":
            alert = Alert(type="CAMERA_OFFLINE", message="Vision service reported camera offline.")
            db.add(alert); db.commit()
            await hub.broadcast({"type": "ALERT", "data": alert_json(alert)})
    return data


@app.post("/api/parking/alerts")
async def create_alert(payload: AlertInput, x_vision_api_key: str | None = Header(default=None), db: Session = Depends(get_db)):
    require_key(x_vision_api_key)
    if payload.slot_id and not db.get(ParkingSlot, payload.slot_id):
        raise HTTPException(status_code=422, detail=f"Unknown slot: {payload.slot_id}")
    alert = Alert(type=payload.type, message=payload.message, slot_id=payload.slot_id)
    db.add(alert); db.commit(); db.refresh(alert)
    await hub.broadcast({"type": "ALERT", "data": alert_json(alert)})
    if payload.type in ("DOUBLE_PARKING", "WRONG_PARKING") and payload.slot_id:
        db.add(ParkingEvent(slot_id=payload.slot_id, event_type=payload.type))
    db.commit()
    return alert_json(alert)


@app.get("/api/parking/recommendation")
def recommendation(ev_required: bool = False, accessible: bool = False, slot_type: str | None = None, db: Session = Depends(get_db)):
    slots = db.scalars(select(ParkingSlot).where(ParkingSlot.status == "FREE")).all()
    # Stable weighted score: distance (45%), lot congestion (20%), type fit (20%), aisle access (15%).
    slots = [s for s in slots if not ev_required or s.type == "EV"]
    slots = [s for s in slots if not accessible or s.type == "ACCESSIBLE"]
    normalized_type = "STANDARD" if slot_type and slot_type.upper() == "NORMAL" else slot_type.upper() if slot_type else None
    slots = [s for s in slots if not normalized_type or ("STANDARD" if s.type == "NORMAL" else s.type) == normalized_type]
    if not slots:
        return {"recommended_slot": None, "reason": "No confirmed free slots are available."}
    total = max(1, db.scalar(select(func.count(ParkingSlot.id))) or 1)
    congestion = 1 - (sum(s.status == "OCCUPIED" for s in db.scalars(select(ParkingSlot)).all()) / total)
    def score(s):
        distance = max(0, 1 - s.distance_from_entrance / 40)
        preference = 1 if normalized_type and ("STANDARD" if s.type == "NORMAL" else s.type) == normalized_type else .7 if s.type in ("NORMAL", "STANDARD") else .5
        # Outer columns (1 and 6) are closest to the aisle in the seeded layout.
        col = (s.slot_number - 1) % 6
        exit_convenience = 1 if col in (0, 5) else .65
        return .45 * distance + .20 * congestion + .20 * preference + .15 * exit_convenience
    chosen = max(slots, key=lambda s: (score(s), -s.slot_number))
    chosen_col = (chosen.slot_number - 1) % 6
    components = {"availability": 1.0, "distance": round(max(0, 1 - chosen.distance_from_entrance / 40), 3), "congestion": round(congestion, 3),
                  "preference": round(1 if normalized_type and ("STANDARD" if chosen.type == "NORMAL" else chosen.type) == normalized_type else .7 if chosen.type in ("NORMAL", "STANDARD") else .5, 3),
                  "exit_convenience": 1.0 if chosen_col in (0, 5) else .65}
    return {"recommended_slot": chosen.id, "reason": "Closest matching available slot with an aisle-access estimate.", "distance_m": chosen.distance_from_entrance, "score": round(score(chosen), 3), "score_components": components, "benefits": ["Closest available", "Low congestion", "Easy exit estimate"]}


@app.get("/api/parking/analytics")
def analytics(db: Session = Depends(get_db)):
    snaps = db.scalars(select(OccupancySnapshot).order_by(desc(OccupancySnapshot.timestamp)).limit(96)).all()[::-1]
    events = db.scalars(select(ParkingEvent)).all()
    sessions = db.scalars(select(ParkingSession)).all()
    completed = [s.duration_seconds for s in sessions if s.duration_seconds is not None]
    slots = db.scalars(select(ParkingSlot)).all()
    uses = {s.id: sum(e.slot_id == s.id and e.event_type == "OCCUPIED" for e in events) for s in slots}
    today = now().date()
    entered = sum(1 for s in sessions if s.entry_time.date() == today)
    exited = sum(1 for s in sessions if s.exit_time and s.exit_time.date() == today)
    peak = max(snaps, key=lambda s: s.occupancy_percentage, default=None)
    most_used = max(slots, key=lambda s: uses[s.id], default=None)
    most_occupied = max(slots, key=lambda s: (s.status == "OCCUPIED", uses[s.id]), default=None)
    hourly = {}
    for e in events:
        if e.event_type not in ("OCCUPIED", "FREED"):
            continue
        hourly.setdefault((e.slot_id, e.timestamp.hour), []).append(e.event_type == "OCCUPIED")
    heatmap = [{"slot_id": s.id, "hours": [{"hour": h, "occupancy": round(100 * sum(hourly.get((s.id, h), [])) / max(1, len(hourly.get((s.id, h), [])))) if hourly.get((s.id, h)) else 0} for h in range(24)]} for s in slots]
    return {"total_spaces": len(slots), "current_occupancy": summary(slots)["occupancy_percentage"], "vehicles_entered_today": entered, "vehicles_exited_today": exited,
            "peak_time": peak.timestamp.isoformat() if peak else None, "most_occupied_slot": most_occupied.id if most_occupied else None, "most_frequently_used_slot": most_used.id if most_used else None,
            "history": [{"time": s.timestamp.isoformat(), "occupancy": s.occupancy_percentage} for s in snaps], "heatmap": heatmap,
            "slot_utilization": [{"slot_id": s.id, "sessions": uses[s.id]} for s in slots],
            "average_duration_seconds": round(sum(completed) / len(completed)) if completed else 0,
            "average_occupancy": round(sum(s.occupancy_percentage for s in snaps) / len(snaps), 1) if snaps else 0,
            "peak_occupancy": max((s.occupancy_percentage for s in snaps), default=0)}


@app.get("/api/parking/prediction")
def prediction(db: Session = Depends(get_db)):
    snaps = db.scalars(select(OccupancySnapshot).order_by(desc(OccupancySnapshot.timestamp)).limit(24)).all()[::-1]
    current = snaps[-1].occupancy_percentage if snaps else 0
    if len(snaps) > 1:
        first = snaps[0].timestamp.replace(tzinfo=timezone.utc) if snaps[0].timestamp.tzinfo is None else snaps[0].timestamp
        last = snaps[-1].timestamp.replace(tzinfo=timezone.utc) if snaps[-1].timestamp.tzinfo is None else snaps[-1].timestamp
        elapsed_hours = max(1 / 60, (last - first).total_seconds() / 3600)
        trend_per_hour = max(-20, min(20, (snaps[-1].occupancy_percentage - snaps[0].occupancy_percentage) / elapsed_hours))
    else:
        trend_per_hour = 0
    return {"current": round(current), "forecast": [{"minutes": n, "occupancy": round(max(0,min(100,current+trend_per_hour*n/60)))} for n in (30,60,90)], "prediction_time": now().isoformat(), "message": "Estimate based on recent occupancy trend; not a guarantee."}


@app.get("/api/parking/forecast")
def forecast(db: Session = Depends(get_db)):
    return prediction(db)


@app.get("/api/parking/sessions")
def sessions(db: Session = Depends(get_db)):
    rows = db.scalars(select(ParkingSession).order_by(desc(ParkingSession.entry_time)).limit(50)).all()
    return [{"id": s.session_code or f"V-{s.id:04}", "slot_id": s.slot_id, "entry_time": s.entry_time.isoformat(),
             "exit_time": s.exit_time.isoformat() if s.exit_time else None, "duration_seconds": s.duration_seconds,
             "active": s.exit_time is None} for s in rows]


@app.get("/api/camera/status")
def camera_status(db: Session = Depends(get_db)):
    camera = db.get(Camera, "CAM-01")
    return {"camera_id": camera.id if camera else "CAM-01", "status": camera.status if camera else "OFFLINE", "source": camera.source if camera else "simulation",
            "last_heartbeat": camera.last_heartbeat.isoformat() if camera and camera.last_heartbeat else None,
            "last_frame": LAST_CAMERA_FRAME["timestamp"],
            "occupancy_stale": not camera or camera.status != "ONLINE", "demo_mode": bool(not camera or camera.source in ("simulation", "dashboard demo"))}


@app.post("/api/vision/frame")
async def vision_frame(payload: CameraFrame, x_vision_api_key: str | None = Header(default=None)):
    require_key(x_vision_api_key)
    try:
        frame = base64.b64decode(payload.jpeg_base64, validate=True)
    except (binascii.Error, ValueError):
        raise HTTPException(status_code=422, detail="jpeg_base64 must be valid base64")
    if not frame.startswith(b"\xff\xd8"):
        raise HTTPException(status_code=422, detail="Frame payload must be a JPEG image")
    LAST_CAMERA_FRAME.update(jpeg=frame, timestamp=(payload.timestamp or now()).isoformat(), camera_id=payload.camera_id)
    return {"accepted": True, "camera_id": payload.camera_id, "timestamp": LAST_CAMERA_FRAME["timestamp"]}


@app.get("/api/camera/frame")
def camera_frame():
    if not LAST_CAMERA_FRAME["jpeg"]:
        raise HTTPException(status_code=404, detail="No camera frame received")
    return Response(content=LAST_CAMERA_FRAME["jpeg"], media_type="image/jpeg", headers={"Cache-Control": "no-store", "X-Frame-Timestamp": LAST_CAMERA_FRAME["timestamp"] or ""})


@app.get("/api/communication/status")
def communication_status():
    db = SessionLocal()
    try:
        rows = db.scalars(select(CommunicationMetric).order_by(desc(CommunicationMetric.timestamp)).limit(2000)).all()
        latest_minute = [m for m in rows if (now() - (m.timestamp.replace(tzinfo=timezone.utc) if m.timestamp.tzinfo is None else m.timestamp)).total_seconds() <= 60]
        received = sum(m.direction == "IN" for m in rows)
        sent = sum(m.direction == "OUT" for m in rows)
        data_rate = sum(m.bytes_count for m in latest_minute)
        last_http_latency = next((m.latency_ms for m in rows if m.direction == "OUT" and m.latency_ms is not None), COMM["last_latency_ms"])
    finally:
        db.close()
    return {**COMM, "last_latency_ms": last_http_latency, "messages_received": received, "messages_sent": sent,
            "http_status": "ONLINE" if COMM["http_status"] < 500 else "ERROR",
            "websocket_clients": len(hub.clients), "websocket_status": "CONNECTED" if hub.clients else "WAITING",
            "uptime_seconds": round((now() - COMM["connected_since"]).total_seconds()) if COMM["connected_since"] else 0,
            "approx_data_rate_bps": round(data_rate * 8 / 60)}


async def demo_transition(slot, status, db, confidence=.97):
    old = slot.status
    if old == status:
        return
    slot.status, slot.confidence, slot.last_updated = status, confidence, now()
    slot.vehicle_detected = status == "OCCUPIED"
    if status == "OCCUPIED":
        slot.occupied_since = now()
        session_code = f"V-{uuid.uuid4().hex[:6].upper()}"
        session = ParkingSession(slot_id=slot.id, session_code=session_code)
        db.add(session)
        event = ParkingEvent(slot_id=slot.id, event_type="OCCUPIED", confidence=confidence)
        db.add(ParkingEvent(slot_id=slot.id, event_type="VEHICLE_ENTERED", confidence=confidence, origin="SIMULATED EVENT"))
    else:
        slot.occupied_since = None
        session = db.scalar(select(ParkingSession).where(ParkingSession.slot_id == slot.id, ParkingSession.exit_time.is_(None)).order_by(desc(ParkingSession.entry_time)))
        if session:
            session.exit_time = now()
            entry = session.entry_time.replace(tzinfo=timezone.utc) if session.entry_time.tzinfo is None else session.entry_time
            session.duration_seconds = max(0, int((session.exit_time - entry).total_seconds()))
        event = ParkingEvent(slot_id=slot.id, event_type="FREED", confidence=confidence)
        db.add(ParkingEvent(slot_id=slot.id, event_type="VEHICLE_EXITED", confidence=confidence, origin="SIMULATED EVENT"))
        session_code = session.session_code if session else None
    event.origin = "SIMULATED EVENT"
    db.add(event); db.commit()
    stats = snapshot(db)
    payload = {"slot_id": slot.id, "status": status, "previous_status": old, "confidence": confidence, "timestamp": slot.last_updated.isoformat(), "origin": "SIMULATED EVENT"}
    await emit("PARKING_UPDATE", payload)
    await emit("SLOT_OCCUPIED" if status == "OCCUPIED" else "SLOT_FREED", {**payload, "message": f"{slot.id} → {status}"})
    await emit("VEHICLE_ENTERED" if status == "OCCUPIED" else "VEHICLE_EXITED", {**payload, "session_id": session_code, "message": f"Anonymous session {session_code or 'closed'} · {slot.id}"})
    await emit("STATISTICS_UPDATE", stats)


@app.post("/api/demo/vehicle-enter")
async def demo_enter(db: Session = Depends(get_db)):
    slot = db.scalar(select(ParkingSlot).where(ParkingSlot.status == "FREE").order_by(ParkingSlot.distance_from_entrance, ParkingSlot.slot_number))
    if not slot: raise HTTPException(status_code=409, detail="No free slot")
    await demo_transition(slot, "OCCUPIED", db)
    return {"slot_id": slot.id, "status": slot.status}


@app.post("/api/demo/vehicle-leave")
async def demo_leave(db: Session = Depends(get_db)):
    slot = db.scalar(select(ParkingSlot).where(ParkingSlot.status == "OCCUPIED").order_by(ParkingSlot.slot_number))
    if not slot: raise HTTPException(status_code=409, detail="No occupied slot")
    await demo_transition(slot, "FREE", db)
    return {"slot_id": slot.id, "status": slot.status}


@app.post("/api/demo/fill-lot")
async def demo_fill(db: Session = Depends(get_db)):
    rows = db.scalars(select(ParkingSlot).where(ParkingSlot.status == "FREE")).all()
    for slot in rows: await demo_transition(slot, "OCCUPIED", db)
    return {"changed": len(rows)}


@app.post("/api/demo/clear-lot")
async def demo_clear(db: Session = Depends(get_db)):
    rows = db.scalars(select(ParkingSlot).where(ParkingSlot.status == "OCCUPIED")).all()
    for slot in rows: await demo_transition(slot, "FREE", db)
    return {"changed": len(rows)}


@app.post("/api/demo/camera-failure")
async def demo_camera_failure(db: Session = Depends(get_db)):
    return await heartbeat(Heartbeat(status="OFFLINE", source="simulation"), None, db)


@app.post("/api/demo/camera-recovery")
async def demo_camera_recovery(db: Session = Depends(get_db)):
    return await heartbeat(Heartbeat(status="ONLINE", source="simulation"), None, db)


@app.post("/api/demo/wrong-parking")
async def demo_wrong_parking(db: Session = Depends(get_db)):
    slot = db.scalar(select(ParkingSlot).where(ParkingSlot.status == "OCCUPIED").order_by(ParkingSlot.slot_number))
    if not slot: raise HTTPException(status_code=409, detail="Occupy a slot first")
    prior = db.scalar(select(func.count(ParkingEvent.id)).where(ParkingEvent.event_type == "WRONG_PARKING")) or 0
    scenario = prior % 4
    if scenario == 2 and db.get(ParkingSlot, "P12"):
        slot = db.get(ParkingSlot, "P12")
    if scenario == 0:
        detail = f"Vehicle is outside the marked area near {slot.id}."
    elif scenario == 1:
        detail = f"Vehicle occupies a restricted space near {slot.id} without authorization."
    elif scenario == 2:
        detail = f"Vehicle occupies EV space {slot.id} without charging permission."
    else:
        detail = f"Vehicle overlaps two adjacent parking slots beside {slot.id}."
    alert = Alert(type="WRONG_PARKING", message=f"Simulated event: {detail}", slot_id=slot.id)
    db.add(alert); db.add(ParkingEvent(slot_id=slot.id, event_type="WRONG_PARKING", origin="SIMULATED EVENT")); db.commit(); db.refresh(alert)
    await emit("WRONG_PARKING", {**alert_json(alert), "origin": "SIMULATED EVENT", "message": alert.message})
    return alert_json(alert)


@app.get("/api/parking/events")
def events(db: Session = Depends(get_db)):
    rows = db.scalars(select(ParkingEvent).order_by(desc(ParkingEvent.timestamp)).limit(40)).all()
    return [{"id": e.id, "slot_id": e.slot_id, "event_type": e.event_type, "timestamp": e.timestamp.isoformat(), "origin": e.origin} for e in rows]


@app.get("/api/parking/alerts")
def alerts(db: Session = Depends(get_db)):
    rows = db.scalars(select(Alert).where(Alert.active.is_(True)).order_by(desc(Alert.timestamp)).limit(30)).all()
    return [alert_json(a) for a in rows]


@app.get("/api/cameras")
def cameras(db: Session = Depends(get_db)):
    return [{"id": c.id, "name": c.name, "source": c.source, "status": c.status, "last_heartbeat": c.last_heartbeat.isoformat() if c.last_heartbeat else None} for c in db.scalars(select(Camera)).all()]


@app.websocket("/ws/parking")
async def websocket_endpoint(websocket: WebSocket):
    await hub.connect(websocket)
    COMM["connected_since"] = now()
    try:
        await websocket.send_json({"type": "CONNECTED", "data": {"timestamp": now().isoformat()}})
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        hub.disconnect(websocket)


app.add_api_websocket_route("/ws", websocket_endpoint)
