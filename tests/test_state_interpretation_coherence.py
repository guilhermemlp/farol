"""Propriedade transversal da Frente 2 (C2.3, auditoria de 2026-09-08):
coerencia entre a classificacao oficial de `state` e qualquer saida
estruturada posterior (`interpretation`, `daily_analysis`).

Nao retesta a matematica de domain_status()/thresholds (isso e' de
test_state.py) nem cada leitura textual isolada (test_interpretation.py,
test_daily_analysis.py). Este arquivo prova, com fixtures sinteticas e sem
banco nenhum, as duas metades da mesma propriedade, para varios cenarios de
uma vez:

    (1) se state.dominios[D].status == 'ok', nenhuma saida estruturada
        posterior pode reclassificar D como alterado;
    (2) se uma saida estruturada afirma que D esta alterado, o status
        OFICIAL de D em state (a mesma fonte, nao um calculo paralelo) tem
        que ser atencao/alerta.

Chama farol.daily_analysis._conclusao/_leitura_interpretacao diretamente
com dicts sinteticos -- essas funcoes nao tocam banco, so' recebem
`dominios`/`performance` ja prontos (mesmo padrao usado para testa-las em
test_daily_analysis.py, sem a fixture de banco quando nao e' preciso).
"""
import unittest

from farol import daily_analysis, interpretation as interp
from farol.metrics import Metric


def _m(status: str, has_data: bool = True) -> Metric:
    return Metric(
        name="x", value=(1.0 if has_data else None), mean_7d=None, mean_30d=None,
        mean_90d=None, sd_90d=None, z=None, trend="indefinido", status=status,
        confidence="alta", n_7d=0, n_30d=0, n_90d=0, higher_is_better=True,
    )


def _dominio(status: str, metricas: dict[str, Metric], determinantes: list[str]) -> dict:
    """Formato de state._dominios() pos-C2.1 -- so' os campos que
    interpretation/daily_analysis realmente consomem."""
    return {"metricas": metricas, "status": status, "confianca": "alta",
            "determinantes": determinantes, "streak": None, "trend": None}


def _rec_ok() -> dict:
    return _dominio("ok", {c: _m("ok") for c in (
        "hrv_avg", "rhr", "night_hr_avg", "stress_avg", "body_battery_max",
        "respiration_avg", "spo2_avg", "spo2_min")},
        determinantes=["hrv_avg", "rhr", "night_hr_avg", "stress_avg",
                       "body_battery_max", "respiration_avg", "spo2_avg", "spo2_min"])


def _sono_ok() -> dict:
    return _dominio("ok", {c: _m("ok") for c in (
        "sleep_total_min", "awake_minutes", "deep_sleep_min", "rem_sleep_min", "sleep_score")},
        determinantes=["sleep_total_min", "awake_minutes", "deep_sleep_min",
                       "rem_sleep_min", "sleep_score"])


def _carga_ok() -> dict:
    return _dominio("ok", {"tsb": _m("ok")}, determinantes=["tsb"])


# ------------------------------------------------------- cenarios de teste
#
# Cada cenario: (nome, dominios, alterados_esperados). `alterados_esperados`
# e' o conjunto de dominios que TODA saida estruturada posterior deve
# concordar estarem fora do padrao -- nem mais, nem menos.

def _cenario_carga_ok_contexto_ruim():
    carga = _dominio(
        "ok",
        {"tsb": _m("ok"), "ctl": _m("alerta"), "atl": _m("atencao"),
         "atl_ctl_ratio": _m("alerta")},
        determinantes=["tsb"],
    )
    return "CARGA ok, CTL/ATL/ratio contextuais fora do padrao", \
        {"CARGA": carga, "RECUPERACAO": _rec_ok(), "SONO": _sono_ok()}, set()


def _cenario_carga_determinante_alterado():
    carga = _dominio(
        "atencao",
        {"tsb": _m("atencao"), "ctl": _m("ok"), "atl": _m("ok"), "atl_ctl_ratio": _m("ok")},
        determinantes=["tsb"],
    )
    return "CARGA com TSB (determinante) alterado", \
        {"CARGA": carga, "RECUPERACAO": _rec_ok(), "SONO": _sono_ok()}, {"CARGA"}


def _cenario_carga_ok_contexto_ruim_mais_sono_alterado():
    carga = _dominio(
        "ok",
        {"tsb": _m("ok"), "ctl": _m("alerta"), "atl": _m("alerta"),
         "atl_ctl_ratio": _m("alerta")},
        determinantes=["tsb"],
    )
    sono = _sono_ok()
    sono["metricas"]["sleep_score"] = _m("atencao")
    sono["status"] = "atencao"
    return "CARGA ok com contexto ruim + SONO genuinamente alterado", \
        {"CARGA": carga, "RECUPERACAO": _rec_ok(), "SONO": sono}, {"SONO"}


def _cenario_recuperacao_alerta():
    rec = _rec_ok()
    rec["metricas"]["hrv_avg"] = _m("alerta")
    rec["status"] = "alerta"
    return "RECUPERACAO com nucleo (hrv_avg) em alerta", \
        {"CARGA": _carga_ok(), "RECUPERACAO": rec, "SONO": _sono_ok()}, {"RECUPERACAO"}


def _cenario_tudo_normal():
    return "Tudo dentro do padrao", \
        {"CARGA": _carga_ok(), "RECUPERACAO": _rec_ok(), "SONO": _sono_ok()}, set()


