"""Testes de farol.interpretation: a camada que transforma a classificacao ja
calculada (state.gather) em uma leitura coerente do estado.

A camada e' pura: entra um dict no formato de state.gather(), saem textos e
contagens. Nenhuma funcao aqui recalcula status/z/confianca -- isso e' do
modelo congelado, testado em test_state.py/test_core.py. Estes testes montam
dias sinteticos (mesmo formato de gather) para cobrir as leituras: normal,
atencao isolada, padrao noturno e alerta.
"""
import unittest

from farol import interpretation as interp
from farol.metrics import Metric


def _m(status="ok", confidence="alta", has_data=True, z=None) -> Metric:
    return Metric(
        name="x", value=(1.0 if has_data else None), mean_7d=None, mean_30d=None,
        mean_90d=None, sd_90d=None, z=z, trend="indefinido", status=status,
        confidence=confidence, n_7d=0, n_30d=0, n_90d=0, higher_is_better=True,
    )


def _dominio(status, confianca="alta", metricas=None, streak=None,
             determinantes=None) -> dict:
    """Sintetico, no formato de state._dominios() pos-C2.1. Sem
    `determinantes` explicito, assume que TODO campo de `metricas` e'
    evidencia (o caso de RECUPERACAO/SONO, sem `primario`) -- e' o que
    todo teste que ja existia nesta suite pressupunha implicitamente.
    Passe `determinantes` explicito para simular um dominio com campo
    contextual (CARGA: so' 'tsb' decide, o resto e' contexto)."""
    metricas = metricas or {}
    return {"metricas": metricas, "status": status, "confianca": confianca,
            "streak": streak, "trend": None,
            "determinantes": determinantes if determinantes is not None else list(metricas)}


def _gather(dominios: dict, performance=None, row=None) -> dict:
    return {"dominios": dominios, "performance": performance or {},
            "row": row, "breakpoints": [], "convergencia_streak": None,
            "convergencia_trend": None}


def _metricas(*pares: tuple[str, str]) -> dict:
    """pares: (campo, status). Todas com confianca alta e dado presente."""
    return {c: _m(s) for c, s in pares}


class _Base(unittest.TestCase):
    def setUp(self):
        # RECUPERACAO completa com nucleo ok (caso "saudavel" de partida).
        self.rec_ok = _metricas(
            ("hrv_avg", "ok"), ("rhr", "ok"), ("night_hr_avg", "ok"),
            ("stress_avg", "ok"), ("body_battery_max", "ok"),
            ("respiration_avg", "ok"), ("spo2_avg", "ok"), ("spo2_min", "ok"))
        self.sono_ok = _metricas(
            ("sleep_total_min", "ok"), ("awake_minutes", "ok"),
            ("deep_sleep_min", "ok"), ("rem_sleep_min", "ok"), ("sleep_score", "ok"))
        self.carga_ok = _metricas(("tsb", "ok"))

    def _dias(self, rec=None, sono=None, carga=None):
        return _gather({
            "CARGA": _dominio("ok", metricas=self.carga_ok),
            "RECUPERACAO": _dominio("ok", metricas=dict(self.rec_ok)),
            "SONO": _dominio("ok", metricas=dict(self.sono_ok)),
        } | {
            **({"CARGA": carga} if carga else {}),
            **({"RECUPERACAO": rec} if rec else {}),
            **({"SONO": sono} if sono else {}),
        })


class TestDiaNormal(_Base):
    def test_estado_normal_e_sem_sinais(self):
        dados = self._dias()
        leitura = interp.interpretar(dados)
        self.assertIn("normal/estavel", leitura["estado_fisiologico"])
        self.assertEqual(leitura["sinal_dominante"], "nenhum sinal relevante hoje")
        self.assertEqual(leitura["n_sinais"], 0)
        self.assertIn("sem convergencia", leitura["convergencia"])
        self.assertIn("sem sinais fora do padrao", leitura["conclusao"])

    def test_performance_sem_dado_e_descrita_sem_inventar(self):
        leitura = interp.interpretar(self._dias())
        self.assertIn("sem treino intervalado", leitura["performance"])

    def test_recuperacao_normal_diz_preservada(self):
        leitura = interp.interpretar(self._dias())
        self.assertIn("preservada", leitura["recuperacao"])


