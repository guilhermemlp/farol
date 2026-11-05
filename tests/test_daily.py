"""Testes de farol.reports.daily: so' o encadeamento ingest -> today.

A sincronizacao real e o relatorio ja' sao testados nos modulos originais; aqui
so' importa que o atalho nao troque a ordem, o numero de dias nem o modo.
"""
import unittest
from unittest import mock

from farol.reports import daily


class TestDaily(unittest.TestCase):
    def _rodar(self, argv: list[str]) -> list[tuple[str, object]]:
        chamadas: list[tuple[str, object]] = []
        with mock.patch.object(daily.ingest, "run",
                               side_effect=lambda d: chamadas.append(("ingest", d))), \
                mock.patch.object(daily.today, "main",
                                  side_effect=lambda a: chamadas.append(("today", a))):
            daily.main(argv)
        return chamadas

    def test_ingest_roda_antes_do_relatorio(self):
        self.assertEqual(self._rodar([]), [("ingest", 3), ("today", [])])

    def test_dias_e_modo_sao_repassados(self):
        self.assertEqual(
            self._rodar(["--days", "1", "--compact"]),
            [("ingest", 1), ("today", ["--compact"])])
        self.assertEqual(
            self._rodar(["--alerts"]),
            [("ingest", 3), ("today", ["--alerts"])])


if __name__ == "__main__":
    unittest.main()
