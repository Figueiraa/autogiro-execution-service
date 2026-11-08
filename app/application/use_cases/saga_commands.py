"""Comandos da Saga recebidos do orquestrador (OS Service).

Cada comando é tratado numa transação: o efeito, o evento de resposta (outbox) e o
registro da mensagem processada. Todos são idempotentes por ordem de serviço: um
comando repetido (reenvio do orquestrador) não refaz o efeito, só responde de novo.
"""

import logging
from collections import defaultdict
from dataclasses import dataclass

from app.application.messages import Envelope
from app.application.ports.unit_of_work import UnitOfWorkFactory
from app.domain.entities.execution import Execution, ExecutionStatus
from app.domain.entities.reservation import (
    MissingPart,
    MissingReason,
    Reservation,
    ReservationItem,
    ReservationStatus,
)

logger = logging.getLogger("autogiro.saga")

DONE = "done"
DUPLICATE = "duplicate"
IGNORED = "ignored"


class ReservationConflictError(Exception):
    """Outra OS reservou a mesma peça no meio desta transação: a mensagem é reprocessada."""


@dataclass
class CommandResult:
    outcome: str
    event: str | None = None


class SagaCommandHandler:
    def __init__(self, uow_factory: UnitOfWorkFactory, queue_capacity: int):
        self._uow = uow_factory
        self._capacity = queue_capacity

    async def handle(self, command: Envelope) -> CommandResult:
        handlers = {
            "ReservarPecas": self._reserve,
            "LiberarPecas": self._release,
            "EnfileirarExecucao": self._enqueue,
        }
        async with self._uow() as uow:
            if not await uow.inbox.register(command.message_id):
                return CommandResult(DUPLICATE)
            handler = handlers.get(command.type) if command.kind == "command" else None
            if handler is None:
                logger.warning("Mensagem %s ignorada pelo Execution Service", command.type)
                return CommandResult(IGNORED)

            event_type, payload = await handler(uow, command)
            await uow.outbox.add(
                Envelope.event(
                    event_type,
                    saga_id=command.saga_id,
                    order_id=command.order_id,
                    payload=payload,
                    causation_id=command.message_id,
                )
            )
        logger.info("OS %s: %s → %s", command.order_id, command.type, event_type)
        return CommandResult(DONE, event_type)

    # ─── ReservarPecas ──────────────────────────────────────────────────────
    async def _reserve(self, uow, command: Envelope) -> tuple[str, dict]:
        existing = await uow.reservations.get(command.order_id)
        if existing is not None:
            return "PecasReservadas", {"items": [vars(item) for item in existing.items]}

        requested: dict[int, int] = defaultdict(int)
        for item in command.payload.get("items", []):
            requested[int(item["part_id"])] += int(item["quantity"])

        # Confere tudo antes de reservar qualquer coisa: a reserva é tudo ou nada.
        missing, items = [], []
        for part_id, quantity in requested.items():
            part = await uow.parts.get(part_id)
            if part is None:
                missing.append(MissingPart(part_id, quantity, 0, MissingReason.NAO_ENCONTRADA))
            elif part.available < quantity:
                missing.append(MissingPart(part_id, quantity, part.available, MissingReason.SEM_ESTOQUE))
            else:
                items.append(ReservationItem(part_id, part.name, quantity, part.unit_price))

        if missing:
            return "PecasIndisponiveis", {"missing": [{**vars(m), "reason": m.reason.value} for m in missing]}

        for item in items:
            if not await uow.parts.try_reserve(item.part_id, item.quantity):
                raise ReservationConflictError(f"Peça {item.part_id} reservada por outra OS ao mesmo tempo")
        await uow.reservations.add(
            Reservation(order_id=command.order_id, saga_id=command.saga_id, items=items)
        )
        return "PecasReservadas", {"items": [vars(item) for item in items]}

    # ─── LiberarPecas (compensação) ─────────────────────────────────────────
    async def _release(self, uow, command: Envelope) -> tuple[str, dict]:
        reservation = await uow.reservations.get(command.order_id)
        if reservation is None or reservation.status != ReservationStatus.RESERVADA:
            # Nada reservado, já liberado ou já usado no reparo: nada a devolver.
            return "PecasLiberadas", {"released": False}

        for item in reservation.items:
            await uow.parts.release(item.part_id, item.quantity)
        await uow.reservations.set_status(command.order_id, ReservationStatus.LIBERADA)
        return "PecasLiberadas", {"released": True}

    # ─── EnfileirarExecucao ─────────────────────────────────────────────────
    async def _enqueue(self, uow, command: Envelope) -> tuple[str, dict]:
        existing = await uow.executions.get(command.order_id)
        queued = await uow.executions.count(ExecutionStatus.NA_FILA)
        if existing is not None:
            return "ExecucaoEnfileirada", {"position": queued}

        # Regra de negócio: a oficina não aceita mais carros do que comporta.
        if queued >= self._capacity:
            return "EnfileiramentoRecusado", {"reason": "FILA_CHEIA"}

        payload = command.payload
        await uow.executions.add(
            Execution.enqueue(
                order_id=command.order_id,
                saga_id=command.saga_id,
                vehicle=payload.get("vehicle", {}),
                services=payload.get("services", []),
                parts=payload.get("parts", []),
                notes=payload.get("notes"),
            )
        )
        return "ExecucaoEnfileirada", {"position": queued + 1}
