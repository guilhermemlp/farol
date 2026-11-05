"""CLI: dashboard HTML de 1 pagina com o estado do atleta, para abrir no
navegador em vez de ler texto no terminal.

Camada de VISUALIZACAO, nao de dominio: nao recalcula nada -- cada secao
chama a mesma funcao publica que o comando de terminal/markdown
correspondente ja usa (state.gather/interpretation.interpretar,
race_prediction.predict, analysis.weekly_load, recovery.recovery_index,
correlations.render_report, changepoint.render_report,
calibration_review.build_report). Onde um desses ja devolve markdown pronto
(correlations/changepoint), o HTML so reformata a tabela `| a | b |` que o
proprio modulo gerou -- nenhuma segunda logica de calculo mora aqui.

Gera um arquivo .html estatico: mesma filosofia do resto do farol ("sem
servidor, sem fila, sem multiplos bancos") -- abre com duplo clique, sem
precisar rodar nada além do comando abaixo.

Uso: python -m farol.reports.dashboard [--date YYYY-MM-DD] [--out CAMINHO]
Sem --date, usa hoje. Sem --out, grava em reports/DASHBOARD.html.
"""
import argparse
import html as _html
import json
from datetime import date as _date
from datetime import timedelta
from importlib import resources

from . import charts, report_sections, today
from ..core import db
from ..domain import daily_state_builder, interpretation, snapshot, state
from ..insights import calibration_auto, changepoint, correlations, race_prediction
from ..records import injuries, nutrition, race_log
from ..domain.analysis import weekly_load
from ..core.config import REPORTS_DIR, load_profile
from ..domain.hrv_variability import hrv_cv
from ..core.labels import ROTULOS, STATUS_LABEL
from ..domain.recovery import PESOS_PADRAO, recovery_index, recovery_label
from ..domain.sleep_regularity import bedtime_regularity
from ..domain.zones import heart_rate_zones

# Mesmo padrao de today._ROTULO (rotulos locais quando o significado e
# especifico deste relatorio) -- ver farol/core/labels.py sobre por que
# sleep_score nao mora no dicionario compartilhado.
_ROTULO = {**ROTULOS, "sleep_score": "Sono", "avg_sleep_stress": "Stress no sono",
           "total_sleep_minutes": "Duracao do sono"}

# Mesmos campos padrao de farol.insights.changepoint (farol/insights/changepoint.py,
# _CAMPOS_PADRAO), mais body_battery_min: achado da vistoria de 2026-09-04
# (auditoria manual apontou quebra de -1.66 SD em 2026-08-03, fora da lista
# curada original porque o campo e' volatil demais para o dominio
# RECUPERACAO -- ver farol/domain/state.py:38 -- mas isso nao o torna irrelevante
# para deteccao de quebra de MEDICAO, que e' uma pergunta diferente).
# Lista local, nao `changepoint._CAMPOS_PADRAO` importado, para nao herdar
# mudanca futura em silencio -- um diff aqui precisa ser uma decisao.
_CAMPOS_CHANGEPOINT = ["rhr", "hrv_avg", "sleep_score", "total_sleep_minutes",
                       "stress_avg", "night_hr_avg", "awakenings", "body_battery_min"]

_METRICAS_RECUPERACAO = ("hrv_avg", "rhr", "night_hr_avg", "respiration_avg",
                          "spo2_avg", "body_battery_min", "stress_avg")

# Janela do PMC: mesma ordem de grandeza do historico de carga do modelo, sem
# alongar a serie a ponto de misturar a procedencia legada (2026-06-10) na
# leitura visual. As linhas sao as do training_load, nao recalculadas aqui.
_JANELA_PMC_DIAS = 180


def _series(conn, column: str, on_date: str, table: str = "daily", days: int = 30) -> list[tuple[str, float]]:
    """Ultimos `days` valores nao-nulos de `column`, terminando em `on_date`
    (inclusive), com a data de cada ponto -- so para desenhar o mini-grafico,
    nao para estatistica (mesma logica de today._serie_recente, que nao
    devolve data e por isso nao serve aqui: o eixo X do grafico precisa
    dela)."""
    inicio = (_date.fromisoformat(on_date) - timedelta(days=days - 1)).isoformat()
    sql = (f"SELECT date, {column} AS v FROM {table} WHERE date >= ? AND date <= ? "
           f"AND {column} IS NOT NULL ORDER BY date")
    return [(r["date"], float(r["v"])) for r in conn.execute(sql, (inicio, on_date)).fetchall()]


def _performance_series(conn, profile: dict, on_date: str, days: int = 90) -> list[dict]:
    """Pontos de corrida para visualizacao, com trabalho tipado sempre ativo.

    `splits.reps` ja e' a saida curada de farol.pipeline.splits e contem somente
    INTERVAL_ACTIVE. Se nao houver repeticoes, o ponto e' explicitamente da
    sessao continua inteira. Caminhada/aquecimento/recuperacao nao sao
    convertidos em um falso ponto de desempenho de tiro.
    """
    inicio = (_date.fromisoformat(on_date) - timedelta(days=days - 1)).isoformat()
    races = {str(item.get("date"))[:10] for item in race_prediction.reference_races(profile)}
    rows = conn.execute(
        "SELECT activity_id, date, name, distance_km, duration_min, "
        "avg_pace_min_km, moving_minutes, avg_hr, splits "
        "FROM activities WHERE date >= ? AND date <= ? "
        "AND lower(type) IN ('running', 'treadmill_running') "
        "AND (source = 'garmin' OR historical_source = 'strava_export') "
        "AND distance_km > 0 AND duration_min > 0 ORDER BY date, activity_id",
        (inicio, on_date),
    ).fetchall()
    points = []
    for row in rows:
        pace = float(row["avg_pace_min_km"] or
                     (row["moving_minutes"] or row["duration_min"]) / row["distance_km"])
        hr = row["avg_hr"]
        scope = "sessao corrida"
        splits = None
        if row["splits"]:
            try:
                splits = json.loads(row["splits"])
            except (TypeError, json.JSONDecodeError):
                splits = None
        reps = (splits or {}).get("reps") if isinstance(splits, dict) else None
        active = [rep for rep in (reps or []) if rep.get("pace_min_km")]
        if active:
            # O resumo salvo por farol.pipeline.splits ja contem somente
            # INTERVAL_ACTIVE. Cada repeticao usa `pace_min_km`; a media do
            # bloco usa `avg_pace_min_km` (nomes distintos de proposito no
            # contrato de splits.py).
            pace = float(splits.get("avg_pace_min_km")) if splits.get("avg_pace_min_km") else (
                sum(float(rep["pace_min_km"]) for rep in active) / len(active))
            active_hr = [float(rep["avg_hr"]) for rep in active if rep.get("avg_hr") is not None]
            hr = sum(active_hr) / len(active_hr) if active_hr else hr
            scope = "segmentos ativos"
        points.append({
            "date": row["date"], "name": row["name"] or "Corrida", "pace": round(pace, 3),
            "hr": float(hr) if hr is not None else None, "distance_km": float(row["distance_km"]),
            "scope": scope, "race": row["date"] in races,
        })
    return points


# ------------------------------------------------------------------ contexto

