"""CLI: estado atual do atleta. Uso: python -m farol.reports.today [--compact | --alerts]

Relatório retrospectivo. Descreve o que aconteceu e como o corpo está em
relação ao padrão do próprio atleta — nunca prescreve treino (PROFILE.md,
regras 7 e 9: prescrição é do treinador).

Este arquivo so' orquestra: coleta o contexto do dia (metricas, indice,
classificacao, registro manual) e entrega a `farol.reports.report_sections`, que
monta as secoes de texto. As regras que decidem *o que* e' um padrao
(`detect_patterns`, `signature_doenca`) continuam aqui; formatacao de secao
mora no outro modulo.

Nenhum limiar fisiológico mora aqui. Tudo passa por farol.core.metrics, que compara
cada valor contra a janela móvel do próprio atleta. Se um número mágico
aparecer neste arquivo de novo, é regressão.
"""
import argparse
import sys
from datetime import date

from . import report_sections
from ..core import db
from ..domain import interpretation, snapshot, state
from ..records import injuries
from ..core.config import load_profile
from ..core.metrics import Metric
from ..domain.recovery import PESOS_PADRAO, recovery_index

# ---------------------------------------------------------------- contexto

def race_context(profile: dict, hoje: date) -> dict | None:
    """Dias até a prova e fase. Muda a leitura de tudo: TSB -12 a 17 dias da
    prova é acúmulo esperado; a 3 dias, é problema."""
    race = (profile.get("race") or {})
    if not race.get("date"):
        return None
    alvo = date.fromisoformat(str(race["date"])[:10])
    dias = (alvo - hoje).days
    if dias < 0:
        fase = "pos-prova"
    elif dias == 0:
        fase = "DIA DA PROVA"
    elif dias <= 3:
        fase = "afinamento final"
    elif dias <= 7:
        fase = "polimento"
    elif dias <= 21:
        fase = "ultima carga antes do polimento"
    else:
        fase = "construcao"
    return {"nome": race.get("name", "prova"), "dias": dias, "fase": fase,
            "distancia_km": race.get("distance_km")}


# ------------------------------------------------------------- convergencia

def detect_patterns(metricas: dict[str, Metric]) -> list[str]:
    """Sinais marcados em conjunto: nenhuma metrica isolada determina nada.

    C3 (VISTORIA_2026-09-04 V2): o texto nao chama metricas de "dominios".
    Dominio e' a agregacao larga de CARGA/RECUPERACAO/SONO classificada por
    farol.domain.state (a regra 8 do PROFILE.md -- 2+ dominios fora do normal -- e'
    implementada la em `state.convergence`, e e' quem alimenta a secao de
    convergencia de DAILY_STATE/DAILY_ANALYSIS). Aqui o ponto e' mais fino:
    quantos sinais individuais estao fora do padrao no mesmo dia, sem
    afirmar dominio nenhum -- dois campos do mesmo dominio (ex.: HRV e RHR,
    RECUPERACAO) contam como 2 sinais, nao como 2 dominios."""
    marcadas = [(k, m) for k, m in metricas.items()
                if m.status in ("alerta", "atencao") and m.has_data]
    if len(marcadas) < 2:
        return []

    nomes = [report_sections.ROTULOS_RELATORIO.get(k, k) for k, _ in marcadas]
    alertas = [f"{len(marcadas)} sinais simultaneamente fora do padrao: {', '.join(nomes)}"]

    for chave, m in marcadas:
        if m.status == "alerta":
            alertas.append(
                f"{report_sections.ROTULOS_RELATORIO.get(chave, chave)} a {abs(m.z):.1f} "
                f"desvios do seu normal (confianca {m.confidence}, {m.n_90d} dias)"
            )

    caindo = [report_sections.ROTULOS_RELATORIO.get(k, k) for k, m in metricas.items()
              if m.trend == "caindo" and m.higher_is_better and m.has_data]
    if len(caindo) >= 2:
        alertas.append(f"tendencia de queda em {' e '.join(caindo)} na ultima semana")
    return alertas


