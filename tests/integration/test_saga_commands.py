"""Comandos da Saga sobre MongoDB: reserva tudo ou nada, liberação e fila."""

import json

import pytest

from app.application.messages import Envelope
from app.application.use_cases.saga_commands import (
    DONE,
    DUPLICATE,
    IGNORED,
    ReservationConflictError,
    SagaCommandHandler,
)
from app.infrastructure.mongo.repositories import MongoPartRepository
from app.infrastructure.mongo.seed import seed


@pytest.fixture
async def estoque(db):
    await seed(db)
    return db


@pytest.fixture
def handler(uow_factory):
    return SagaCommandHandler(uow_factory, queue_capacity=2)


def _comando(tipo, order_id=7, payload=None) -> Envelope:
    return Envelope.command(tipo, saga_id="saga-7", order_id=order_id, payload=payload or {})


async def _eventos(db) -> list[tuple[str, dict]]:
    docs = [doc async for doc in db["outbox"].find().sort("created_at")]
    return [(doc["type"], json.loads(doc["body"])) for doc in docs]


async def _peca(db, part_id) -> dict:
    return await db["parts"].find_one({"_id": part_id})


class TestReserva:
    async def test_reserva_todas_as_pecas_e_responde_com_nome_e_preco(self, handler, estoque):
        comando = _comando("ReservarPecas", payload={"items": [{"part_id": 2, "quantity": 2}, {"part_id": 1, "quantity": 4}]})

        resultado = await handler.handle(comando)

        assert (resultado.outcome, resultado.event) == (DONE, "PecasReservadas")
        tipo, evento = (await _eventos(estoque))[0]
        assert evento["payload"]["items"] == [
            {"part_id": 2, "name": "Filtro de óleo", "quantity": 2, "unit_price": 32.5},
            {"part_id": 1, "name": "Óleo sintético 5W30", "quantity": 4, "unit_price": 45.9},
        ]
        assert (tipo, evento["causation_id"], evento["saga_id"]) == ("PecasReservadas", comando.message_id, "saga-7")
        assert (await _peca(estoque, 2))["reserved_quantity"] == 2
        assert (await _peca(estoque, 1))["reserved_quantity"] == 4

    async def test_e_tudo_ou_nada(self, handler, estoque):
        comando = _comando(
            "ReservarPecas",
            payload={"items": [{"part_id": 2, "quantity": 1}, {"part_id": 5, "quantity": 99}, {"part_id": 404, "quantity": 1}]},
        )

        resultado = await handler.handle(comando)

        assert resultado.event == "PecasIndisponiveis"
        _, evento = (await _eventos(estoque))[0]
        assert evento["payload"]["missing"] == [
            {"part_id": 5, "requested": 99, "available": 15, "reason": "SEM_ESTOQUE"},
            {"part_id": 404, "requested": 1, "available": 0, "reason": "NAO_ENCONTRADA"},
        ]
        assert (await _peca(estoque, 2))["reserved_quantity"] == 0  # nada foi reservado
        assert await estoque["reservations"].count_documents({}) == 0

    async def test_soma_pedidos_repetidos_da_mesma_peca(self, handler, estoque):
        comando = _comando("ReservarPecas", payload={"items": [{"part_id": 2, "quantity": 1}, {"part_id": 2, "quantity": 2}]})

        await handler.handle(comando)

        assert (await _peca(estoque, 2))["reserved_quantity"] == 3

    async def test_os_sem_pecas_reserva_lista_vazia(self, handler, estoque):
        resultado = await handler.handle(_comando("ReservarPecas", payload={"items": []}))
        assert resultado.event == "PecasReservadas"

    async def test_comando_reenviado_nao_reserva_de_novo(self, handler, estoque):
        payload = {"items": [{"part_id": 2, "quantity": 2}]}
        await handler.handle(_comando("ReservarPecas", payload=payload))

        reenvio = await handler.handle(_comando("ReservarPecas", payload=payload))

        assert reenvio.event == "PecasReservadas"
        assert (await _peca(estoque, 2))["reserved_quantity"] == 2
        assert [tipo for tipo, _ in await _eventos(estoque)] == ["PecasReservadas", "PecasReservadas"]

    async def test_mesma_mensagem_entregue_duas_vezes_e_descartada(self, handler, estoque):
        comando = _comando("ReservarPecas", payload={"items": [{"part_id": 2, "quantity": 1}]})

        assert (await handler.handle(comando)).outcome == DONE
        assert (await handler.handle(comando)).outcome == DUPLICATE
        assert len(await _eventos(estoque)) == 1

    async def test_conflito_com_outra_os_desfaz_a_transacao(self, handler, estoque, monkeypatch):
        async def outra_os_levou(self, part_id, quantity):
            return False

        monkeypatch.setattr(MongoPartRepository, "try_reserve", outra_os_levou)
        comando = _comando("ReservarPecas", payload={"items": [{"part_id": 2, "quantity": 1}]})

        with pytest.raises(ReservationConflictError):
            await handler.handle(comando)

        assert await estoque["reservations"].count_documents({}) == 0
        assert await estoque["processed_messages"].count_documents({}) == 0  # será reprocessada
        assert await estoque["outbox"].count_documents({}) == 0