def build_context(conn, profile: dict, on_date: str) -> dict:
    """Junta o que cada modulo ja calcula, sem reclassificar nada. Devolve um
    dict simples (primitivos/objetos Metric/dataclasses existentes) -- quem
    converte para HTML e' render_html()."""
    ctx: dict = {"on_date": on_date, "profile": profile}

    # Mesmo retrato do dia que farol.reports.today monta (farol/domain/snapshot.py) -- o
    # dashboard nao mantem uma segunda lista de campos/tabelas.
    metricas = snapshot.daily_metrics(conn, on_date)
    vo2max = snapshot.vo2max_metric(conn, on_date)

    pesos = ((profile.get("recovery_index") or {}).get("weights")) or PESOS_PADRAO
    indice, notas = recovery_index(metricas, pesos)

    ctx["metricas"] = metricas
    ctx["vo2max"] = vo2max
    ctx["indice"] = indice
    ctx["notas"] = notas
    ctx["pesos"] = pesos
    ctx["padroes"] = today.detect_patterns(metricas)
    ctx["doenca"] = today.signature_doenca(metricas)
    ctx["hrv_cv"] = hrv_cv(conn, on_date)
    ctx["regularidade_sono"] = bedtime_regularity(conn, on_date)

    hoje = _date.fromisoformat(on_date)
    ctx["prova"] = today.race_context(profile, hoje)

    daily_row = conn.execute("SELECT * FROM daily WHERE date = ?", (on_date,)).fetchone()
    ctx["daily"] = dict(daily_row) if daily_row else None
    tl_row = conn.execute("SELECT * FROM training_load WHERE date = ?", (on_date,)).fetchone()
    ctx["training_load"] = dict(tl_row) if tl_row else None
    ctx["atividades"] = [dict(a) for a in conn.execute(
        "SELECT * FROM activities WHERE date = ? ORDER BY activity_id", (on_date,)).fetchall()]
    ctx["faixas_fc"] = heart_rate_zones(profile)

    ctx["semanas"] = weekly_load(conn, on_date, semanas=8)

    # Uma serie curta por metrica que aparece com bullet/sparkline. Mesma
    # janela de 30 dias e mesma tabela de origem da metrica (tsb mora em
    # training_load). Isto e' eixo visual -- baseline continua vindo do Metric.
    campos_spark = (*_METRICAS_RECUPERACAO, "sleep_score", "total_sleep_minutes",
                    "avg_sleep_stress", "tsb")
    ctx["sparklines"] = {
        campo: _series(conn, campo, on_date,
                       table="training_load" if campo == "tsb" else "daily")
        for campo in campos_spark
    }

    try:
        dados_state = state.gather(conn, on_date)
        ctx["state"] = dados_state
        ctx["leitura"] = interpretation.interpretar(dados_state)
    except ValueError:
        ctx["state"] = None
        ctx["leitura"] = None

    ctx["race"] = _race_section(conn, profile, on_date)
    ctx["performance_series"] = _performance_series(conn, profile, on_date)
    ctx["injury"] = injuries.latest_observation(on_date)
    observacoes_lesao = injuries.observations_as_of(on_date)
    ctx["injury_history"] = observacoes_lesao[-7:]
    ctx["pmc"] = _pmc_block(conn, profile, on_date, observacoes_lesao)
    race_target = profile.get("race") or {}
    race_date = str(race_target.get("date") or "")[:10]
    # Relatos da prova sao anexos manuais: entram apenas depois da data da
    # propria prova, para que um dashboard historico nao veja o futuro.
    ctx["race_log"] = (race_log.entry_as_of(race_date, on_date)
                       if race_date else None)
    ctx["nutrition_config"] = nutrition.config_from_profile(profile)
    # `ate=on_date`: um dashboard historico so pode mostrar correlacao/
    # quebra calculada com dado disponivel ate aquela data -- achado da
    # auditoria de 2026-09-08 (correlations/changepoint antes rodavam
    # sempre sobre MAX(date) real, mesmo num dashboard de data passada).
    ctx["correlations_md"] = correlations.render_report(conn, ate=on_date)
    ctx["changepoint_md"] = changepoint.render_report(conn, _CAMPOS_CHANGEPOINT, ate=on_date)
    ctx["calibration_md"] = _calibration_text(conn, profile, on_date)

    return ctx


def _pmc_block(conn, profile: dict, on_date: str, observacoes: list[dict]) -> dict:
    """Serie de carga de 180 dias + anotacoes de contexto, sem recalcular nada.

    `training_load` ja' guarda ctl/atl/tsb por dia -- o grafico le exatamente
    isso. As anotacoes sao fatos do proprio perfil/registro: provas declaradas
    ate on_date, primeira observacao de cada area de lesao na janela e as
    quebras de medicao conhecidas (daily_state_builder). Nada posterior a
    on_date entra: dashboard historico nao ve o futuro.
    """
    inicio = (_date.fromisoformat(on_date) - timedelta(days=_JANELA_PMC_DIAS - 1)).isoformat()
    series = [dict(linha) for linha in conn.execute(
        "SELECT date, ctl, atl, tsb FROM training_load "
        "WHERE date >= ? AND date <= ? ORDER BY date",
        (inicio, on_date)).fetchall()]

    anotacoes = []
    declaradas = list(race_prediction.reference_races(profile))
    if (profile.get("race") or {}).get("date"):
        declaradas.append(profile["race"])
    for prova in declaradas:
        data = str(prova.get("date") or "")[:10]
        if inicio <= data <= on_date:
            anotacoes.append({"date": data, "kind": "prova",
                              "label": str(prova.get("name") or "prova")})

    # Lesao: uma marca por area (a primeira observacao na janela), nao uma por
    # dia de acompanhamento -- o registro e' diario, a lesao e' o episodio.
    primeira_por_area: dict[str, str] = {}
    for observacao in observacoes:
        data = str(observacao.get("date") or "")[:10]
        if not (inicio <= data <= on_date):
            continue
        area = str(observacao.get("area") or "area nao informada")
        anterior = primeira_por_area.get(area)
        if anterior is None or data < anterior:
            primeira_por_area[area] = data
    for area, data in primeira_por_area.items():
        anotacoes.append({"date": data, "kind": "lesao", "label": f"lesao: {area}"})

    for quebra in daily_state_builder.measurement_breakpoints():
        data = str(quebra.get("detected_on") or "")[:10]
        if inicio <= data <= on_date:
            anotacoes.append({"date": data, "kind": "quebra",
                              "label": "quebra de medicao"})

    # Duas quebras no mesmo dia (awakenings + sleep_score) viram uma linha so';
    # prova e lesao no mesmo dia continuam distintas (tipos diferentes).
    vistas = set()
    unicas = []
    for anotacao in sorted(anotacoes, key=lambda a: (a["date"], a["kind"], a["label"])):
        chave = (anotacao["date"], anotacao["kind"])
        if chave in vistas:
            continue
        vistas.add(chave)
        unicas.append(anotacao)

    return {"inicio": inicio, "fim": on_date, "series": series, "anotacoes": unicas}


def _race_section(conn, profile: dict, on_date: str) -> dict | None:
    race = profile.get("race") or {}
    alvo_km = race.get("distance_km")
    if not alvo_km:
        return None
    refs = race_prediction.reference_races(profile)
    provas = race_prediction.observed_performances(conn, [r["date"] for r in refs])
    if not provas:
        return {"race": race, "provas": [], "predicao": None}
    # Ancora nunca pode ser uma prova posterior a on_date -- um dashboard
    # historico nao pode usar conhecimento que so existiria no futuro
    # daquela data (achado da auditoria de 2026-09-08: dashboard de 01/07
    # escolhia a corrida de 16/08 como ancora). Sem prova valida ate
    # on_date, mesma condicao ja usada acima para "sem prova nenhuma" --
    # nenhuma ancora e' inventada.
    provas_ate_on_date = [p for p in provas if p["date"] <= on_date]
    if not provas_ate_on_date:
        return {"race": race, "provas": [], "predicao": None}
    ancora = max(provas_ate_on_date, key=lambda p: p["date"])
    predicao = race_prediction.predict(ancora["distance_km"], ancora["duration_min"], alvo_km)
    vdot = race_prediction.vdot_from_time(ancora["distance_km"], ancora["duration_min"])
    tsb = race_prediction.tsb_mais_recente(conn, on_date)
    return {"race": race, "provas": provas, "ancora": ancora, "predicao": predicao,
            "vdot": vdot, "tsb": tsb, "alvo_km": alvo_km}


