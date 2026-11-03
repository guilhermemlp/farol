"""Suporte aos testes golden: rodam contra o `data/farol.db` real.

O banco real tem os dados pessoais do atleta e nao e' versionado (gitignore:
`farol.db`, `profile.yaml`). Ele e' reconstruido localmente por
`python -m farol.ingest` (que faz login no Garmin) ou `python -m farol.load`
(que le a planilha). Num ambiente limpo (CI, checkout novo), esse banco nao
existe -- e os testes golden que dependem dele devem ser PULADOS com mensagem
clara, nao falhar com erro de banco inexistente (premissa da auditoria F8:
"o banco real nao esta versionado; se um CI futuro rodar em ambiente limpo,
precisara de fixture" -- aqui a fixture e' a presenca do banco real, e a
ausencia dela vira skip informativo, nunca falha).

Os testes em memoria (a grande maioria) nao usam isto: so as classes que
chamam `db.connect()` sem argumento (banco real).
"""
import os
import unittest

from farol.config import DB_PATH


def skip_unless_real_db():
    """Decorator de classe: pula a classe inteira quando `data/farol.db` nao
    existe (ou a env `FAROL_DB_PATH` aponta para outro lugar). Uso:

        @skip_unless_real_db()
        class TestGoldenSample(unittest.TestCase):
            ...

    A mensagem explica por que e o que rodar para ter o banco."
    """
    return unittest.skipUnless(
        os.path.exists(DB_PATH),
        f"sem banco real em {DB_PATH} (dados pessoais nao versionados); "
        "rode `python -m farol.load` (planilha) ou `python -m farol.ingest` "
        "(Garmin) para criar, ou rode os testes em memoria sem depender do banco.",
    )