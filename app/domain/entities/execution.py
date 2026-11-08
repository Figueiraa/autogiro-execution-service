"""Execução de uma ordem de serviço na oficina: fila, diagnóstico, reparo e conclusão."""

import enum
from dataclasses import dataclass, field
from datetime import UTC, datetime

from app.domain.exceptions import BusinessRuleError, InvalidStatusTransitionError


class ExecutionStatus(str, enum.Enum):
    NA_FILA = "NA_FILA"
    EM_DIAGNOSTICO = "EM_DIAGNOSTICO"
    EM_REPARO = "EM_REPARO"
    FINALIZADA = "FINALIZADA"
    CANCELADA = "CANCELADA"


_S = ExecutionStatus

VALID_TRANSITIONS: dict[ExecutionStatus, list[ExecutionStatus]] = {
    _S.NA_FILA: [_S.EM_DIAGNOSTICO, _S.CANCELADA],
    _S.EM_DIAGNOSTICO: [_S.EM_REPARO, _S.CANCELADA],
    _S.EM_REPARO: [_S.FINALIZADA, _S.CANCELADA],
    _S.FINALIZADA: [],
    _S.CANCELADA: [],
}

# Evento publicado para o orquestrador a cada mudança.
EVENT_FOR_STATUS: dict[ExecutionStatus, str] = {
    _S.EM_DIAGNOSTICO: "DiagnosticoIniciado",
    _S.EM_REPARO: "ReparoIniciado",
    _S.FINALIZADA: "ExecucaoFinalizada",
    _S.CANCELADA: "ExecucaoCancelada",
}


@dataclass
class TimelineEntry:
    status: ExecutionStatus
    at: datetime
    note: str | None = None


@dataclass
class Execution:
    """Documento da execução: cresce com a linha do tempo do diagnóstico e do reparo."""

    order_id: int
    saga_id: str
    vehicle: dict
    services: list[dict] = field(default_factory=list)
    parts: list[dict] = field(default_factory=list)
    notes: str | None = None
    status: ExecutionStatus = ExecutionStatus.NA_FILA
    cancel_reason: str | None = None
    queued_at: datetime | None = None
    timeline: list[TimelineEntry] = field(default_factory=list)

    @classmethod
    def enqueue(
        cls,
        *,
        order_id: int,
        saga_id: str,
        vehicle: dict,
        services: list[dict],
        parts: list[dict],
        notes: str | None,
        now: datetime | None = None,
    ) -> "Execution":
        moment = now or datetime.now(UTC)
        return cls(
            order_id=order_id,
            saga_id=saga_id,
            vehicle=vehicle,
            services=services,
            parts=parts,
            notes=notes,
            queued_at=moment,
            timeline=[TimelineEntry(ExecutionStatus.NA_FILA, moment, "Entrou na fila da oficina")],
        )

    def advance(self, target: ExecutionStatus, note: str | None = None, now: datetime | None = None) -> str:
        """Muda o status, registra na linha do tempo e devolve o evento a publicar."""
        if target not in VALID_TRANSITIONS[self.status]:
            raise InvalidStatusTransitionError(self.status.value, target.value)
        if target == ExecutionStatus.CANCELADA:
            if not note:
                raise BusinessRuleError("Informe o motivo do cancelamento")
            self.cancel_reason = note

        self.status = target
        self.timeline.append(TimelineEntry(target, now or datetime.now(UTC), note))
        return EVENT_FOR_STATUS[target]