def _calibration_text(conn, profile: dict, on_date: str) -> str:
    """Calibracao objetiva, gerada do banco ate a data vista no painel."""
    return calibration_auto.build_report(conn, profile, on_date)


# -------------------------------------------------------------- formatacao html

def _esc(v) -> str:
    return _html.escape("" if v is None else str(v))


def _md_table_html(markdown: str, css_class: str = "data mono") -> str:
    """Uma tabela `| a | b |` de um relatorio markdown (correlations/
    changepoint, que ja geram essa tabela para o terminal) -> HTML. So
    reformata texto que o proprio modulo ja produziu -- nenhum dado novo."""
    linhas = [ln for ln in markdown.splitlines() if ln.strip().startswith("|")]
    if len(linhas) < 2:
        return "<p>Sem dado suficiente.</p>"

    def _cols(ln: str) -> list[str]:
        return [c.strip() for c in ln.strip().strip("|").split("|")]

    header = _cols(linhas[0])
    corpo = [_cols(ln) for ln in linhas[2:]]
    partes = [f'<div class="table-scroll"><table class="{css_class}"><thead><tr>']
    partes += [f"<th>{_esc(h)}</th>" for h in header]
    partes.append("</tr></thead><tbody>")
    for row in corpo:
        partes.append("<tr>")
        for i, c in enumerate(row):
            cls = ' class="num"' if i > 0 else ""
            partes.append(f"<td{cls}>{_esc(c)}</td>")
        partes.append("</tr>")
    partes.append("</tbody></table></div>")
    return "".join(partes)


def _metric_row_html(campo: str, m, unidade: str = "", serie=None) -> str:
    rotulo = _ROTULO.get(campo, campo)
    if not m.has_data:
        return (f'<div class="metric-row"><div class="label">{_esc(rotulo)}</div>'
                f'<div class="metric-bullet"></div><div class="val">sem dado</div>'
                f'<div class="ctx"></div><div class="metric-spark"></div></div>')
    baseline = f"normal {m.mean_90d:.1f}{unidade}" if m.mean_90d is not None else "sem baseline"
    z_txt = f"z {m.z:+.2f}" if m.z is not None else "z —"
    dot = "dot-warn" if m.status in ("atencao", "alerta") else "dot-ok"
    # Bullet: faixa normal (+/- 1 sd) e marcador de hoje -- magnitude e
    # posicao na propria variacao, nao uma barra de z a partir do centro.
    bullet = charts.bullet_svg(m.value, m.mean_90d, m.sd_90d, status=m.status,
                               title=rotulo, unit=unidade)
    spark = charts.sparkline_svg(serie or [], baseline=m.mean_90d,
                                 title=f"{rotulo} nos ultimos 30 dias", unit=unidade)
    return (
        f'<div class="metric-row"><div class="label"><span class="dot {dot}"></span>{_esc(rotulo)}</div>'
        f'<div class="metric-bullet">{bullet}</div>'
        f'<div class="val">{m.value:g}{_esc(unidade)}</div>'
        f'<div class="ctx">{_esc(baseline)} &middot; {_esc(z_txt)}</div>'
        f'<div class="metric-spark">{spark}</div></div>'
    )


_PILL_STATUS = {"ok": "pill-ok", "atencao": "pill-warn",
                "alerta": "pill-crit", "sem_dado": "pill-warn"}

# Confianca, streak e tendencia ja' vem calculados por state.gather()
# (domain_status + longitudinal) -- o dashboard so' traduz o rotulo. Nenhuma
# reclassificacao e nenhum limiar novo aqui.
_CONFIANCA_LABEL = {"alta": "confianca alta", "media": "confianca media",
                    "baixa": "confianca baixa"}
_TREND_LABEL = {
    "IMPROVING": ("\u2197 melhorando", "trend-melhora"),
    "WORSENING": ("\u2198 piorando", "trend-piora"),
    "STABLE": ("\u2192 estavel", "trend-estavel"),
    "INSUFFICIENT_DATA": ("sem base 7d", "trend-sem-base"),
}


def _streak_texto(streak: dict | None) -> str:
    dias = (streak or {}).get("dias") or 0
    if not dias:
        return "sem streak ativo"
    inicio = (streak or {}).get("data_inicio")
    return f"{dias} dia(s) de streak" + (f" desde {inicio}" if inicio else "")


def _idade(birth_date: str | None, on_date: str) -> int | None:
    if not birth_date:
        return None
    b = _date.fromisoformat(str(birth_date)[:10])
    d = _date.fromisoformat(on_date)
    return d.year - b.year - ((d.month, d.day) < (b.month, b.day))


# ------------------------------------------------------------------- template

def _head_html() -> str:
    """Cabecalho da pagina: CSS vem de assets/css (fonte unica) e e' embutido
    no HTML final. Nem `<link>` relativo -- que quebraria quando o arquivo e'
    aberto de reports/ -- nem CDN: o dashboard abre e imprime sem rede."""
    css = (resources.files(__package__) / "assets" / "css" / "dashboard.css"
           ).read_text(encoding="utf-8")
    return f"""<!doctype html>
<title>Farol</title>
<style>
{css}</style>
"""


_SCRIPT_RUNTIME = """
function farolNutrition(defaults){
  var ids=['nut-distance','nut-duration','nut-start','nut-carbs','nut-interval','nut-caffeine','nut-pre','nut-cutoff','nut-coffee']; var get=function(id){return document.getElementById(id);};
  function draw(){var duration=Number(get('nut-duration').value)||1, carbs=Number(get('nut-carbs').value)||0, interval=Math.max(1,Number(get('nut-interval').value)||30), pre=Math.max(0,Number(get('nut-pre').value)||0), cutoff=Math.max(0,Number(get('nut-cutoff').value)||0), cafeAt=Math.max(0,Number(get('nut-caffeine').value)||0), start=get('nut-start').value, events=[[-pre,'Gel regular',carbs,0]], used=false; for(var minute=interval;minute<=duration-cutoff;minute+=interval){var cafe=!used&&minute>=cafeAt;events.push([minute,cafe?'Ultra Gel':'Gel regular',carbs,cafe?defaults.gel_caffeine_mg:0]);used=used||cafe;} var total=events.reduce(function(s,e){return s+e[2];},0), caffeine=events.reduce(function(s,e){return s+e[3];},0), base=start?new Date('2000-01-01T'+start+':00'):null; get('nutrition-summary').textContent=events.length+' gel(is) · '+total.toFixed(0)+' g carboidrato · '+(total/duration*60).toFixed(1)+' g/h · '+caffeine.toFixed(0)+' mg cafeina'; get('nutrition-body').innerHTML=events.map(function(e){var clock='—';if(base){var d=new Date(base.getTime()+e[0]*60000);clock=String(d.getHours()).padStart(2,'0')+':'+String(d.getMinutes()).padStart(2,'0');}return '<tr><td>'+ (e[0]<0?Math.abs(e[0])+' min antes':e[0]+' min')+'</td><td>'+clock+'</td><td>'+e[1]+'</td><td class="num">'+e[2].toFixed(0)+' g</td><td class="num">'+e[3].toFixed(0)+' mg</td><td>agua junto</td></tr>';}).join(''); get('nutrition-coffee').textContent=get('nut-coffee').checked?'Cafe opcional antes: apenas se ja e habitual e tolerado.':'Sem cafe no planejamento.';}
  ids.forEach(function(id){var e=get(id);if(e)e.addEventListener('input',draw);if(e)e.addEventListener('change',draw);});draw();
}
"""


