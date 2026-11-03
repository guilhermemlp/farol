"""Inteligencia longitudinal: primeira capacidade capaz de responder "ha
quanto tempo" em vez de so "como esta hoje".

farol.state classifica um dia isolado (NORMAL/BORDERLINE/ALERTA por dominio,
convergencia do dia). Isso nao distingue um dia ruim isolado de uma sequencia
de dias ruins -- exatamente a lacuna que farol_daily_state, sendo uma tabela
diaria, nao preenche sozinha. `streak` anda para tras a partir de uma data,
reaproveitando farol.state.gather() dia a dia (mesmos limiares, mesma
classificacao -- nada recalculado aqui), e mede havia quanto tempo o sinal
esta continuamente ativo.

`trend` (mais abaixo) e independente de `streak`: responde "esta piorando,
melhorando ou estavel", nao "ha quanto tempo". As duas coexistem -- duracao
do episodio e direcao da trajetoria sao perguntas diferentes.

Sem CLI proprio de proposito: e uma capacidade de biblioteca, consumida por
farol.state.gather() (streak/trend por dominio e por convergencia), que por
sua vez alimenta tanto DAILY_STATE.md quanto DAILY_ANALYSIS.md -- nao e um
relatorio novo.
"""
from datetime import date as _date
from datetime import timedelta

from . import state

_DOMINIOS = ("CARGA", "RECUPERACAO", "SONO")
_MAX_DIAS_PADRAO = 90  # mesma janela de baseline usada em farol.metrics/state


def domain_status_by_date(conn, on_date: str) -> dict[str, str]:
    """Status (ok/atencao/alerta/sem_dado) de CARGA/RECUPERACAO/SONO em
    `on_date`. Dict vazio se nao ha farol_daily_state para a data.

    Usa state.basic_status(), nao state.gather(): gather() agora inclui
    streak/trend (que sao construidos chamando esta funcao dia a dia) --
    passar por gather() aqui recriaria o ciclo que basic_status() existe
    para evitar."""
    try:
        return state.basic_status(conn, on_date)
    except ValueError:
        return {}


def streak(conn, on_date: str, dominio: str | None = None,
           max_dias: int = _MAX_DIAS_PADRAO) -> dict:
    """Dias consecutivos, terminando em `on_date`, com o sinal ativo.

    `dominio="CARGA"|"RECUPERACAO"|"SONO"`: ativo = status em
    (atencao, alerta) naquele dominio especifico.
    `dominio=None`: ativo = convergencia do dia (2+ dominios em
    atencao/alerta simultaneamente) -- mesma regra de farol.state.convergence.

    Anda dia a dia sem pular buracos: um dia sem farol_daily_state, ou com o
    dominio em `sem_dado`, interrompe a contagem -- persistencia nao pode ser
    afirmada sobre um dia sem evidencia. `max_dias` limita o quanto se anda
    para tras (nao ha necessidade de ir alem da propria janela de baseline).
    """
    if dominio is not None and dominio not in _DOMINIOS:
        raise ValueError(f"dominio invalido: {dominio!r} (esperado um de {_DOMINIOS} ou None)")

    d = _date.fromisoformat(on_date)
    dias = 0
    data_inicio = None
    for _ in range(max_dias):
        ds = d.isoformat()
        status_do_dia = domain_status_by_date(conn, ds)
        if not status_do_dia:
            break

        if dominio is not None:
            ativo = status_do_dia.get(dominio) in ("atencao", "alerta")
        else:
            ativo = len([s for s in status_do_dia.values() if s in ("atencao", "alerta")]) >= 2

        if not ativo:
            break

        dias += 1
        data_inicio = ds
        d -= timedelta(days=1)

    return {
        "dominio": dominio or "CONVERGENCIA",
        "dias": dias,
        "data_inicio": data_inicio,
        "data_fim": on_date if dias else None,
    }


def all_streaks(conn, on_date: str, max_dias: int = _MAX_DIAS_PADRAO) -> dict[str, dict]:
    """streak() para os 3 dominios e para a convergencia, num dicionario --
    conveniencia para quem quer o retrato completo de um dia."""
    return {
        **{nome: streak(conn, on_date, nome, max_dias) for nome in _DOMINIOS},
        "CONVERGENCIA": streak(conn, on_date, None, max_dias),
    }


# ------------------------------------------------------------------ trend
# `streak` responde "ha quantos dias isso esta acontecendo" (duracao).
# `trend` responde "isso esta piorando, melhorando ou estavel" (direcao).
# Sao independentes -- trend nao le nem depende de streak.

_DIRECAO = ("IMPROVING", "STABLE", "WORSENING")
_JANELA_TREND_PADRAO = 7

