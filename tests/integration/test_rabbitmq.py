"""Mensageria contra RabbitMQ e MongoDB de verdade.

Roda quando `TEST_RABBITMQ_URL` e `TEST_MONGODB_URL` estão definidas (no CI,
containers dos dois). Sem elas, os testes são pulados.
"""

import asyncio
import json
import os
import uuid

import aio_pika
import pytest

from app.application.messages import EXCHANGE_COMMANDS, EXCHANGE_EVENTS, Envelope
from app.infrastructure.config import settings
from app.infrastructure.messaging.consumer import ERROR_HEADER, RETRY_HEADER, QueueConsumer
from app.infrastructure.messaging.rabbitmq import RabbitMQClient, dead_letter_queue
from app.infrastructure.messaging.runtime import QUEUE_COMMANDS, MessagingRuntime
from app.infrastructure.mongo.seed import seed

RABBITMQ_URL = os.getenv("TEST_RABBITMQ_URL")

pytestmark = pytest.mark.skipif(not RABBITMQ_URL, reason="TEST_RABBITMQ_URL não definida")


@pytest.fixture
async def canal():
    conexao = await aio_pika.connect_robust(RABBITMQ_URL)
    canal = await conexao.channel()
    yield canal
    await conexao.close()


@pytest.fixture
def intervalos_curtos(monkeypatch):
    monkeypatch.setattr(settings, "OUTBOX_POLL_SECONDS", 0.1)


async def _esperar_mensagem(fila, segundos: float = 10.0):
    for _ in range(int(segundos / 0.1)):
        mensagem = await fila.get(no_ack=True, fail=False)
        if mensagem is not None:
            return mensagem
        await asyncio.sleep(0.1)
    return None


async def test_comando_de_reserva_vira_evento_publicado(canal, uow_factory, db, intervalos_curtos):
    await seed(db)
    await canal.queue_delete(QUEUE_COMMANDS)
    runtime = MessagingRuntime(uow_factory, db, RabbitMQClient(RABBITMQ_URL))
    await runtime.start()
    respostas = await canal.declare_queue(exclusive=True)
    await respostas.bind(EXCHANGE_EVENTS, routing_key="execution.#")

    comando = Envelope.command(
        "ReservarPecas", saga_id="saga-1", order_id=1, payload={"items": [{"part_id": 2, "quantity": 1}]}
    )
    exchange = await canal.get_exchange(EXCHANGE_COMMANDS)
    await exchange.publish(aio_pika.Message(comando.to_json().encode()), routing_key=comando.routing_key)
    mensagem = await _esperar_mensagem(respostas)
    assert await runtime.is_up()
    await runtime.stop()

    assert mensagem is not None
    evento = json.loads(mensagem.body)
    assert (mensagem.type, evento["causation_id"]) == ("PecasReservadas", comando.message_id)
    assert evento["payload"]["items"][0]["name"] == "Filtro de óleo"


async def test_mensagem_invalida_vai_para_a_dlq(canal, uow_factory, db, intervalos_curtos):
    await canal.queue_delete(QUEUE_COMMANDS)
    await canal.queue_delete(dead_letter_queue(QUEUE_COMMANDS))
    runtime = MessagingRuntime(uow_factory, db, RabbitMQClient(RABBITMQ_URL))
    await runtime.start()

    exchange = await canal.get_exchange(EXCHANGE_COMMANDS)
    await exchange.publish(aio_pika.Message(b"{lixo"), routing_key="execution.reservar-pecas")
    dlq = await canal.declare_queue(dead_letter_queue(QUEUE_COMMANDS), durable=True)
    mensagem = await _esperar_mensagem(dlq)
    await runtime.stop()

    assert mensagem is not None
    assert "Envelope inválido" in mensagem.headers[ERROR_HEADER]


async def test_falha_tecnica_passa_pelo_retry_e_termina_na_dlq(canal):
    fila_nome = f"teste.falhas.{uuid.uuid4().hex[:8]}"
    cliente = RabbitMQClient(RABBITMQ_URL, retry_delay_ms=200)
    await cliente.connect()
    await cliente.declare_exchanges()
    fila = await cliente.declare_queue_with_retry(fila_nome, EXCHANGE_COMMANDS, (f"{fila_nome}.x",))
    tentativas = []

    async def sempre_falha(envelope):
        tentativas.append(envelope.message_id)
        raise ConnectionError("banco fora do ar")

    await fila.consume(QueueConsumer(cliente, fila_nome, sempre_falha, max_retries=1))
    comando = Envelope.command("ReservarPecas", saga_id="s", order_id=1, payload={})
    await cliente.publish("", fila_nome, comando.to_json().encode(), message_id=comando.message_id, message_type="X")

    dlq = await canal.declare_queue(dead_letter_queue(fila_nome), durable=True)
    mensagem = await _esperar_mensagem(dlq)
    await cliente.close()
    for nome in (fila_nome, f"{fila_nome}.retry", f"{fila_nome}.dlq"):
        await canal.queue_delete(nome)

    assert mensagem is not None
    assert len(tentativas) == 2
    assert mensagem.headers[RETRY_HEADER] == 1


async def test_api_sobe_com_banco_e_mensageria(monkeypatch, mongo):
    from app.main import create_app, lifespan

    monkeypatch.setattr(settings, "RABBITMQ_URL", RABBITMQ_URL)
    monkeypatch.setattr(settings, "MONGODB_URL", os.environ["TEST_MONGODB_URL"])
    monkeypatch.setattr(settings, "MONGODB_DATABASE", mongo[1].name)
    monkeypatch.setattr(settings, "OUTBOX_POLL_SECONDS", 3600)
    app = create_app()

    async with lifespan(app):
        assert await app.state.readiness_checks["database"]() is True
        assert await app.state.readiness_checks["broker"]() is True
