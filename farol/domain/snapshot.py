"""Metricas diarias compartilhadas por `farol.reports.today` e `farol.reports.dashboard`.

Os dois relatorios mostram o mesmo retrato do dia. Antes cada um montava o seu
proprio dicionario de `Metric` campo por campo -- duas listas paralelas com a
mesma escolha de tabela/coluna (TSB em `training_load`, RHR em `daily` para
respeitar a quebra de fonte de 2026-06-11). Uma delas podia ganhar um campo
sem a outra, e a duplicacao nao acrescentava nada: a regra estatistica
continua em `farol.core.metrics`, e a direcao de cada campo tambem.

Este modulo so' orquestra `daily_metric`/`from_daily`, sem limiar nem calculo
proprio. A ordem de `CAMPOS_DIARIOS` e' a ordem de exibicao/iteracao usada
pelo relatorio de terminal; consumidores que precisam de outra ordem (o
dashboard agrupa recuperacao e sono em secoes) indexam o dict por chave.
"""
from ..core.metrics import Metric, daily_metric, from_daily

# Ordem do relatorio de terminal (today): recuperacao, sono, carga e os
# campos complementares que hoje tambem entram em detect_patterns. A
# mensagem de convergencia cita os sinais nesta ordem.
CAMPOS_DIARIOS = (
    "hrv_avg", "rhr", "sleep_score", "night_hr_avg", "total_sleep_minutes",
    "tsb", "respiration_avg", "spo2_avg", "body_battery_min", "stress_avg",
    "avg_sleep_stress",
)

# VO2max do Garmin fica fora de `CAMPOS_DIARIOS` de proposito: e' um sinal
# lento, exibido como tendencia, nunca como evidencia de convergencia. Ver
# o comentario em farol/reports/today.py e docs/MODEL_V1.1.md.
_CAMPO_VO2MAX = "vo2max"


def daily_metrics(conn, on_date: str) -> dict[str, Metric]:
    """As `Metric` do dia, na ordem de `CAMPOS_DIARIOS`.

    Nao altera nenhuma regra: cada campo passa por `daily_metric` (direcao em
    `farol.core.metrics.DIRECTION`, baseline individual e respeito a
    `DATE_SPLITS`), exceto `tsb`, que mora em `training_load` e por isso usa
    `from_daily` com a tabela explicita.
    """
    metricas: dict[str, Metric] = {}
    for campo in CAMPOS_DIARIOS:
        if campo == "tsb":
            metricas[campo] = from_daily(
                conn, "tsb", on_date, higher_is_better=True, table="training_load")
        else:
            metricas[campo] = daily_metric(conn, campo, on_date)
    return metricas


def vo2max_metric(conn, on_date: str) -> Metric:
    """VO2max do Garmin como `Metric` de tendencia -- fora de `daily_metrics`
    porque nao participa de detect_patterns/recovery_index."""
    return daily_metric(conn, _CAMPO_VO2MAX, on_date)
