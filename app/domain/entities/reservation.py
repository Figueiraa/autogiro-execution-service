import enum
from dataclasses import dataclass, field
from datetime import datetime


class ReservationStatus(str, enum.Enum):
    RESERVADA = "RESERVADA"
    LIBERADA = "LIBERADA"
    CONSUMIDA = "CONSUMIDA"


class MissingReason(str, enum.Enum):
    SEM_ESTOQUE = "SEM_ESTOQUE"
    NAO_ENCONTRADA = "NAO_ENCONTRADA"


@dataclass
class ReservationItem:
    part_id: int
    name: str
    quantity: int
    unit_price: float


@dataclass
class MissingPart:
    """Peça que impediu a reserva."""

    part_id: int
    requested: int
    available: int
    reason: MissingReason


@dataclass
class Reservation:
    """Peças separadas para uma ordem de serviço (uma reserva por OS).

    É tudo ou nada: se uma peça falta, nenhuma é reservada.
    """

    order_id: int
    saga_id: str
    items: list[ReservationItem] = field(default_factory=list)
    status: ReservationStatus = ReservationStatus.RESERVADA
    created_at: datetime | None = None
    updated_at: datetime | None = None
