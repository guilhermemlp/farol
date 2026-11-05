"""Testes de farol.reports.charts: primitivas SVG puras do dashboard.

Nao retesta dominio: cada funcao recebe numeros ja calculados e devolve
string SVG. O que se protege aqui e' robustez (serie vazia/1 ponto/sd=0),
ausencia de coordenada invalida e escape de texto vindo do banco.
"""
import re
import unittest
from datetime import date, timedelta

from farol.reports import charts


def _assert_sem_coordenada_invalida(teste, svg: str) -> None:
    teste.assertNotIn("nan", svg.lower())
    teste.assertNotIn("inf", svg.lower())


class TestSparkline(unittest.TestCase):
    def test_serie_vazia_devolve_string_vazia(self):
        self.assertEqual(charts.sparkline_svg([]), "")

    def test_um_ponto_desenha_svg_sem_quebrar(self):
        svg = charts.sparkline_svg([("2026-09-04", 18.0)])
        self.assertIn("<svg", svg)
        self.assertIn('role="img"', svg)
        self.assertIn("<title>", svg)
        _assert_sem_coordenada_invalida(self, svg)

    def test_serie_constante_nao_divide_por_zero(self):
        serie = [(f"2026-09-0{i}", 60.0) for i in range(1, 4)]
        svg = charts.sparkline_svg(serie, baseline=60.0)
        _assert_sem_coordenada_invalida(self, svg)
        self.assertIn("polyline", svg)

    def test_baseline_vira_linha_tracejada(self):
        serie = [(f"2026-09-0{i}", 50.0 + i) for i in range(1, 4)]
        svg = charts.sparkline_svg(serie, baseline=52.0)
        self.assertIn('class="spark-base"', svg)

    def test_sem_baseline_nao_desenha_linha(self):
        svg = charts.sparkline_svg([1.0, 2.0, 3.0])
        self.assertNotIn('class="spark-base"', svg)

    def test_aceita_lista_de_valores_sem_data(self):
        svg = charts.sparkline_svg([1.0, 2.0, 3.0])
        self.assertIn("polyline", svg)

    def test_escapa_titulo(self):
        svg = charts.sparkline_svg([1.0, 2.0], title="<script>alert(1)</script>")
        self.assertNotIn("<script>", svg)
        self.assertIn("&lt;script&gt;", svg)


class TestBullet(unittest.TestCase):
    def test_valor_ausente_devolve_string_vazia(self):
        self.assertEqual(charts.bullet_svg(None, 60.0, 5.0), "")

    def test_faixa_e_media_com_sd(self):
        svg = charts.bullet_svg(58.0, mean=62.0, sd=4.0, status="atencao", unit=" ms")
        self.assertIn('class="bullet-band"', svg)
        self.assertIn('class="bullet-mean"', svg)
        self.assertIn("bullet-atencao", svg)
        _assert_sem_coordenada_invalida(self, svg)

    def test_sem_sd_ainda_desenha_marcador(self):
        svg = charts.bullet_svg(58.0, mean=None, sd=None, status="ok")
        self.assertNotIn("bullet-band", svg)
        self.assertIn("bullet-dot bullet-ok", svg)
        _assert_sem_coordenada_invalida(self, svg)

    def test_valor_fora_da_faixa_fica_dentro_do_viewbox(self):
        svg = charts.bullet_svg(200.0, mean=60.0, sd=2.0, width=200)
        cx = float(svg.split('class="bullet-dot bullet-ok" cx="')[1].split('"')[0])
        self.assertGreaterEqual(cx, 5.0)
        self.assertLessEqual(cx, 195.0)

    def test_status_desconhecido_nao_vira_css_arbitrario(self):
        svg = charts.bullet_svg(10.0, mean=10.0, sd=1.0, status="<x>")
        self.assertIn("bullet-ok", svg)
        self.assertNotIn("<x>", svg)


class TestPerformance(unittest.TestCase):
    def _points(self, com_fc=True):
        return [
            {"date": "2026-09-01", "name": "Corrida", "pace": 6.0,
             "hr": 150.0 if com_fc else None, "distance_km": 5.0,
             "scope": "sessao corrida", "race": False},
            {"date": "2026-09-03", "name": "Tiros", "pace": 5.4,
             "hr": 168.0 if com_fc else None, "distance_km": 8.0,
             "scope": "segmentos ativos", "race": False},
        ]

    def test_sem_pontos_devolve_string_vazia(self):
        self.assertEqual(charts.pace_trend_svg([]), "")
        self.assertEqual(charts.pace_trend_svg([], {"min": 5.0, "max": 6.0}), "")

    def test_um_ponto_desenha_svg(self):
        svg = charts.pace_trend_svg(self._points()[:1])
        self.assertIn("<svg", svg)
        _assert_sem_coordenada_invalida(self, svg)

    def test_faixa_prevista_aparece_quando_ha_predicao(self):
        svg = charts.pace_trend_svg(self._points(), {"min": 5.2, "max": 5.8})
        self.assertIn('class="pred-band"', svg)

    def test_ponto_de_prova_recebe_classe_propria(self):
        points = self._points()
        points[1]["race"] = True
        svg = charts.pace_trend_svg(points)
        self.assertIn("dot-race", svg)

    def test_escapa_nome_da_atividade(self):
        points = self._points()
        points[0]["name"] = "<script>alert(1)</script>"
        svg = charts.pace_trend_svg(points)
        self.assertNotIn("<script>", svg)
        self.assertIn("&lt;script&gt;", svg)

    def test_sem_fc_devolve_fallback_textual(self):
        html = charts.pace_hr_svg(self._points(com_fc=False))
        self.assertIn("chart-empty", html)
        self.assertNotIn("<svg", html)

    def test_com_fc_desenha_pontos(self):
        svg = charts.pace_hr_svg(self._points())
        self.assertEqual(svg.count('class="perf-dot'), 2)
        _assert_sem_coordenada_invalida(self, svg)


