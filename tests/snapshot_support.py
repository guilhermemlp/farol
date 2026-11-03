"""Snapshots de texto com atualizacao explicita.

Atualizar um snapshot nunca acontece so' porque a saida mudou: a comparacao
falha e o teste diz o comando. Para regravar, rode a suite (ou apenas estes
testes) com FAROL_UPDATE_SNAPSHOTS=1 -- a mudanca fica visivel no diff do
git, do mesmo jeito que qualquer outra alteracao de codigo.

Novas linhas sao gravadas como LF para nao variar com o sistema; a leitura
usa o modo texto normal (universal newlines).
"""
import os
from pathlib import Path

SNAPSHOTS_DIR = Path(__file__).with_name("snapshots")
_ENV_ATUALIZAR = "FAROL_UPDATE_SNAPSHOTS"


def assert_snapshot(caso, nome: str, texto: str) -> None:
    caminho = SNAPSHOTS_DIR / nome
    if os.environ.get(_ENV_ATUALIZAR) == "1":
        caminho.parent.mkdir(parents=True, exist_ok=True)
        caminho.write_text(texto, encoding="utf-8", newline="\n")
    if not caminho.exists():
        caso.fail(
            f"snapshot ausente: {caminho}. "
            f"Rode com {_ENV_ATUALIZAR}=1 para gerar.")
    esperado = caminho.read_text(encoding="utf-8")
    caso.assertEqual(
        texto, esperado,
        f"snapshot divergente: {nome}. Se a mudanca for intencional, "
        f"rode com {_ENV_ATUALIZAR}=1 e revise o diff.")
