"""Correlacao defasada (lag) entre deltas contra a propria baseline de 90
dias -- nao entre valores brutos.

Recomendacao 8 do parecer tecnico de 2026-09-04, respondendo diretamente ao
pedido de comparar estresse (e outras metricas) por correlacao. Duas
decisoes deliberadas, as duas para nao repetir erros que series temporais
diarias de fisiologia convidam:

1. Correlaciona `delta_vs_baseline` (farol.core.metrics.Metric: valor menos a
   propria media de 90 dias), nao o valor bruto. Series fisiologicas tem
   tendencia lenta compartilhada (estacao, fase de treino, deriva de sensor,
   ou quebra de fonte tipo a de 11/06 -- ver docs/MODEL_V1.0.md) que infla
   correlacao entre valores brutos sem relacao causal nenhuma.
   delta_vs_baseline remove essa tendencia comum, e ja herda de
   farol.core.metrics o mesmo tratamento de quebra de fonte que RHR usa (cada
   delta e' computado contra a baseline da PROPRIA fonte daquele dia).
2. Nunca reporta r sem n ao lado, e nunca calcula p-valor: dado diario de
   fisiologia e' autocorrelacionado (o HRV de hoje prediz o de amanha so por
   inercia biologica, nao por relacao causal com nada), entao um p-valor
   ingenuo superestimaria confianca. O numero de pares e' informacao; a
   leitura de "isso importa" fica com quem le -- mesma filosofia de
   PROFILE.md regra 3 (nunca diagnosticar de uma janela isolada) e regra 6
   (tendencia de varias semanas > mudanca isolada).

Resultado e' sempre descritivo, nunca prescritivo: gera hipotese, nao
confirma causa.
"""
import argparse
import sqlite3
from dataclasses import dataclass
from datetime import date as _date
from datetime import timedelta
from statistics import StatisticsError, correlation

from ..core import db
from ..core.metrics import from_daily

# Abaixo disso o erro padrao de uma correlacao (~1/sqrt(n-3)) passa de 0.2 --
# ruido demais para qualquer leitura, mesmo informal.
_MIN_N = 30


@dataclass(frozen=True)
class Correlacao:
    """Resultado de um par com defasagem.

    `d0`/`d1` sao a primeira e a ultima data de X que efetivamente formaram
    um par com Y dentro do corte -- nao o intervalo nominal consultado.
    """

    campo_x: str
    campo_y: str
    lag_dias: int
    r: float
    n: int
    d0: str
    d1: str


def _deltas(conn, campo: str, tabela: str, datas: list[str]) -> dict[str, float]:
    """delta_vs_baseline de `campo` para cada data em `datas` que tiver
    dado -- dict data->delta, so com as datas presentes."""
    saida = {}
    for d in datas:
        m = from_daily(conn, campo, d, table=tabela, respect_source=True)
        if m.delta_vs_baseline is not None:
            saida[d] = m.delta_vs_baseline
    return saida


def lagged_correlation(
    conn: sqlite3.Connection, campo_x: str, campo_y: str, lag_dias: int = 0,
    desde: str | None = None, ate: str | None = None,
    tabela_x: str = "daily", tabela_y: str = "daily",
) -> Correlacao | None:
    """Correlacao de Pearson entre delta_vs_baseline(campo_x, data) e
    delta_vs_baseline(campo_y, data + lag_dias), para todas as datas no
    intervalo [desde, ate] em que os dois lados tem dado. None com menos de
    _MIN_N pares.

    `lag_dias=1` compara X de um dia com Y do dia seguinte (ex.: estresse de
    hoje vs sono de amanha). `lag_dias=0` e' concorrente (mesmo dia).
    `desde`/`ate` default para os ultimos 365 dias com dado em `daily`."""
    if ate is None:
        row = conn.execute("SELECT MAX(date) AS d FROM daily").fetchone()
        ate = row["d"] if row and row["d"] else None
    if ate is None:
        return None
    if desde is None:
        desde = (_date.fromisoformat(ate) - timedelta(days=364)).isoformat()

    todas_datas = [
        r["date"] for r in conn.execute(
            "SELECT DISTINCT date FROM daily WHERE date >= ? AND date <= ? ORDER BY date",
            (desde, ate),
        ).fetchall()
    ]
    if not todas_datas:
        return None

    # Lado Y nunca pode passar de `ate`, mesmo deslocado por lag_dias --
    # senao um corte as-of vaza para o futuro (achado da auditoria de
    # 2026-09-08: com lag_dias=1, o Y de `ate` e' `ate+1`, que a versao
    # anterior buscava e comparava sem checar o corte). Com ate=None, `ate`
    # ja foi resolvido acima para MAX(date) real -- nao existe linha alem
    # dele na tabela, entao este filtro e' um no-op e o comportamento
    # default nao muda.
    datas_y_deslocadas = sorted({
        d_desl for d in todas_datas
        if (d_desl := (_date.fromisoformat(d) + timedelta(days=lag_dias)).isoformat()) <= ate
    })

    deltas_x = _deltas(conn, campo_x, tabela_x, todas_datas)
    deltas_y = _deltas(conn, campo_y, tabela_y, datas_y_deslocadas)

    pares = []
    datas_usadas = []
    for d in todas_datas:
        d_deslocada = (_date.fromisoformat(d) + timedelta(days=lag_dias)).isoformat()
        if d in deltas_x and d_deslocada in deltas_y:
            pares.append((deltas_x[d], deltas_y[d_deslocada]))
            datas_usadas.append(d)

    if len(pares) < _MIN_N:
        return None

    xs, ys = zip(*pares)
    try:
        r = correlation(xs, ys)
    except StatisticsError:
        return None

    # d0/d1 sao as datas de X que realmente entraram em algum par -- nao o
    # intervalo nominal [desde, ate]. Com lag > 0 o ultimo dia do intervalo
    # nao tem Y dentro do corte e ficava anunciado como se tivesse sido
    # usado, inflando o periodo reportado em `lag_dias`.
    return Correlacao(
        campo_x=campo_x, campo_y=campo_y, lag_dias=lag_dias,
        r=round(r, 2), n=len(pares), d0=datas_usadas[0], d1=datas_usadas[-1],
    )


