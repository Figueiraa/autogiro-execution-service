"""Conexão com o MongoDB, índices e unidade de trabalho (transação)."""

from pymongo import ASCENDING, AsyncMongoClient
from pymongo.asynchronous.database import AsyncDatabase

from app.infrastructure.mongo.repositories import (
    EXECUTIONS,
    OUTBOX,
    RESERVATIONS,
    MongoExecutionRepository,
    MongoInbox,
    MongoOutbox,
    MongoPartRepository,
    MongoReservationRepository,
)


def create_client(url: str) -> AsyncMongoClient:
    # tz_aware: as datas voltam com fuso (UTC), como foram gravadas.
    return AsyncMongoClient(url, tz_aware=True, serverSelectionTimeoutMS=5000)


async def ensure_indexes(db: AsyncDatabase) -> None:
    await db[RESERVATIONS].create_index("order_id", unique=True)
    await db[EXECUTIONS].create_index("order_id", unique=True)
    await db[EXECUTIONS].create_index([("status", ASCENDING), ("queued_at", ASCENDING)])
    await db[OUTBOX].create_index([("published_at", ASCENDING), ("created_at", ASCENDING)])


class MongoUnitOfWork:
    """Uma transação do MongoDB: confirma se o bloco termina bem, desfaz se der erro.

    Transações exigem replica set (o Atlas M0 é um; localmente o Mongo sobe com
    `--replSet rs0`).
    """

    def __init__(self, client: AsyncMongoClient, db: AsyncDatabase):
        self._client = client
        self._db = db
        self._session = None

    async def __aenter__(self) -> "MongoUnitOfWork":
        self._session = self._client.start_session()
        await self._session.start_transaction()
        self.parts = MongoPartRepository(self._db, self._session)
        self.reservations = MongoReservationRepository(self._db, self._session)
        self.executions = MongoExecutionRepository(self._db, self._session)
        self.outbox = MongoOutbox(self._db, self._session)
        self.inbox = MongoInbox(self._db, self._session)
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        try:
            if exc_type is None:
                await self._session.commit_transaction()
            else:
                await self._session.abort_transaction()
        finally:
            await self._session.end_session()


def unit_of_work_factory(client: AsyncMongoClient, db: AsyncDatabase):
    return lambda: MongoUnitOfWork(client, db)
