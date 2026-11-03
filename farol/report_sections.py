"""Formatacao das secoes do relatorio de terminal (`python -m farol.today`).

Este modulo so' monta texto. Nao le banco por conta propria, nao classifica
nada e nao tem limiar fisiologico: recebe de `farol.today` o contexto do dia
ja calculado (`ReportContext`) e devolve a sequencia de linhas que o
orquestrador imprime. A separacao existe para que a formatacao possa ser
testada por snapshot sem tocar no banco pessoal, e para que `today.py` fique
com a coleta de dados e as regras (detect_patterns/signature_doenca).

`render_report(ctx)` reproduz exatamente os `print()` que antes viviam em
`main`: cada elemento da lista vira uma linha (string vazia = linha em
branco), entao `print(render_report(ctx), end="")` tem a mesma saida byte a
byte que a versao anterior.

Cor (ANSI) segue a mesma regra de antes: so' quando stdout e' um terminal e
NO_COLOR nao esta definido. Redirecionar a saida nunca recebe escape.
"""
import json
import os
import sqlite3
import sys
from dataclasses import dataclass
from datetime import date, timedelta

from . import nutrition, race_prediction, state
from .analysis import (
    aerobic_efficiency, continuous_mechanics, efficiency_verdict, hr_drift,
    interval_efficiency, interval_mechanics, mechanical_verdict,
    pace_consistency, weekly_load,
)
from .hrv_variability import hrv_cv
from .injuries import observation_text
from .labels import ROTULOS
from .metrics import Metric
from .recovery import recovery_label, recovery_status
from .sleep_regularity import bedtime_regularity
from .zones import heart_rate_zones

# "Sono" fica local de proposito: aqui rotula a fatia do indice de
# recuperacao (junto de HRV/FC repouso/TSB), nao o campo bruto -- ver
# farol/labels.py.
ROTULOS_RELATORIO = {**ROTULOS, "sleep_score": "Sono", "avg_sleep_stress": "Stress no sono"}

MARCA = {"ok": "ok", "atencao": "atencao", "alerta": "ALERTA", "sem_dado": "sem dado"}
SETA = {"subindo": "^", "caindo": "v", "estavel": "=", "indefinido": "?"}

# campo em analysis.py -> (rotulo, unidade, menor_e_melhor). Mesma direcao ja
# usada em analysis.py::assess_deterioration_detected.
_MECANICA = (
    ("cadencia", "Cadencia", "spm", False),
    ("gct", "Tempo de contato", "ms", True),
    ("oscilacao", "Oscilacao vertical", "cm", True),
)

# Cor so no terminal (nunca em saida redirecionada/pipe -- um arquivo ou outro
# programa lendo isto nao deve receber sequencia de escape) e respeitando a
# convencao NO_COLOR (https://no-color.org/). So decora MARCA/indice na
# saida -- nao muda nenhum texto nem valor, so como ele aparece na tela.
_USAR_COR = sys.stdout.isatty() and os.environ.get("NO_COLOR") is None
_COR = {"ok": "\033[32m", "atencao": "\033[33m", "alerta": "\033[31m"}
_COR_RESET = "\033[0m"

# Recomendacao 9 do parecer tecnico de 2026-09-04, reabrindo a exclusao de
# "dashboard" da FASE 10 com uma distincao: isto e' formatacao de texto no
# mesmo relatorio de terminal que ja existe, nao infraestrutura nova --
# zero servidor, zero dependencia, zero tela propria.
_BLOCOS_SPARK = "▁▂▃▄▅▆▇█"


@dataclass
class ReportContext:
    """Tudo o que o relatorio precisa, ja' coletado por `farol.today`.

    Nenhum campo e' recalculado aqui. `dados_estado`/`leitura` sao None quando
    `state.gather` nao tem `farol_daily_state` para o dia -- o resto do
    relatorio continua valido (metricas vem de `daily`/`training_load`).
    """

    conn: sqlite3.Connection
    hoje: date
    hoje_str: str
    profile: dict
    metricas: dict[str, Metric]
    vo2max_metric: Metric
    indice: float | None
    notas: dict
    pesos: dict
    padroes: list[str]
    doenca: str | None
    prova: dict | None
    tl: sqlite3.Row | None
    daily: dict
    atividades: list[dict]
    dados_estado: dict | None
    leitura: dict | None
    injury: dict | None
    injury_alert: str | None