class TestPmc(unittest.TestCase):
    INICIO = "2026-03-09"
    FIM = "2026-04-07"

    def _serie(self, dias=5, inicio=date(2026, 3, 9)):
        return [{"date": (inicio + timedelta(days=i)).isoformat(),
                 "ctl": 50.0 + i, "atl": 55.0 + i, "tsb": -5.0 + i}
                for i in range(dias)]

    def _svg(self, serie=None, anotacoes=None):
        return charts.pmc_svg(serie if serie is not None else self._serie(),
                              anotacoes, inicio=self.INICIO, fim=self.FIM)

    def test_sem_serie_devolve_vazio(self):
        self.assertEqual(charts.pmc_svg([], inicio=self.INICIO, fim=self.FIM), "")

    def test_barras_de_tsb_por_sinal(self):
        serie = self._serie()
        serie[4]["tsb"] = 2.0
        svg = self._svg(serie)
        self.assertEqual(svg.count('class="tsb-neg"'), 4)
        self.assertEqual(svg.count('class="tsb-pos"'), 1)

    def test_tsb_ausente_nao_desenha_barra(self):
        serie = self._serie()
        serie[0]["tsb"] = None
        self.assertEqual(self._svg(serie).count("<rect"), 4)

    def test_buraco_de_dia_quebra_a_linha(self):
        serie = self._serie(2) + self._serie(2, inicio=date(2026, 3, 20))
        self.assertEqual(self._svg(serie).count('class="pmc-line pmc-ctl"'), 2)

    def test_anotacao_fora_da_janela_e_ignorada(self):
        anotacoes = [{"date": "2026-06-11", "kind": "quebra", "label": "quebra"},
                     {"date": "2026-01-01", "kind": "prova", "label": "antiga"}]
        self.assertNotIn("anno-line", self._svg(anotacoes=anotacoes))

    def test_anotacao_dentro_da_janela_desenha_linha_e_rotulo(self):
        anotacoes = [{"date": "2026-03-10", "kind": "lesao",
                      "label": "lesao: <panturrilha>"}]
        svg = self._svg(anotacoes=anotacoes)
        self.assertEqual(svg.count('class="anno-line anno-lesao"'), 1)
        self.assertIn("&lt;panturrilha&gt;", svg)
        self.assertNotIn("<panturrilha>", svg)

    def test_rotulos_proximos_nao_se_sobrepoem(self):
        anotacoes = [
            {"date": "2026-03-10", "kind": "prova", "label": "Prova longa de teste"},
            {"date": "2026-03-11", "kind": "prova", "label": "Outra prova longa"},
            {"date": "2026-03-12", "kind": "quebra", "label": "quebra de medicao"},
        ]
        svg = self._svg(anotacoes=anotacoes)
        # Toda anotacao mantem a linha vertical; o texto pode ser omitido
        # quando encostaria no vizinho (as notas abaixo listam todas).
        self.assertEqual(svg.count('class="anno-line'), 3)
        self.assertLess(svg.count('<text class="anno-label'), 3)
        _assert_sem_coordenada_invalida(self, svg)

    def test_tipo_desconhecido_nao_vira_css_arbitrario(self):
        svg = self._svg(anotacoes=[{"date": "2026-03-10", "kind": "<x>", "label": "y"}])
        self.assertIn("anno-quebra", svg)
        self.assertNotIn("<x>", svg)

    def test_eixo_y_cresce_para_cima(self):
        svg = self._svg()
        rotulos = [float(v) for v in
                   re.findall(r'class="axis-label"[^>]*>(-?[\d.]+)<', svg)]
        self.assertEqual(len(rotulos), 3)
        self.assertGreater(rotulos[0], rotulos[-1])

    def test_nao_gera_coordenada_invalida(self):
        svg = self._svg(anotacoes=[{"date": "2026-03-10", "kind": "prova", "label": "10k"}])
        _assert_sem_coordenada_invalida(self, svg)


if __name__ == "__main__":
    unittest.main()
