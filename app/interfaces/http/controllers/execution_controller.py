from fastapi import APIRouter, Depends, Query

from app.application.use_cases.executions import ExecutionUseCases
from app.domain.entities.execution import ExecutionStatus
from app.interfaces.http.dependencies import get_current_principal, get_uow_factory
from app.interfaces.http.schemas.schemas import CancelRequest, ExecutionResponse, StepNote

router = APIRouter(
    prefix="/executions", tags=["Fila de execução"], dependencies=[Depends(get_current_principal)]
)


def _use_cases(uow_factory=Depends(get_uow_factory)) -> ExecutionUseCases:
    return ExecutionUseCases(uow_factory)


@router.get("", response_model=list[ExecutionResponse])
async def list_executions(
    status: ExecutionStatus | None = Query(None, description="Filtra por status"),
    executions: ExecutionUseCases = Depends(_use_cases),
):
    """Fila da oficina, na ordem de chegada."""
    return [ExecutionResponse.from_domain(e) for e in await executions.list(status)]


@router.get("/{order_id}", response_model=ExecutionResponse)
async def get_execution(order_id: int, executions: ExecutionUseCases = Depends(_use_cases)):
    """Execução da OS, com a linha do tempo do diagnóstico e do reparo."""
    return ExecutionResponse.from_domain(await executions.get(order_id))


@router.post("/{order_id}/diagnosis", response_model=ExecutionResponse)
async def start_diagnosis(
    order_id: int, data: StepNote | None = None, executions: ExecutionUseCases = Depends(_use_cases)
):
    """`NA_FILA → EM_DIAGNOSTICO`."""
    execution = await executions.advance(
        order_id, ExecutionStatus.EM_DIAGNOSTICO, data.note if data else None
    )
    return ExecutionResponse.from_domain(execution)


@router.post("/{order_id}/repair", response_model=ExecutionResponse)
async def start_repair(
    order_id: int, data: StepNote | None = None, executions: ExecutionUseCases = Depends(_use_cases)
):
    """`EM_DIAGNOSTICO → EM_REPARO`."""
    execution = await executions.advance(order_id, ExecutionStatus.EM_REPARO, data.note if data else None)
    return ExecutionResponse.from_domain(execution)


@router.post("/{order_id}/finish", response_model=ExecutionResponse)
async def finish(
    order_id: int, data: StepNote | None = None, executions: ExecutionUseCases = Depends(_use_cases)
):
    """`EM_REPARO → FINALIZADA`. As peças reservadas são baixadas do estoque."""
    execution = await executions.advance(order_id, ExecutionStatus.FINALIZADA, data.note if data else None)
    return ExecutionResponse.from_domain(execution)


@router.post("/{order_id}/cancel", response_model=ExecutionResponse)
async def cancel(order_id: int, data: CancelRequest, executions: ExecutionUseCases = Depends(_use_cases)):
    """Cancela a execução (por exemplo, reparo inviável). Dispara as compensações da Saga."""
    execution = await executions.advance(order_id, ExecutionStatus.CANCELADA, data.reason)
    return ExecutionResponse.from_domain(execution)
