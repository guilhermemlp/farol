"""Análises de corrida que dependem de mais de um dia.

Duas coisas moram aqui:

1. Eficiência de corrida — a regra 5 do PROFILE.md ("pace x FC é mais
   informativo que VO2max do Garmin isolado para acompanhar evolução"),
   estendida também ao lado mecânico (cadência, tempo de contato, oscilação
   vertical), sempre pace × indicador.
2. Monotonia e strain de Foster — quanto a carga da semana é repetitiva.

Nota metodológica sobre eficiência: bpm cru (ou spm, ou ms, ou cm) NÃO serve
para tendência. Correr mais rápido melhora qualquer indicador cru até certo
ponto, então uma sequência de treinos fortes "melhora" o número sem nenhum
ganho de forma. O que serve é o indicador a um pace de referência fixo:
ajusta-se indicador ~ pace na janela e lê-se a reta num pace só.
`aerobic_efficiency`/`interval_efficiency` fazem isso para FC;
`interval_mechanics` para cadência, tempo de contato e oscilação vertical.

Tudo aqui usa só atividades com `source='garmin'`. As legadas têm distância em
unidade desconhecida e não constante (ver sheets.py) e envenenariam qualquer
cálculo de pace.
"""
import json
from dataclasses import dataclass
from datetime import date as _date
from datetime import timedelta
from statistics import StatisticsError, fmean, linear_regression, mean, median, pstdev

# Mínimo de corridas para uma regressão significar alguma coisa. Abaixo disso a
# reta passa a descrever o ruído.
_MIN_CORRIDAS = 6


@dataclass(frozen=True)
class Efficiency:
    """Um indicador (FC, cadência, tempo de contato ou oscilação vertical)
    estimado num pace de referência, para uma janela de tempo. Genérico o
    bastante para qualquer indicador ~ pace -- só a unidade muda."""

    rotulo: str
    n: int
    pace_ref: float
    valor_ref: float
    erro_padrao: float      # incerteza da estimativa, na unidade do indicador
    inclinacao: float       # variacao do indicador por min/km
    d0: str
    d1: str


def fit_hr_at_pace(
    paces: list[float], hrs: list[float], pace_ref: float
) -> tuple[float, float, float] | None:
    """Ajusta FC ~ pace e devolve (FC prevista em pace_ref, erro padrão, inclinação).

    O erro padrão da previsão é o que impede de comemorar 0,2 bpm de diferença
    entre dois blocos: com n=19 e resíduo de ~5 bpm, qualquer coisa abaixo de
    ~2 bpm é ruído."""
    if len(paces) < _MIN_CORRIDAS or len(set(paces)) < 2:
        return None
    try:
        inclinacao, intercepto = linear_regression(paces, hrs)
    except StatisticsError:
        return None

    previsto = intercepto + inclinacao * pace_ref
    residuos = [h - (intercepto + inclinacao * p) for p, h in zip(paces, hrs)]
    s = pstdev(residuos)

    media_pace = fmean(paces)
    sxx = sum((p - media_pace) ** 2 for p in paces)
    if sxx == 0:
        return None
    erro = s * ((1 / len(paces)) + ((pace_ref - media_pace) ** 2) / sxx) ** 0.5

    return round(previsto, 1), round(erro, 1), round(inclinacao, 1)


def _runs(conn, ate: str, desde: str | None = None) -> list[dict]:
    """Corridas contínuas do Garmin (longão, rodagem, tempo run etc.), uma por
    atividade, em ordem cronológica.

    Aqui o ponto é a atividade inteira -- diferente de `_interval_reps`, mas
    sem o mesmo problema de diluição: o Garmin já exclui tempo parado por
    auto-pause, então pace/FC/cadência/GCT/oscilação médios da atividade são
    tempo em corrida, não tempo parado. A diluição que existe é outra: pace e
    esforço variam dentro da própria corrida (largada mais forte, cansaço no
    fim), o que `aerobic_efficiency`/`continuous_mechanics` não veem -- só a
    média do dia inteiro. Campos ausentes ficam None, como em
    `_interval_reps`."""
    sql = ("SELECT date, duration_min / distance_km AS pace, avg_hr, avg_cadence, "
           "ground_contact_time, vertical_oscillation FROM activities "
           "WHERE type = 'running' AND source = 'garmin' AND distance_km > 1 "
           "AND duration_min IS NOT NULL AND date <= ?")
    args: list = [ate]
    if desde:
        sql += " AND date >= ?"
        args.append(desde)
    sql += " ORDER BY date"
    return [{
        "date": r["date"], "pace": float(r["pace"]), "hr": r["avg_hr"],
        "cadencia": r["avg_cadence"], "gct": r["ground_contact_time"],
        "oscilacao": r["vertical_oscillation"],
    } for r in conn.execute(sql, args).fetchall()]


