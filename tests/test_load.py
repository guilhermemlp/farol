"""CLIs que nao devem executar trabalho ao receber --help ou argumento invalido.

Regressao: `python -m farol.pipeline.load --help` ignorava os argumentos e
comecava a reconstruir o banco pessoal. Ajuda e erro de uso precisam sair antes
de qualquer conexao (de escrita ou leitura) ou leitura de planilha.
"""
import unittest
from unittest import mock

from farol.insights import correlations
from farol.pipeline import load


class TestLoadCliArgs(unittest.TestCase):
    def _assert_no_work(self, argv):
        with mock.patch.object(load.db, "connect_write") as connect_write, \
                mock.patch.object(load, "load_profile") as load_profile, \
                mock.patch.object(load.openpyxl, "load_workbook") as load_workbook:
            with self.assertRaises(SystemExit) as ctx:
                load.main(argv)
        connect_write.assert_not_called()
        load_profile.assert_not_called()
        load_workbook.assert_not_called()
        return ctx.exception.code

    def test_help_sai_sem_tocar_no_banco(self):
        self.assertEqual(self._assert_no_work(["--help"]), 0)

    def test_argumento_invalido_sai_sem_tocar_no_banco(self):
        self.assertNotEqual(self._assert_no_work(["--nao-existe"]), 0)


class TestCorrelationsCliArgs(unittest.TestCase):
    def test_help_sai_sem_abrir_o_banco(self):
        with mock.patch.object(correlations.db, "connect_readonly") as connect:
            with self.assertRaises(SystemExit) as ctx:
                correlations.main(["--help"])
        connect.assert_not_called()
        self.assertEqual(ctx.exception.code, 0)


if __name__ == "__main__":
    unittest.main()
