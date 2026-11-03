"""Detector de candidata a nova quebra de medicao: um campo do Garmin pode
mudar de definicao/fonte no meio do historico. Ja aconteceu duas vezes --
`rhr` em 2026-06-11 (+5,9 bpm, migracao de pipeline, na verdade com o
campo `source` mudando em 2026-06-04) e `awakenings` em 2026-08-01 (19x,
mudanca de definicao da API) -- as duas achadas por auditoria manual
pontual (reports/ANALISE_HISTORICA_MODEL_V1.md), nao por uma rotina que
roda sozinha.

Metodo: CUSUM (Page, 1954) sobre a serie padronizada -- soma cumulativa dos
desvios em relacao a media geral, e o ponto de maior |soma cumulativa| e' a
melhor estimativa de onde um unico deslocamento de nivel aconteceu. Prefere
CUSUM a uma comparacao de janela local (primeira versao deste modulo, ver
historico do commit) porque CUSUM usa toda a serie de cada lado, nao so 14
pontos locais -- a diferenca importa na pratica: rodado contra o historico
real, a janela local ACHAVA a quebra de `awakenings` (deslocamento grande,
19x) mas PERDIA a de `rhr` (deslocamento pequeno, +5,9 bpm contra sd~4 --
abaixo do poder estatistico de uma janela de 14 pontos). CUSUM acha as duas,
a poucos dias da data real documentada.

Recomendacao 11 do parecer tecnico de 2026-09-04. Deliberadamente uma
ESTIMATIVA para revisao humana, nao um classificador automatico: uma
mudanca abrupta de nivel pode ser quebra de medicao (como rhr/awakenings)
OU mudanca fisiologica/de contexto real (doenca, viagem, fase de treino) --
so quem tem o contexto decide qual. Sem teste de significancia formal
(exigiria bootstrap/permutacao para levar em conta autocorrelacao diaria,
complexidade sem base solida para calibrar aqui): reporta o efeito em
desvios-padrao e deixa o piso de "vale revisar" explicito, sem fingir
p-valor que os dados nao sustentam.

Uso: python -m farol.changepoint [--campo CAMPO ...]
"""
import argparse
import sqlite3
from dataclasses import dataclass
from statistics import StatisticsError, pstdev

from . import db

_MIN_PONTOS = 20        # por lado, para media/sd dos segmentos nao serem ruido
_SHIFT_MIN_REPORTAR = 1.0  # SD -- abaixo disso nem entra no relatorio

# Campos numericos de `daily` com cobertura ampla o bastante para os dois
# segmentos (antes/depois) terem pontos suficientes na maioria dos casos.
# Curado, nao "toda coluna de daily": mesma logica de
# farol.correlations._PARES_DE_INTERESSE (evitar pesca estatistica).
_CAMPOS_PADRAO = ["rhr", "hrv_avg", "sleep_score", "total_sleep_minutes",
                  "stress_avg", "night_hr_avg", "awakenings"]


@dataclass(frozen=True)
class Candidata:
    campo: str
    data: str
    media_antes: float
    media_depois: float
    shift_sd: float
    n_antes: int
    n_depois: int


class CampoInvalido(ValueError):
    """`--campo`/`--tabela` nao correspondem a uma coluna real. Levantado
    antes de montar a query -- achado da auditoria de 2026-09-04: `campo`
    entra direto numa f-string SQL (nomes de coluna nao aceitam
    parametrizacao com `?`), e sem essa checagem um valor invalido so
    aparecia como sqlite3.OperationalError cru."""


def _valida_campo(conn: sqlite3.Connection, campo: str, tabela: str) -> None:
    colunas = {r["name"] for r in conn.execute(f"PRAGMA table_info({tabela})")}
    if not colunas:
        raise CampoInvalido(f"tabela '{tabela}' nao existe")
    if campo not in colunas:
        raise CampoInvalido(
            f"'{campo}' nao e' uma coluna de '{tabela}'. Colunas validas: {', '.join(sorted(colunas))}"
        )


def _serie(conn: sqlite3.Connection, campo: str, tabela: str,
           ate: str | None = None) -> list[tuple[str, float]]:
    """`ate` (opcional): so pontos com `date <= ate`. Sem `ate` (default),
    comportamento identico ao anterior -- toda a serie disponivel."""
    _valida_campo(conn, campo, tabela)
    sql = f"SELECT date, {campo} AS v FROM {tabela} WHERE {campo} IS NOT NULL"
    args: list = []
    if ate is not None:
        sql += " AND date <= ?"
        args.append(ate)
    sql += " ORDER BY date"
    return [(r["date"], float(r["v"])) for r in conn.execute(sql, args).fetchall()]