def colorir(texto: str, status: str) -> str:
    if not _USAR_COR or status not in _COR:
        return texto
    return f"{_COR[status]}{texto}{_COR_RESET}"


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
    partes.append(f"| 7d {SETA[m.trend]}")
    partes.append(f"| {colorir(MARCA[m.status], m.status)}")
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


def _texto_tendencia(trend: dict | None) -> str:
    if not trend:
        return "? dados insuficientes"
    return {
        "WORSENING": "↑ piorando",
        "IMPROVING": "↓ melhorando",
        "STABLE": "→ estável",
        "INSUFFICIENT_DATA": "? dados insuficientes",
    }.get(trend.get("direcao"), "? dados insuficientes")


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


# ------------------------------------------------------------- secoes

def _secao_resumo(ctx: ReportContext) -> list[str]:
    linhas = [
        "RESUMO",
        "  Como ler: confira confianca e sinais, depois tendencia e persistencia;",
        "  convergencia orienta revisao das evidencias, nao decide o que fazer.",
    ]
    if ctx.indice is not None:
        status_indice = recovery_status(ctx.indice)
        linhas.append(f"  Recuperacao: {colorir(f'{ctx.indice}/10', status_indice)} "
                      f"— {recovery_label(ctx.indice)}")
    else:
        linhas.append("  Recuperacao: sem dado suficiente para calcular")

    if ctx.prova:
        linhas.append(f"  Prova: {ctx.prova['nome']} em {ctx.prova['dias']} dias "
                      f"— fase de {ctx.prova['fase']}")
        # F1 (auditoria): pace-alvo estimado da prova recente mais proxima, se
        # houver ancora observada em profile.yaml -> recent_races. Descritivo,
        # nao prescricao (PROFILE.md regra 9) -- mostra o numero e o TSB atual,
        # e para ai. Mesma etica do resto do farol: sem ancora, nao chuta.
        if ctx.prova.get("distancia_km"):
            refs = race_prediction.reference_races(ctx.profile)
            provas = race_prediction.observed_performances(ctx.conn, [r["date"] for r in refs])
            if provas:
                ancora = max(provas, key=lambda p: p["date"])
                pred = race_prediction.predict(
                    ancora["distance_km"], ancora["duration_min"], ctx.prova["distancia_km"])
                linhas.append(f"  Previsao da meia: {race_prediction._fmt_tempo(pred['min'])} "
                              f"a {race_prediction._fmt_tempo(pred['max'])} "
                              f"(faixa entre Riegel/Cameron/VDOT; nao e intervalo de confianca, "
                              f"ancora {ancora['date']})")
                tsb = race_prediction.tsb_mais_recente(ctx.conn, ctx.hoje_str)
                if tsb:
                    linhas.append(f"  TSB hoje: {tsb['tsb']:+.1f} (alvo conceitual no dia da "
                                  f"prova: descansado/positivo -- decisao do treinador)")
                plan = nutrition.build_plan(
                    (pred["min"] + pred["max"]) / 2, nutrition.config_from_profile(ctx.profile),
                    (ctx.profile.get("race") or {}).get("start_time"))
                linhas.append(f"  Nutricao configurada: {len(plan['events'])} gel(is), "
                              f"{plan['total_carbs_g']:.0f} g carboidrato e "
                              f"{plan['total_caffeine_mg']:.0f} mg de cafeina em gel "
                              f"(agua junto aos geis).")

    if ctx.atividades:
        linhas.append(f"  Treino hoje: {resumo_atividades(ctx.atividades)}")
    else:
        linhas.append("  Treino hoje: nenhum registrado")

    if ctx.doenca:
        linhas.append(f"  {colorir('Atencao:', 'atencao')} {ctx.doenca}")
    elif ctx.padroes:
        linhas.append(f"  {colorir('Atencao:', 'atencao')} {ctx.padroes[0]}")

    conf_baixa = [ROTULOS_RELATORIO.get(k, k) for k, m in ctx.metricas.items()
                  if m.confidence == "baixa"]
    if conf_baixa:
        linhas.append(f"  Confianca baixa em: {', '.join(conf_baixa)}")

    # F9: falha de endpoint nao e' estado fisiologico -- e' cobertura de dado.
    # O aviso explica por que um campo apareceu vazio no relatorio, senao o
    # "sem dado" parece ausencia real de medicao. Mesma secao do RESUMO, mas
    # nunca vira padrao/doenca nem entram no indice.
    for aviso in avisos_erros_sync(ctx.daily.get("errors")):
        linhas.append(f"  {colorir('Atencao:', 'atencao')} {aviso}")

    # Leitura compacta em linguagem (farol.interpretation) -- mesmo texto da
    # secao 11 do DAILY_ANALYSIS.md, derivado da MESMA classificacao de
    # farol.state (state.gather le farol_daily_state; as metricas detalhadas
    # acima leem `daily` -- sao duas superficies, e a leitura usa a
    # classificacao por dominio, fonte unica dos relatorios).
    if ctx.leitura:
        linhas.append("")
        linhas.append("  LEITURA:")
        linhas.append(f"    Estado: {ctx.leitura['estado_fisiologico']}")
        linhas.append(f"    Sinal dominante: {ctx.leitura['sinal_dominante']}")
        linhas.append(f"    Convergencia: {ctx.leitura['convergencia']}")
        linhas.append(f"    Conclusao: {ctx.leitura['conclusao']}")

    if ctx.dados_estado:
        linhas += _secao_evolucao(ctx.dados_estado)

    linhas.append(f"  Lesao (registro manual): {observation_text(ctx.injury)}")
    if ctx.injury_alert:
        linhas.append(f"  {colorir('Alerta:', 'atencao')} {ctx.injury_alert}")
    linhas.append("")
    return linhas