def _interval_reps(conn, ate: str, desde: str | None = None) -> list[dict]:
    """Tiros (rep a rep) de treinos intervalados do Garmin, em ordem cronologica.

    Cada tiro e' o proprio segmento de corrida do treino intervalado -- nunca
    o aquecimento, a recuperacao entre tiros ou a caminhada, porque
    `splits.py` so grava em `reps` os segmentos de trabalho (ver
    `farol/pipeline/splits.py` e o README, secao `farol.pipeline.splits`). Pace, FC, cadencia,
    tempo de contato e oscilacao vertical aqui sao sempre tempo em corrida
    forte, nunca tempo parado ou andando.

    Cada tiro vira um ponto independente, ao contrario de `_runs` que usa a
    media da corrida inteira e dilui aquecimento/recuperacao junto com o
    esforco. Sessoes com 1 "rep" sao corridas continuas inteiras tratadas
    como tiro unico por `splits.py` -- nao sao intervalo real, e sao
    descartadas aqui (`len(reps) < 2`). Campos ausentes no tiro (dispositivo
    sem pod de dinamica de corrida, por exemplo) ficam None -- quem consome
    filtra pelo campo que precisa."""
    sql = ("SELECT date, splits FROM activities WHERE type = 'running' AND source = 'garmin' "
           "AND splits IS NOT NULL AND date <= ?")
    args: list = [ate]
    if desde:
        sql += " AND date >= ?"
        args.append(desde)
    sql += " ORDER BY date"
    saida = []
    for r in conn.execute(sql, args).fetchall():
        try:
            dados = json.loads(r["splits"])
        except (ValueError, TypeError):
            continue
        reps = dados.get("reps") if isinstance(dados, dict) else None
        if not reps or len(reps) < 2:
            continue
        for rep in reps:
            pace = rep.get("pace_min_km")
            if not pace:
                continue
            saida.append({
                "date": r["date"],
                "pace": float(pace),
                "hr": rep.get("avg_hr"),
                "cadencia": rep.get("avg_cadence"),
                "gct": rep.get("ground_contact_time"),
                "oscilacao": rep.get("vertical_oscillation"),
            })
    return saida


def _split_and_fit(pontos: list[tuple[str, float, float]],
                    pace_ref: float | None) -> list[Efficiency]:
    """Divide `pontos` (data, pace, valor) em duas metades cronologicas e
    ajusta valor ~ pace em cada uma, no mesmo pace_ref -- o nucleo comum a
    `aerobic_efficiency`, `interval_efficiency` e `interval_mechanics`.

    `pace_ref` sai da mediana dos paces quando nao e dado -- avaliar a reta
    num pace nunca corrido seria extrapolacao."""
    if len(pontos) < _MIN_CORRIDAS * 2:
        return []

    if pace_ref is None:
        pace_ref = round(median([p for _, p, _ in pontos]), 2)

    meio = len(pontos) // 2
    saida = []
    for rotulo, bloco in (("primeira metade", pontos[:meio]),
                          ("segunda metade", pontos[meio:])):
        ajuste = fit_hr_at_pace([p for _, p, _ in bloco], [v for _, _, v in bloco], pace_ref)
        if ajuste is None:
            continue
        previsto, erro, inclinacao = ajuste
        saida.append(Efficiency(
            rotulo=rotulo, n=len(bloco), pace_ref=pace_ref, valor_ref=previsto,
            erro_padrao=erro, inclinacao=inclinacao, d0=bloco[0][0], d1=bloco[-1][0],
        ))
    return saida


def interval_efficiency(conn, ate: str, pace_ref: float | None = None) -> list[Efficiency]:
    """Como `aerobic_efficiency`, mas tiro a tiro em vez de corrida inteira.

    Mais denso na faixa de esforco forte -- e' onde a pergunta "minha FC no
    mesmo pace forte esta caindo?" e' respondida com o dado certo, nao com a
    media de corridas que incluem aquecimento e recuperacao."""
    tiros = [(t["date"], t["pace"], t["hr"]) for t in _interval_reps(conn, ate) if t["hr"]]
    return _split_and_fit(tiros, pace_ref)