def _prova_futura(ctx: dict) -> bool:
    """A prova declarada ainda esta' por vir? Mesma regra do farol.reports.today:
    depois da data, contexto/previsao/nutricao daquela prova nao aparecem mais
    como se fossem alvo -- o dashboard aguarda a proxima ser declarada."""
    prova = ctx.get("prova")
    return bool(prova) and prova["dias"] >= 0


def _hero_html(ctx: dict) -> str:
    profile = ctx["profile"]
    athlete = profile.get("athlete") or {}
    idade = _idade(athlete.get("birth_date"), ctx["on_date"])
    prova = ctx.get("prova")
    if prova and prova["dias"] >= 0:
        prova_em = f"{prova['dias']} dias"
    elif prova:
        prova_em = "pos-prova"
    else:
        prova_em = "—"
    nome = athlete.get("name") or "atleta"
    return f"""
<header class="hero"><div class="wrap">
  <div class="eyebrow">Farol</div>
  <h1 class="title">Estado do atleta</h1>
  <p class="subtitle">Recuperacao, carga de treino e previsao de prova de {_esc(nome)}, a partir dos seus dados do Garmin -- comparado sempre contra o seu proprio padrao, nunca uma populacao generica.</p>
  <div class="hero-meta">
    <div><div class="k">Atleta</div><div class="v" style="font-size:14px;font-family:var(--font-sans);font-weight:600;">{_esc(nome)}</div></div>
    <div><div class="k">Data</div><div class="v">{_esc(ctx['on_date'])}</div></div>
    <div><div class="k">Idade</div><div class="v">{_esc(idade) if idade is not None else '—'}</div></div>
    <div><div class="k">Prova em</div><div class="v">{_esc(prova_em)}</div></div>
  </div>
</div></header>
"""


def _resumo_html(ctx: dict) -> str:
    indice = ctx["indice"]
    if indice is not None:
        # Contribuintes do indice: as notas por campo que recovery_index()
        # ja' calculou (mesmo contrato do terminal, _secao_recuperacao).
        # Mostra o que puxou o indice para cima/baixo, sem recalcular nada.
        chips = []
        for chave, nota in ctx["notas"].items():
            rotulo = report_sections.ROTULOS_RELATORIO.get(chave, chave)
            peso = ctx["pesos"].get(chave, 0)
            valor = f"{nota:.1f}" if nota is not None else "—"
            chips.append(f'<span title="peso {peso:.0%}"><b>{_esc(rotulo)}</b> {valor}</span>')
        contrib_html = f'<div class="contrib">{"".join(chips)}</div>' if chips else ""
        tile_recuperacao = (f'<div class="tile"><div class="k">Indice de recuperacao</div>'
                             f'<div class="v">{indice:g}<span class="u">/10</span></div>'
                             f'<div class="sub">{_esc(recovery_label(indice))}</div>'
                             f'{contrib_html}</div>')
    else:
        tile_recuperacao = ('<div class="tile"><div class="k">Indice de recuperacao</div>'
                             '<div class="v">—</div><div class="sub">sem dado suficiente</div></div>')

    race = ctx["race"]
    if race and race.get("predicao") and _prova_futura(ctx):
        p = race["predicao"]
        tile_prova = (f'<div class="tile"><div class="k">{_esc(race["race"].get("name", "Prova"))}</div>'
                      f'<div class="v" style="font-size:22px;">{race_prediction._fmt_tempo(p["min"])}'
                      f'&ndash;{race_prediction._fmt_tempo(p["max"])}</div>'
                      f'<div class="sub">Riegel/Cameron/VDOT sobre {_esc(race["ancora"]["date"])}</div></div>')
    elif ctx.get("prova"):
        # Prova ja realizada: nao reutilizar a anterior como alvo.
        tile_prova = ('<div class="tile"><div class="k">Prova</div>'
                      '<div class="v">—</div>'
                      '<div class="sub">nenhuma futura declarada em profile.yaml</div></div>')
    else:
        tile_prova = ('<div class="tile"><div class="k">Previsao de prova</div>'
                      '<div class="v">—</div><div class="sub">sem prova observada declarada</div></div>')

    tsb_m = ctx["metricas"].get("tsb")
    tsb_sub = ("alvo conceitual p/ prova: positivo" if _prova_futura(ctx)
               else "saldo entre carga aguda e cronica")
    if tsb_m and tsb_m.has_data:
        spark_tsb = charts.sparkline_svg(
            ctx["sparklines"].get("tsb") or [], baseline=tsb_m.mean_90d,
            title="TSB nos ultimos 30 dias", height=44, digits=1)
        tile_tsb = (f'<div class="tile"><div class="k">TSB hoje</div>'
                    f'<div class="v">{tsb_m.value:+.1f}</div>'
                    f'<div class="tile-spark">{spark_tsb}</div>'
                    f'<div class="sub">{tsb_sub}</div></div>')
    else:
        tile_tsb = ('<div class="tile"><div class="k">TSB hoje</div><div class="v">—</div>'
                    '<div class="sub">sem dado</div></div>')

    atividades = ctx["atividades"]
    if atividades:
        primeira = atividades[0]
        km = f"{primeira['distance_km']:.2f} km" if primeira.get("distance_km") else ""
        # Sem distancia (pilates, forca), o numero principal e' a duracao --
        # repetir o tipo aqui e' duplicar o que o resumo abaixo ja' diz.
        duracao = (f"{primeira['duration_min']:.0f} min"
                   if primeira.get("duration_min") else _esc(primeira.get("type") or "atividade"))
        tile_treino = (f'<div class="tile"><div class="k">Treino de hoje</div>'
                       f'<div class="v" style="font-size:19px;">{_esc(km) or _esc(duracao)}</div>'
                       f'<div class="sub">{_esc(report_sections.resumo_atividades(atividades))}</div></div>')
    else:
        tile_treino = ('<div class="tile"><div class="k">Treino de hoje</div>'
                       '<div class="v" style="font-size:19px;">—</div><div class="sub">nenhum registrado</div></div>')

    domains_html = ""
    leitura_html = ""
    if ctx["state"]:
        blocos = []
        for nome, d in ctx["state"]["dominios"].items():
            rotulo, emoji = STATUS_LABEL[d["status"]]
            cls = _PILL_STATUS[d["status"]]
            confianca = _CONFIANCA_LABEL.get(d.get("confianca"), "confianca —")
            direcao = (d.get("trend") or {}).get("direcao") or "INSUFFICIENT_DATA"
            tendencia, trend_cls = _TREND_LABEL.get(direcao, _TREND_LABEL["INSUFFICIENT_DATA"])
            evidencias = d.get("evidencias") or []
            detalhe = (f"{len(evidencias)} campos; "
                       f"{len(d.get('ausentes') or [])} sem dado")
            blocos.append(
                f'<div class="domain">'
                f'<div class="domain-id"><div class="name">{nome.capitalize()}</div>'
                f'<div class="streak">{_esc(_streak_texto(d.get("streak")))}</div></div>'
                f'<div class="domain-right"><span class="pill {cls}">{emoji} {rotulo}</span>'
                f'<div class="meta" title="{_esc(detalhe)}">'
                f'<span class="conf">{_esc(confianca)}</span>'
                f'<span class="trend {trend_cls}">{_esc(tendencia)}</span></div></div>'
                f'</div>'
            )
        domains_html = f'<div class="domains">{"".join(blocos)}</div>'

    if ctx["leitura"]:
        leitura = ctx["leitura"]
        leitura_html = f"""
    <div class="card" style="margin-top:16px;">
      <blockquote class="read">&ldquo;{_esc(leitura['estado_fisiologico'])}. Sinal dominante: {_esc(leitura['sinal_dominante'])}.
      Convergencia: {_esc(leitura['convergencia'])}. {_esc(leitura['conclusao'])}.&rdquo;</blockquote>
      <div style="font-size:11.5px;color:var(--ink-dim);margin-top:8px;">&mdash; farol.domain.interpretation, sobre a classificacao de state.gather(). Descreve; nao prescreve treino.</div>
    </div>"""

    # Mesmo alerta principal do RESUMO RAPIDO do terminal: doenca ou o
    # primeiro padrao combinado. Nao e' diagnostico -- so' destaca o que
    # merece leitura.
    alerta = ctx.get("doenca") or ((ctx.get("padroes") or [None])[0])
    if alerta:
        alerta_html = (f'<div class="callout alert-manual"><span class="tag tag-warn">Atencao</span>'
                       f'<p style="margin:6px 0 0;">{_esc(alerta)}</p></div>')
    else:
        alerta_html = ""

    # Ordem alinhada ao terminal: visao do dia, alerta, LEITURA, dominios.
    return f"""
<section>
  <div class="section-head"><h2>Resumo rapido</h2><div class="section-note">Mesma hierarquia do relatorio de terminal: indice, treino, alerta, leitura e dominios.</div></div>
  <div class="tiles">{tile_recuperacao}{tile_treino}{tile_tsb}{tile_prova}</div>
  {alerta_html}
  {leitura_html}
  {domains_html}
</section>
"""