def _cenario_carga_sem_dado_nao_conta_como_alterado():
    carga = _dominio("sem_dado", {"tsb": _m("sem_dado", has_data=False)}, determinantes=["tsb"])
    return "CARGA sem dado -- nao e' o mesmo que alterado", \
        {"CARGA": carga, "RECUPERACAO": _rec_ok(), "SONO": _sono_ok()}, set()


_CENARIOS = [
    _cenario_carga_ok_contexto_ruim,
    _cenario_carga_determinante_alterado,
    _cenario_carga_ok_contexto_ruim_mais_sono_alterado,
    _cenario_recuperacao_alerta,
    _cenario_tudo_normal,
    _cenario_carga_sem_dado_nao_conta_como_alterado,
]


class TestCoerenciaStateInterpretation(unittest.TestCase):
    """Propriedade nas duas direcoes, aplicada a sinais_por_dominio() em
    cada cenario -- nao um caso isolado."""

    def test_sinais_por_dominio_bate_exatamente_com_o_esperado(self):
        for construir in _CENARIOS:
            nome, dominios, esperados = construir()
            with self.subTest(cenario=nome):
                sinais = interp.sinais_por_dominio(dominios)
                self.assertEqual(set(sinais.keys()), esperados)

    def test_propriedade_inversa_status_ok_nunca_e_reclassificado(self):
        # Se state diz 'ok', nenhuma saida estruturada pode contradizer.
        for construir in _CENARIOS:
            nome, dominios, _ = construir()
            with self.subTest(cenario=nome):
                sinais = interp.sinais_por_dominio(dominios)
                for nome_dominio, d in dominios.items():
                    if d["status"] == "ok":
                        self.assertNotIn(nome_dominio, sinais)

    def test_propriedade_direta_alterado_tem_status_oficial_compativel(self):
        # Se uma saida diz que D esta alterado, o status OFICIAL de D (a
        # mesma fonte que domain_status() produz) tem que ser atencao/alerta
        # -- nunca 'ok' nem 'sem_dado'.
        for construir in _CENARIOS:
            nome, dominios, _ = construir()
            with self.subTest(cenario=nome):
                sinais = interp.sinais_por_dominio(dominios)
                for nome_dominio in sinais:
                    self.assertIn(dominios[nome_dominio]["status"], ("atencao", "alerta"))


class TestCoerenciaInterpretarCompleto(unittest.TestCase):
    """Mesma propriedade, agora sobre interpretation.interpretar() (o dict
    completo consumido por daily_analysis/today/dashboard)."""

    def _gather(self, dominios):
        return {"dominios": dominios, "performance": {}, "row": None,
                "breakpoints": [], "convergencia_streak": None, "convergencia_trend": None}

    def test_n_dominios_fora_e_dominios_fora_batem_com_o_esperado(self):
        for construir in _CENARIOS:
            nome, dominios, esperados = construir()
            with self.subTest(cenario=nome):
                leitura = interp.interpretar(self._gather(dominios))
                self.assertEqual(leitura["n_dominios_fora"], len(esperados))
                for nome_dominio in esperados:
                    self.assertIn(nome_dominio, leitura["dominios_fora"])
                if not esperados:
                    self.assertEqual(leitura["dominios_fora"], "nenhum")

    def test_sinal_dominante_so_cita_dominios_realmente_alterados(self):
        for construir in _CENARIOS:
            nome, dominios, esperados = construir()
            with self.subTest(cenario=nome):
                leitura = interp.interpretar(self._gather(dominios))
                dominios_ok = [n for n, d in dominios.items() if d["status"] == "ok"]
                for nome_dominio in dominios_ok:
                    self.assertNotIn(nome_dominio, leitura["sinal_dominante"])


class TestCoerenciaDailyAnalysis(unittest.TestCase):
    """Mesma propriedade sobre as funcoes de farol.daily_analysis que
    produzem texto de conclusao -- chamadas direto com dicts sinteticos,
    sem banco (_conclusao/_leitura_interpretacao so' recebem dominios/
    performance/row prontos, nunca tocam farol_daily_state)."""

    def test_conclusao_nao_cita_dominio_ok_como_evidencia_de_problema(self):
        for construir in _CENARIOS:
            nome, dominios, esperados = construir()
            with self.subTest(cenario=nome):
                linhas = daily_analysis._conclusao(dominios, {})
                texto = "\n".join(linhas)
                if "CARGA" not in esperados:
                    self.assertIn("Evidencia de carga elevada?** Nao.", texto)
                if "RECUPERACAO" not in esperados:
                    self.assertIn("Recuperacao comprometida?** Nao.", texto)
                if "SONO" not in esperados:
                    self.assertIn("Sono comprometido?** Nao.", texto)

    def test_leitura_interpretacao_lista_exatamente_os_dominios_alterados(self):
        for construir in _CENARIOS:
            nome, dominios, esperados = construir()
            with self.subTest(cenario=nome):
                linhas = daily_analysis._leitura_interpretacao(dominios, {}, None)
                texto = "\n".join(linhas)
                bloco_sinais = texto.split("Sinais fora do padrao por dominio")[1]
                for nome_dominio in ("CARGA", "RECUPERACAO", "SONO"):
                    if nome_dominio in esperados:
                        self.assertIn(nome_dominio, bloco_sinais)
                    else:
                        self.assertNotIn(nome_dominio, bloco_sinais)


if __name__ == "__main__":
    unittest.main()