def _secao_evolucao(dados_estado: dict) -> list[str]:
    """Mostra status, confianca, streak e direcao ja calculados por state.gather()."""
    linhas = ["  EVOLUCAO DOS DOMINIOS (tendencia de severidade, janela de 7 dias)",
              "    Dominio       Estado       Confianca   Streak       Tendencia"]
    for nome, dominio in dados_estado["dominios"].items():
        rotulo, _ = state._LABEL[dominio["status"]]
        streak = dominio["streak"]
        streak_txt = f"{streak['dias']}d" if streak["dias"] else "—"
        linhas.append(f"    {nome:<14} {rotulo:<12} {dominio['confianca']:<11} "
                      f"{streak_txt:<12} {_texto_tendencia(dominio['trend'])}")

    status_por_dominio = {nome: dominio["status"]
                          for nome, dominio in dados_estado["dominios"].items()}
    convergencia = state.convergence(status_por_dominio)
    streak = dados_estado["convergencia_streak"]
    tendencia = _texto_tendencia(dados_estado["convergencia_trend"])
    if convergencia:
        persiste = f"{streak['dias']}d" if streak["dias"] else "sem streak"
        linhas.append(f"    Convergencia: ativa ({persiste}; {tendencia})")
    else:
        linhas.append(f"    Convergencia: nao ativa hoje ({tendencia})")
    linhas.append("    Nota: setas descrevem mudanca de severidade, nao recomendam treino.")
    return linhas


