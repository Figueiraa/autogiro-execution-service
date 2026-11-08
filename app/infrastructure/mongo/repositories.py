"""Adaptadores do MongoDB. Toda operação participa da sessão (transação) recebida."""

from datetime import UTC, datetime

from pymongo import ReturnDocument
from pymongo.asynchronous.client_session import AsyncClientSession
from pymongo.asynchronous.database import AsyncDatabase

from app.application.messages import Envelope
from app.domain.entities.execution import Execution, ExecutionStatus, TimelineEntry
from app.domain.entities.part import Part
from app.domain.entities.reservation import Reservation, ReservationItem, ReservationStatus

PARTS = "parts"
RESERVATIONS = "reservations"
EXECUTIONS = "executions"
OUTBOX = "outbox"
PROCESSED = "processed_messages"
COUNTERS = "counters"


def _now() -> datetime:
    return datetime.now(UTC)


class _Repository:
    def __init__(self, db: AsyncDatabase, session: AsyncClientSession | None):
        self._db = db
        self._session = session


# ─── Peças ──────────────────────────────────────────────────────────────────


def _part_from(doc: dict) -> Part:
    return Part(
        id=doc["_id"],
        name=doc["name"],
        description=doc.get("description"),
        unit=doc.get("unit", "un"),
        unit_price=float(doc["unit_price"]),
        stock_quantity=doc["stock_quantity"],
        reserved_quantity=doc.get("reserved_quantity", 0),
        created_at=doc.get("created_at"),
        updated_at=doc.get("updated_at"),
    )


class MongoPartRepository(_Repository):
    async def list(self) -> list[Part]:
        cursor = self._db[PARTS].find({}, session=self._session).sort("name")
        return [_part_from(doc) async for doc in cursor]

    async def get(self, part_id: int) -> Part | None:
        doc = await self._db[PARTS].find_one({"_id": part_id}, session=self._session)
        return _part_from(doc) if doc else None

    async def add(self, part: Part) -> Part:
        counter = await self._db[COUNTERS].find_one_and_update(
            {"_id": PARTS},
            {"$inc": {"seq": 1}},
            upsert=True,
            return_document=ReturnDocument.AFTER,
            session=self._session,
        )
        now = _now()
        doc = {
            "_id": counter["seq"],
            "name": part.name,
            "description": part.description,
            "unit": part.unit,
            "unit_price": part.unit_price,
            "stock_quantity": part.stock_quantity,
            "reserved_quantity": 0,
            "created_at": now,
            "updated_at": now,
        }
        await self._db[PARTS].insert_one(doc, session=self._session)
        return _part_from(doc)

    async def update(self, part: Part) -> Part:
        doc = await self._db[PARTS].find_one_and_update(
            {"_id": part.id},
            {
                "$set": {
                    "name": part.name,
                    "description": part.description,
                    "unit": part.unit,
                    "unit_price": part.unit_price,
                    "stock_quantity": part.stock_quantity,
                    "updated_at": _now(),
                }
            },
            return_document=ReturnDocument.AFTER,
            session=self._session,
        )
        return _part_from(doc)

    async def delete(self, part_id: int) -> None:
        await self._db[PARTS].delete_one({"_id": part_id}, session=self._session)

    async def try_reserve(self, part_id: int, quantity: int) -> bool:
        # Atualização condicional e atômica: só reserva se o saldo ainda comportar.
        result = await self._db[PARTS].update_one(
            {
                "_id": part_id,
                "$expr": {"$gte": [{"$subtract": ["$stock_quantity", "$reserved_quantity"]}, quantity]},
            },
            {"$inc": {"reserved_quantity": quantity}, "$set": {"updated_at": _now()}},
            session=self._session,
        )
        return result.modified_count == 1

    async def release(self, part_id: int, quantity: int) -> None:
        await self._db[PARTS].update_one(
            {"_id": part_id},
            {"$inc": {"reserved_quantity": -quantity}, "$set": {"updated_at": _now()}},
            session=self._session,
        )

    async def consume(self, part_id: int, quantity: int) -> None:
        await self._db[PARTS].update_one(
            {"_id": part_id},
            {
                "$inc": {"stock_quantity": -quantity, "reserved_quantity": -quantity},
                "$set": {"updated_at": _now()},
            },
            session=self._session,
        )


# ─── Reservas ───────────────────────────────────────────────────────────────


