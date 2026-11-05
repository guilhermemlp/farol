"""CLI: rotina diaria em um comando -- sincroniza e imprime o relatorio.

Uso: python -m farol.reports.daily [--days 3] [--compact | --alerts]

Diferente de `farol.reports.today`, este comando TOCA o Garmin e a planilha: ele chama
`farol.pipeline.ingest` (rede + escrita local) e, em seguida, `farol.reports.today` (que so'
le o banco). E' um atalho para o fluxo manual "ingest -> today", nada mais --
toda a logica continua nos dois modulos originais.
"""
import argparse

from . import today
from ..pipeline import ingest


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        description="Sincroniza o Garmin e imprime o relatorio do dia "
                    "(farol.pipeline.ingest + farol.reports.today).")
    parser.add_argument(
        "--days", type=int, default=3,
        help="quantos dias para tras sincronizar (padrao: 3)")
    modo = parser.add_mutually_exclusive_group()
    modo.add_argument(
        "--compact", action="store_true",
        help="relatorio curto (resumo rapido + leitura + dominios)")
    modo.add_argument(
        "--alerts", action="store_true",
        help="so' o que esta' em atencao/alerta")
    args = parser.parse_args(argv)

    ingest.run(args.days)
    today.main(["--compact"] if args.compact else
               ["--alerts"] if args.alerts else [])


if __name__ == "__main__":
    main()