def _prontidao_html(ctx: dict) -> str:
    race = ctx["race"]
    # Prova ja realizada nao tem "dias restantes" nem prontidao a estimar.
    if not race or not _prova_futura(ctx):
        return ""
    prova = race["race"]
    try:
        dias = (_date.fromisoformat(str(prova.get("date"))[:10]) - _date.fromisoformat(ctx["on_date"])).days
    except (TypeError, ValueError):
        dias = None
    indice = f"{ctx['indice']:g}/10" if ctx["indice"] is not None else "—"
    tsb = race.get("tsb")
    tsb_text = f"{tsb['tsb']:+.1f}" if tsb else "—"
    p = race.get("predicao")
    anchor = race.get("ancora")
    anchor_time = race_prediction._fmt_tempo(anchor["duration_min"]) if anchor else "—"
    anchor_detail = (f"{_esc(anchor['date'])} · {_esc(race_prediction._fmt_pace(anchor['duration_min'] / anchor['distance_km']))}"
                     if anchor else "sem prova observada")
    forecast = (f"{race_prediction._fmt_tempo(p['min'])}&ndash;{race_prediction._fmt_tempo(p['max'])}"
                if p else "—")
    return f"""
<section>
  <div class="section-head"><h2>Prontidao para a prova</h2><div class="section-note">Painel descritivo: estado atual e estimativa; nao e uma liberacao medica nem prescricao.</div></div>
  <div class="readiness">
    <div><div class="label">Dias restantes</div><div class="value">{_esc(dias) if dias is not None else '—'}</div><div class="detail">{_esc(prova.get('date') or 'data nao definida')}</div></div>
    <div><div class="label">TSB atual</div><div class="value">{_esc(tsb_text)}</div><div class="detail">{_esc(tsb['date']) if tsb else 'sem dado'}</div></div>
    <div><div class="label">Recuperacao</div><div class="value">{_esc(indice)}</div><div class="detail">indice Farol de hoje</div></div>
    <div><div class="label">Ultima prova 10 km</div><div class="value">{anchor_time}</div><div class="detail">{anchor_detail}</div></div>
    <div><div class="label">Faixa 21,1 km</div><div class="value">{forecast}</div><div class="detail">Riegel / Cameron / VDOT</div></div>
  </div>
</section>
"""


def _prediction_pace(ctx: dict) -> dict | None:
    """Faixa prevista convertida para pace (min/km), so' para o eixo do grafico."""
    race = ctx.get("race") or {}
    predicao = race.get("predicao")
    distancia = race.get("alvo_km")
    if not predicao or not distancia:
        return None
    return {"min": predicao["min"] / distancia, "max": predicao["max"] / distancia}


def _desempenho_html(ctx: dict) -> str:
    points = ctx["performance_series"]
    if not points:
        return ""
    pace_svg = charts.pace_trend_svg(points, _prediction_pace(ctx))
    hr_svg = charts.pace_hr_svg(points)
    return f"""
<section>
  <div class="section-head"><h2>Desempenho recente</h2><div class="section-note">90 dias de corridas; cada ponto traz o resumo da sessao no title (tooltip nativo, sem JavaScript).</div></div>
  <div class="performance-grid">
    <div class="card"><div class="form-label">Pace ao longo das sessoes</div>{pace_svg}</div>
    <div class="card"><div class="form-label">Pace x frequencia cardiaca</div>{hr_svg}</div>
  </div>
  <div class="legend"><span><i style="background:var(--accent)"></i>sessao corrida</span><span><i style="background:var(--ok)"></i>segmentos ativos Garmin</span><span><i style="background:var(--warn)"></i>prova declarada</span><span>linha tracejada: tendencia linear</span></div>
  <div class="callout" style="margin-top:14px;"><span class="tag tag-info">Regra explicita</span><h3>Caminhadas e recuperacoes nao entram na analise dos tiros.</h3><p>Quando existem splits tipados, o ponto usa exclusivamente os segmentos <code class="mono">INTERVAL_ACTIVE</code> que o Garmin marcou como trabalho. Aquecimento, recuperacao e caminhada ficam fora. Sem splits tipados, o tooltip identifica o valor como sessao corrida.</p></div>
</section>
"""


