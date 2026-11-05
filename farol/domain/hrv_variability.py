"""Coeficiente de variacao (CV) do HRV numa janela movel -- sinal diferente
da media que farol.core.metrics ja acompanha.

Plews & Buchheit (HRV guiado por treino): o CV do LnRMSSD rolante de 7 dias
COLAPSANDO -- ficando artificialmente estavel perto da media, em vez de
oscilar normalmente dia a dia -- e' um indicador precoce de overreaching
nao-funcional, muitas vezes antes de a MEDIA do HRV cair de forma visivel
(ver parecer tecnico de 2026-09-04, referencia 2). E' o oposto do que a
intuicao sugere: menos variacao no HRV nao e' sempre bom sinal.

Deliberadamente sem bucket ok/atencao/alerta e sem entrar em
detect_patterns/domain_status: e' uma janela movel (7 dias, a maioria
compartilhada de um dia para o seguinte), sujeita ao mesmo problema de
autocorrelacao que tirou sleep_regularity_min do dominio SONO -- ver
farol/domain/state.py. E nao ha limiar numerico validado (na literatura ou no
historico deste usuario) para "quanto e' colapso" -- diferente do limiar de
signature_doenca, que foi calibrado contra 214 dias reais antes de virar
codigo, aqui nao ha base equivalente ainda. Mostra os dois numeros (CV
curto e CV de referencia) e deixa o padrao para o olho humano, ao longo de
varios dias -- exatamente como CTL/ATL sao mostrados sem z-score.
"""
from datetime import date as _date
from datetime import timedelta
from statistics import pstdev

_MIN_PONTOS_CURTO = 4   # janela de 7 dias, ~40-60% de cobertura tipica de HRV
_MIN_PONTOS_REF = 8     # janela de 30 dias -- mesmo piso de metrics._N_MEDIA


def _cv(conn, on_date: str, window_days: int, min_pontos: int) -> float | None:
    inicio = (_date.fromisoformat(on_date) - timedelta(days=window_days - 1)).isoformat()
    valores = [
        float(r["hrv_avg"]) for r in conn.execute(
            "SELECT hrv_avg FROM daily WHERE date >= ? AND date <= ? AND hrv_avg IS NOT NULL",
            (inicio, on_date),
        ).fetchall()
    ]
    if len(valores) < min_pontos:
        return None
    media = sum(valores) / len(valores)
    if media == 0:
        return None
    return round(pstdev(valores) / media * 100, 1)


def hrv_cv(conn, on_date: str) -> dict | None:
    """CV (%) do HRV numa janela curta (7d) e numa de referencia (30d),
    ambas terminando em `on_date` (inclusive). None se a janela curta nao
    tiver dado suficiente -- a de referencia pode faltar sem impedir o
    resultado (so perde o contexto de comparacao)."""
    curto = _cv(conn, on_date, 7, _MIN_PONTOS_CURTO)
    if curto is None:
        return None
    referencia = _cv(conn, on_date, 30, _MIN_PONTOS_REF)
    return {"cv_7d": curto, "cv_30d": referencia}