# Ordem de severidade reaproveitada de state._SEVERIDADE (ok < atencao <
# alerta) -- os mesmos tres niveis que farol.state ja classifica, nenhuma
# escala nova. "sem_dado" fica de fora de proposito: um dia sem evidencia nao
# e "nivel zero", e ausencia de ponto na serie (ver _severidade_dominio).
_SEVERIDADE_DOMINIO = {k: v for k, v in state._SEVERIDADE.items() if k != "sem_dado"}


def _severidade_dominio(status: str | None) -> int | None:
    """Mapeia o status ja calculado por farol.state para um inteiro
    ordenavel. None para 'sem_dado' (ou ausencia) -- o dia nao entra na
    serie, nao vira um "meio termo" artificial."""
    return _SEVERIDADE_DOMINIO.get(status)


def _severidade_convergencia(status_do_dia: dict[str, str]) -> int:
    """0/1: convergencia do dia (2+ dominios em atencao/alerta), mesma regra
    ja usada em streak() e farol.state.convergence(). Sempre definida quando
    o dia tem farol_daily_state -- nao ha 'sem_dado' de convergencia."""
    fora = sum(1 for s in status_do_dia.values() if s in ("atencao", "alerta"))
    return 1 if fora >= 2 else 0


def _serie(conn, on_date: str, dominio: str | None, window: int) -> list[tuple[str, int]]:
    """(data, severidade) validos nos ultimos `window` dias corridos
    terminando em `on_date`, em ordem cronologica. Um dia sem
    farol_daily_state, ou com o dominio 'sem_dado', e omitido -- nao
    preenchido, nao interpolado."""
    fim = _date.fromisoformat(on_date)
    pontos = []
    for i in range(window - 1, -1, -1):
        ds = (fim - timedelta(days=i)).isoformat()
        status_do_dia = domain_status_by_date(conn, ds)
        if not status_do_dia:
            continue
        sev = (_severidade_convergencia(status_do_dia) if dominio is None
               else _severidade_dominio(status_do_dia.get(dominio)))
        if sev is not None:
            pontos.append((ds, sev))
    return pontos


def trend(conn, on_date: str, dominio: str | None = None,
          window: int = _JANELA_TREND_PADRAO) -> dict:
    """Direcao do dominio (ou da convergencia, com `dominio=None`) nos
    ultimos `window` dias corridos terminando em `on_date`.

    Metodo: compara a severidade do primeiro e do ultimo dia **com dado**
    dentro da janela -- a menor propriedade matematica capaz de expressar
    direcao (e preciso de pelo menos 2 pontos para "subir" ou "descer"
    significar algo; um so ponto nao tem direcao). Isso e deliberado, nao uma
    simplificacao: uma janela que comeca e termina no mesmo nivel volta
    STABLE mesmo que tenha oscilado no meio (ex.: NORMAL...ALERTA...NORMAL) --
    oscilacao que se desfaz sozinha nao e tendencia, e forcar isso a
    IMPROVING ou WORSENING inventaria informacao que os dados nao sustentam.

    Com menos de 2 pontos validos (dados insuficientes, buracos, ou dominio
    'sem_dado' na janela inteira) devolve INSUFFICIENT_DATA -- nunca STABLE
    por falta de dado; a distincao entre "estavel" e "nao sei" e proposital.
    """
    if dominio is not None and dominio not in _DOMINIOS:
        raise ValueError(f"dominio invalido: {dominio!r} (esperado um de {_DOMINIOS} ou None)")
    if window < 1:
        raise ValueError("window precisa ser >= 1")

    pontos = _serie(conn, on_date, dominio, window)
    base = {"dominio": dominio or "CONVERGENCIA", "janela_dias": window, "n_pontos": len(pontos)}

    if len(pontos) < 2:
        return {**base, "direcao": "INSUFFICIENT_DATA", "data_inicio": None, "data_fim": None}

    sev_inicio, sev_fim = pontos[0][1], pontos[-1][1]
    if sev_fim > sev_inicio:
        direcao = "WORSENING"
    elif sev_fim < sev_inicio:
        direcao = "IMPROVING"
    else:
        direcao = "STABLE"

    return {**base, "direcao": direcao, "data_inicio": pontos[0][0], "data_fim": pontos[-1][0]}


def all_trends(conn, on_date: str, window: int = _JANELA_TREND_PADRAO) -> dict[str, dict]:
    """trend() para os 3 dominios e para a convergencia -- espelha
    all_streaks()."""
    return {
        **{nome: trend(conn, on_date, nome, window) for nome in _DOMINIOS},
        "CONVERGENCIA": trend(conn, on_date, None, window),
    }


# --------------------------------------------------------------- episodes
# `streak` mede a duracao vista de UMA data. `trend` mede a direcao numa
# janela. `episodes` responde "o que aconteceu" num INTERVALO -- pode conter
# varios episodios separados, cada um descrito compondo streak()+trend() no
# seu proprio dia final. Nenhuma regra de "ativo" e reescrita aqui: quem
# decide isso continua sendo streak() (chamado com max_dias=1 para checar um
# unico dia, sem custo de andar para tras).