def interval_mechanics(conn, ate: str, campo: str, pace_ref: float | None = None) -> list[Efficiency]:
    """Como `interval_efficiency`, mas para um indicador mecanico em vez de FC:
    economia de corrida pelo lado biomecanico, ortogonal a economia cardiaca.

    `campo` e' uma chave de `_interval_reps`: 'cadencia', 'gct' ou
    'oscilacao'. Mesma base de dados (tiro a tiro, nunca recuperacao/caminhada)
    e mesmo criterio de significancia de `_split_and_fit`."""
    pontos = [(t["date"], t["pace"], t[campo]) for t in _interval_reps(conn, ate) if t[campo]]
    return _split_and_fit(pontos, pace_ref)


def continuous_mechanics(conn, ate: str, campo: str, pace_ref: float | None = None) -> list[Efficiency]:
    """Como `interval_mechanics`, mas para corrida contínua (longão, rodagem,
    tempo run) em vez de tiro de intervalado -- usa a atividade inteira como
    ponto, porque o Garmin já exclui tempo parado por auto-pause (ver
    `_runs`). `campo` é uma chave de `_runs`: 'cadencia', 'gct' ou
    'oscilacao'."""
    pontos = [(r["date"], r["pace"], r[campo]) for r in _runs(conn, ate) if r[campo]]
    return _split_and_fit(pontos, pace_ref)


def aerobic_efficiency(conn, ate: str, pace_ref: float | None = None) -> list[Efficiency]:
    """Compara a primeira metade do histórico de corrida com a segunda."""
    corridas = [(r["date"], r["pace"], r["hr"]) for r in _runs(conn, ate) if r["hr"]]
    return _split_and_fit(corridas, pace_ref)


def efficiency_verdict(blocos: list[Efficiency]) -> str | None:
    """Traduz a comparação de FC em uma frase, respeitando a incerteza.

    A diferença só é chamada de mudança quando supera a soma dos erros padrão
    dos dois blocos. FC mais baixa no mesmo pace = economia melhor."""
    if len(blocos) < 2:
        return None
    antes, depois = blocos[0], blocos[1]
    delta = depois.valor_ref - antes.valor_ref
    limiar = antes.erro_padrao + depois.erro_padrao
    if abs(delta) <= limiar:
        return (f"sem mudanca detectavel ({delta:+.1f} bpm, dentro da incerteza "
                f"de +/-{limiar:.1f})")
    if delta < 0:
        return f"economia melhorou: {abs(delta):.1f} bpm a menos no mesmo pace"
    return f"custo subiu: {delta:.1f} bpm a mais no mesmo pace"


def mechanical_verdict(blocos: list[Efficiency], nome: str, unidade: str,
                        menor_e_melhor: bool) -> str | None:
    """Como `efficiency_verdict`, mas para um indicador mecanico -- unidade e
    direcao de melhora variam por indicador, então não dá pra reaproveitar o
    texto fixo em bpm.

    `menor_e_melhor` segue a mesma direção já usada em
    `assess_deterioration_detected`: tempo de contato e oscilação vertical
    mais baixos são economia melhor; cadência mais baixa é o sinal de fadiga
    técnica que aquela função já trata como piora, então aqui é o inverso
    (`menor_e_melhor=False`).

    Fala em "sinal de", não em "piora"/"melhora" secas: a janela comparada
    aqui é só os últimos ~3 meses (fonte `garmin`), e pode coincidir com fase
    de acúmulo de carga (regra 3/6 do PROFILE.md -- resposta autonômica não
    se diagnostica de uma janela curta isolada, tendência de semanas importa
    mais). Um "sinal de fadiga técnica" pode ser fadiga temporária da carga
    atual, não perda permanente de economia -- só convergência com outros
    indicadores ao longo de mais tempo confirmaria isso."""
    if len(blocos) < 2:
        return None
    antes, depois = blocos[0], blocos[1]
    delta = depois.valor_ref - antes.valor_ref
    limiar = antes.erro_padrao + depois.erro_padrao
    if abs(delta) <= limiar:
        return (f"sem mudanca detectavel ({delta:+.1f} {unidade}, dentro da incerteza "
                f"de +/-{limiar:.1f})")
    piorou = (delta > 0) if menor_e_melhor else (delta < 0)
    verbo = "subiu" if delta > 0 else "caiu"
    sinal = "sinal de fadiga tecnica" if piorou else "sinal de economia melhorando"
    return f"{nome} {verbo} {abs(delta):.1f} {unidade} no mesmo pace -- {sinal}"


