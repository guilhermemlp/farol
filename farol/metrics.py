"""A primitiva do perfil: uma métrica nunca viaja sozinha.

Todo valor sai daqui já acompanhado das próprias médias móveis, desvio-padrão,
z-score, tendência e confiança. É isso que substitui os limiares fixos que
estavam espalhados pelo relatório (`baseline_hrv = 18.5`, `rhr - 72`): não
existe mais onde escrever um número mágico, porque a comparação é sempre contra
a janela do próprio atleta.

Regras de janela, todas deliberadas:

- O dia-alvo NUNCA entra nas próprias baselines. Comparar hoje contra uma média
  que contém hoje atenua o desvio e faz um dia atípico puxar a própria
  referência.
- `z` usa a janela de 90 dias, mais estável que a de 30 para desvio-padrão.
- `confidence` deriva de quantos dias realmente tinham dado, não do tamanho
  nominal da janela: 39% dos dias não têm HRV, e uma tendência calculada sobre
  metade dos pontos não vale o mesmo que uma completa.

O núcleo (`build_metric`) é função pura sobre listas de float — testável sem
banco, sem rede e sem planilha.
"""
from dataclasses import dataclass
from datetime import date as _date
from datetime import timedelta
from statistics import mean, pstdev

# Abaixo de 8 pontos qualquer média é anedota; acima de 20 já dá para confiar
# na dispersão. Números escolhidos para a densidade real deste banco.
_N_ALTA = 20
_N_MEDIA = 8

# Quanto o z precisa andar para deixar de ser ruído. 0.5 sd é a fronteira usual
# entre "variação normal do dia" e "algo mudou".
_Z_ATENCAO = -0.5
_Z_ALERTA = -1.5

# Tendência: quanto a média de 7 dias precisa se afastar da de 30, em sd.
_TREND_SD = 0.35


@dataclass(frozen=True)
class Metric:
    """Um valor com o seu próprio contexto estatístico.

    `higher_is_better` é o que permite ao consumidor não saber fisiologia:
    HRV alto é bom, FC de repouso alta não é. `status` já vem resolvido para
    a direção certa.
    """

    name: str
    value: float | None
    mean_7d: float | None
    mean_30d: float | None
    mean_90d: float | None
    sd_90d: float | None
    z: float | None
    trend: str          # subindo | estavel | caindo | indefinido
    status: str         # ok | atencao | alerta | sem_dado
    confidence: str     # alta | media | baixa
    n_7d: int
    n_30d: int
    n_90d: int
    higher_is_better: bool

    @property
    def delta_vs_baseline(self) -> float | None:
        """Diferença absoluta contra a média de 90 dias, na unidade da métrica."""
        if self.value is None or self.mean_90d is None:
            return None
        return round(self.value - self.mean_90d, 2)

    @property
    def has_data(self) -> bool:
        return self.value is not None


def _avg(values: list[float]) -> float | None:
    return round(mean(values), 2) if values else None


def _confidence(n: int) -> str:
    if n >= _N_ALTA:
        return "alta"
    if n >= _N_MEDIA:
        return "media"
    return "baixa"


def _trend(mean_7d: float | None, mean_30d: float | None, sd: float | None) -> str:
    """Tendência recente = onde a última semana caiu em relação ao mês, medida
    em desvios-padrão. Sem sd utilizável não há como dizer se a diferença é
    sinal ou ruído, então devolve 'indefinido' em vez de chutar."""
    if mean_7d is None or mean_30d is None or not sd:
        return "indefinido"
    delta_sd = (mean_7d - mean_30d) / sd
    if delta_sd > _TREND_SD:
        return "subindo"
    if delta_sd < -_TREND_SD:
        return "caindo"
    return "estavel"


def _status(z: float | None, higher_is_better: bool) -> str:
    """Converte o z para a direção fisiológica correta e classifica.

    Para HRV, z negativo é ruim. Para FC de repouso, z positivo é que é ruim —
    o sinal é invertido antes de comparar, para que os limiares fiquem em um
    lugar só."""
    if z is None:
        return "sem_dado"
    effective = z if higher_is_better else -z
    if effective <= _Z_ALERTA:
        return "alerta"
    if effective <= _Z_ATENCAO:
        return "atencao"
    return "ok"


def build_metric(
    name: str,
    value: float | None,
    window_7: list[float],
    window_30: list[float],
    window_90: list[float],
    higher_is_better: bool = True,
) -> Metric:
    """Núcleo puro. As três janelas já vêm sem o dia-alvo e sem os None."""
    mean_7d = _avg(window_7)
    mean_30d = _avg(window_30)
    mean_90d = _avg(window_90)

    # pstdev aceita 1 ponto, mas um desvio com poucos pontos é instável demais
    # para ancorar um z — abaixo de _N_MEDIA preferimos não ter z a ter um ruim.
    sd_90d = round(pstdev(window_90), 2) if len(window_90) >= _N_MEDIA else None

    z = None
    if value is not None and mean_90d is not None and sd_90d:
        z = round((value - mean_90d) / sd_90d, 2)

    return Metric(
        name=name,
        value=value,
        mean_7d=mean_7d,
        mean_30d=mean_30d,
        mean_90d=mean_90d,
        sd_90d=sd_90d,
        z=z,
        trend=_trend(mean_7d, mean_30d, sd_90d),
        status=_status(z, higher_is_better),
        confidence=_confidence(len(window_90)),
        n_7d=len(window_7),
        n_30d=len(window_30),
        n_90d=len(window_90),
        higher_is_better=higher_is_better,
    )