class MongoReservationRepository(_Repository):
    async def get(self, order_id: int) -> Reservation | None:
        doc = await self._db[RESERVATIONS].find_one({"order_id": order_id}, session=self._session)
        if doc is None:
            return None
        return Reservation(
            order_id=doc["order_id"],
            saga_id=doc["saga_id"],
            status=ReservationStatus(doc["status"]),
            items=[ReservationItem(**item) for item in doc["items"]],
            created_at=doc.get("created_at"),
            updated_at=doc.get("updated_at"),
        )

    async def add(self, reservation: Reservation) -> None:
        now = _now()
        await self._db[RESERVATIONS].insert_one(
            {
                "order_id": reservation.order_id,
                "saga_id": reservation.saga_id,
                "status": reservation.status.value,
                "items": [vars(item) for item in reservation.items],
                "created_at": now,
                "updated_at": now,
            },
            session=self._session,
        )

    async def set_status(self, order_id: int, status: ReservationStatus) -> None:
        await self._db[RESERVATIONS].update_one(
            {"order_id": order_id},
            {"$set": {"status": status.value, "updated_at": _now()}},
            session=self._session,
        )


# ─── Execuções ──────────────────────────────────────────────────────────────


def _execution_from(doc: dict) -> Execution:
    return Execution(
        order_id=doc["order_id"],
        saga_id=doc["saga_id"],
        vehicle=doc["vehicle"],
        services=doc["services"],
        parts=doc["parts"],
        notes=doc.get("notes"),
        status=ExecutionStatus(doc["status"]),
        cancel_reason=doc.get("cancel_reason"),
        queued_at=doc["queued_at"],
        timeline=[
            TimelineEntry(ExecutionStatus(entry["status"]), entry["at"], entry.get("note"))
            for entry in doc["timeline"]
        ],
    )


def _execution_doc(execution: Execution) -> dict:
    return {
        "order_id": execution.order_id,
        "saga_id": execution.saga_id,
        "vehicle": execution.vehicle,
        "services": execution.services,
        "parts": execution.parts,
        "notes": execution.notes,
        "status": execution.status.value,
        "cancel_reason": execution.cancel_reason,
        "queued_at": execution.queued_at,
        "timeline": [
            {"status": entry.status.value, "at": entry.at, "note": entry.note} for entry in execution.timeline
        ],
    }


class MongoExecutionRepository(_Repository):
    async def get(self, order_id: int) -> Execution | None:
        doc = await self._db[EXECUTIONS].find_one({"order_id": order_id}, session=self._session)
        return _execution_from(doc) if doc else None

    async def add(self, execution: Execution) -> None:
        await self._db[EXECUTIONS].insert_one(_execution_doc(execution), session=self._session)

    async def save(self, execution: Execution) -> None:
        await self._db[EXECUTIONS].replace_one(
            {"order_id": execution.order_id}, _execution_doc(execution), session=self._session
        )

    async def list(self, status: ExecutionStatus | None = None) -> list[Execution]:
        query = {"status": status.value} if status else {}
        cursor = self._db[EXECUTIONS].find(query, session=self._session).sort("queued_at")
        return [_execution_from(doc) async for doc in cursor]

    async def count(self, status: ExecutionStatus) -> int:
        return await self._db[EXECUTIONS].count_documents({"status": status.value}, session=self._session)


# ─── Outbox e mensagens processadas ─────────────────────────────────────────


class MongoOutbox(_Repository):
    async def add(self, envelope: Envelope) -> None:
        await self._db[OUTBOX].insert_one(
            {
                "_id": envelope.message_id,
                "type": envelope.type,
                "exchange": envelope.exchange,
                "routing_key": envelope.routing_key,
                "body": envelope.to_json(),
                "created_at": _now(),
                "published_at": None,
            },
            session=self._session,
        )


class MongoInbox(_Repository):
    async def register(self, message_id: str) -> bool:
        # Consulta antes de inserir: dentro de uma transação, um erro de chave
        # duplicada aborta a transação inteira. Se duas réplicas inserirem ao mesmo
        # tempo, o conflito faz a mensagem ser reprocessada, e aí ela já consta aqui.
        if await self._db[PROCESSED].find_one({"_id": message_id}, session=self._session):
            return False
        await self._db[PROCESSED].insert_one(
            {"_id": message_id, "processed_at": _now()}, session=self._session
        )
        return True