def _cusum_peak(valores: list[float]) -> int | None:
    """Indice (0-based) do ponto que MAXIMIZA |soma cumulativa dos desvios
    padronizados| -- a melhor estimativa de onde um unico deslocamento de
    nivel aconteceu na serie inteira (Page, 1954). None se sd for zero
    (serie constante, sem o que detectar)."""
    media = sum(valores) / len(valores)
    sd = pstdev(valores)
    if not sd:
        return None
    s = 0.0
    picos = []
    for v in valores:
        s += (v - media) / sd
        picos.append(s)
    return max(range(len(picos)), key=lambda i: abs(picos[i]))


def scan(conn: sqlite3.Connection, campo: str, tabela: str = "daily",
         ate: str | None = None) -> Candidata | None:
    """Estimativa de UM candidato a quebra de nivel em `campo` (o mais
    forte, se houver mais de um deslocamento real na serie -- este modulo
    nao faz segmentacao recursiva). None se nao houver dado suficiente, ou
    se o efeito no ponto estimado ficar abaixo de _SHIFT_MIN_REPORTAR.

    `ate` (opcional): restringe a serie a `date <= ate` -- uma leitura as-of
    nao pode estimar uma quebra usando pontos posteriores ao corte. Sem
    `ate` (default), comportamento identico ao anterior."""
    pontos = _serie(conn, campo, tabela, ate=ate)
    if len(pontos) < _MIN_PONTOS * 2:
        return None

    datas = [d for d, _ in pontos]
    valores = [v for _, v in pontos]
    idx = _cusum_peak(valores)
    if idx is None:
        return None

    antes = valores[:idx + 1]
    depois = valores[idx + 1:]
    if len(antes) < _MIN_PONTOS or len(depois) < _MIN_PONTOS:
        return None

    media_antes = sum(antes) / len(antes)
    media_depois = sum(depois) / len(depois)
    try:
        sd = pstdev(antes + depois)
    except StatisticsError:
        return None
    if not sd:
        return None

    shift = (media_depois - media_antes) / sd
    if abs(shift) < _SHIFT_MIN_REPORTAR:
        return None

    return Candidata(
        campo=campo, data=datas[idx + 1], media_antes=round(media_antes, 2),
        media_depois=round(media_depois, 2), shift_sd=round(shift, 2),
        n_antes=len(antes), n_depois=len(depois),
    )


def render_report(conn: sqlite3.Connection, campos: list[str],
                   ate: str | None = None) -> str:
    """`ate` (opcional): repassado a scan() -- ver seu docstring. Sem `ate`
    (default), comportamento identico ao anterior."""
    linhas = [
        "# CANDIDATA A QUEBRA DE MEDICAO -- farol.changepoint",
        "",
        "Estimativa (CUSUM) para revisao humana, nao classificacao",
        "automatica: uma mudanca abrupta de nivel pode ser quebra de",
        "medicao (como rhr/awakenings) OU mudanca fisiologica/de contexto",
        "real (doenca, viagem, fase de treino). So quem tem o contexto",
        "decide qual. Sem teste de significancia formal -- so o efeito em",
        "desvios-padrao.",
        "",
        "| campo | data estimada | media antes | media depois | shift (sd) | n antes/depois |",
        "|-------|----------------|-------------|--------------|------------|----------------|",
    ]
    algo = False
    for campo in campos:
        try:
            c = scan(conn, campo, ate=ate)
        except CampoInvalido as exc:
            linhas.append(f"| {campo} | ERRO: {exc} | | | | |")
            algo = True
            continue
        if c is None:
            continue
        algo = True
        linhas.append(
            f"| {c.campo} | {c.data} | {c.media_antes:g} | {c.media_depois:g} | "
            f"{c.shift_sd:+.2f} | {c.n_antes}/{c.n_depois} |"
        )
    if not algo:
        linhas.append(f"| — | nenhuma candidata com \\|shift\\| >= {_SHIFT_MIN_REPORTAR} SD | | | | |")
    return "\n".join(linhas) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Estima candidata a nova quebra de medicao (CUSUM) em campos de `daily`.")
    parser.add_argument("--campo", action="append", dest="campos",
                         help="Campo a varrer (repetivel). Sem isto, varre o conjunto padrao.")
    args = parser.parse_args()

    conn = db.connect_readonly()
    try:
        print(render_report(conn, args.campos or _CAMPOS_PADRAO))
    finally:
        conn.close()


if __name__ == "__main__":
    main()
