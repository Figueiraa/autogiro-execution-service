from datetime import datetime

from pydantic import BaseModel, Field

from app.domain.entities.execution import Execution, ExecutionStatus
from app.domain.entities.part import Part


class PartCreate(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    unit_price: float = Field(ge=0)
    stock_quantity: int = Field(0, ge=0)
    unit: str = "un"
    description: str | None = None


class PartUpdate(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=255)
    unit_price: float | None = Field(None, ge=0)
    unit: str | None = None
    description: str | None = None


class StockAdjust(BaseModel):
    quantity: int = Field(description="Positivo para entrada, negativo para baixa manual")


class PartResponse(BaseModel):
    id: int
    name: str
    description: str | None
    unit: str
    unit_price: float
    stock_quantity: int
    reserved_quantity: int
    available: int

    @classmethod
    def from_domain(cls, part: Part) -> "PartResponse":
        return cls(
            id=part.id,
            name=part.name,
            description=part.description,
            unit=part.unit,
            unit_price=part.unit_price,
            stock_quantity=part.stock_quantity,
            reserved_quantity=part.reserved_quantity,
            available=part.available,
        )


class StepNote(BaseModel):
    note: str | None = Field(None, max_length=500, description="Observação do mecânico")


class CancelRequest(BaseModel):
    reason: str = Field(min_length=3, max_length=500, description="Por que a execução foi cancelada")


class TimelineResponse(BaseModel):
    status: ExecutionStatus
    at: datetime
    note: str | None


class ExecutionResponse(BaseModel):
    order_id: int
    status: ExecutionStatus
    vehicle: dict
    services: list[dict]
    parts: list[dict]
    notes: str | None
    cancel_reason: str | None
    queued_at: datetime
    timeline: list[TimelineResponse]

    @classmethod
    def from_domain(cls, execution: Execution) -> "ExecutionResponse":
        return cls(
            order_id=execution.order_id,
            status=execution.status,
            vehicle=execution.vehicle,
            services=execution.services,
            parts=execution.parts,
            notes=execution.notes,
            cancel_reason=execution.cancel_reason,
            queued_at=execution.queued_at,
            timeline=[TimelineResponse(status=t.status, at=t.at, note=t.note) for t in execution.timeline],
        )
