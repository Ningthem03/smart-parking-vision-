from datetime import datetime, timezone
from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Integer, JSON, String
from sqlalchemy.orm import Mapped, mapped_column
from .database import Base


def now():
    return datetime.now(timezone.utc)


class ParkingLot(Base):
    __tablename__ = "parking_lots"
    id: Mapped[str] = mapped_column(String, primary_key=True)
    name: Mapped[str] = mapped_column(String, default="SmartVision Demo Lot")
    location: Mapped[str] = mapped_column(String, default="Campus")


class ParkingSlot(Base):
    __tablename__ = "parking_slots"
    id: Mapped[str] = mapped_column(String, primary_key=True)
    parking_lot_id: Mapped[str] = mapped_column(ForeignKey("parking_lots.id"))
    slot_number: Mapped[int] = mapped_column(Integer)
    type: Mapped[str] = mapped_column(String, default="STANDARD")
    coordinates: Mapped[list] = mapped_column(JSON, default=list)
    status: Mapped[str] = mapped_column(String, default="FREE")
    confidence: Mapped[float] = mapped_column(Float, default=0.0)
    last_updated: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    occupied_since: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    vehicle_detected: Mapped[bool] = mapped_column(Boolean, default=False)
    vehicle_type: Mapped[str | None] = mapped_column(String, nullable=True)
    distance_from_entrance: Mapped[float] = mapped_column(Float, default=0.0)
    camera_id: Mapped[str] = mapped_column(String, default="CAM-01")


class ParkingEvent(Base):
    __tablename__ = "parking_events"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    slot_id: Mapped[str] = mapped_column(ForeignKey("parking_slots.id"))
    event_type: Mapped[str] = mapped_column(String)
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    confidence: Mapped[float] = mapped_column(Float, default=0.0)
    origin: Mapped[str] = mapped_column(String, default="REPORTED EVENT")


class ParkingSession(Base):
    __tablename__ = "parking_sessions"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    slot_id: Mapped[str] = mapped_column(ForeignKey("parking_slots.id"))
    entry_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    exit_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    duration_seconds: Mapped[int | None] = mapped_column(Integer, nullable=True)
    session_code: Mapped[str | None] = mapped_column(String, nullable=True)


class Camera(Base):
    __tablename__ = "cameras"
    id: Mapped[str] = mapped_column(String, primary_key=True)
    name: Mapped[str] = mapped_column(String, default="Entrance camera")
    source: Mapped[str] = mapped_column(String, default="simulation")
    status: Mapped[str] = mapped_column(String, default="OFFLINE")
    last_heartbeat: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class OccupancySnapshot(Base):
    __tablename__ = "occupancy_snapshots"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    parking_lot_id: Mapped[str] = mapped_column(ForeignKey("parking_lots.id"))
    occupied_count: Mapped[int] = mapped_column(Integer)
    free_count: Mapped[int] = mapped_column(Integer)
    occupancy_percentage: Mapped[float] = mapped_column(Float)
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class Alert(Base):
    __tablename__ = "alerts"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    type: Mapped[str] = mapped_column(String)
    message: Mapped[str] = mapped_column(String)
    slot_id: Mapped[str | None] = mapped_column(String, nullable=True)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class CommunicationMetric(Base):
    __tablename__ = "communication_metrics"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    direction: Mapped[str] = mapped_column(String)
    event_type: Mapped[str] = mapped_column(String)
    bytes_count: Mapped[int] = mapped_column(Integer, default=0)
    latency_ms: Mapped[float | None] = mapped_column(Float, nullable=True)
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