# Sinais agudos do proprio corpo -- nao qualidade/duracao de sono (tem
# interpretacao propria) nem TSB (e' a variavel que distingue a causa,
# nao um sintoma). Recomendacao 5 do parecer tecnico de 2026-09-04.
# body_battery_min fica FORA de _SINAIS_DOENCA de proposito (mesmo motivo
# que o tirou de state._RECUPERACAO, ver state.py:38): sd_90d alta e
# piso volatil do Garmin marcam 94% dos dias recentes como atencao/alerta --
# um sinal que quase nunca e' silencio nao discrimina resposta imunologica
# ativa de ruido. Com ele dentro, 3-de-6 disparava em ~20% dos dias; sem
# ele, 3-de-5 volta para ~6% (faixa calibrada de ~8% do MODEL_V1.1).
_SINAIS_DOENCA = ("hrv_avg", "rhr", "night_hr_avg", "respiration_avg", "spo2_avg")


def signature_doenca(metricas: dict[str, Metric]) -> str | None:
    """Distingue, dentro da mesma convergencia da regra 8, duas causas com
    assinatura diferente: '3+ sinais agudos fora do padrao porque o treino
    recente pesou' (TSB carregado explica) de '3+ sinais agudos fora do
    padrao SEM carga de treino recente elevada' (TSB carregado NAO explica --
    padrao mais proximo do que estudos de deteccao de doenca via wearable
    associam a resposta imunologica em curso, ver parecer tecnico).

    TSB e' o proxy de 'precedido por pico de carga': TSB = CTL - ATL, entao
    TSB baixo JA e' a assinatura de fadiga aguda recente -- nao precisa
    olhar ATL em separado para saber se houve pico.

    Guarda-corpo obrigatorio (PROFILE.md, regra de medical_context.md): so
    descreve o padrao ('compativel com'), nunca diagnostica. O achado clinico
    do proprio usuario (resposta cardiovascular compativel com hiperatividade
    simpatica) so pode ser citado como compativel, nunca ampliado -- mesma
    regra vale aqui, para uma hipotese nova.

    Exige 3 dos 5 sinais (nao 2): a regra 8 original foi calibrada para 2 de
    3 DOMINIOS largos (CARGA/RECUPERACAO/SONO). Aplicar o mesmo "2" a 5
    campos individuais e' um bar bem mais facil de cruzar -- rodado contra
    o historico real, 2-de-5 disparava em 17% dos dias (~1 em 6), frequencia
    incompativel com "resposta imunologica ativa" ser algo raro. 3-de-5 (~50%
    dos candidatos, proximo da proporcao original de 2-de-3) cai para ~8%.
    body_battery_min ja' ficou fora da contagem (5 sinais, nao 6) -- medido
    contra o historico real, ele marcava 33/35 dias recentes (94%) e fazia o
    3-de-6 disparar em ~20% dos dias; sem ele, o 3-de-5 dispara em 6% do
    historico (13/212), dentro da faixa calibrada. Limiar de partida, nao
    calibrado com percepcao real -- revisar contra CALIBRACAO_LOG.txt depois
    de acumular entradas (mesmo principio de calibration.py: decisao sobre o
    modelo so com evidencia real, nunca a partir de uma leitura teorica
    isolada).

    None se: menos de 3 sinais agudos marcados; ou TSB sem dado suficiente
    para descartar a explicacao de treino (nao vira 'compativel com doenca'
    por omissao); ou TSB carregado (aí' e' fadiga de treino, nao isto)."""
    marcados = [k for k in _SINAIS_DOENCA
                if k in metricas and metricas[k].has_data
                and metricas[k].status in ("atencao", "alerta")]
    if len(marcados) < 3:
        return None

    tsb = metricas.get("tsb")
    if tsb is None or not tsb.has_data or tsb.z is None:
        return None
    if tsb.status in ("atencao", "alerta"):
        return None

    nomes = [report_sections.ROTULOS_RELATORIO.get(k, k) for k in marcados]
    return (f"padrao compativel com resposta imunologica ativa: {', '.join(nomes)} "
            f"fora do seu normal ao mesmo tempo, sem carga de treino recente elevada "
            f"(TSB {tsb.value:+.1f}, dentro do seu normal) -- nao e' diagnostico, so "
            f"descreve o padrao; qualquer coisa persistente e' avaliacao medica")