def pace_consistency(reps: list[dict]) -> tuple[float, str] | None:
    """Coeficiente de variacao do pace entre os tiros. Substitui o texto fixo
    que dizia 'Excelente' sem medir nada."""
    paces = [r["pace_min_km"] for r in reps if r.get("pace_min_km")]
    if len(paces) < 2:
        return None
    cv = pstdev(paces) / mean(paces) * 100
    if cv < 2:
        rotulo = "excelente"
    elif cv < 4:
        rotulo = "boa"
    else:
        rotulo = "irregular"
    return round(cv, 1), rotulo


def hr_drift(reps: list[dict]) -> int | None:
    """Diferenca de FC media entre o primeiro e o ultimo tiro. Subir muito no
    mesmo pace = custo cardiovascular crescente dentro da sessao."""
    hrs = [r["avg_hr"] for r in reps if r.get("avg_hr")]
    if len(hrs) < 2:
        return None
    return round(hrs[-1] - hrs[0])


def foster_monotony(cargas_diarias: list[float]) -> tuple[float, float] | None:
    """Monotonia e strain de Foster para uma semana.

    monotonia = media / desvio-padrao das cargas diarias
    strain    = carga total * monotonia

    Monotonia acima de 2,0 e o marcador classico de risco: treinar sempre na
    mesma dose, sem dias verdadeiramente faceis nem verdadeiramente dificeis.
    Dias de descanso contam como 0 e sao parte do calculo -- sao eles que criam
    a variacao que protege."""
    if len(cargas_diarias) < 3:
        return None
    sd = pstdev(cargas_diarias)
    if sd == 0:
        return None
    # Arredonda a monotonia ANTES de derivar o strain: os dois numeros aparecem
    # juntos no relatorio, e quem refizer a conta a partir do valor exibido tem
    # de chegar no mesmo strain.
    monotonia = round(fmean(cargas_diarias) / sd, 2)
    return monotonia, round(sum(cargas_diarias) * monotonia)


def weekly_load(conn, ate: str, semanas: int = 4) -> list[dict]:
    """Carga por semana encerrada em `ate`, da mais recente para a mais antiga."""
    fim = _date.fromisoformat(ate)
    saida = []
    for i in range(semanas):
        w1 = fim - timedelta(days=7 * i)
        w0 = w1 - timedelta(days=6)
        cargas = [float(r["t"]) for r in conn.execute(
            "SELECT COALESCE(trimp_day, 0) AS t FROM training_load "
            "WHERE date >= ? AND date <= ? ORDER BY date",
            (w0.isoformat(), w1.isoformat())).fetchall()]
        km = conn.execute(
            "SELECT COALESCE(SUM(distance_km), 0) AS km, COUNT(*) AS n FROM activities "
            "WHERE type IN ('running', 'treadmill_running') "
            "AND (source = 'garmin' OR "
            "(source = 'legado' AND historical_source = 'strava_export')) "
            "AND date >= ? AND date <= ?",
            (w0.isoformat(), w1.isoformat())).fetchone()

        foster = foster_monotony(cargas)
        saida.append({
            "inicio": w0.isoformat(),
            "fim": w1.isoformat(),
            "km": round(km["km"], 1),
            "sessoes": km["n"],
            "trimp": round(sum(cargas)),
            "monotonia": foster[0] if foster else None,
            "strain": foster[1] if foster else None,
        })
    return saida


# --------------------------------------------------- DOMINIO PERFORMANCE
# MODEL_V1.0.md, dominio 4: output_preserved e deterioration_detected.
# So se aplicam a dias com treino intervalado reconhecido (activities.splits
# com 'reps'), porque so ai existem os pontos de comparacao intra-sessao que
# o modelo pede (tiro a tiro) e um "pace do dia" comparavel entre sessoes.

_MIN_SESSOES_BASELINE = 3    # sessoes previas minimas p/ o baseline de 90d nao ser ruido
_JANELA_BASELINE_DIAS = 90
_LIMIAR_OUTPUT = 0.05        # 5% -- limiar do MODEL_V1.0.md p/ queda de output
_LIMIAR_HR_DRIFT_BPM = 5     # acima do ruido tipico de FC media do Garmin (~2-3 bpm)
_LIMIAR_CADENCIA_SPM = 2
_LIMIAR_GCT_MS = 5
_LIMIAR_OSCILACAO_CM = 0.3


