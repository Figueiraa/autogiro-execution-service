"""Andamento da execução pelo mecânico: diagnóstico, reparo, finalização e cancelamento.

Cada mudança publica um evento para o orquestrador (pelo outbox, na mesma
transação). Ao finalizar, as peças reservadas são baixadas do estoque.
"""

from app.application.messages import Envelope
from app.application.ports.unit_of_work import UnitOfWorkFactory
from app.domain.entities.execution import Execution, ExecutionStatus
from app.domain.entities.reservation import ReservationStatus
from app.domain.exceptions import NotFoundError


class ExecutionUseCases:
    def __init__(self, uow_factory: UnitOfWorkFactory):
        self._uow = uow_factory

    async def list(self, status: ExecutionStatus | None = None) -> list[Execution]:
        async with self._uow() as uow:
            return await uow.executions.list(status)

    async def get(self, order_id: int) -> Execution:
        async with self._uow() as uow:
            return await self._get(uow, order_id)

    async def advance(self, order_id: int, target: ExecutionStatus, note: str | None = None) -> Execution:
        async with self._uow() as uow:
            execution = await self._get(uow, order_id)
            event_type = execution.advance(target, note)

            if target == ExecutionStatus.FINALIZADA:
                await self._consume_reserved_parts(uow, order_id)

            await uow.executions.save(execution)
            payload = {"reason": note} if target == ExecutionStatus.CANCELADA else {"note": note}
            await uow.outbox.add(
                Envelope.event(event_type, saga_id=execution.saga_id, order_id=order_id, payload=payload)
            )
            return execution

    @staticmethod
    async def _consume_reserved_parts(uow, order_id: int) -> None:
        reservation = await uow.reservations.get(order_id)
        if reservation is None or reservation.status != ReservationStatus.RESERVADA:
            return
        for item in reservation.items:
            await uow.parts.consume(item.part_id, item.quantity)
        await uow.reservations.set_status(order_id, ReservationStatus.CONSUMIDA)

    @staticmethod
    async def _get(uow, order_id: int) -> Execution:
        execution = await uow.executions.get(order_id)
        if execution is None:
            raise NotFoundError("Execução da OS", order_id)
        return execution
