from datetime import datetime
from typing import Literal
from pydantic import BaseModel, Field


class SlotUpdate(BaseModel):
    slot_id: str
    status: Literal["FREE", "OCCUPIED", "UNKNOWN"]
    confidence: float = Field(ge=0, le=1)
    timestamp: datetime | None = None


class ParkingUpdate(BaseModel):
    parking_lot_id: str = "LOT-01"
    camera_id: str = "CAM-01"
    source: str = "simulation"
    slots: list[SlotUpdate] = Field(min_length=1, max_length=200)


class Heartbeat(BaseModel):
    camera_id: str = "CAM-01"
    timestamp: datetime | None = None
    status: Literal["ONLINE", "OFFLINE"] = "ONLINE"
    source: str = "simulation"


class CameraFrame(BaseModel):
    camera_id: str = "CAM-01"
    timestamp: datetime | None = None
    jpeg_base64: str = Field(min_length=1, max_length=2_000_000)


class AlertInput(BaseModel):
    type: Literal["WRONG_PARKING", "DOUBLE_PARKING", "CAMERA_OFFLINE"]
    message: str = Field(min_length=1, max_length=300)
    slot_id: str | None = None
