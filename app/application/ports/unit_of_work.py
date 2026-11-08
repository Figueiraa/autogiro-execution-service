"""Portas de persistência do Execution Service.

Todas as operações de um caso de uso acontecem dentro de uma unidade de trabalho
(uma transação no MongoDB): estoque, reserva, execução, o evento a publicar
(outbox) e o registro da mensagem processada ficam valendo juntos ou nenhum.
"""

from collections.abc import Callable
from typing import Protocol

from app.application.messages import Envelope
from app.domain.entities.execution import Execution, ExecutionStatus
from app.domain.entities.part import Part
from app.domain.entities.reservation import Reservation, ReservationStatus


class PartRepositoryPort(Protocol):
    async def list(self) -> list[Part]: ...

    async def get(self, part_id: int) -> Part | None: ...

    async def add(self, part: Part) -> Part: ...

    async def update(self, part: Part) -> Part: ...

    async def delete(self, part_id: int) -> None: ...

    async def try_reserve(self, part_id: int, quantity: int) -> bool:
        """Reserva se ainda houver saldo; devolve False se outra OS levou antes."""
        ...

    async def release(self, part_id: int, quantity: int) -> None: ...

    async def consume(self, part_id: int, quantity: int) -> None:
        """Baixa do estoque o que estava reservado (peça usada no reparo)."""
        ...


class ReservationRepositoryPort(Protocol):
    async def get(self, order_id: int) -> Reservation | None: ...

    async def add(self, reservation: Reservation) -> None: ...

    async def set_status(self, order_id: int, status: ReservationStatus) -> None: ...


class ExecutionRepositoryPort(Protocol):
    async def get(self, order_id: int) -> Execution | None: ...

    async def add(self, execution: Execution) -> None: ...

    async def save(self, execution: Execution) -> None: ...

    async def list(self, status: ExecutionStatus | None = None) -> list[Execution]: ...

    async def count(self, status: ExecutionStatus) -> int: ...


class OutboxPort(Protocol):
    async def add(self, envelope: Envelope) -> None: ...


class InboxPort(Protocol):
    async def register(self, message_id: str) -> bool:
        """Registra a mensagem; devolve False se ela já tinha sido processada."""
        ...


class UnitOfWorkPort(Protocol):
    parts: PartRepositoryPort
    reservations: ReservationRepositoryPort
    executions: ExecutionRepositoryPort
    outbox: OutboxPort
    inbox: InboxPort

    async def __aenter__(self) -> "UnitOfWorkPort": ...

    async def __aexit__(self, exc_type, exc, tb) -> None: ...


UnitOfWorkFactory = Callable[[], UnitOfWorkPort]
