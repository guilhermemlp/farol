"""Testes de farol.insights.calibration_review: parse do CALIBRACAO_LOG.txt e
estatisticas de concordancia, so quando ha entradas preenchidas suficientes."""
import re
import sqlite3
import unittest
from datetime import date, timedelta

from farol.core import db
from farol.insights import calibration, calibration_review
from farol.domain.daily_state_builder import build_daily_state

_PROFILE = {"recovery_index": {"weights": {
    "sleep_score": 0.30, "hrv_avg": 0.30, "rhr": 0.20, "tsb": 0.20,
}}}


def _memory_db() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    db.ensure_schema(conn)
    return conn


def _preenche_bateu(texto_entrada: str, resposta: str) -> str:
    """Simula o preenchimento manual do campo -- troca a linha '-' em
    branco por '- {resposta}', a mesma edicao que um humano faria no
    arquivo."""
    novo, n = re.subn(
        r"(BATEU COM A REALIDADE\?[^\n]*\n)-\n",
        lambda m: m.group(1) + f"- {resposta}\n",
        texto_entrada, count=1,
    )
    assert n == 1, "fixture nao encontrou a linha em branco esperada"
    return novo


class TestParseLog(unittest.TestCase):
    def test_log_vazio_devolve_lista_vazia(self):
        self.assertEqual(calibration_review.parse_log(""), [])

    def test_log_real_com_uma_entrada_preenchida(self):
        # Formato identico ao que calibration.append_entry produz de
        # verdade -- a mesma fixture de duas entradas do log real.
        texto = (
            "============================================================\n"
            "DATA: 2026-09-03\n"
            "============================================================\n"
            "\n"
            "FAROL (state.gather + farol.reports.today)\n"
            "- INDICE DE RECUPERACAO: 7.1/10 -- dentro do seu normal\n"
            "- CARGA: NORMAL\n"
            "- RECUPERACAO: NORMAL\n"
            "- SONO: NORMAL\n"
            "- PERFORMANCE: sem dado (sem treino intervalado no dia)\n"
            "- CONVERGENCIA: nenhum padrao (no maximo 1 dominio fora do normal)\n"
            "\n"
            "LONGITUDINAL\n"
            "- CARGA: sem streak ativo\n"
            "\n"
            "BATEU COM A REALIDADE? (preencher manualmente: sim / nao / parcial)\n"
            "- sim\n"
            "\n"
            "OBSERVACOES:\n"
            "- nenhuma\n"
            "\n"
            "\n"
            "============================================================\n"
            "DATA: 2026-09-04\n"
            "============================================================\n"
            "\n"
            "FAROL (state.gather + farol.reports.today)\n"
            "- INDICE DE RECUPERACAO: 6.9/10 -- dentro do seu normal\n"
            "\n"
            "BATEU COM A REALIDADE? (preencher manualmente: sim / nao / parcial)\n"
            "-\n"
            "\n"
            "OBSERVACOES:\n"
            "-\n"
        )
        entradas = calibration_review.parse_log(texto)
        self.assertEqual(len(entradas), 2)
        self.assertEqual(entradas[0].date, "2026-09-03")
        self.assertEqual(entradas[0].bateu, "sim")
        self.assertEqual(entradas[1].date, "2026-09-04")
        self.assertIsNone(entradas[1].bateu)


class TestBuildReport(unittest.TestCase):
    """Gera um log sintetico via o pipeline real (build_entry/render_entry),
    preenche BATEU COM A REALIDADE? por edicao de texto (como um humano
    faria), e confere as estatisticas."""

    def setUp(self):
        self.conn = _memory_db()
        self.base_date = date(2026, 6, 1)
        for i in range(120, -1, -1):
            d = (self.base_date - timedelta(days=i)).isoformat()
            self.conn.execute(
                """INSERT INTO daily
                   (date, rhr, hrv_avg, stress_avg, body_battery_max,
                    night_hr_avg, total_sleep_minutes, awake_minutes,
                    deep_sleep_minutes, rem_sleep_minutes, sleep_score)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (d, 68 + i % 5, 17.0 + (i % 4) * 0.5, 40 + i % 10, 28 + i % 6,
                 80 + i % 5, 400 + i % 40, 30 + i % 20, 70 + i % 20,
                 50 + i % 20, 50 + i % 10),
            )
        self.conn.commit()
        for i in range(120, -1, -1):
            build_daily_state(self.conn, (self.base_date - timedelta(days=i)).isoformat())

    def tearDown(self):
        self.conn.close()

    def _gera_log(self, n_dias: int, respostas: list[str | None]) -> str:
        """n_dias entradas consecutivas terminando em base_date, preenchidas
        conforme `respostas` (None = deixa em branco)."""
        assert len(respostas) == n_dias
        blocos = []
        for i in range(n_dias):
            d = (self.base_date - timedelta(days=n_dias - 1 - i)).isoformat()
            entry = calibration.build_entry(self.conn, d, _PROFILE)
            texto = calibration.render_entry(entry)
            if respostas[i] is not None:
                texto = _preenche_bateu(texto, respostas[i])
            blocos.append(texto)
        return "\n".join(blocos)

    def test_menos_de_14_preenchidas_nao_calcula_estatistica(self):
        texto = self._gera_log(13, ["sim"] * 13)
        relatorio = calibration_review.build_report(texto)
        self.assertIn("Menos de 14", relatorio)
        self.assertNotIn("Distribuicao", relatorio)

    def test_14_preenchidas_calcula_distribuicao(self):
        respostas = ["sim"] * 10 + ["nao"] * 2 + ["parcial"] * 2
        texto = self._gera_log(14, respostas)
        relatorio = calibration_review.build_report(texto)
        self.assertIn("Distribuicao", relatorio)
        self.assertIn("sim: 10/14 (71%)", relatorio)
        self.assertIn("nao: 2/14 (14%)", relatorio)
        self.assertIn("parcial: 2/14 (14%)", relatorio)

    def test_entradas_em_branco_nao_contam_para_o_minimo(self):
        # 14 entradas totais, mas so 10 preenchidas -- ainda abaixo do piso.
        respostas = ["sim"] * 10 + [None] * 4
        texto = self._gera_log(14, respostas)
        relatorio = calibration_review.build_report(texto)
        self.assertIn("10 com", relatorio)
        self.assertIn("Menos de 14", relatorio)

    def test_resposta_com_frase_livre_classifica_pela_primeira_palavra(self):
        respostas = ["sim, mas cansado"] * 14
        texto = self._gera_log(14, respostas)
        relatorio = calibration_review.build_report(texto)
        self.assertIn("sim: 14/14 (100%)", relatorio)


if __name__ == "__main__":
    unittest.main()
