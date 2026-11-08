from dataclasses import dataclass

from app.application.ports.unit_of_work import UnitOfWorkFactory
from app.domain.entities.part import Part
from app.domain.exceptions import BusinessRuleError, NotFoundError


@dataclass
class PartInput:
    name: str
    unit_price: float
    stock_quantity: int = 0
    unit: str = "un"
    description: str | None = None


@dataclass
class PartUpdate:
    name: str | None = None
    unit_price: float | None = None
    unit: str | None = None
    description: str | None = None


class PartUseCases:
    """Cadastro e estoque de peças."""

    def __init__(self, uow_factory: UnitOfWorkFactory):
        self._uow = uow_factory

    async def list(self) -> list[Part]:
        async with self._uow() as uow:
            return await uow.parts.list()

    async def get(self, part_id: int) -> Part:
        async with self._uow() as uow:
            return await self._get(uow, part_id)

    async def create(self, data: PartInput) -> Part:
        if data.stock_quantity < 0:
            raise BusinessRuleError("O estoque inicial não pode ser negativo")
        async with self._uow() as uow:
            return await uow.parts.add(
                Part(
                    name=data.name,
                    unit_price=data.unit_price,
                    stock_quantity=data.stock_quantity,
                    unit=data.unit,
                    description=data.description,
                )
            )

    async def update(self, part_id: int, data: PartUpdate) -> Part:
        async with self._uow() as uow:
            part = await self._get(uow, part_id)
            for field_name in ("name", "unit_price", "unit", "description"):
                value = getattr(data, field_name)
                if value is not None:
                    setattr(part, field_name, value)
            return await uow.parts.update(part)

    async def adjust_stock(self, part_id: int, delta: int) -> Part:
        async with self._uow() as uow:
            part = await self._get(uow, part_id)
            part.adjust_stock(delta)
            return await uow.parts.update(part)

    async def delete(self, part_id: int) -> None:
        async with self._uow() as uow:
            part = await self._get(uow, part_id)
            if part.reserved_quantity:
                raise BusinessRuleError(
                    f"A peça '{part.name}' tem unidades reservadas e não pode ser removida"
                )
            await uow.parts.delete(part_id)

    @staticmethod
    async def _get(uow, part_id: int) -> Part:
        part = await uow.parts.get(part_id)
        if part is None:
            raise NotFoundError("Peça", part_id)
        return part