def _work_session(conn, date: str) -> dict | None:
    """Resumo (farol.pipeline.splits.summarize_work_segments) do treino intervalado do
    dia, ou None se nenhuma atividade de corrida daquele dia tem essa
    estrutura. Sessoes com 1 rep sao corridas continuas inteiras tipadas por
    `farol.pipeline.splits` como tiro unico -- nao sao intervalo real e sao
    descartadas, mesma regra de `_interval_reps` (len(reps) >= 2). Pego de
    activities.splits -- gravado pelo ingest, nao pelo load a partir da
    planilha (ver README, secao 'Estrutura')."""
    for r in conn.execute(
        "SELECT splits FROM activities WHERE date = ? AND type = 'running' AND splits IS NOT NULL",
        (date,),
    ):
        try:
            dados = json.loads(r["splits"])
        except (ValueError, TypeError):
            continue
        reps = dados.get("reps") if isinstance(dados, dict) else None
        if reps and len(reps) >= 2:
            return dados
    return None


def _prior_interval_paces(conn, date: str, dias: int = _JANELA_BASELINE_DIAS) -> list[float]:
    """avg_pace_min_km dos treinos intervalados anteriores a `date`, dentro da
    janela de `dias`. Base de comparacao do output_preserved."""
    desde = (_date.fromisoformat(date) - timedelta(days=dias)).isoformat()
    paces = []
    for r in conn.execute(
        "SELECT splits FROM activities WHERE type = 'running' AND splits IS NOT NULL "
        "AND date >= ? AND date < ? ORDER BY date",
        (desde, date),
    ):
        try:
            dados = json.loads(r["splits"])
        except (ValueError, TypeError):
            continue
        reps = dados.get("reps") if isinstance(dados, dict) else None
        if (reps and len(reps) >= 2 and isinstance(dados, dict)
                and dados.get("avg_pace_min_km")):
            paces.append(dados["avg_pace_min_km"])
    return paces


def assess_output_preserved(conn, date: str) -> str | None:
    """'yes' se o pace medio do treino intervalado do dia esta dentro de 5%
    do padrao dos ultimos 90 dias, 'no' se cai mais que isso, 'indeterminate'
    sem baseline suficiente. None se o dia nao tem treino intervalado real
    (>= 2 reps; corrida continua tipada como 1 rep nao conta)."""
    sessao = _work_session(conn, date)
    if sessao is None or not sessao.get("avg_pace_min_km"):
        return None
    baseline = _prior_interval_paces(conn, date)
    if len(baseline) < _MIN_SESSOES_BASELINE:
        return "indeterminate"
    media = fmean(baseline)
    variacao = (sessao["avg_pace_min_km"] - media) / media  # positivo = mais lento
    return "no" if variacao > _LIMIAR_OUTPUT else "yes"


def assess_deterioration_detected(conn, date: str) -> str | None:
    """'yes' se algum sinal de degradacao tecnica aparece entre o primeiro e
    o ultimo tiro do treino intervalado do dia (deriva de FC, queda de
    cadencia, aumento de GCT/oscilacao vertical, ou pace irregular entre
    tiros). 'no' se nenhum sinal aparece. 'indeterminate' com so 1 tiro (sem
    o que comparar -- hoje inalcancavel, pois `_work_session` ja exige
    >= 2 reps; mantido como guarda defensiva). None se o dia nao tem treino
    intervalado real."""
    sessao = _work_session(conn, date)
    if sessao is None:
        return None
    reps = sessao["reps"]
    if len(reps) < 2:
        return "indeterminate"

    sinais: list[bool] = []

    drift = hr_drift(reps)
    if drift is not None:
        sinais.append(drift > _LIMIAR_HR_DRIFT_BPM)

    primeiro, ultimo = reps[0], reps[-1]
    if primeiro.get("avg_cadence") and ultimo.get("avg_cadence"):
        sinais.append(ultimo["avg_cadence"] < primeiro["avg_cadence"] - _LIMIAR_CADENCIA_SPM)
    if primeiro.get("ground_contact_time") and ultimo.get("ground_contact_time"):
        sinais.append(ultimo["ground_contact_time"] > primeiro["ground_contact_time"] + _LIMIAR_GCT_MS)
    if primeiro.get("vertical_oscillation") and ultimo.get("vertical_oscillation"):
        sinais.append(ultimo["vertical_oscillation"] > primeiro["vertical_oscillation"] + _LIMIAR_OSCILACAO_CM)

    consistencia = pace_consistency(reps)
    if consistencia is not None:
        sinais.append(consistencia[1] == "irregular")

    if not sinais:
        return "indeterminate"
    return "yes" if any(sinais) else "no"