def _nutricao_html(ctx: dict) -> str:
    # A agenda e' da proxima prova: com a prova declarada ja realizada, nao
    # reciclar a anterior (mesma regra do RESUMO do terminal).
    if ctx.get("race") and not _prova_futura(ctx):
        return ""
    config = ctx["nutrition_config"]
    race = ctx["race"] or {}
    prediction = race.get("predicao")
    default_duration = int(round((prediction["min"] + prediction["max"]) / 2)) if prediction else 120
    race_data = race.get("race") or {}
    distance = race.get("alvo_km") or race_data.get("distance_km") or 21.1
    start = race_data.get("start_time") or ""
    coffee = "checked" if config["coffee_before_race"] else ""
    return f"""
<section>
  <div class="section-head"><h2>Plano de nutricao da prova</h2><div class="section-note">Editor por tempo, adaptavel para qualquer quilometragem. Ajuste apenas produtos e estrategia ja tolerados.</div></div>
  <div class="card">
    <div class="form-grid">
      <label>Distancia (km)<input id="nut-distance" type="number" min="1" step="0.1" value="{_esc(distance)}"></label>
      <label>Tempo estimado (min)<input id="nut-duration" type="number" min="1" step="1" value="{default_duration}"></label>
      <label>Largada<input id="nut-start" type="time" value="{_esc(start)}"></label>
      <label>Carboidrato / gel (g)<input id="nut-carbs" type="number" min="0" step="1" value="{config['gel_carbohydrate_g']:g}"></label>
      <label>Intervalo (min)<input id="nut-interval" type="number" min="1" step="1" value="{config['interval_minutes']}"></label>
      <label>Ultra Gel a partir de (min)<input id="nut-caffeine" type="number" min="0" step="1" value="{config['caffeine_at_minutes']}"></label>
      <label>Gel pre-largada (min)<input id="nut-pre" type="number" min="0" step="1" value="{config['pre_start_minutes']}"></label>
      <label>Parar géis faltando (min)<input id="nut-cutoff" type="number" min="0" step="1" value="{config['last_gel_cutoff_minutes']}"></label>
      <label style="align-content:end;grid-template-columns:auto 1fr;align-items:center;"><input id="nut-coffee" type="checkbox" {coffee} style="width:auto;">Cafe antes da prova</label>
    </div>
    <div id="nutrition-summary" class="nutrition-summary"></div>
    <div class="table-scroll"><table class="data mono"><thead><tr><th>momento</th><th>horario</th><th>produto</th><th class="num">carbo.</th><th class="num">cafeina</th><th>lembrete</th></tr></thead><tbody id="nutrition-body"></tbody></table></div>
    <p id="nutrition-coffee" class="notice"></p>
    <p class="notice">Todo gel esta acompanhado do lembrete de agua. O painel contabiliza o gel pre-largada e troca apenas um gel regular pelo Ultra Gel; cafe nao recebe estimativa de cafeina porque a dose depende da xicara/produto. Isto organiza ingestao, nao substitui orientacao clinica ou teste de tolerancia.</p>
  </div>
</section>
"""


def _lesoes_html(ctx: dict) -> str:
    entry = ctx["injury"]
    alert = injuries.observation_alert(entry)
    history = ctx["injury_history"]
    shoes = [item.get("name") for item in (ctx["profile"].get("shoes") or []) if item.get("name")]
    latest = injuries.observation_text(entry)
    alert_html = (f'<div class="callout alert-manual"><span class="tag tag-warn">Alerta do registro</span><h3>{_esc(alert)}</h3><p>O alerta vem do que foi marcado manualmente; nao altera os dominios fisiologicos do Farol.</p></div>'
                  if alert else "")
    rows = "".join(f"<li><b>{_esc(item.get('date'))}</b> — {_esc(injuries.observation_text(item))}</li>" for item in reversed(history))
    shoe_hint = ", ".join(shoes) if shoes else "nome do tenis"
    return f"""
<section>
  <div class="section-head"><h2>Monitoramento de lesoes</h2><div class="section-note">Registro manual diario; observacao, nao diagnostico.</div></div>
  {alert_html}
  <div class="card"><div class="form-label">Ultima observacao ate { _esc(ctx['on_date']) }</div><p style="margin:7px 0 0;">{_esc(latest)}</p>
    <p class="notice">Registre area, dor (0&ndash;10), inchaco/roxo, tenis, capacidade de caminhar/correr e se piorou. Se marcar piora ou incapacidade de correr, o Farol exibe: <b>nao correr enquanto a piora persistir / ate reavaliar</b>.</p>
    <p class="notice">Fonte gravavel: <code class="mono">python -m farol.records.injuries add --area "area" --pain 0 --swelling nenhum --shoe "{_esc(shoe_hint)}" --walk sim --run sim</code>. Acrescente <code class="mono">--worse</code> se houve piora.</p>
    {f'<ul class="injury-history">{rows}</ul>' if rows else ''}
  </div>
</section>
"""


def _registro_pos_prova_html(ctx: dict) -> str:
    race = ctx["profile"].get("race") or {}
    race_date = str(race.get("date") or "")[:10]
    if not race_date:
        return ""
    if race_date > ctx["on_date"]:
        body = (f"Registro disponivel depois da prova prevista para "
                f"<b>{_esc(race_date)}</b>.")
    else:
        body = _esc(race_log.summary(ctx["race_log"]))
    command = (
        f'python -m farol.records.race_log add --date {race_date} '
        '--course "percurso/altimetria" --weather "clima" --water-ml 0 '
        '--nutrition "geis e horarios" --nutrition-carbs-g 0 '
        '--nutrition-caffeine-mg 0 --rpe 0 --gi "nenhum" '
        '--foot-pain 0 --foot-swelling "nenhum" --shoe "tenis"'
    )
    return f"""
<section>
  <div class="section-head"><h2>Registro pos-prova</h2><div class="section-note">Contexto manual da prova; nao e metrica Garmin.</div></div>
  <div class="card"><div class="form-label">Prova de {_esc(race_date)}</div><p style="margin:7px 0 0;">{body}</p>
    <p class="notice">Registre percurso, clima, agua, nutricao realmente usada, RPE, sintomas gastrointestinais, pe e tenis. Isso ajuda a interpretar a sessao, mas nao altera TSB, carga, recuperacao, classificacao ou previsao.</p>
    <p class="notice">Fonte gravavel: <code class="mono">{_esc(command)}</code>.</p>
  </div>
</section>
"""


def _pmc_html(ctx: dict) -> str:
    pmc = ctx["pmc"]
    svg = charts.pmc_svg(pmc["series"], pmc["anotacoes"],
                         inicio=pmc["inicio"], fim=pmc["fim"])
    if not svg:
        return ""
    notas = "".join(f'<li><b>{_esc(a["date"])}</b> {_esc(a["label"])}</li>'
                    for a in pmc["anotacoes"])
    notas_html = f'<ul class="pmc-notes">{notas}</ul>' if notas else ""
    return f"""
<section>
  <div class="section-head"><h2>Carga de 180 dias (PMC)</h2><div class="section-note">CTL = carga cronica (forma), ATL = aguda (fadiga), TSB = saldo entre as duas. Valores como estao em training_load -- nenhuma constante de tempo e' recalculada aqui.</div></div>
  <div class="card pmc-card">
    {svg}
    <div class="legend"><span><i style="background:var(--accent)"></i>CTL (forma)</span><span><i style="background:var(--warn)"></i>ATL (fadiga)</span><span><i style="background:var(--ok);opacity:.5"></i>TSB positivo</span><span><i style="background:var(--critical);opacity:.5"></i>TSB negativo</span><span>verticais: provas, lesoes e quebras de medicao</span></div>
    {notas_html}
  </div>
</section>
"""


def _charts_html(ctx: dict) -> str:
    m = ctx["metricas"]
    cartoes = (("hrv_avg", "HRV", " ms", 0), ("rhr", "FC de repouso", " bpm", 0),
                ("sleep_score", "Sleep score", "", 0), ("tsb", "TSB", "", 1))
    blocos = []
    for campo, titulo, unidade, digitos in cartoes:
        metrica = m.get(campo)
        if metrica is None:
            continue
        valor = f"{metrica.value:.{digitos}f}{unidade}" if metrica.has_data else "—"
        svg = charts.sparkline_svg(
            ctx["sparklines"].get(campo) or [],
            baseline=metrica.mean_90d if metrica.has_data else None,
            title=f"{titulo} nos ultimos 30 dias", unit=unidade, digits=digitos)
        blocos.append(
            f'<div class="chart-card"><div class="head"><div class="title">{_esc(titulo)}</div>'
            f'<div class="now">{_esc(valor)}</div></div>{svg}'
            f'<div class="foot"><span>ha 30 dias</span><span>hoje</span></div></div>')
    return f"""
<section>
  <div class="section-head"><h2>Ultimos 30 dias</h2><div class="section-note">Linha tracejada = sua media movel de 90 dias (o "normal" pessoal). Desenhado na geracao: imprime e abre sem JavaScript.</div></div>
  <div class="chart-grid">{''.join(blocos)}</div>
</section>
"""