def _window(
    conn,
    column: str,
    on_date: str,
    days: int,
    table: str = "daily",
    source: str | None = None,
    split_after: str | None = None,
) -> list[float]:
    """Valores não-nulos em [on_date - days, on_date). Fim exclusivo: o dia-alvo
    não entra na própria baseline. Com `source`, a janela fica restrita a linhas
    da mesma procedência. Com `split_after`, a janela fica restrita ao MESMO
    LADO da quebra de medição que `on_date` (política split_by_date): depois da
    quebra, só valores >= split_after; antes, só valores < split_after. O
    dia-alvo define o lado -- a janela nunca cruza a fronteira."""
    start = (_date.fromisoformat(on_date) - timedelta(days=days)).isoformat()
    sql = ("SELECT %s AS v FROM %s WHERE date >= ? AND date < ? AND %s IS NOT NULL"
           % (column, table, column))
    args: list = [start, on_date]
    if source is not None:
        sql += " AND source = ?"
        args.append(source)
    if split_after is not None:
        if on_date >= split_after:
            sql += " AND date >= ?"
            args.append(split_after)
        else:
            sql += " AND date < ?"
            args.append(split_after)
    return [float(r["v"]) for r in conn.execute(sql, args).fetchall()]


def _source_of(conn, on_date: str) -> str | None:
    row = conn.execute("SELECT source FROM daily WHERE date = ?", (on_date,)).fetchone()
    return row["source"] if row else None


def from_daily(
    conn,
    column: str,
    on_date: str,
    higher_is_better: bool = True,
    table: str = "daily",
    respect_source: bool = True,
    respect_date_split: bool = True,
) -> Metric:
    """Monta a Metric de uma coluna para uma data.

    `table` existe porque o TSB mora em `training_load`, não em `daily` — e "o
    meu TSB está mais negativo do que costuma ficar" é uma leitura tão válida
    quanto a de HRV.

    `respect_source` impede que a baseline cruze fontes de dado. O histórico
    anterior a 11/06/2026 veio de outro pipeline e o RHR dele está deslocado
    ~6 bpm; misturar os dois inflaria o desvio-padrão e produziria um z-score
    que mede troca de origem, não fisiologia. Só se aplica a `daily`, que é a
    tabela que carrega a coluna `source`.

    `respect_date_split` impede que a baseline cruze quebras de medição por
    data (DATE_SPLITS, política split_by_date) -- sleep_score mudou de
    definição em 2026-08-01 e a janela de 90 dias de um dia de setembro não
    pode misturar scores de julho com os de agosto. Aplica-se por nome de
    coluna, valendo para `daily` e `farol_daily_state`."""
    row = conn.execute(
        "SELECT %s AS v FROM %s WHERE date = ?" % (column, table), (on_date,)
    ).fetchone()
    value = float(row["v"]) if row and row["v"] is not None else None

    source = _source_of(conn, on_date) if (respect_source and table == "daily") else None
    split_after = DATE_SPLITS.get(column) if respect_date_split else None

    return build_metric(
        name=column,
        value=value,
        window_7=_window(conn, column, on_date, 7, table, source, split_after),
        window_30=_window(conn, column, on_date, 30, table, source, split_after),
        window_90=_window(conn, column, on_date, 90, table, source, split_after),
        higher_is_better=higher_is_better,
    )


# Quebras de medição POR DATA (política split_by_date do measurement_breakpoints
# de daily_state_builder): a partir de `data`, o campo deixou de ser comparável
# com o próprio passado na MESMA fonte, e a baseline não pode cruzar a fronteira.
# Diferente de `source` (procedência, daily.source), aqui a separação é temporal.
# sleep_score: a atualização da API do Garmin de 2026-08-01 que mudou awakenings
# também mudou a definição do score -- deslocamento pequeno na média do histórico
# real (59,3 -> 56,6; CUSUM sem candidata >= 1.0 SD), mas a definição mudou, e a
# política registrada é a mesma de awakenings. Nomes de coluna, não de tabela:
# vale para `daily` e para `farol_daily_state` (que a espelha).
DATE_SPLITS = {
    "sleep_score": "2026-08-01",
}


# Direção fisiológica de cada coluna, num lugar só. Quem consome não precisa
# saber que FC de repouso alta é ruim e HRV alto é bom.
DIRECTION = {
    "hrv_avg": True,
    "sleep_score": True,
    "total_sleep_minutes": True,
    "deep_sleep_minutes": True,
    "rem_sleep_minutes": True,
    "body_battery_max": True,
    "body_battery_min": True,
    "spo2_avg": True,
    "spo2_min": True,
    "rhr": False,
    "night_hr_avg": False,
    "stress_avg": False,
    "awakenings": False,
    "awake_minutes": False,
    "avg_sleep_stress": False,
    "respiration_avg": False,
    "training_readiness_score": True,
    "vo2max": True,
}


def daily_metric(conn, column: str, on_date: str) -> Metric:
    """`from_daily` com a direção já resolvida por DIRECTION."""
    return from_daily(conn, column, on_date, DIRECTION.get(column, True))
