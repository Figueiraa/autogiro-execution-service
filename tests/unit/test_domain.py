"""Regras de estoque e da execução na oficina."""

from datetime import UTC, datetime

import pytest

from app.domain.entities.execution import EVENT_FOR_STATUS, VALID_TRANSITIONS, Execution, ExecutionStatus
from app.domain.entities.part import Part
from app.domain.exceptions import BusinessRuleError, InvalidStatusTransitionError

S = ExecutionStatus
T0 = datetime(2026, 11, 10, 9, 0, tzinfo=UTC)


class TestPeca:
    def test_disponivel_e_o_estoque_menos_o_reservado(self):
        assert Part(name="Filtro", unit_price=30, stock_quantity=10, reserved_quantity=4).available == 6

    def test_entrada_de_estoque(self):
        peca = Part(name="Filtro", unit_price=30, stock_quantity=10)
        peca.adjust_stock(5)
        assert peca.stock_quantity == 15

    def test_baixa_nao_pode_deixar_o_estoque_abaixo_do_reservado(self):
        peca = Part(name="Filtro", unit_price=30, stock_quantity=10, reserved_quantity=8)
        with pytest.raises(BusinessRuleError, match="abaixo das 8 unidades reservadas"):
            peca.adjust_stock(-3)
        assert peca.stock_quantity == 10


def _execucao() -> Execution:
    return Execution.enqueue(
        order_id=7,
        saga_id="s-1",
        vehicle={"plate": "ABC1D23"},
        services=[{"name": "Troca de óleo"}],
        parts=[],
        notes="barulho",
        now=T0,
    )


class TestExecucao:
    def test_entra_na_fila_com_a_linha_do_tempo_iniciada(self):
        execucao = _execucao()

        assert (execucao.status, execucao.queued_at) == (S.NA_FILA, T0)
        assert [(t.status, t.at) for t in execucao.timeline] == [(S.NA_FILA, T0)]

    def test_diagnostico_reparo_e_finalizacao(self):
        execucao = _execucao()

        eventos = [
            execucao.advance(S.EM_DIAGNOSTICO, "motor batendo"),
            execucao.advance(S.EM_REPARO),
            execucao.advance(S.FINALIZADA, "teste de rodagem ok"),
        ]

        assert eventos == ["DiagnosticoIniciado", "ReparoIniciado", "ExecucaoFinalizada"]
        assert [t.status for t in execucao.timeline] == [S.NA_FILA, S.EM_DIAGNOSTICO, S.EM_REPARO, S.FINALIZADA]
        assert execucao.timeline[1].note == "motor batendo"

    def test_nao_pula_etapas(self):
        with pytest.raises(InvalidStatusTransitionError):
            _execucao().advance(S.FINALIZADA)

    def test_cancelamento_exige_motivo_e_e_registrado(self):
        execucao = _execucao()
        with pytest.raises(BusinessRuleError, match="motivo"):
            execucao.advance(S.CANCELADA)

        assert execucao.advance(S.CANCELADA, "reparo inviável") == "ExecucaoCancelada"
        assert execucao.cancel_reason == "reparo inviável"

    def test_finalizada_e_cancelada_sao_estados_finais(self):
        assert VALID_TRANSITIONS[S.FINALIZADA] == []
        assert VALID_TRANSITIONS[S.CANCELADA] == []

    def test_toda_mudanca_publica_um_evento(self):
        destinos = {destino for destinos in VALID_TRANSITIONS.values() for destino in destinos}
        assert destinos == set(EVENT_FOR_STATUS)