_SEVERIDADE_DOMINIO_REVERSA = {v: k for k, v in _SEVERIDADE_DOMINIO.items()}


def _dia_ativo(conn, on_date: str, dominio: str | None) -> bool:
    """Um dia isolado conta como ativo? Delegado a streak() (max_dias=1) --
    nao reimplementa a regra de atencao/alerta/convergencia."""
    return streak(conn, on_date, dominio, max_dias=1)["dias"] > 0


def _resumo_intervalo(conn, inicio: str, fim: str, dominio: str | None) -> tuple:
    """Varre [inicio, fim] (que ja se sabe inteiramente ativo) uma vez para
    tirar severidade maxima e, no caso de convergencia, quais dominios
    contribuiram -- leitura pura do que farol.state ja classificou, sem
    inventar nivel novo."""
    d, fim_d = _date.fromisoformat(inicio), _date.fromisoformat(fim)
    severidade_maxima = None
    dominios_envolvidos: set[str] = set()
    while d <= fim_d:
        status_do_dia = domain_status_by_date(conn, d.isoformat())
        if dominio is not None:
            dominios_envolvidos.add(dominio)
            sev = _severidade_dominio(status_do_dia.get(dominio))
        else:
            fora = {n for n, s in status_do_dia.items() if s in ("atencao", "alerta")}
            dominios_envolvidos |= fora
            sev = len(fora) if len(fora) >= 2 else None
        if sev is not None:
            severidade_maxima = sev if severidade_maxima is None else max(severidade_maxima, sev)
        d += timedelta(days=1)

    severidade_label = (severidade_maxima if dominio is None
                         else _SEVERIDADE_DOMINIO_REVERSA.get(severidade_maxima))
    return severidade_label, sorted(dominios_envolvidos)


def _descreve_episodio(conn, fim: str, dominio: str | None, em_andamento: bool) -> dict:
    s = streak(conn, fim, dominio)  # duracao e inicio reais -- pode comecar antes da janela pedida
    t = trend(conn, fim, dominio, window=max(s["dias"], 1))  # trajetoria ao longo do proprio episodio
    severidade_maxima, dominios_envolvidos = _resumo_intervalo(conn, s["data_inicio"], fim, dominio)

    return {
        "dominio": dominio or "CONVERGENCIA",
        "data_inicio": s["data_inicio"],
        "data_fim": fim,
        "dias": s["dias"],
        "severidade_maxima": severidade_maxima,
        "em_andamento": em_andamento,
        "direcao": t["direcao"],
        "dominios_envolvidos": dominios_envolvidos,
    }


def episodes(conn, start_date: str, end_date: str, dominio: str | None = None) -> list[dict]:
    """Episodios (sequencias maximas de dias ativos) do dominio -- ou da
    convergencia, com `dominio=None` -- dentro de [start_date, end_date].

    Um episodio comeca no 1o dia ativo apos um dia nao-ativo (ou no inicio
    da janela) e termina no dia antes de um dia nao-ativo, `sem_dado`, ou
    sem registro -- zero tolerancia a interrupcao, um `NORMAL` no meio
    encerra o episodio (mesma regra de streak(), so aplicada ao longo de um
    intervalo em vez de olhando para tras de uma unica data). Sem piso de
    duracao: um episodio de 1 dia e devolvido como tal.

    `data_inicio` de um episodio pode ser anterior a `start_date` quando ele
    ja estava em curso ao entrar na janela -- streak() enxerga o passado real
    independente de onde a consulta comecou. `em_andamento=True` quando o
    episodio ainda esta ativo em `end_date` (pode continuar depois da janela
    consultada; isto nao e o mesmo que ele ter terminado)."""
    if dominio is not None and dominio not in _DOMINIOS:
        raise ValueError(f"dominio invalido: {dominio!r} (esperado um de {_DOMINIOS} ou None)")
    d0, d1 = _date.fromisoformat(start_date), _date.fromisoformat(end_date)
    if d1 < d0:
        raise ValueError("end_date precisa ser >= start_date")

    resultado = []
    d = d0
    while d <= d1:
        ds = d.isoformat()
        if _dia_ativo(conn, ds, dominio):
            e_ultimo_dia_da_janela = d == d1
            proximo_ativo = (not e_ultimo_dia_da_janela
                              and _dia_ativo(conn, (d + timedelta(days=1)).isoformat(), dominio))
            if not proximo_ativo:
                resultado.append(_descreve_episodio(conn, ds, dominio, e_ultimo_dia_da_janela))
        d += timedelta(days=1)
    return resultado


def all_episodes(conn, start_date: str, end_date: str) -> dict[str, list[dict]]:
    """episodes() para os 3 dominios e para a convergencia -- espelha
    all_streaks()/all_trends()."""
    return {
        **{nome: episodes(conn, start_date, end_date, nome) for nome in _DOMINIOS},
        "CONVERGENCIA": episodes(conn, start_date, end_date, None),
    }