def _secao_treino(ctx: ReportContext) -> list[str]:
    if not ctx.atividades:
        return []
    linhas = ["-" * 66, "TREINO REALIZADO", "-" * 66]
    faixas = heart_rate_zones(ctx.profile)
    for act in ctx.atividades:
        linhas.append(f"  {act['type'] or 'atividade'}: {act['name'] or '(sem nome)'}")

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
            linhas.append("  " + " | ".join(partes))

        if act.get("avg_hr"):
            zona = zone_of(act["avg_hr"], faixas)
            linhas.append(f"  FC {act['avg_hr']} bpm{zona} (max {act.get('max_hr') or '—'}) "
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
            linhas.append("  " + " | ".join(bio))

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
            linhas.append(f"\n  Tiros ({resumo['n_reps']}x ~{resumo['avg_distance_km']*1000:.0f}m):")
            for j, r in enumerate(resumo["reps"], 1):
                linhas.append(f"    {j}: {r['pace_min_km']:.2f} min/km | FC {r['avg_hr']:.0f}"
                              f"-{r['max_hr']:.0f} | cad {r['avg_cadence']:.0f}")
            cons = pace_consistency(resumo["reps"])
            if cons:
                cv, rot = cons
                linhas.append(f"  Consistencia do pace: CV {cv}% ({rot})")
            drift = hr_drift(resumo["reps"])
            if drift is not None:
                linhas.append(f"  Deriva de FC do 1o ao ultimo tiro: {drift:+d} bpm")
        linhas.append("")
    return linhas


def _secao_carga(ctx: ReportContext) -> list[str]:
    linhas = ["-" * 66, "CARGA DE TREINO", "-" * 66]
    if ctx.tl:
        linhas.append(f"  CTL {ctx.tl['ctl']:.1f} (forma)   ATL {ctx.tl['atl']:.1f} (fadiga aguda)")
    linhas.append(linha_metrica(ctx.metricas["tsb"], "TSB", spark=sparkline(
        _serie_recente(ctx.conn, "tsb", ctx.hoje_str, table="training_load"))))

    if ctx.tl and ctx.tl["ctl"]:
        linhas.append(f"  ATL/CTL {ctx.tl['atl'] / ctx.tl['ctl']:.2f}  (acima de 1 = carga aguda "
                      f"maior que a cronica)")
    linhas.append(linha_metrica(ctx.vo2max_metric, "VO2max (Garmin)", " ml/kg/min"))

    # 8 semanas, nao 4: com janela curta a semana leve de 06/08 fica de fora e a
    # progressao parece um salto de 53%, quando contra o patamar de julho e 16%.
    semanas = weekly_load(ctx.conn, ctx.hoje_str, semanas=8)
    if semanas:
        linhas.append("")
        linhas.append("  Semana                     km   sessoes  TRIMP  monotonia")
        for i, s in enumerate(semanas):
            mono = f"{s['monotonia']:.2f}" if s["monotonia"] is not None else "  -  "
            marca = "  <- semana atual" if i == 0 else ""
            if (s["monotonia"] or 0) > 2.0:
                marca = "  <- repetitiva"
            # Mostra o intervalo inteiro: so a data de inicio dava a impressao de
            # que os dados paravam ali, quando a linha ja inclui hoje.
            linhas.append(f"  {s['inicio']} a {s['fim']}  {s['km']:5.1f}  {s['sessoes']:5d}  "
                          f"{s['trimp']:5d}     {mono}{marca}")

        atual = semanas[0]["trimp"]
        media4 = sum(x["trimp"] for x in semanas[:4]) / 4
        if media4:
            linhas.append("")
            linhas.append(f"  Carga 7d {atual}  |  media semanal das 4 ultimas {media4:.0f}"
                          f"  |  razao {atual / media4:.2f}")
        linhas.append(f"  Soma 14d {sum(x['trimp'] for x in semanas[:2])}  |  "
                      f"28d {sum(x['trimp'] for x in semanas[:4])}")
        linhas.append("  (monotonia > 2.0 = carga sem contraste entre dias, marcador de risco)")
    linhas.append("")
    return linhas


def _secao_mecanica(ctx: ReportContext, titulo: str, funcao_analise) -> list[str]:
    """Secao de economia mecanica (cadencia/tempo de contato/oscilacao
    vertical), usando `funcao_analise(conn, hoje_str, campo)` --
    `interval_mechanics` (tiro) ou `continuous_mechanics` (corrida inteira)."""
    blocos = []
    for campo, nome, unidade, menor_e_melhor in _MECANICA:
        blocos_m = funcao_analise(ctx.conn, ctx.hoje_str, campo)
        if blocos_m:
            blocos.append((nome, unidade, menor_e_melhor, blocos_m))
    if not blocos:
        return []
    linhas = ["-" * 66, titulo, "-" * 66]
    for nome, unidade, menor_e_melhor, blocos_m in blocos:
        linhas.append(f"  {nome}:")
        for b in blocos_m:
            linhas.append(f"    {b.rotulo:<16} {b.valor_ref:.1f} +/- {b.erro_padrao:.1f} {unidade} "
                          f"a {b.pace_ref:.2f} min/km   (n={b.n}, {b.d0} a {b.d1})")
        veredito_m = mechanical_verdict(blocos_m, nome.lower(), unidade, menor_e_melhor)
        if veredito_m:
            linhas.append(f"    -> {veredito_m}")
    linhas.append("")
    return linhas


def _secao_eficiencia(ctx: ReportContext) -> list[str]:
    # Regra 6 do PROFILE.md: tendencia de semanas importa mais que alteracao
    # isolada. Essas comparacoes (primeira vs segunda metade do historico)
    # mudam pouco de um dia para o outro -- olhar todo dia so acrescenta
    # ruido, entao aparecem quinzenalmente (dias 1 e 15), nao a cada corrida.
    if ctx.hoje.day not in (1, 15):
        proxima = _proxima_analise_quinzenal(ctx.hoje)
        return [f"(Economia de corrida: analise quinzenal, proxima em {proxima.isoformat()})", ""]

    linhas = []
    # Regra 5 do PROFILE.md: pace x FC acompanha evolucao melhor que o
    # VO2max do relogio. FC lida sempre no mesmo pace de referencia,
    # senao um bloco de treinos fortes "melhora" o indicador sem ganho
    # nenhum de forma.
    blocos = aerobic_efficiency(ctx.conn, ctx.hoje_str)
    if blocos:
        linhas += ["-" * 66, "EFICIENCIA AEROBICA", "-" * 66]
        for b in blocos:
            linhas.append(f"  {b.rotulo:<16} FC {b.valor_ref:.1f} +/- {b.erro_padrao:.1f} bpm "
                          f"a {b.pace_ref:.2f} min/km   (n={b.n}, {b.d0} a {b.d1})")
        veredito = efficiency_verdict(blocos)
        if veredito:
            linhas.append(f"  -> {veredito}")
        linhas.append("")

    # Mesma regra 5, mas tiro a tiro em vez de corrida inteira -- mais
    # denso na faixa de esforco forte (analysis.py::interval_efficiency).
    blocos_tiro = interval_efficiency(ctx.conn, ctx.hoje_str)
    if blocos_tiro:
        linhas += ["-" * 66, "EFICIENCIA EM TIRO", "-" * 66]
        for b in blocos_tiro:
            linhas.append(f"  {b.rotulo:<16} FC {b.valor_ref:.1f} +/- {b.erro_padrao:.1f} bpm "
                          f"a {b.pace_ref:.2f} min/km   (n={b.n}, {b.d0} a {b.d1})")
        veredito_tiro = efficiency_verdict(blocos_tiro)
        if veredito_tiro:
            linhas.append(f"  -> {veredito_tiro}")
        linhas.append("")

    # Mesma ideia, lado mecanico: economia de corrida por cadencia/tempo
    # de contato/oscilacao vertical. Ortogonal a economia cardiaca. Em
    # tiro (nunca recuperacao/caminhada -- analysis.py::_interval_reps)
    # e em corrida continua (longao, rodagem -- analysis.py::_runs).
    linhas += _secao_mecanica(ctx, "ECONOMIA DE CORRIDA (MECANICA, EM TIRO)",
                              interval_mechanics)
    linhas += _secao_mecanica(ctx, "ECONOMIA DE CORRIDA (MECANICA, CONTINUA)",
                              continuous_mechanics)
    return linhas


def _secao_recuperacao(ctx: ReportContext) -> list[str]:
    linhas = ["-" * 66, "RECUPERACAO", "-" * 66]
    if ctx.indice is not None:
        status_indice = recovery_status(ctx.indice)
        linhas.append(f"  Indice {colorir(f'{ctx.indice}/10', status_indice)} — "
                      f"{recovery_label(ctx.indice)}"
                      "   (6.5 = exatamente na sua media)")
        for chave, nota in ctx.notas.items():
            peso = ctx.pesos.get(chave, 0)
            texto = f"{nota}/10" if nota is not None else "sem dado"
            linhas.append(f"    {ROTULOS_RELATORIO.get(chave, chave):<12} {texto:>9}   peso {peso:.0%}")
        linhas.append("")
    linhas.append(linha_metrica(ctx.metricas["hrv_avg"], "HRV", " ms",
                                spark=sparkline(_serie_recente(ctx.conn, "hrv_avg", ctx.hoje_str))))
    # Sinal diferente da media acima: CV colapsando (ficando artificialmente
    # estavel) e' indicador precoce de overreaching nao-funcional na
    # literatura (Plews & Buchheit), as vezes antes da media cair. Sem
    # bucket/limiar de proposito -- ver farol/hrv_variability.py.
    cv = hrv_cv(ctx.conn, ctx.hoje_str)
    if cv is not None:
        ref = f"{cv['cv_30d']:.1f}%" if cv["cv_30d"] is not None else "sem dado"
        linhas.append(f"  {'HRV CV':<14} {cv['cv_7d']:.1f}% (7d) | referencia 30d {ref}")
    linhas.append(linha_metrica(ctx.metricas["rhr"], "FC repouso", " bpm",
                                spark=sparkline(_serie_recente(ctx.conn, "rhr", ctx.hoje_str))))
    linhas.append(linha_metrica(ctx.metricas["night_hr_avg"], "FC noturna", " bpm",
                                spark=sparkline(_serie_recente(ctx.conn, "night_hr_avg", ctx.hoje_str))))
    linhas.append(linha_metrica(ctx.metricas["respiration_avg"], "Respiracao", " rpm"))
    linhas.append(linha_metrica(ctx.metricas["spo2_avg"], "SpO2", "%"))
    linhas.append(linha_metrica(ctx.metricas["body_battery_min"], "Body Battery min", ""))
    linhas.append(linha_metrica(ctx.metricas["stress_avg"], "Stress", ""))
    linhas.append("")
    return linhas


def _secao_sono(ctx: ReportContext) -> list[str]:
    linhas = ["-" * 66, "SONO", "-" * 66,
              linha_metrica(ctx.metricas["sleep_score"], "Score", "/100",
                            spark=sparkline(_serie_recente(ctx.conn, "sleep_score", ctx.hoje_str))),
              linha_metrica(ctx.metricas["total_sleep_minutes"], "Duracao", " min"),
              linha_metrica(ctx.metricas["avg_sleep_stress"], "Stress no sono", "")]
    total = ctx.daily.get("total_sleep_minutes")
    if total:
        for campo, rotulo in (("deep_sleep_minutes", "Profundo"),
                              ("light_sleep_minutes", "Leve"),
                              ("rem_sleep_minutes", "REM")):
            v = ctx.daily.get(campo)
            if v is not None:
                linhas.append(f"  {rotulo:<14} {v:.0f} min ({v / total * 100:.0f}%)")
        desp = ctx.daily.get("awakenings")
        if desp is not None:
            linhas.append(f"  {'Fragmentacao':<14} {desp} despertares ({desp / (total / 60):.1f}/h)")
    if ctx.daily.get("sleep_quality"):
        linhas.append(f"  {'Qualidade':<14} {ctx.daily['sleep_quality']}")
    # Informativo, sem z-score/streak de proposito: e' uma janela movel de 14
    # dias, fortemente autocorrelacionada dia a dia -- alimentar isto em
    # detect_patterns/longitudinal inflaria "dias consecutivos ruins" so pela
    # sobreposicao da janela, nao por evidencia independente repetida
    # (achado ao validar contra o historico real). So o numero, como
    # contexto -- sem bucket ok/atencao/alerta.
    regularidade = bedtime_regularity(ctx.conn, ctx.hoje_str)
    if regularidade is not None:
        linhas.append(f"  {'Regularidade':<14} {regularidade:.0f} min de SD no horario de dormir (14d)")
    linhas.append("")
    return linhas


def _secao_padroes(ctx: ReportContext) -> list[str]:
    todos_padroes = ([ctx.doenca] if ctx.doenca else []) + ctx.padroes
    if not todos_padroes:
        return []
    linhas = ["-" * 66, "PADROES DETECTADOS (convergencia de 2+ indicadores)", "-" * 66]
    linhas += [f"  - {p}" for p in todos_padroes]
    linhas.append("")
    return linhas


def _secao_rodape() -> list[str]:
    linha = "=" * 66
    return [
        linha,
        "  Leitura retrospectiva contra seu historico; nao e diagnostico nem decisao de treino.",
        "  Convergencia e alerta estatistico para revisar evidencias, nao um oraculo.",
        "  Esta execucao le o banco local e nao consulta o Garmin. Atualize via farol.ingest;",
        "  ingestao depende de rede. Clima de percurso e' consulta externa separada (Open-Meteo).",
        linha + "\n",
    ]


def render_report(ctx: ReportContext) -> str:
    """Relatorio completo do dia como texto, na mesma ordem/espacos que a
    versao que imprimia seção por seção em `today.main`."""
    linha = "=" * 66
    linhas = [f"\n{linha}\n  ESTADO ATUAL — {ctx.hoje_str}\n{linha}\n"]
    linhas += _secao_resumo(ctx)
    linhas += _secao_treino(ctx)
    linhas += _secao_carga(ctx)
    linhas += _secao_eficiencia(ctx)
    linhas += _secao_recuperacao(ctx)
    linhas += _secao_sono(ctx)
    linhas += _secao_padroes(ctx)
    linhas += _secao_rodape()
    return "\n".join(linhas) + "\n"