def _carga_html(ctx: dict) -> str:
    semanas = ctx["semanas"]
    tl = ctx["training_load"]
    if not semanas:
        return ""
    max_km = max(s["km"] for s in semanas) or 1
    ordenadas = list(reversed(semanas))
    bars = []
    for i, s in enumerate(ordenadas):
        atual = s is semanas[0]
        pct = s["km"] / max_km * 100
        bars.append(
            f'<div class="bar-col{" current" if atual else ""}"><div class="val">{s["km"]:.1f}</div>'
            f'<div class="bar-el"><div class="fill" style="height:{pct:.1f}%;"></div></div>'
            f'<div class="lbl">{_esc(s["inicio"][5:])}</div></div>'
        )
    rows = []
    for s in semanas:
        mono = f'{s["monotonia"]:.2f}' if s["monotonia"] is not None else "—"
        tr_attr = ' class="current"' if s is semanas[0] else ""
        rows.append(
            f'<tr{tr_attr}>'
            f'<td>{_esc(s["inicio"])} a {_esc(s["fim"])}</td>'
            f'<td class="num">{s["km"]:.1f}</td><td class="num">{s["sessoes"]}</td>'
            f'<td class="num">{s["trimp"]:.0f}</td><td class="num">{mono}</td></tr>'
        )
    ctl_atl = ""
    if tl:
        ctl_atl = (f'CTL {tl["ctl"]:.1f} (forma) &middot; ATL {tl["atl"]:.1f} (fadiga aguda)'
                   + (f' &middot; ATL/CTL {tl["atl"] / tl["ctl"]:.2f}' if tl.get("ctl") else ""))
    return f"""
<section>
  <div class="section-head"><h2>Carga de treino</h2><div class="section-note">8 semanas fechadas + a atual. Monotonia de Foster acima de 2,0 e' marcador de risco.</div></div>
  <div class="card">
    <div class="bars">{''.join(bars)}</div>
    <div class="table-scroll"><table class="data mono"><thead><tr><th>semana</th><th class="num">km</th><th class="num">sessoes</th><th class="num">TRIMP</th><th class="num">monotonia</th></tr></thead>
    <tbody>{''.join(rows)}</tbody></table></div>
    <div style="font-size:12px;color:var(--ink-dim);margin-top:12px;">{_esc(ctl_atl)}</div>
  </div>
</section>
"""


def _recuperacao_html(ctx: dict) -> str:
    m = ctx["metricas"]
    unidades = {"hrv_avg": " ms", "rhr": " bpm", "night_hr_avg": " bpm",
                "respiration_avg": " rpm", "spo2_avg": "%", "body_battery_min": "",
                "stress_avg": ""}
    linhas = "".join(_metric_row_html(c, m[c], unidades.get(c, ""), ctx["sparklines"].get(c))
                     for c in _METRICAS_RECUPERACAO if c in m)
    cv = ctx["hrv_cv"]
    cv_txt = ""
    if cv is not None:
        ref = f'{cv["cv_30d"]:.1f}%' if cv["cv_30d"] is not None else "sem dado"
        cv_txt = (f'<div style="font-size:12px;color:var(--ink-dim);margin-top:10px;">'
                  f'HRV CV {cv["cv_7d"]:.1f}% (7d) &middot; referencia 30d {ref}</div>')
    return f"""
<section>
  <div class="section-head"><h2>Recuperacao</h2><div class="section-note">Marcador = hoje; faixa = media +/- 1 sd dos seus 90 dias; tracejado = media. z-score no fim da linha.</div></div>
  <div class="card"><div class="metric-grid">{linhas}</div>{cv_txt}</div>
</section>
"""


def _sono_html(ctx: dict) -> str:
    m = ctx["metricas"]
    campos = ("sleep_score", "total_sleep_minutes", "avg_sleep_stress")
    unidades = {"sleep_score": "/100", "total_sleep_minutes": " min", "avg_sleep_stress": ""}
    linhas = "".join(_metric_row_html(c, m[c], unidades.get(c, ""), ctx["sparklines"].get(c))
                     for c in campos if c in m)
    daily = ctx["daily"] or {}
    total = daily.get("total_sleep_minutes")
    detalhe = []
    if total:
        for campo, rotulo in (("deep_sleep_minutes", "Profundo"), ("light_sleep_minutes", "Leve"),
                              ("rem_sleep_minutes", "REM")):
            v = daily.get(campo)
            if v is not None:
                detalhe.append(f"<div><b>{v:.0f} min</b> {rotulo.lower()} ({v / total * 100:.0f}%)</div>")
        desp = daily.get("awakenings")
        if desp is not None:
            detalhe.append(f"<div><b>{desp}</b> despertares ({desp / (total / 60):.1f}/h)</div>")
    reg = ctx["regularidade_sono"]
    if reg is not None:
        detalhe.append(f"<div><b>{reg:.0f} min</b> DP no horario de dormir (14d)</div>")
    detalhe_html = ""
    if detalhe:
        detalhe_html = (f'<div style="display:flex;gap:18px;flex-wrap:wrap;margin-top:16px;'
                        f'padding-top:14px;border-top:1px solid var(--border);font-size:12.5px;">'
                        f'{"".join(detalhe)}</div>')
    return f"""
<section>
  <div class="section-head"><h2>Sono</h2></div>
  <div class="card"><div class="metric-grid">{linhas}</div>{detalhe_html}</div>
</section>
"""


def _prova_html(ctx: dict) -> str:
    race = ctx["race"]
    if not race:
        return ""
    if not race.get("predicao"):
        return """
<section>
  <div class="section-head"><h2>Previsao de prova</h2></div>
  <div class="card"><p style="margin:0;">Sem prova observada no banco para as datas declaradas em
  <code class="mono">profile.yaml -&gt; recent_races</code>. Sem ancora, o farol nao chuta um tempo.</p></div>
</section>
"""
    p, ancora = race["predicao"], race["ancora"]
    alvo_km = race["alvo_km"]
    linhas = []
    for nome, chave in (("Riegel", "riegel"), ("Cameron", "cameron"), ("VDOT (Daniels)", "vdot")):
        t = p[chave]
        linhas.append(f'<tr><td>{nome}</td><td class="num">{race_prediction._fmt_tempo(t)}</td>'
                      f'<td class="num">{race_prediction._fmt_pace(t / alvo_km)}</td></tr>')
    linhas.append(f'<tr class="current"><td><b>faixa (incerteza do metodo)</b></td>'
                  f'<td class="num"><b>{race_prediction._fmt_tempo(p["min"])}&ndash;{race_prediction._fmt_tempo(p["max"])}</b></td>'
                  f'<td class="num"><b>{race_prediction._fmt_pace(p["min"] / alvo_km)}&ndash;{race_prediction._fmt_pace(p["max"] / alvo_km)}</b></td></tr>')
    tsb_txt = ""
    if race.get("tsb"):
        tsb_txt = f' TSB no dia da ancora de calculo: {race["tsb"]["tsb"]:+.1f} ({race["tsb"]["date"]}).'
    return f"""
<section>
  <div class="section-head"><h2>Previsao de prova</h2>
  <div class="section-note">{_esc(race['race'].get('name', 'prova'))} &middot; ancora: {_esc(ancora['name'])}
  ({_esc(ancora['date'])}, {ancora['distance_km']:.2f} km em {race_prediction._fmt_tempo(ancora['duration_min'])})</div></div>
  <div class="card">
    <div class="table-scroll"><table class="data mono"><thead><tr><th>modelo</th><th class="num">tempo</th><th class="num">pace</th></tr></thead>
    <tbody>{''.join(linhas)}</tbody></table></div>
    <div style="font-size:12px;color:var(--ink-dim);margin-top:12px;">VDOT implicito pela ancora: {race["vdot"]:.1f}.{tsb_txt}</div>
  </div>
</section>
"""


