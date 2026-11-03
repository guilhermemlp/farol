"""CLI: estado atual do atleta. Uso: python -m farol.today

Relatório retrospectivo. Descreve o que aconteceu e como o corpo está em
relação ao padrão do próprio atleta — nunca prescreve treino (PROFILE.md,
regras 7 e 9: prescrição é do treinador).

Nenhum limiar fisiológico mora aqui. Tudo passa por farol.metrics, que compara
cada valor contra a janela móvel do próprio atleta. Se um número mágico
aparecer neste arquivo de novo, é regressão.
"""
import json
import os
import sys
from datetime import date, timedelta

from . import db, injuries, interpretation, nutrition, race_prediction, state
from .analysis import (
    aerobic_efficiency, continuous_mechanics, efficiency_verdict, hr_drift, interval_efficiency,
    interval_mechanics, mechanical_verdict, pace_consistency, weekly_load,
)
from .config import load_profile
from .hrv_variability import hrv_cv
from .labels import ROTULOS
from .metrics import Metric, daily_metric, from_daily
from .recovery import PESOS_PADRAO, recovery_index, recovery_label, recovery_status
from .sleep_regularity import bedtime_regularity
from .zones import heart_rate_zones

# "Sono" fica local de proposito: aqui rotula a fatia do indice de
# recuperacao (junto de HRV/FC repouso/TSB), nao o campo bruto -- ver
# farol/labels.py.
_ROTULO = {**ROTULOS, "sleep_score": "Sono", "avg_sleep_stress": "Stress no sono"}

_MARCA = {"ok": "ok", "atencao": "atencao", "alerta": "ALERTA", "sem_dado": "sem dado"}
_SETA = {"subindo": "^", "caindo": "v", "estavel": "=", "indefinido": "?"}

# campo em analysis.py -> (rotulo, unidade, menor_e_melhor). Mesma direcao ja
# usada em analysis.py::assess_deterioration_detected.
_MECANICA = (
    ("cadencia", "Cadencia", "spm", False),
    ("gct", "Tempo de contato", "ms", True),
    ("oscilacao", "Oscilacao vertical", "cm", True),
)

# Cor so no terminal (nunca em saida redirecionada/pipe -- um arquivo ou outro
# programa lendo isto nao deve receber sequencia de escape) e respeitando a
# convencao NO_COLOR (https://no-color.org/). So decora _MARCA/indice na
# saida -- nao muda nenhum texto nem valor, so como ele aparece na tela.
_USAR_COR = sys.stdout.isatty() and os.environ.get("NO_COLOR") is None
_COR = {"ok": "\033[32m", "atencao": "\033[33m", "alerta": "\033[31m"}
_COR_RESET = "\033[0m"


