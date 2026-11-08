from dataclasses import dataclass
from datetime import datetime

from app.domain.exceptions import BusinessRuleError


@dataclass
class Part:
    """Peça ou insumo do estoque da oficina.

    `reserved_quantity` é o que está prometido a ordens de serviço ainda não
    executadas: está no estoque, mas não pode ser reservado de novo.
    """

    name: str
    unit_price: float
    stock_quantity: int = 0
    reserved_quantity: int = 0
    unit: str = "un"
    description: str | None = None
    id: int | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None

    @property
    def available(self) -> int:
        return self.stock_quantity - self.reserved_quantity

    def adjust_stock(self, delta: int) -> None:
        """Entrada (positivo) ou baixa manual (negativo) de estoque."""
        new_quantity = self.stock_quantity + delta
        if new_quantity < self.reserved_quantity:
            raise BusinessRuleError(
                f"Estoque de '{self.name}' ficaria em {new_quantity}, abaixo das "
                f"{self.reserved_quantity} unidades reservadas"
            )
        self.stock_quantity = new_quantity