class TestAtencaoIsolada(_Base):
    def _um_dominio_1_campo(self):
        rec = _dominio("atencao", metricas=self.rec_ok | {"spo2_min": _m("atencao")})
        return self._dias(rec=rec)

    def test_um_dominio_um_campo_nao_vira_convergencia(self):
        leitura = interp.interpretar(self._um_dominio_1_campo())
        self.assertIn("sem convergencia", leitura["convergencia"])
        self.assertIn("atencao concentrada em RECUPERACAO", leitura["estado_fisiologico"])
        self.assertEqual(leitura["n_dominios_fora"], 1)
        self.assertEqual(leitura["n_sinais"], 1)

    def test_sinal_isolado_nao_e_fadiga_relevante(self):
        leitura = interp.interpretar(self._um_dominio_1_campo())
        self.assertIn("sinal isolado", leitura["conclusao"])

    def test_dominio_fora_carrega_persistencia_do_streak(self):
        rec = _dominio("atencao", metricas=self.rec_ok | {"spo2_min": _m("atencao")},
                       streak={"dias": 2, "data_inicio": "2026-09-03"})
        leitura = interp.interpretar(self._dias(rec=rec))
        self.assertIn("RECUPERACAO", leitura["dominios_fora"])
        self.assertIn("2 dia(s)", leitura["dominios_fora"])


class TestPadraoNoturno(_Base):
    """O dia real da vistoria (04/09): RECUPERACAO e SONO em atencao, cada um
    puxado por 1 campo ligado a noite -- SpO2 minima e sono profundo. O nucleo
    autonomico diurno esta preservado."""

    def _noite_04_09(self):
        rec = _dominio("atencao", metricas=self.rec_ok | {"spo2_min": _m("atencao")})
        sono = _dominio("atencao",
                        metricas=self.sono_ok | {"deep_sleep_min": _m("atencao")})
        return self._dias(rec=rec, sono=sono)

    def test_estado_e_de_atencao_nao_deterioracao(self):
        leitura = interp.interpretar(self._noite_04_09())
        self.assertIn("atencao em 2 dominios", leitura["estado_fisiologico"])
        self.assertIn("sinal concentrado, nao deterioracao ampla",
                      leitura["estado_fisiologico"])
        self.assertNotIn("sistemica", leitura["estado_fisiologico"])

    def test_sinal_dominante_nomeia_a_noite(self):
        leitura = interp.interpretar(self._noite_04_09())
        self.assertIn("alteracao noturna", leitura["sinal_dominante"])
        self.assertIn("SpO2 minima", leitura["sinal_dominante"])
        self.assertIn("Sono profundo", leitura["sinal_dominante"])

    def test_convergencia_fraca_2_sinais_2_dominios(self):
        leitura = interp.interpretar(self._noite_04_09())
        self.assertIn("fraca", leitura["convergencia"])
        self.assertIn("2 dominios", leitura["convergencia"])
        self.assertIn("2 sinais", leitura["convergencia"])

    def test_recuperacao_distinguida_por_campo_periferico(self):
        leitura = interp.interpretar(self._noite_04_09())
        self.assertIn("em atencao", leitura["recuperacao"])
        self.assertIn("complementar", leitura["recuperacao"])
        self.assertNotIn("nucleo autonomico fora", leitura["recuperacao"])

    def test_sono_diz_que_dimensao_foi_afetada(self):
        leitura = interp.interpretar(self._noite_04_09())
        self.assertIn("arquitetura", leitura["sono"])
        self.assertIn("Sono profundo", leitura["sono"])

    def test_conclusao_da_vistoria(self):
        leitura = interp.interpretar(self._noite_04_09())
        self.assertIn("nao ha evidencia suficiente", leitura["conclusao"])
        self.assertIn("sinal de atencao", leitura["conclusao"])

    def test_performance_sem_intervalado_nao_entra(self):
        leitura = interp.interpretar(self._noite_04_09())
        self.assertIn("sem treino intervalado", leitura["performance"])
        self.assertNotIn("queda de output", leitura["conclusao"])


class TestConvergenciaForte(_Base):
    """Varios campos do nucleo em dominios diferentes ao mesmo tempo -- o caso
    em que a regra 8 (PROFILE) realmente sinaliza padrao."""

    def _nucleo_fora_2_dominios(self):
        rec = _dominio("alerta", metricas=self.rec_ok
                       | {"hrv_avg": _m("alerta"), "rhr": _m("atencao")})
        sono = _dominio("atencao", metricas=self.sono_ok
                        | {"sleep_score": _m("atencao"), "deep_sleep_min": _m("atencao")})
        return self._dias(rec=rec, sono=sono)

    def test_estado_detecta_nucleo_comprometido(self):
        leitura = interp.interpretar(self._nucleo_fora_2_dominios())
        self.assertIn("alerta", leitura["estado_fisiologico"])

    def test_convergencia_forte(self):
        leitura = interp.interpretar(self._nucleo_fora_2_dominios())
        self.assertIn("forte", leitura["convergencia"])
        self.assertIn("4 sinais", leitura["convergencia"])

    def test_conclusao_de_alteracao_relevante(self):
        leitura = interp.interpretar(self._nucleo_fora_2_dominios())
        self.assertIn("alteracao relevante", leitura["conclusao"])

    def test_deterioracao_sistemica_true(self):
        leitura = interp.interpretar(self._nucleo_fora_2_dominios())
        self.assertTrue(leitura["deterioracao_sistemica"])


