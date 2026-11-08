"""Estoque de demonstração e índices. Idempotente: rodar de novo não duplica nada.

Uso:
    python -m app.infrastructure.mongo.seed
"""

import asyncio
import logging
from datetime import UTC, datetime

from pymongo.asynchronous.database import AsyncDatabase

from app.infrastructure.config import settings
from app.infrastructure.logging_config import setup_logging
from app.infrastructure.mongo.database import create_client, ensure_indexes
from app.infrastructure.mongo.repositories import COUNTERS, PARTS

logger = logging.getLogger("autogiro.seed")

DEMO_PARTS = [
    (1, "Óleo sintético 5W30", "Litro de óleo sintético para motor", 45.90, 120, "L"),
    (2, "Filtro de óleo", "Filtro de óleo universal linha leve", 32.50, 80, "un"),
    (3, "Filtro de ar", "Filtro de ar do motor", 58.00, 60, "un"),
    (4, "Pastilha de freio dianteira", "Jogo de pastilhas cerâmicas", 210.00, 35, "jg"),
    (5, "Correia dentada", "Correia dentada com tensor", 340.00, 15, "un"),
    (6, "Bomba d'água", "Bomba d'água para linha leve", 275.00, 12, "un"),
    (7, "Fluido de freio DOT4", "Frasco de 500 ml", 38.90, 50, "un"),
    (8, 'Palheta limpador 24"', "Palheta dianteira 24 polegadas", 42.00, 40, "un"),
]


async def seed(db: AsyncDatabase) -> int:
    await ensure_indexes(db)
    now = datetime.now(UTC)
    created = 0
    for part_id, name, description, price, stock, unit in DEMO_PARTS:
        result = await db[PARTS].update_one(
            {"_id": part_id},
            {
                "$setOnInsert": {
                    "name": name,
                    "description": description,
                    "unit_price": price,
                    "stock_quantity": stock,
                    "reserved_quantity": 0,
                    "unit": unit,
                    "created_at": now,
                    "updated_at": now,
                }
            },
            upsert=True,
        )
        created += 1 if result.upserted_id is not None else 0
    # Novas peças continuam a numeração depois das de demonstração.
    await db[COUNTERS].update_one({"_id": PARTS}, {"$max": {"seq": len(DEMO_PARTS)}}, upsert=True)
    return created


async def main() -> None:  # pragma: no cover - ponto de entrada de linha de comando
    setup_logging(settings.LOG_LEVEL, service=settings.SERVICE_NAME)
    client = create_client(settings.MONGODB_URL)
    created = await seed(client[settings.MONGODB_DATABASE])
    logger.info("Estoque de demonstração: %d peça(s) criada(s)", created)
    await client.close()


if __name__ == "__main__":  # pragma: no cover
    asyncio.run(main())
