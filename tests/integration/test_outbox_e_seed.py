"""Publicação do outbox, laço periódico e estoque de demonstração."""

import asyncio
import json

from app.application.messages import Envelope
from app.application.use_cases.saga_commands import SagaCommandHandler
from app.infrastructure.messaging.runtime import publish_pending, run_every
from app.infrastructure.mongo.seed import DEMO_PARTS, seed


class PublicadorFalso:
    def __init__(self):
        self.publicadas = []

    async def __call__(self, exchange, routing_key, body, *, message_id, message_type, headers=None):
        self.publicadas.append((exchange, routing_key, json.loads(body), message_type, message_id))


class TestOutbox:
    async def test_publica_cada_evento_uma_vez_e_na_ordem(self, uow_factory, db):
        await seed(db)
        handler = SagaCommandHandler(uow_factory, queue_capacity=5)
        for order_id in (1, 2):
            await handler.handle(
                Envelope.command("ReservarPecas", saga_id=f"s-{order_id}", order_id=order_id, payload={"items": []})
            )
        publicador = PublicadorFalso()

        assert await publish_pending(db, publicador) == 2
        assert await publish_pending(db, publicador) == 0

        exchange, rota, corpo, tipo, _ = publicador.publicadas[0]
        assert (exchange, rota, tipo, corpo["order_id"]) == ("autogiro.events", "execution.pecas-reservadas", "PecasReservadas", 1)
        assert publicador.publicadas[1][2]["order_id"] == 2


class TestSeed:
    async def test_cria_o_estoque_uma_vez_e_continua_a_numeracao(self, auth_client, db):
        assert await seed(db) == len(DEMO_PARTS)
        assert await seed(db) == 0

        nova = await auth_client.post("/api/v1/parts", json={"name": "Lâmpada H4", "unit_price": 25})

        assert nova.json()["id"] == len(DEMO_PARTS) + 1


class TestLacoPeriodico:
    async def test_continua_rodando_mesmo_quando_a_tarefa_falha(self):
        execucoes = []
        segunda_execucao = asyncio.Event()

        async def tarefa():
            execucoes.append(1)
            if len(execucoes) >= 2:
                segunda_execucao.set()
            raise RuntimeError("falha temporária")

        laco = asyncio.create_task(run_every(0.01, tarefa, "teste"))
        await asyncio.wait_for(segunda_execucao.wait(), timeout=5)
        laco.cancel()
        await asyncio.gather(laco, return_exceptions=True)

        assert len(execucoes) >= 2