# ---------------------------------------------------------------- orquestracao

def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        description="Relatorio diario de estado, carga e recuperacao (offline).")
    modo = parser.add_mutually_exclusive_group()
    modo.add_argument(
        "--compact", action="store_true",
        help="so' o resumo rapido, a leitura e a evolucao dos dominios "
             "(sem treino, carga, recuperacao e sono detalhados)")
    modo.add_argument(
        "--alerts", action="store_true",
        help="so' o que esta' em atencao/alerta: dominios, padroes, lesao e "
             "cobertura incompleta")
    args = parser.parse_args(argv)

    try:
        profile = load_profile()
    except FileNotFoundError as exc:
        print(exc)
        sys.exit(1)

    conn = db.connect_readonly()
    hoje = date.today()
    hoje_str = hoje.isoformat()

    if conn.execute("SELECT 1 FROM daily WHERE date = ?", (hoje_str,)).fetchone() is None:
        print(f"\nNenhum dado para {hoje_str}. Rode 'python -m farol.pipeline.ingest' primeiro.\n")
        conn.close()
        return

    # Toda comparacao a partir daqui e contra a janela do proprio atleta. A
    # montagem e' compartilhada com farol.reports.dashboard (farol/domain/snapshot.py): os
    # dois relatorios mostram o mesmo retrato do dia, sem listas paralelas.
    metricas = snapshot.daily_metrics(conn, hoje_str)

    # VO2max do Garmin: fica FORA de `metricas` de proposito, nao entra em
    # detect_patterns/recovery_index. E' um sinal lento (o Garmin so
    # recalcula depois de certos treinos, entao o mesmo valor se repete por
    # dias), diferente dos sinais agudos que a regra de convergencia (regra
    # 8) foi desenhada para cruzar -- deixar entrar inflaria streak/padrao
    # com um valor que nao mudou de verdade. PROFILE.md e explicito: nao e'
    # comparavel ao VO2max do exame ergometrico (53,8 ml/kg/min) nem deve
    # ancorar nada -- so a tendencia contra a propria janela de 90d importa
    # (regra 6), por isso o rotulo diz "(Garmin)" sempre.
    vo2max_metric = snapshot.vo2max_metric(conn, hoje_str)

    pesos = ((profile.get("recovery_index") or {}).get("weights")) or PESOS_PADRAO
    indice, notas = recovery_index(metricas, pesos)
    padroes = detect_patterns(metricas)
    doenca = signature_doenca(metricas)
    prova = race_context(profile, hoje)

    tl = conn.execute("SELECT * FROM training_load WHERE date = ?", (hoje_str,)).fetchone()
    daily = dict(conn.execute("SELECT * FROM daily WHERE date = ?", (hoje_str,)).fetchone())
    atividades = [dict(a) for a in conn.execute(
        "SELECT * FROM activities WHERE date = ? ORDER BY activity_id", (hoje_str,)).fetchall()]

    # Classificacao por dominio (fonte unica dos relatorios) e leitura em
    # linguagem. `daily_metric` acima le `daily`; a leitura usa
    # farol_daily_state -- duas superficies, uma unica classificacao.
    leitura = None
    dados_estado = None
    try:
        dados_estado = state.gather(conn, hoje_str)
        leitura = interpretation.interpretar(dados_estado)
    except ValueError:
        leitura = None

    injury = injuries.latest_observation(hoje_str)
    injury_alert = injuries.observation_alert(injury)

    ctx = report_sections.ReportContext(
        conn=conn, hoje=hoje, hoje_str=hoje_str, profile=profile,
        metricas=metricas, vo2max_metric=vo2max_metric,
        indice=indice, notas=notas, pesos=pesos,
        padroes=padroes, doenca=doenca, prova=prova,
        tl=tl, daily=daily, atividades=atividades,
        dados_estado=dados_estado, leitura=leitura,
        injury=injury, injury_alert=injury_alert,
    )
    print(report_sections.render_report(
        ctx, compact=args.compact, alerts_only=args.alerts), end="")

    conn.close()


if __name__ == "__main__":
    main()
