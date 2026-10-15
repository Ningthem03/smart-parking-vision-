from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app import main
from app.database import Base, get_db
from app.models import ParkingEvent, ParkingSession


def test_parking_updates_sessions_and_recommendation(monkeypatch):
    test_engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    TestSession = sessionmaker(bind=test_engine, expire_on_commit=False)
    monkeypatch.setattr(main, "engine", test_engine)
    monkeypatch.setattr(main, "SessionLocal", TestSession)

    def override_db():
        db = TestSession()
        try:
            yield db
        finally:
            db.close()

    main.app.dependency_overrides[get_db] = override_db
    try:
        with TestClient(main.app) as client:
            initial = client.get("/api/parking/status").json()
            assert initial["total"] == 12
            response = client.post("/api/parking/update", json={"parking_lot_id": "LOT-01", "slots": [
                {"slot_id": "P01", "status": "OCCUPIED", "confidence": .95}]})
            assert response.status_code == 200
            assert response.json()["changes"] == 1
            db = TestSession()
            try:
                assert db.scalar(select(ParkingEvent).where(ParkingEvent.slot_id == "P01")).event_type == "OCCUPIED"
                session = db.scalar(select(ParkingSession).where(ParkingSession.slot_id == "P01"))
                assert session and session.exit_time is None
            finally:
                db.close()
            assert client.get("/api/parking/recommendation").json()["recommended_slot"] == "P02"
            response = client.post("/api/parking/update", json={"parking_lot_id": "LOT-01", "slots": [
                {"slot_id": "P01", "status": "FREE", "confidence": .93}]})
            assert response.status_code == 200
            db = TestSession()
            try:
                session = db.scalar(select(ParkingSession).where(ParkingSession.slot_id == "P01"))
                assert session and session.exit_time and session.duration_seconds is not None
            finally:
                db.close()
    finally:
        main.app.dependency_overrides.clear()
        Base.metadata.drop_all(test_engine)
        test_engine.dispose()


def test_payload_validation_and_camera_heartbeat(monkeypatch):
    test_engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    TestSession = sessionmaker(bind=test_engine, expire_on_commit=False)
    monkeypatch.setattr(main, "engine", test_engine)
    monkeypatch.setattr(main, "SessionLocal", TestSession)

    def override_db():
        db = TestSession()
        try:
            yield db
        finally:
            db.close()

    main.app.dependency_overrides[get_db] = override_db
    try:
        with TestClient(main.app) as client:
            invalid = client.post("/api/parking/update", json={"slots": [{"slot_id": "P01", "status": "PARKED", "confidence": 2}]})
            assert invalid.status_code == 422
            camera = client.post("/api/vision/heartbeat", json={"camera_id": "CAM-01", "status": "OFFLINE", "source": "test"})
            assert camera.status_code == 200
            assert camera.json()["status"] == "OFFLINE"
            with client.websocket_connect("/ws/parking") as socket:
                assert socket.receive_json()["type"] == "CONNECTED"
                changed = client.post("/api/parking/update", json={"parking_lot_id": "LOT-01", "slots": [
                    {"slot_id": "P01", "status": "OCCUPIED", "confidence": .9}]})
                assert changed.status_code == 200
                assert socket.receive_json()["type"] == "PARKING_UPDATE"
    finally:
        main.app.dependency_overrides.clear()
        Base.metadata.drop_all(test_engine)
        test_engine.dispose()