# Pares de interesse para o CLI -- (rotulo_x, campo_x, tabela_x, lag_dias,
# rotulo_y, campo_y, tabela_y). Curado, nao exaustivo: testar toda
# combinacao de campos e' pesca estatistica (aumenta a chance de achar
# correlacao por acaso) -- cada par aqui responde uma pergunta especifica
# que PROFILE.md ou o parecer tecnico ja levantaram.
_PARES_DE_INTERESSE = [
    ("Stress hoje", "stress_avg", "daily", 1, "Sono amanha", "sleep_score", "daily"),
    ("Sono hoje", "sleep_score", "daily", 1, "HRV amanha", "hrv_avg", "daily"),
    ("HRV hoje", "hrv_avg", "daily", 1, "Stress amanha", "stress_avg", "daily"),
    ("Stress hoje", "stress_avg", "daily", 0, "HRV hoje", "hrv_avg", "daily"),
    ("TSB hoje", "tsb", "training_load", 1, "Sono amanha", "sleep_score", "daily"),
    ("TSB hoje", "tsb", "training_load", 0, "Stress hoje", "stress_avg", "daily"),
    ("Respiracao hoje", "respiration_avg", "daily", 1, "HRV amanha", "hrv_avg", "daily"),
]


def render_report(conn: sqlite3.Connection, ate: str | None = None) -> str:
    """`ate` (opcional): limita todos os pares a `date <= ate`, dos dois
    lados de cada par -- ver lagged_correlation(). Sem `ate` (default),
    comportamento identico ao anterior (usa MAX(date) real, retrospectivo)."""
    linhas = [
        "# CORRELACOES -- farol.insights.correlations",
        "",
        "Correlacao entre DESVIOS contra a propria baseline de 90 dias (nao",
        "valores brutos), com defasagem explicita. Descritivo, gera hipotese --",
        f"nunca confirma causa (PROFILE.md regra 3). Minimo de {_MIN_N} pares para",
        "reportar um numero; menos que isso e' 'dados insuficientes'.",
        "",
        "| X | Y | lag | r | n | periodo |",
        "|---|---|-----|---|---|---------|",
    ]
    for rotulo_x, campo_x, tabela_x, lag, rotulo_y, campo_y, tabela_y in _PARES_DE_INTERESSE:
        resultado = lagged_correlation(conn, campo_x, campo_y, lag, ate=ate, tabela_x=tabela_x, tabela_y=tabela_y)
        if resultado is None:
            linhas.append(f"| {rotulo_x} | {rotulo_y} | {lag}d | — | — | dados insuficientes (n<{_MIN_N}) |")
        else:
            linhas.append(
                f"| {rotulo_x} | {rotulo_y} | {lag}d | {resultado.r:+.2f} | {resultado.n} | "
                f"{resultado.d0} a {resultado.d1} |"
            )
    linhas += [
        "",
        "Leitura: |r| < 0.2 e' desprezivel na pratica mesmo com n alto; nada aqui",
        "e' um teste de significancia (dado diario e' autocorrelacionado -- um",
        "p-valor ingenuo superestimaria confianca). Um r alto e persistente ao",
        "longo de varias reexecucoes vale mais que uma leitura isolada.",
    ]
    return "\n".join(linhas) + "\n"


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        description="Correlacao defasada entre desvios contra a baseline pessoal.")
    parser.parse_args(argv)
    conn = db.connect_readonly()
    try:
        print(render_report(conn))
    finally:
        conn.close()


if __name__ == "__main__":
    main()
