from fastapi import APIRouter, Depends, status

from app.application.use_cases.parts import PartInput, PartUseCases
from app.application.use_cases.parts import PartUpdate as PartUpdateInput
from app.interfaces.http.dependencies import get_current_principal, get_uow_factory
from app.interfaces.http.schemas.schemas import PartCreate, PartResponse, PartUpdate, StockAdjust

router = APIRouter(prefix="/parts", tags=["Peças e estoque"], dependencies=[Depends(get_current_principal)])


def _use_cases(uow_factory=Depends(get_uow_factory)) -> PartUseCases:
    return PartUseCases(uow_factory)


@router.get("", response_model=list[PartResponse])
async def list_parts(parts: PartUseCases = Depends(_use_cases)):
    return [PartResponse.from_domain(part) for part in await parts.list()]


@router.get("/{part_id}", response_model=PartResponse)
async def get_part(part_id: int, parts: PartUseCases = Depends(_use_cases)):
    """Detalhe da peça, com o saldo disponível (estoque menos o reservado)."""
    return PartResponse.from_domain(await parts.get(part_id))


@router.post("", response_model=PartResponse, status_code=status.HTTP_201_CREATED)
async def create_part(data: PartCreate, parts: PartUseCases = Depends(_use_cases)):
    return PartResponse.from_domain(await parts.create(PartInput(**data.model_dump())))


@router.patch("/{part_id}", response_model=PartResponse)
async def update_part(part_id: int, data: PartUpdate, parts: PartUseCases = Depends(_use_cases)):
    return PartResponse.from_domain(await parts.update(part_id, PartUpdateInput(**data.model_dump())))


@router.post("/{part_id}/stock", response_model=PartResponse)
async def adjust_stock(part_id: int, data: StockAdjust, parts: PartUseCases = Depends(_use_cases)):
    """Entrada ou baixa manual de estoque. Não pode deixar o estoque abaixo do reservado."""
    return PartResponse.from_domain(await parts.adjust_stock(part_id, data.quantity))


@router.delete("/{part_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_part(part_id: int, parts: PartUseCases = Depends(_use_cases)):
    await parts.delete(part_id)
