"""Fila de execução pela API: diagnóstico, reparo, finalização e cancelamento."""

import json

from app.application.messages import Envelope
from app.application.use_cases.saga_commands import SagaCommandHandler
from app.infrastructure.mongo.seed import seed

PAYLOAD = {
    "vehicle": {"plate": "ABC1D23", "brand": "Fiat", "model": "Argo"},
    "services": [{"service_type_id": 1, "name": "Troca de óleo", "quantity": 1}],
    "parts": [{"part_id": 2, "name": "Filtro de óleo", "quantity": 2}],
    "notes": "barulho",
}


async def _enfileirar(uow_factory, db, order_id=7, reservar=True):
    await seed(db)
    handler = SagaCommandHandler(uow_factory, queue_capacity=10)
    if reservar:
        await handler.handle(
            Envelope.command(
                "ReservarPecas", saga_id="saga-7", order_id=order_id, payload={"items": [{"part_id": 2, "quantity": 2}]}
            )
        )
    await handler.handle(Envelope.command("EnfileirarExecucao", saga_id="saga-7", order_id=order_id, payload=PAYLOAD))


async def _ultimo_evento(db) -> dict:
    doc = await db["outbox"].find_one(sort=[("created_at", -1)])
    return json.loads(doc["body"])


class TestAndamento:
    async def test_diagnostico_reparo_e_finalizacao_publicam_eventos(self, auth_client, uow_factory, db):
        await _enfileirar(uow_factory, db)
        eventos = []
        for rota, nota in (("diagnosis", "motor batendo"), ("repair", None), ("finish", "teste ok")):
            r = await auth_client.post(f"/api/v1/executions/7/{rota}", json={"note": nota} if nota else None)
            assert r.status_code == 200, r.text
            evento = await _ultimo_evento(db)
            eventos.append((evento["type"], evento["payload"], evento["saga_id"]))

        assert eventos == [
            ("DiagnosticoIniciado", {"note": "motor batendo"}, "saga-7"),
            ("ReparoIniciado", {"note": None}, "saga-7"),
            ("ExecucaoFinalizada", {"note": "teste ok"}, "saga-7"),
        ]
        execucao = (await auth_client.get("/api/v1/executions/7")).json()
        assert execucao["status"] == "FINALIZADA"
        assert [t["status"] for t in execucao["timeline"]] == ["NA_FILA", "EM_DIAGNOSTICO", "EM_REPARO", "FINALIZADA"]

    async def test_finalizar_baixa_as_pecas_reservadas_do_estoque(self, auth_client, uow_factory, db):
        await _enfileirar(uow_factory, db)
        for rota in ("diagnosis", "repair", "finish"):
            await auth_client.post(f"/api/v1/executions/7/{rota}")

        filtro = (await auth_client.get("/api/v1/parts/2")).json()
        assert (filtro["stock_quantity"], filtro["reserved_quantity"]) == (78, 0)
        assert (await db["reservations"].find_one({"order_id": 7}))["status"] == "CONSUMIDA"

    async def test_finalizar_sem_reserva_nao_mexe_no_estoque(self, auth_client, uow_factory, db):
        await _enfileirar(uow_factory, db, reservar=False)
        for rota in ("diagnosis", "repair", "finish"):
            await auth_client.post(f"/api/v1/executions/7/{rota}")

        assert (await auth_client.get("/api/v1/parts/2")).json()["stock_quantity"] == 80

    async def test_cancelar_exige_motivo_e_publica_o_cancelamento(self, auth_client, uow_factory, db):
        await _enfileirar(uow_factory, db)

        sem_motivo = await auth_client.post("/api/v1/executions/7/cancel", json={})
        r = await auth_client.post("/api/v1/executions/7/cancel", json={"reason": "reparo inviável"})

        assert sem_motivo.status_code == 422
        assert (r.json()["status"], r.json()["cancel_reason"]) == ("CANCELADA", "reparo inviável")
        evento = await _ultimo_evento(db)
        assert (evento["type"], evento["payload"]) == ("ExecucaoCancelada", {"reason": "reparo inviável"})

    async def test_nao_pula_etapas(self, auth_client, uow_factory, db):
        await _enfileirar(uow_factory, db)

        r = await auth_client.post("/api/v1/executions/7/finish")

        assert r.status_code == 422
        assert "NA_FILA" in r.json()["detail"]


class TestConsulta:
    async def test_fila_por_ordem_de_chegada_e_filtro_por_status(self, auth_client, uow_factory, db):
        await _enfileirar(uow_factory, db, order_id=1, reservar=False)
        await _enfileirar(uow_factory, db, order_id=2, reservar=False)
        await auth_client.post("/api/v1/executions/1/diagnosis")

        todas = (await auth_client.get("/api/v1/executions")).json()
        na_fila = (await auth_client.get("/api/v1/executions", params={"status": "NA_FILA"})).json()

        assert [e["order_id"] for e in todas] == [1, 2]
        assert [e["order_id"] for e in na_fila] == [2]

    async def test_execucao_inexistente(self, auth_client, uow_factory, db):
        assert (await auth_client.get("/api/v1/executions/999")).status_code == 404
        assert (await auth_client.post("/api/v1/executions/999/diagnosis")).status_code == 404