def _analises_html(ctx: dict) -> str:
    return f"""
<section>
  <div class="section-head"><h2>Correlacoes e quebras de medicao</h2>
  <div class="section-note">Correlacao entre desvios da propria baseline -- gera hipotese, nunca confirma causa.
  Calculado com dados disponiveis ate {_esc(ctx['on_date'])} -- nenhuma correlacao ou quebra usa data posterior a esta.</div></div>
  <div class="card">{_md_table_html(ctx['correlations_md'])}</div>
  <div class="card">{_md_table_html(ctx['changepoint_md'])}</div>
</section>
"""


def _calibracao_html(ctx: dict) -> str:
    return f"""
<section>
  <div class="section-head"><h2>Calibracao automatica do modelo</h2></div>
  <div class="card"><pre class="report">{_esc(ctx['calibration_md'].strip())}</pre></div>
</section>
"""


def _fold_html(titulo: str, hint: str, conteudo: str, aberto: bool = False) -> str:
    """Secao terciaria em <details>: fechada por padrao, sem JavaScript.

    `aberto=True` so' quando o proprio conteudo pede atencao (ex.: alerta do
    registro de lesao) -- esconder um alerta manual seria pior que a densidade.
    """
    if not conteudo:
        return ""
    atributo = " open" if aberto else ""
    return (f'<details class="fold"{atributo}><summary><span>{_esc(titulo)}</span>'
            f'<span class="hint">{_esc(hint)}</span></summary>{conteudo}</details>')


def _perfil_html(ctx: dict) -> str:
    profile = ctx["profile"]
    athlete = profile.get("athlete") or {}
    race = profile.get("race") or {}
    zonas = (f'<span class="badge">FC max <b>{profile.get("hr_max", "—")}</b></span>'
            f'<span class="badge">FC repouso <b>{profile.get("hr_rest", "—")}</b></span>'
            f'<span class="badge">LTHR <b>{profile.get("lthr", "—")}</b></span>')
    floors = profile.get("hr_zone_floors") or {}
    if floors:
        zonas += (f'<span class="badge">Z1 {floors.get("z1", "—")} &middot; Z2 {floors.get("z2", "—")} '
                 f'&middot; Z3 {floors.get("z3", "—")} &middot; Z4 {floors.get("z4", "—")}</span>')
    return f"""
<section>
  <div class="section-head"><h2>Perfil</h2><div class="section-note">De data/profile.yaml.</div></div>
  <div class="card">
    <div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(160px,1fr));gap:14px;">
      <div><div class="k" style="font-size:11px;text-transform:uppercase;color:var(--ink-dim);">Nome</div>
      <div style="font-family:var(--font-mono);font-size:14px;">{_esc(athlete.get('name', '—'))}</div></div>
      <div><div class="k" style="font-size:11px;text-transform:uppercase;color:var(--ink-dim);">Altura</div>
      <div style="font-family:var(--font-mono);font-size:14px;">{_esc(athlete.get('height_cm', '—'))} cm</div></div>
      <div><div class="k" style="font-size:11px;text-transform:uppercase;color:var(--ink-dim);">Prova-alvo</div>
      <div style="font-family:var(--font-mono);font-size:14px;">{_esc(race.get('name', '—'))} ({_esc(race.get('date', '—'))})</div></div>
    </div>
  </div>
  <div class="card"><div class="badge-row">{zonas}</div></div>
</section>
"""


def render_html(ctx: dict) -> str:
    """HTML de 1 pagina. Graficos ja vem em SVG; o unico JS e' o editor de
    nutricao, que recalcula a tabela a partir dos valores do formulario."""
    script = (
        "<script>" + _SCRIPT_RUNTIME +
        f"var FAROL_NUTRITION = {json.dumps(ctx['nutrition_config'])};\n"
        "farolNutrition(FAROL_NUTRITION);\n"
        "</script>"
    )
    alerta_lesao = injuries.observation_alert(ctx["injury"])
    corpo = "".join([
        _hero_html(ctx),
        '<div class="wrap">',
        _resumo_html(ctx),
        _prontidao_html(ctx),
        _pmc_html(ctx),
        _charts_html(ctx),
        _desempenho_html(ctx),
        _carga_html(ctx),
        _recuperacao_html(ctx),
        _sono_html(ctx),
        _prova_html(ctx),
        _nutricao_html(ctx),
        # Nivel 3: detalhe sob demanda, sem esconder alerta manual.
        _fold_html("Monitoramento de lesoes", "registro manual",
                   _lesoes_html(ctx), aberto=bool(alerta_lesao)),
        _fold_html("Registro pos-prova", "contexto manual",
                   _registro_pos_prova_html(ctx)),
        _fold_html("Correlacoes e quebras de medicao", "hipotese, nunca causa",
                   _analises_html(ctx)),
        _fold_html("Calibracao automatica do modelo", "objetivo vs modelo",
                   _calibracao_html(ctx)),
        _fold_html("Perfil", "data/profile.yaml", _perfil_html(ctx)),
        f'<footer>Gerado em {_esc(ctx["on_date"])} por <code class="mono">python -m farol.reports.dashboard</code>. '
        "Le apenas o banco SQLite local; nao consulta Garmin nem clima durante a geracao. "
        "A atualizacao Garmin e separada via farol.pipeline.ingest; clima do percurso usa consulta externa. "
        "Descreve o que os dados mostram; nao prescreve treino nem diagnostica (PROFILE.md, regras 7 e 9).</footer>",
        "</div>",
    ])
    return _head_html() + corpo + script


def build_html(conn, profile: dict, on_date: str) -> str:
    return render_html(build_context(conn, profile, on_date))


def main() -> None:
    parser = argparse.ArgumentParser(description="Gera o dashboard HTML de 1 pagina do estado do atleta.")
    parser.add_argument("--date", default=None, help="Data YYYY-MM-DD (padrao: hoje)")
    parser.add_argument("--out", default=str(REPORTS_DIR / "DASHBOARD.html"), help="Caminho de saida")
    args = parser.parse_args()

    try:
        profile = load_profile()
    except FileNotFoundError as exc:
        print(exc)
        raise SystemExit(1)

    on_date = args.date or _date.today().isoformat()
    conn = db.connect_readonly()
    try:
        texto = build_html(conn, profile, on_date)
    finally:
        conn.close()

    with open(args.out, "w", encoding="utf-8") as f:
        f.write(texto)
    print(f"Dashboard de {on_date} gravado em {args.out}")


if __name__ == "__main__":
    main()