class TestLiberacao:
    async def test_devolve_as_pecas_reservadas(self, handler, estoque):
        await handler.handle(_comando("ReservarPecas", payload={"items": [{"part_id": 2, "quantity": 3}]}))

        resultado = await handler.handle(_comando("LiberarPecas", payload={"reason": "ORCAMENTO_RECUSADO"}))

        assert resultado.event == "PecasLiberadas"
        assert (await _eventos(estoque))[-1][1]["payload"] == {"released": True}
        assert (await _peca(estoque, 2))["reserved_quantity"] == 0
        assert (await estoque["reservations"].find_one({"order_id": 7}))["status"] == "LIBERADA"

    async def test_sem_reserva_ou_ja_liberada_nao_faz_nada(self, handler, estoque):
        await handler.handle(_comando("LiberarPecas"))
        await handler.handle(_comando("ReservarPecas", payload={"items": [{"part_id": 2, "quantity": 1}]}))
        await handler.handle(_comando("LiberarPecas"))
        await handler.handle(_comando("LiberarPecas"))

        liberacoes = [e["payload"]["released"] for tipo, e in await _eventos(estoque) if tipo == "PecasLiberadas"]
        assert liberacoes == [False, True, False]
        assert (await _peca(estoque, 2))["reserved_quantity"] == 0


class TestFila:
    PAYLOAD = {
        "vehicle": {"plate": "ABC1D23", "brand": "Fiat", "model": "Argo"},
        "services": [{"service_type_id": 1, "name": "Troca de óleo", "quantity": 1}],
        "parts": [{"part_id": 2, "name": "Filtro de óleo", "quantity": 1}],
        "notes": "barulho",
    }

    async def test_enfileira_com_a_posicao(self, handler, estoque):
        primeira = await handler.handle(_comando("EnfileirarExecucao", order_id=1, payload=self.PAYLOAD))
        await handler.handle(_comando("EnfileirarExecucao", order_id=2, payload=self.PAYLOAD))

        assert primeira.event == "ExecucaoEnfileirada"
        posicoes = [e["payload"]["position"] for _, e in await _eventos(estoque)]
        assert posicoes == [1, 2]
        execucao = await estoque["executions"].find_one({"order_id": 1})
        assert (execucao["status"], execucao["vehicle"]["plate"], execucao["notes"]) == ("NA_FILA", "ABC1D23", "barulho")

    async def test_fila_cheia_recusa(self, handler, estoque):
        for order_id in (1, 2):
            await handler.handle(_comando("EnfileirarExecucao", order_id=order_id, payload=self.PAYLOAD))

        resultado = await handler.handle(_comando("EnfileirarExecucao", order_id=3, payload=self.PAYLOAD))

        assert resultado.event == "EnfileiramentoRecusado"
        assert (await _eventos(estoque))[-1][1]["payload"] == {"reason": "FILA_CHEIA"}
        assert await estoque["executions"].count_documents({}) == 2

    async def test_reenvio_nao_duplica_a_execucao(self, handler, estoque):
        await handler.handle(_comando("EnfileirarExecucao", payload=self.PAYLOAD))
        reenvio = await handler.handle(_comando("EnfileirarExecucao", payload=self.PAYLOAD))

        assert reenvio.event == "ExecucaoEnfileirada"
        assert await estoque["executions"].count_documents({}) == 1


class TestMensagensIgnoradas:
    async def test_comando_que_nao_e_do_execution(self, handler, estoque):
        assert (await handler.handle(_comando("GerarOrcamento"))).outcome == IGNORED

    async def test_evento_no_lugar_de_comando(self, handler, estoque):
        evento = Envelope.event("PecasReservadas", saga_id="s", order_id=1, payload={})
        assert (await handler.handle(evento)).outcome == IGNORED
