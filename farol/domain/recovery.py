"""Índice único de recuperação: média ponderada de HRV/sono/RHR/TSB.

Compartilhado por farol.reports.today (relatório do dia) e farol.insights.calibration (log de
calibração) -- vivia dentro de today.py antes, com calibration.py importando
a constante privada _PESOS_PADRAO de lá.
"""
from ..core.metrics import Metric

# Pesos do índice de recuperação. Configuráveis em profile.yaml
# (recovery_index.weights) -- peso numérico embutido sem calibração é
# proibido, e a calibração precisa ser editável sem mexer em código.
PESOS_PADRAO = {
    "sleep_score": 0.30,
    "hrv_avg": 0.30,
    "rhr": 0.20,
    "tsb": 0.20,
}


def score_from(m: Metric) -> float | None:
    """z-score -> nota 0-10, com os mesmos cortes que a Metric usa para status.

    Os pontos de quebra sao -0.5 e -1.5 desvios: exatamente a fronteira
    ok/atencao/alerta de farol.core.metrics. Sem isso o indice e o status contam
    historias diferentes -- um HRV a -0.46 sd, que a Metric chama de "ok",
    pontuaria 3.8/10 e o resumo diria que a recuperacao esta ruim.

    Ancoras: z=0 (na sua media) -> 6.5   z=-0.5 -> 6.0   z=-1.5 -> 3.5
    A direcao ja vem resolvida pela Metric, entao FC de repouso alta pontua
    baixo sem precisar de um `if` aqui."""
    if m.z is None:
        return None
    efetivo = m.z if m.higher_is_better else -m.z
    if efetivo >= 0:
        nota = 6.5 + min(efetivo, 2.0) * 1.75      # ate 10 em +2 sd
    elif efetivo >= -0.5:
        nota = 6.0 + (efetivo + 0.5) * 1.0         # faixa "ok"
    elif efetivo >= -1.5:
        nota = 3.5 + (efetivo + 1.5) * 2.5         # faixa "atencao"
    else:
        nota = 3.5 + (efetivo + 1.5) * 2.0         # faixa "alerta"
    return round(max(0.0, min(10.0, nota)), 1)


def recovery_index(metricas: dict[str, Metric], pesos: dict) -> tuple[float | None, dict]:
    """Media ponderada das notas, renormalizada pelos pesos que tinham dado.
    Um dia sem HRV nao vira 'HRV mediano' — o peso dele sai da conta."""
    notas, peso_util = {}, 0.0
    total = 0.0
    for chave, peso in pesos.items():
        m = metricas.get(chave)
        nota = score_from(m) if m else None
        notas[chave] = nota
        if nota is not None:
            total += nota * peso
            peso_util += peso
    if peso_util == 0:
        return None, notas
    return round(total / peso_util, 1), notas


def recovery_status(indice: float | None) -> str:
    """Bucket ok/atencao/alerta/sem_dado do indice -- mesmas fronteiras de
    recovery_label (6.0 e 3.5), extraidas para nao duplicar o limiar em cada
    lugar que colore ou compara o indice (today.py tinha isto embutido duas
    vezes; calibration.py precisa do mesmo bucket para comparar contra
    Metric.status, que ja fala ok/atencao/alerta/sem_dado)."""
    if indice is None:
        return "sem_dado"
    if indice >= 6.0:
        return "ok"
    if indice >= 3.5:
        return "atencao"
    return "alerta"


def recovery_label(indice: float | None) -> str:
    """Faixas casadas com score_from: 6.0 e a fronteira do "ok" (-0.5 sd) e
    3.5 a do "alerta" (-1.5 sd)."""
    if indice is None:
        return "sem dado suficiente"
    if indice >= 8.0:
        return "acima do seu normal"
    if indice >= 6.0:
        return "dentro do seu normal"
    if indice >= 3.5:
        return "abaixo do seu normal"
    return "bem abaixo do seu normal"