class TestPerformanceComTreino(_Base):
    def test_output_preservado_aparece(self):
        dados = self._dias()
        dados["performance"] = {"output_preserved": "yes",
                                "deterioration_detected": "no"}
        leitura = interp.interpretar(dados)
        self.assertIn("preservada no treino intervalado", leitura["performance"])

    def test_queda_de_output_entra_na_conclusao(self):
        dados = self._dias()
        dados["performance"] = {"output_preserved": "no",
                                "deterioration_detected": "yes"}
        leitura = interp.interpretar(dados)
        self.assertIn("queda de output", leitura["performance"])
        self.assertIn("queda de output", leitura["conclusao"])


class TestCargaContextualNuncaContaComoSinal(_Base):
    """C2.2 (auditoria de 2026-09-08): TSB normal decide CARGA='ok', mesmo
    com CTL/ATL/ratio individualmente fora do padrao -- interpretation nao
    pode reclassificar contexto como sinal, nem contabiliza-lo em
    convergencia/sinal_dominante."""

    def _carga_ok_com_contexto_ruim(self):
        return _dominio(
            "ok",
            metricas={"tsb": _m("ok"), "ctl": _m("alerta"), "atl": _m("atencao"),
                      "atl_ctl_ratio": _m("alerta")},
            determinantes=["tsb"],
        )

    def test_carga_nao_aparece_como_sinal(self):
        sinais = interp.sinais_por_dominio({"CARGA": self._carga_ok_com_contexto_ruim()})
        self.assertNotIn("CARGA", sinais)

    def test_carga_normal_nao_conta_para_convergencia_mesmo_com_sono_alterado(self):
        sono = _dominio("atencao", metricas=self.sono_ok | {"sleep_score": _m("atencao")})
        dados = self._dias(carga=self._carga_ok_com_contexto_ruim(), sono=sono)
        leitura = interp.interpretar(dados)
        # So' 1 dominio realmente fora (SONO) -- CARGA nunca deveria contar,
        # nem para n_dominios_fora nem para a contagem de convergencia.
        self.assertEqual(leitura["n_dominios_fora"], 1)
        self.assertIn("sem convergencia", leitura["convergencia"])
        self.assertIn("atencao concentrada em SONO", leitura["estado_fisiologico"])
        self.assertNotIn("CARGA", leitura["sinal_dominante"])

    def test_carga_nao_aparece_na_lista_de_dominios_fora(self):
        dados = self._dias(carga=self._carga_ok_com_contexto_ruim())
        leitura = interp.interpretar(dados)
        self.assertEqual(leitura["dominios_fora"], "nenhum")

    def test_caso_inverso_tsb_alterado_e_refletido_como_sinal(self):
        # TSB (o unico determinante de CARGA) fora do padrao -- isto TEM que
        # aparecer, sem nenhuma supressao.
        carga_alterada = _dominio(
            "atencao",
            metricas={"tsb": _m("atencao"), "ctl": _m("ok"), "atl": _m("ok"),
                      "atl_ctl_ratio": _m("ok")},
            determinantes=["tsb"],
        )
        dados = self._dias(carga=carga_alterada)
        leitura = interp.interpretar(dados)
        self.assertEqual(leitura["n_dominios_fora"], 1)
        self.assertIn("CARGA", leitura["dominios_fora"])
        sinais = interp.sinais_por_dominio(dados["dominios"])
        self.assertIn("CARGA", sinais)
        self.assertIn("TSB", sinais["CARGA"])


class TestCarga(_Base):
    def test_carga_normal_com_contexto_de_pico(self):
        row = {"atl_ctl_ratio": 1.16, "ctl": 65.14, "tsb": -3.35}
        dados = self._dias()
        dados["row"] = row
        leitura = interp.interpretar(dados)
        self.assertIn("elevada em relacao a media recente", leitura["carga"])
        self.assertIn("nao excepcional", leitura["carga"])

    def test_carga_sem_pico_fica_normal(self):
        dados = self._dias()
        leitura = interp.interpretar(dados)
        self.assertIn("normal/estavel", leitura["carga"])


if __name__ == "__main__":
    unittest.main()