def _colorir(texto: str, status: str) -> str:
    if not _USAR_COR or status not in _COR:
        return texto
    return f"{_COR[status]}{texto}{_COR_RESET}"


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
    farol.state (a regra 8 do PROFILE.md -- 2+ dominios fora do normal -- e'
    implementada la em `state.convergence`, e e' quem alimenta a secao de
    convergencia de DAILY_STATE/DAILY_ANALYSIS). Aqui o ponto e' mais fino:
    quantos sinais individuais estao fora do padrao no mesmo dia, sem
    afirmar dominio nenhum -- dois campos do mesmo dominio (ex.: HRV e RHR,
    RECUPERACAO) contam como 2 sinais, nao como 2 dominios."""
    marcadas = [(k, m) for k, m in metricas.items()
                if m.status in ("alerta", "atencao") and m.has_data]
    if len(marcadas) < 2:
        return []

    nomes = [_ROTULO.get(k, k) for k, _ in marcadas]
    alertas = [f"{len(marcadas)} sinais simultaneamente fora do padrao: {', '.join(nomes)}"]

    for chave, m in marcadas:
        if m.status == "alerta":
            alertas.append(
                f"{_ROTULO.get(chave, chave)} a {abs(m.z):.1f} desvios do seu normal "
                f"(confianca {m.confidence}, {m.n_90d} dias)"
            )

    caindo = [_ROTULO.get(k, k) for k, m in metricas.items()
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

    nomes = [_ROTULO.get(k, k) for k in marcados]
    return (f"padrao compativel com resposta imunologica ativa: {', '.join(nomes)} "
            f"fora do seu normal ao mesmo tempo, sem carga de treino recente elevada "
            f"(TSB {tsb.value:+.1f}, dentro do seu normal) -- nao e' diagnostico, so "
            f"descreve o padrao; qualquer coisa persistente e' avaliacao medica")


# ------------------------------------------------------------------ treino

def resumo_atividades(atividades: list[dict]) -> str:
    """Uma linha com TODOS os treinos do dia, nao so o primeiro.

    Um dia pode ter corrida de manha e pilates a noite, ou nenhum treino, ou
    forca sem corrida nenhuma."""
    return " + ".join(
        f"{(act.get('type') or 'atividade').replace('_', ' ')} "
        f"({classify_workout_demand(act).lower()})"
        for act in atividades
    )


def zone_of(hr: float | None, faixas: list[dict]) -> str:
    """Em que zona de FC um valor cai. Devolve string ja formatada para o
    relatorio, vazia quando nao da para dizer.

    Usa as zonas ancoradas no seu LTHR (farol.zones), nao percentuais genericos
    de FC maxima."""
    if not hr or not faixas:
        return ""
    for f in faixas:
        if f["hr_min"] <= hr <= f["hr_max"]:
            return f" ({f['zone']} {f['name'].lower()})"
    if hr < faixas[0]["hr_min"]:
        return " (abaixo de Z1)"
    return f" ({faixas[-1]['zone']})"


def classify_workout_demand(act: dict) -> str:
    t = act.get("trimp") or 0
    if t >= 100:
        return "MUITO ALTA"
    if t >= 70:
        return "ALTA"
    if t >= 40:
        return "MODERADA"
    return "BAIXA"


def _texto_tendencia(trend: dict | None) -> str:
    if not trend:
        return "? dados insuficientes"
    return {
        "WORSENING": "↑ piorando",
        "IMPROVING": "↓ melhorando",
        "STABLE": "→ estável",
        "INSUFFICIENT_DATA": "? dados insuficientes",
    }.get(trend.get("direcao"), "? dados insuficientes")


def _imprime_resumo_longitudinal(dados_estado: dict) -> None:
    """Mostra status, confianca, streak e direcao ja calculados por state.gather()."""
    print("  EVOLUCAO DOS DOMINIOS (tendencia de severidade, janela de 7 dias)")
    print("    Dominio       Estado       Confianca   Streak       Tendencia")
    for nome, dominio in dados_estado["dominios"].items():
        rotulo, _ = state._LABEL[dominio["status"]]
        streak = dominio["streak"]
        streak_txt = f"{streak['dias']}d" if streak["dias"] else "—"
        print(f"    {nome:<14} {rotulo:<12} {dominio['confianca']:<11} "
              f"{streak_txt:<12} {_texto_tendencia(dominio['trend'])}")

    status_por_dominio = {nome: dominio["status"]
                          for nome, dominio in dados_estado["dominios"].items()}
    convergencia = state.convergence(status_por_dominio)
    streak = dados_estado["convergencia_streak"]
    tendencia = _texto_tendencia(dados_estado["convergencia_trend"])
    if convergencia:
        persiste = f"{streak['dias']}d" if streak["dias"] else "sem streak"
        print(f"    Convergencia: ativa ({persiste}; {tendencia})")
    else:
        print(f"    Convergencia: nao ativa hoje ({tendencia})")
    print("    Nota: setas descrevem mudanca de severidade, nao recomendam treino.")


# ------------------------------------------------------------- formatacao

# Recomendacao 9 do parecer tecnico de 2026-09-04, reabrindo a exclusao de
# "dashboard" da FASE 10 com uma distincao: isto e' formatacao de texto no
# mesmo relatorio de terminal que ja existe, nao infraestrutura nova --
# zero servidor, zero dependencia, zero tela propria.
_BLOCOS_SPARK = "▁▂▃▄▅▆▇█"


def sparkline(valores: list[float]) -> str:
    """Sparkline Unicode (blocos de altura) de uma serie, escala min-max
    linear -- pista visual de tendencia, nao grafico preciso. Serie vazia
    devolve "". Serie com todos os valores iguais (sd=0, inclui 1 ponto so)
    devolve o bloco do meio repetido, nunca erro de divisao por zero."""
    if not valores:
        return ""
    lo, hi = min(valores), max(valores)
    if hi == lo:
        return _BLOCOS_SPARK[len(_BLOCOS_SPARK) // 2] * len(valores)
    escala = len(_BLOCOS_SPARK) - 1
    return "".join(
        _BLOCOS_SPARK[round((v - lo) / (hi - lo) * escala)] for v in valores
    )


def _serie_recente(conn, column: str, on_date: str, table: str = "daily", dias: int = 30) -> list[float]:
    """Ultimos `dias` valores nao-nulos de `column`, terminando em `on_date`
    (inclusive) -- so para o sparkline visual, nao para estatistica.
    Diferente de farol.metrics._window (que exclui o dia-alvo para nao
    diluir a propria baseline), aqui o dia-alvo entra de proposito: o ponto
    e' mostrar onde hoje cai dentro da forma recente, nao comparar hoje
    contra uma media que o exclui. Buracos (dia sem medicao) sao omitidos,
    nunca interpolados -- mesma regra de farol.longitudinal."""
    inicio = (date.fromisoformat(on_date) - timedelta(days=dias - 1)).isoformat()
    sql = (f"SELECT {column} AS v FROM {table} WHERE date >= ? AND date <= ? "
           f"AND {column} IS NOT NULL ORDER BY date")
    return [float(r["v"]) for r in conn.execute(sql, (inicio, on_date)).fetchall()]


def linha_metrica(m: Metric, rotulo: str, unidade: str = "", spark: str = "") -> str:
    if not m.has_data:
        return f"  {rotulo:<14} sem dado"
    valor_txt = f"{m.value:g}{unidade}" + (f" {spark}" if spark else "")
    partes = [f"  {rotulo:<14} {valor_txt}"]
    if m.mean_90d is not None:
        partes.append(f"| normal {m.mean_90d:g}{unidade}")
    if m.z is not None:
        partes.append(f"| z {m.z:+.2f}")
    partes.append(f"| 7d {_SETA[m.trend]}")
    partes.append(f"| {_colorir(_MARCA[m.status], m.status)}")
    if m.confidence != "alta":
        partes.append(f"(confianca {m.confidence}, n={m.n_90d})")
    return " ".join(partes)


def _proxima_analise_quinzenal(hoje: date) -> date:
    """Proximo dia 1 ou 15 do mes -- so usado para o aviso quando a secao de
    economia de corrida nao aparece hoje."""
    if hoje.day < 15:
        return hoje.replace(day=15)
    if hoje.month == 12:
        return date(hoje.year + 1, 1, 1)
    return date(hoje.year, hoje.month + 1, 1)


def _imprime_mecanica(conn, hoje_str: str, titulo: str, funcao_analise) -> None:
    """Secao de economia mecanica (cadencia/tempo de contato/oscilacao
    vertical), usando `funcao_analise(conn, hoje_str, campo)` --
    `interval_mechanics` (tiro) ou `continuous_mechanics` (corrida inteira)."""
    linhas = []
    for campo, nome, unidade, menor_e_melhor in _MECANICA:
        blocos_m = funcao_analise(conn, hoje_str, campo)
        if blocos_m:
            linhas.append((nome, unidade, menor_e_melhor, blocos_m))
    if not linhas:
        return
    print("-" * 66)
    print(titulo)
    print("-" * 66)
    for nome, unidade, menor_e_melhor, blocos_m in linhas:
        print(f"  {nome}:")
        for b in blocos_m:
            print(f"    {b.rotulo:<16} {b.valor_ref:.1f} +/- {b.erro_padrao:.1f} {unidade} "
                  f"a {b.pace_ref:.2f} min/km   (n={b.n}, {b.d0} a {b.d1})")
        veredito_m = mechanical_verdict(blocos_m, nome.lower(), unidade, menor_e_melhor)
        if veredito_m:
            print(f"    -> {veredito_m}")
    print()


# F9 (auditoria): nome do endpoint do Garmin -> campos do relatorio que ele
# alimenta. Usado para transformar `daily.errors` (gravado pelo ingest quando
# o _safe captura uma falha) em aviso legivel: "hoje veio sem SpO2: endpoint
# get_stats falhou" em vez de um campo vazio sem explicacao.
_ENDPOINT_CAMPOS = {
    "get_stats": "passos, calorias, FC repouso, stress, Body Battery, SpO2, hidratacao",
    "get_sleep_data": "sono (duracao, fases, score)",
    "get_hrv_data": "HRV",
    "get_training_status": "VO2max e status de treino",
    "get_training_readiness": "prontidao para treino",
    "get_respiration_data": "respiracao",
}


def avisos_erros_sync(errors: str | None) -> list[str]:
    """F9: `daily.errors` -> lista de avisos legiveis para o relatorio.

    Um dia pode ter mais de um endpoint falho (separados por '; '); cada um
    vira um aviso dizendo o que ficou sem dado e por que. None/vazio devolve
    lista vazia -- dia sincronizado por inteiro nao gera aviso."""
    if not errors:
        return []
    avisos = []
    for nome in (e.strip() for e in errors.split(";") if e.strip()):
        campos = _ENDPOINT_CAMPOS.get(nome, "dados")
        avisos.append(f"sincronizacao incompleta: {campos} vieram sem dado "
                      f"(endpoint {nome} falhou no Garmin)")
    return avisos


def main() -> None:
    try:
        profile = load_profile()
    except FileNotFoundError as exc:
        print(exc)
        sys.exit(1)

    conn = db.connect_readonly()
    hoje = date.today()
    hoje_str = hoje.isoformat()

    if conn.execute("SELECT 1 FROM daily WHERE date = ?", (hoje_str,)).fetchone() is None:
        print(f"\nNenhum dado para {hoje_str}. Rode 'python -m farol.ingest' primeiro.\n")
        conn.close()
        return

    # Toda comparacao a partir daqui e contra a janela do proprio atleta.
    metricas = {
        "hrv_avg": daily_metric(conn, "hrv_avg", hoje_str),
        "rhr": daily_metric(conn, "rhr", hoje_str),
        "sleep_score": daily_metric(conn, "sleep_score", hoje_str),
        "night_hr_avg": daily_metric(conn, "night_hr_avg", hoje_str),
        "total_sleep_minutes": daily_metric(conn, "total_sleep_minutes", hoje_str),
        "tsb": from_daily(conn, "tsb", hoje_str, higher_is_better=True, table="training_load"),
        # Ja coletados pelo Garmin, so nunca lidos ate agora (parecer tecnico
        # de 2026-09-04): entram no mesmo tratamento de confianca/convergencia
        # que HRV, sem entrar em PESOS_PADRAO -- mesmo precedente que
        # body_battery_max ja segue (no dominio, fora do indice).
        "respiration_avg": daily_metric(conn, "respiration_avg", hoje_str),
        "spo2_avg": daily_metric(conn, "spo2_avg", hoje_str),
        "body_battery_min": daily_metric(conn, "body_battery_min", hoje_str),
        # stress_avg ja estava em state._RECUPERACAO (DAILY_STATE.md) desde
        # antes desta sessao, mas nunca tinha entrado no relatorio de
        # terminal nem em detect_patterns -- lacuna real: estresse (diario e
        # noturno) e' o sinal mais diretamente ligado ao achado clinico do
        # usuario (resposta cardiovascular compativel com hiperatividade
        # simpatica/atividade vagal reduzida, PROFILE.md), pedido
        # explicitamente em 2026-09-04. avg_sleep_stress e' novo em
        # qualquer lugar do farol -- estava capturado desde sempre
        # (garmin_client/sheets/DIRECTION ja tinham a entrada), nunca lido.
        # Os dois verificados contra o historico real antes de entrar aqui:
        # 16% dos dias cada, mesma faixa normal de HRV/RHR -- nenhum dos
        # dois tem o problema de volatilidade que tirou body_battery_min do
        # dominio.
        "stress_avg": daily_metric(conn, "stress_avg", hoje_str),
        "avg_sleep_stress": daily_metric(conn, "avg_sleep_stress", hoje_str),
    }

    # VO2max do Garmin: fica FORA de `metricas` de proposito, nao entra em
    # detect_patterns/recovery_index. E' um sinal lento (o Garmin so
    # recalcula depois de certos treinos, entao o mesmo valor se repete por
    # dias), diferente dos sinais agudos que a regra de convergencia (regra
    # 8) foi desenhada para cruzar -- deixar entrar inflaria streak/padrao
    # com um valor que nao mudou de verdade. PROFILE.md e explicito: nao e'
    # comparavel ao VO2max do exame ergometrico (53,8 ml/kg/min) nem deve
    # ancorar nada -- so a tendencia contra a propria janela de 90d importa
    # (regra 6), por isso o rotulo diz "(Garmin)" sempre.
    vo2max_metric = daily_metric(conn, "vo2max", hoje_str)

    pesos = ((profile.get("recovery_index") or {}).get("weights")) or PESOS_PADRAO
    indice, notas = recovery_index(metricas, pesos)
    padroes = detect_patterns(metricas)
    doenca = signature_doenca(metricas)
    prova = race_context(profile, hoje)

    tl = conn.execute("SELECT * FROM training_load WHERE date = ?", (hoje_str,)).fetchone()
    daily = dict(conn.execute("SELECT * FROM daily WHERE date = ?", (hoje_str,)).fetchone())
    atividades = [dict(a) for a in conn.execute(
        "SELECT * FROM activities WHERE date = ? ORDER BY activity_id", (hoje_str,)).fetchall()]

    L = "=" * 66
    print(f"\n{L}\n  ESTADO ATUAL — {hoje_str}\n{L}\n")

    # ---------------------------------------------------------- resumo
    print("RESUMO")
    print("  Como ler: confira confianca e sinais, depois tendencia e persistencia;")
    print("  convergencia orienta revisao das evidencias, nao decide o que fazer.")
    if indice is not None:
        status_indice = recovery_status(indice)
        print(f"  Recuperacao: {_colorir(f'{indice}/10', status_indice)} — {recovery_label(indice)}")
    else:
        print("  Recuperacao: sem dado suficiente para calcular")
    if prova:
        print(f"  Prova: {prova['nome']} em {prova['dias']} dias — fase de {prova['fase']}")
        # F1 (auditoria): pace-alvo estimado da prova recente mais proxima, se
        # houver ancora observada em profile.yaml -> recent_races. Descritivo,
        # nao prescricao (PROFILE.md regra 9) -- mostra o numero e o TSB atual,
        # e para ai. Mesma etica do resto do farol: sem ancora, nao chuta.
        if prova.get("distancia_km"):
            refs = race_prediction.reference_races(profile)
            provas = race_prediction.observed_performances(conn, [r["date"] for r in refs])
            if provas:
                ancora = max(provas, key=lambda p: p["date"])
                pred = race_prediction.predict(
                    ancora["distance_km"], ancora["duration_min"], prova["distancia_km"])
                print(f"  Previsao da meia: {race_prediction._fmt_tempo(pred['min'])} "
                      f"a {race_prediction._fmt_tempo(pred['max'])} "
                      f"(faixa entre Riegel/Cameron/VDOT; nao e intervalo de confianca, "
                      f"ancora {ancora['date']})")
                tsb = race_prediction.tsb_mais_recente(conn, hoje_str)
                if tsb:
                    print(f"  TSB hoje: {tsb['tsb']:+.1f} (alvo conceitual no dia da "
                          f"prova: descansado/positivo -- decisao do treinador)")
                plan = nutrition.build_plan(
                    (pred["min"] + pred["max"]) / 2, nutrition.config_from_profile(profile),
                    (profile.get("race") or {}).get("start_time"))
                print(f"  Nutricao configurada: {len(plan['events'])} gel(is), "
                      f"{plan['total_carbs_g']:.0f} g carboidrato e "
                      f"{plan['total_caffeine_mg']:.0f} mg de cafeina em gel "
                      f"(agua junto aos geis).")
    if atividades:
        print(f"  Treino hoje: {resumo_atividades(atividades)}")
    else:
        print("  Treino hoje: nenhum registrado")
    if doenca:
        print(f"  {_colorir('Atencao:', 'atencao')} {doenca}")
    elif padroes:
        print(f"  {_colorir('Atencao:', 'atencao')} {padroes[0]}")
    conf_baixa = [_ROTULO.get(k, k) for k, m in metricas.items() if m.confidence == "baixa"]
    if conf_baixa:
        print(f"  Confianca baixa em: {', '.join(conf_baixa)}")
    # F9: falha de endpoint nao e' estado fisiologico -- e' cobertura de dado.
    # O aviso explica por que um campo apareceu vazio no relatorio, senao o
    # "sem dado" parece ausencia real de medicao. Mesma secao do RESUMO, mas
    # nunca vira padrao/doenca nem entram no indice.
    for aviso in avisos_erros_sync(daily.get("errors")):
        print(f"  {_colorir('Atencao:', 'atencao')} {aviso}")
    # Leitura compacta em linguagem (farol.interpretation) -- mesmo texto da
    # secao 11 do DAILY_ANALYSIS.md, derivado da MESMA classificacao de
    # farol.state (state.gather le farol_daily_state; as metricas detalhadas
    # acima leem `daily` -- sao duas superfícies, e a leitura usa a
    # classificacao por dominio, fonte unica dos relatorios).
    leitura = None
    dados_estado = None
    try:
        dados_estado = state.gather(conn, hoje_str)
        leitura = interpretation.interpretar(dados_estado)
    except ValueError:
        leitura = None
    if leitura:
        print()
        print("  LEITURA:")
        print(f"    Estado: {leitura['estado_fisiologico']}")
        print(f"    Sinal dominante: {leitura['sinal_dominante']}")
        print(f"    Convergencia: {leitura['convergencia']}")
        print(f"    Conclusao: {leitura['conclusao']}")
    if dados_estado:
        _imprime_resumo_longitudinal(dados_estado)
    injury = injuries.latest_observation(hoje_str)
    injury_alert = injuries.observation_alert(injury)
    print(f"  Lesao (registro manual): {injuries.observation_text(injury)}")
    if injury_alert:
        print(f"  {_colorir('Alerta:', 'atencao')} {injury_alert}")
    print()

    # ---------------------------------------------------------- treino
    if atividades:
        print("-" * 66)
        print("TREINO REALIZADO")
        print("-" * 66)
        faixas = heart_rate_zones(profile)
        for act in atividades:
            print(f"  {act['type'] or 'atividade'}: {act['name'] or '(sem nome)'}")

            # Nem toda atividade percorre distancia: pilates vem com 0.0 e
            # breathwork com NULL, e 0.0 e falsy em Python. Condicionar a
            # duracao a ter quilometro fazia uma sessao de 60 min de pilates
            # aparecer sem duracao nenhuma.
            partes = []
            if act.get("duration_min"):
                partes.append(f"{act['duration_min']:.0f} min")
            if act.get("distance_km"):
                partes.append(f"{act['distance_km']:.2f} km")
                if act.get("duration_min"):
                    partes.append(f"pace {act['duration_min'] / act['distance_km']:.2f} min/km")
            if partes:
                print("  " + " | ".join(partes))

            if act.get("avg_hr"):
                zona = zone_of(act["avg_hr"], faixas)
                print(f"  FC {act['avg_hr']} bpm{zona} (max {act.get('max_hr') or '—'}) "
                      f"| TRIMP {act.get('trimp') or 0:.0f} | exigencia {classify_workout_demand(act)}")
            # Biomecanica so existe de fato em corrida, e mesmo la nem todo
            # campo vem. O Garmin devolve cadencia de 0,3 spm em pilates e NULL
            # em GCT/oscilacao -- imprimir "GCT 0 ms" seria inventar uma medicao
            # que nao houve (licao do BUGS.md do AthletIQ: 0 nunca substitui
            # ausente). Nenhuma cadencia humana fica abaixo de 50 spm.
            bio = []
            if (act.get("avg_cadence") or 0) > 50:
                bio.append(f"cadencia {act['avg_cadence']:.0f} spm")
            if act.get("ground_contact_time"):
                bio.append(f"GCT {act['ground_contact_time']:.0f} ms")
            if act.get("vertical_oscillation"):
                bio.append(f"oscilacao {act['vertical_oscillation']:.1f} cm")
            if bio:
                print("  " + " | ".join(bio))

            # Tiros vem do banco, gravados pelo ingest -- antes o relatorio fazia
            # login no Garmin toda vez so para montar este bloco. O filtro por
            # tipo e a checagem de forma existem porque a planilha herdada tem
            # conteudo antigo nesta coluna para pilates e forca.
            resumo = None
            if act.get("type") == "running" and act.get("splits"):
                try:
                    dados = json.loads(act["splits"])
                    if isinstance(dados, dict) and dados.get("reps"):
                        resumo = dados
                except (ValueError, TypeError):
                    resumo = None
            if resumo:
                    print(f"\n  Tiros ({resumo['n_reps']}x ~{resumo['avg_distance_km']*1000:.0f}m):")
                    for j, r in enumerate(resumo["reps"], 1):
                        print(f"    {j}: {r['pace_min_km']:.2f} min/km | FC {r['avg_hr']:.0f}"
                              f"-{r['max_hr']:.0f} | cad {r['avg_cadence']:.0f}")
                    cons = pace_consistency(resumo["reps"])
                    if cons:
                        cv, rot = cons
                        print(f"  Consistencia do pace: CV {cv}% ({rot})")
                    drift = hr_drift(resumo["reps"])
                    if drift is not None:
                        print(f"  Deriva de FC do 1o ao ultimo tiro: {drift:+d} bpm")
            print()

    # ------------------------------------------------------ carga e estado
    print("-" * 66)
    print("CARGA DE TREINO")
    print("-" * 66)
    if tl:
        print(f"  CTL {tl['ctl']:.1f} (forma)   ATL {tl['atl']:.1f} (fadiga aguda)")
    print(linha_metrica(metricas["tsb"], "TSB", spark=sparkline(
        _serie_recente(conn, "tsb", hoje_str, table="training_load"))))

    if tl and tl["ctl"]:
        print(f"  ATL/CTL {tl['atl'] / tl['ctl']:.2f}  (acima de 1 = carga aguda "
              f"maior que a cronica)")
    print(linha_metrica(vo2max_metric, "VO2max (Garmin)", " ml/kg/min"))

    # 8 semanas, nao 4: com janela curta a semana leve de 06/08 fica de fora e a
    # progressao parece um salto de 53%, quando contra o patamar de julho e 16%.
    semanas = weekly_load(conn, hoje_str, semanas=8)
    if semanas:
        print()
        print("  Semana                     km   sessoes  TRIMP  monotonia")
        for i, s in enumerate(semanas):
            mono = f"{s['monotonia']:.2f}" if s["monotonia"] is not None else "  -  "
            marca = "  <- semana atual" if i == 0 else ""
            if (s["monotonia"] or 0) > 2.0:
                marca = "  <- repetitiva"
            # Mostra o intervalo inteiro: so a data de inicio dava a impressao de
            # que os dados paravam ali, quando a linha ja inclui hoje.
            print(f"  {s['inicio']} a {s['fim']}  {s['km']:5.1f}  {s['sessoes']:5d}  "
                  f"{s['trimp']:5d}     {mono}{marca}")

        atual = semanas[0]["trimp"]
        media4 = sum(x["trimp"] for x in semanas[:4]) / 4
        if media4:
            print()
            print(f"  Carga 7d {atual}  |  media semanal das 4 ultimas {media4:.0f}"
                  f"  |  razao {atual / media4:.2f}")
        print(f"  Soma 14d {sum(x['trimp'] for x in semanas[:2])}  |  "
              f"28d {sum(x['trimp'] for x in semanas[:4])}")
        print("  (monotonia > 2.0 = carga sem contraste entre dias, marcador de risco)")
    print()

    # Regra 6 do PROFILE.md: tendencia de semanas importa mais que alteracao
    # isolada. Essas comparacoes (primeira vs segunda metade do historico)
    # mudam pouco de um dia para o outro -- olhar todo dia so acrescenta
    # ruido, entao aparecem quinzenalmente (dias 1 e 15), nao a cada corrida.
    if hoje.day in (1, 15):
        # Regra 5 do PROFILE.md: pace x FC acompanha evolucao melhor que o
        # VO2max do relogio. FC lida sempre no mesmo pace de referencia,
        # senao um bloco de treinos fortes "melhora" o indicador sem ganho
        # nenhum de forma.
        blocos = aerobic_efficiency(conn, hoje_str)
        if blocos:
            print("-" * 66)
            print("EFICIENCIA AEROBICA")
            print("-" * 66)
            for b in blocos:
                print(f"  {b.rotulo:<16} FC {b.valor_ref:.1f} +/- {b.erro_padrao:.1f} bpm "
                      f"a {b.pace_ref:.2f} min/km   (n={b.n}, {b.d0} a {b.d1})")
            veredito = efficiency_verdict(blocos)
            if veredito:
                print(f"  -> {veredito}")
            print()

        # Mesma regra 5, mas tiro a tiro em vez de corrida inteira -- mais
        # denso na faixa de esforco forte (analysis.py::interval_efficiency).
        blocos_tiro = interval_efficiency(conn, hoje_str)
        if blocos_tiro:
            print("-" * 66)
            print("EFICIENCIA EM TIRO")
            print("-" * 66)
            for b in blocos_tiro:
                print(f"  {b.rotulo:<16} FC {b.valor_ref:.1f} +/- {b.erro_padrao:.1f} bpm "
                      f"a {b.pace_ref:.2f} min/km   (n={b.n}, {b.d0} a {b.d1})")
            veredito_tiro = efficiency_verdict(blocos_tiro)
            if veredito_tiro:
                print(f"  -> {veredito_tiro}")
            print()

        # Mesma ideia, lado mecanico: economia de corrida por cadencia/tempo
        # de contato/oscilacao vertical. Ortogonal a economia cardiaca. Em
        # tiro (nunca recuperacao/caminhada -- analysis.py::_interval_reps)
        # e em corrida continua (longao, rodagem -- analysis.py::_runs).
        _imprime_mecanica(conn, hoje_str, "ECONOMIA DE CORRIDA (MECANICA, EM TIRO)",
                           interval_mechanics)
        _imprime_mecanica(conn, hoje_str, "ECONOMIA DE CORRIDA (MECANICA, CONTINUA)",
                           continuous_mechanics)
    else:
        proxima = _proxima_analise_quinzenal(hoje)
        print(f"(Economia de corrida: analise quinzenal, proxima em {proxima.isoformat()})")
        print()

    print("-" * 66)
    print("RECUPERACAO")
    print("-" * 66)
    if indice is not None:
        status_indice = recovery_status(indice)
        print(f"  Indice {_colorir(f'{indice}/10', status_indice)} — {recovery_label(indice)}"
              "   (6.5 = exatamente na sua media)")
        for chave, nota in notas.items():
            peso = pesos.get(chave, 0)
            texto = f"{nota}/10" if nota is not None else "sem dado"
            print(f"    {_ROTULO.get(chave, chave):<12} {texto:>9}   peso {peso:.0%}")
        print()
    print(linha_metrica(metricas["hrv_avg"], "HRV", " ms",
                         spark=sparkline(_serie_recente(conn, "hrv_avg", hoje_str))))
    # Sinal diferente da media acima: CV colapsando (ficando artificialmente
    # estavel) e' indicador precoce de overreaching nao-funcional na
    # literatura (Plews & Buchheit), as vezes antes da media cair. Sem
    # bucket/limiar de proposito -- ver farol/hrv_variability.py.
    cv = hrv_cv(conn, hoje_str)
    if cv is not None:
        ref = f"{cv['cv_30d']:.1f}%" if cv["cv_30d"] is not None else "sem dado"
        print(f"  {'HRV CV':<14} {cv['cv_7d']:.1f}% (7d) | referencia 30d {ref}")
    print(linha_metrica(metricas["rhr"], "FC repouso", " bpm",
                         spark=sparkline(_serie_recente(conn, "rhr", hoje_str))))
    print(linha_metrica(metricas["night_hr_avg"], "FC noturna", " bpm",
                         spark=sparkline(_serie_recente(conn, "night_hr_avg", hoje_str))))
    print(linha_metrica(metricas["respiration_avg"], "Respiracao", " rpm"))
    print(linha_metrica(metricas["spo2_avg"], "SpO2", "%"))
    print(linha_metrica(metricas["body_battery_min"], "Body Battery min", ""))
    print(linha_metrica(metricas["stress_avg"], "Stress", ""))
    print()

    # ------------------------------------------------------------- sono
    print("-" * 66)
    print("SONO")
    print("-" * 66)
    print(linha_metrica(metricas["sleep_score"], "Score", "/100",
                         spark=sparkline(_serie_recente(conn, "sleep_score", hoje_str))))
    print(linha_metrica(metricas["total_sleep_minutes"], "Duracao", " min"))
    print(linha_metrica(metricas["avg_sleep_stress"], "Stress no sono", ""))
    total = daily.get("total_sleep_minutes")
    if total:
        for campo, rotulo in (("deep_sleep_minutes", "Profundo"),
                              ("light_sleep_minutes", "Leve"),
                              ("rem_sleep_minutes", "REM")):
            v = daily.get(campo)
            if v is not None:
                print(f"  {rotulo:<14} {v:.0f} min ({v / total * 100:.0f}%)")
        desp = daily.get("awakenings")
        if desp is not None:
            print(f"  {'Fragmentacao':<14} {desp} despertares ({desp / (total / 60):.1f}/h)")
    if daily.get("sleep_quality"):
        print(f"  {'Qualidade':<14} {daily['sleep_quality']}")
    # Informativo, sem z-score/streak de proposito: e' uma janela movel de 14
    # dias, fortemente autocorrelacionada dia a dia -- alimentar isto em
    # detect_patterns/longitudinal inflaria "dias consecutivos ruins" so pela
    # sobreposicao da janela, nao por evidencia independente repetida
    # (achado ao validar contra o historico real). So o numero, como
    # contexto -- sem bucket ok/atencao/alerta.
    regularidade = bedtime_regularity(conn, hoje_str)
    if regularidade is not None:
        print(f"  {'Regularidade':<14} {regularidade:.0f} min de SD no horario de dormir (14d)")
    print()

    # ---------------------------------------------------------- padroes
    todos_padroes = ([doenca] if doenca else []) + padroes
    if todos_padroes:
        print("-" * 66)
        print("PADROES DETECTADOS (convergencia de 2+ indicadores)")
        print("-" * 66)
        for p in todos_padroes:
            print(f"  - {p}")
        print()

    print(L)
    print("  Leitura retrospectiva contra seu historico; nao e diagnostico nem decisao de treino.")
    print("  Convergencia e alerta estatistico para revisar evidencias, nao um oraculo.")
    print("  Esta execucao le o banco local e nao consulta o Garmin. Atualize via farol.ingest;")
    print("  ingestao depende de rede. Clima de percurso e' consulta externa separada (Open-Meteo).")
    print(L + "\n")

    conn.close()


if __name__ == "__main__":
    main()
