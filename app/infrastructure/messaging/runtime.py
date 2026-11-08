"""Tarefas de mensageria que rodam junto com a API do Execution Service.

- consumidor da fila `execution.commands` (comandos do orquestrador);
- publicador do outbox (eventos gravados na mesma transação de cada mudança).
"""

import asyncio
import logging
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime

from pymongo.asynchronous.database import AsyncDatabase

from app.application.messages import EXCHANGE_COMMANDS
from app.application.ports.unit_of_work import UnitOfWorkFactory
from app.application.use_cases.saga_commands import SagaCommandHandler
from app.infrastructure.config import settings
from app.infrastructure.messaging.consumer import QueueConsumer
from app.infrastructure.messaging.rabbitmq import RabbitMQClient
from app.infrastructure.mongo.repositories import OUTBOX
from app.infrastructure.observability.tracing import publish_span

logger = logging.getLogger("autogiro.messaging")

QUEUE_COMMANDS = "execution.commands"
COMMAND_BINDINGS = (
    "execution.reservar-pecas",
    "execution.liberar-pecas",
    "execution.enfileirar-execucao",
)

Publish = Callable[..., Awaitable[None]]


async def publish_pending(db: AsyncDatabase, publish: Publish, limit: int = 100) -> int:
    """Publica os eventos pendentes do outbox, na ordem em que foram gravados.

    Marca cada um como publicado depois do publish confirmado. Se o processo cair
    entre os dois, o evento sai de novo — o orquestrador descarta a repetição.
    """
    published = 0
    cursor = db[OUTBOX].find({"published_at": None}).sort("created_at").limit(limit)
    async for doc in cursor:
        with publish_span(
            exchange=doc["exchange"],
            routing_key=doc["routing_key"],
            message_id=doc["_id"],
            message_type=doc["type"],
            body=doc["body"],
            stored_context=doc.get("trace_context"),
        ) as headers:
            await publish(
                doc["exchange"],
                doc["routing_key"],
                doc["body"].encode("utf-8"),
                message_id=doc["_id"],
                message_type=doc["type"],
                headers=headers,
            )
        await db[OUTBOX].update_one({"_id": doc["_id"]}, {"$set": {"published_at": datetime.now(UTC)}})
        published += 1
    return published


async def run_every(interval: float, job: Callable[[], Awaitable[object]], name: str) -> None:
    """Executa `job` periodicamente até a tarefa ser cancelada; erros não derrubam o laço."""
    while True:
        try:
            await job()
        except asyncio.CancelledError:  # pragma: no cover - encerramento normal
            raise
        except Exception:
            logger.exception("Falha na tarefa periódica %s", name)
        await asyncio.sleep(interval)


class MessagingRuntime:
    def __init__(
        self, uow_factory: UnitOfWorkFactory, db: AsyncDatabase, client: RabbitMQClient | None = None
    ):
        self._uow_factory = uow_factory
        self._db = db
        self.client = client or RabbitMQClient(
            settings.RABBITMQ_URL, retry_delay_ms=settings.MESSAGE_RETRY_DELAY_MS
        )
        self._tasks: list[asyncio.Task] = []

    async def start(self) -> None:
        await self.client.connect()
        await self.client.declare_exchanges()
        queue = await self.client.declare_queue_with_retry(
            QUEUE_COMMANDS, EXCHANGE_COMMANDS, COMMAND_BINDINGS
        )
        handler = SagaCommandHandler(self._uow_factory, settings.EXECUTION_QUEUE_CAPACITY)
        await queue.consume(
            QueueConsumer(self.client, QUEUE_COMMANDS, handler.handle, settings.MESSAGE_MAX_RETRIES)
        )
        self._tasks = [
            asyncio.create_task(
                run_every(
                    settings.OUTBOX_POLL_SECONDS,
                    lambda: publish_pending(self._db, self.client.publish),
                    "outbox",
                )
            )
        ]
        logger.info("Mensageria iniciada: consumindo %s", QUEUE_COMMANDS)

    async def stop(self) -> None:
        for task in self._tasks:
            task.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
        await self.client.close()

    async def is_up(self) -> bool:
        return self.client.is_connected
