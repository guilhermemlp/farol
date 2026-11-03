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

Uso: python -m farol.dashboard [--date YYYY-MM-DD] [--out CAMINHO]
Sem --date, usa hoje. Sem --out, grava em reports/DASHBOARD.html.
"""
import argparse
import html as _html
import json
from datetime import date as _date
from datetime import timedelta

from . import (calibration_auto, changepoint, correlations, db, injuries,
               interpretation, nutrition, race_log, race_prediction, snapshot, state, today)
from .analysis import weekly_load
from .config import REPORTS_DIR, load_profile
from .hrv_variability import hrv_cv
from .labels import ROTULOS
from .recovery import PESOS_PADRAO, recovery_index, recovery_label
from .sleep_regularity import bedtime_regularity
from .zones import heart_rate_zones

# Mesmo padrao de today._ROTULO (rotulos locais quando o significado e
# especifico deste relatorio) -- ver farol/labels.py sobre por que
# sleep_score nao mora no dicionario compartilhado.
_ROTULO = {**ROTULOS, "sleep_score": "Sono", "avg_sleep_stress": "Stress no sono",
           "total_sleep_minutes": "Duracao do sono"}

# Mesmos campos padrao de farol.changepoint (farol/changepoint.py,
# _CAMPOS_PADRAO), mais body_battery_min: achado da vistoria de 2026-09-04
# (auditoria manual apontou quebra de -1.66 SD em 2026-08-03, fora da lista
# curada original porque o campo e' volatil demais para o dominio
# RECUPERACAO -- ver farol/state.py:38 -- mas isso nao o torna irrelevante
# para deteccao de quebra de MEDICAO, que e' uma pergunta diferente).
# Lista local, nao `changepoint._CAMPOS_PADRAO` importado, para nao herdar
# mudanca futura em silencio -- um diff aqui precisa ser uma decisao.
_CAMPOS_CHANGEPOINT = ["rhr", "hrv_avg", "sleep_score", "total_sleep_minutes",
                       "stress_avg", "night_hr_avg", "awakenings", "body_battery_min"]

_METRICAS_RECUPERACAO = ("hrv_avg", "rhr", "night_hr_avg", "respiration_avg",
                          "spo2_avg", "body_battery_min", "stress_avg")


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

    `splits.reps` ja e' a saida curada de farol.splits e contem somente
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
            # O resumo salvo por farol.splits ja contem somente
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

    # Mesmo retrato do dia que farol.today monta (farol/snapshot.py) -- o
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

    ctx["sparklines"] = {
        "hrv_avg": _series(conn, "hrv_avg", on_date),
        "rhr": _series(conn, "rhr", on_date),
        "sleep_score": _series(conn, "sleep_score", on_date),
        "tsb": _series(conn, "tsb", on_date, table="training_load"),
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
    ctx["injury_history"] = injuries.observations_as_of(on_date)[-7:]
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


def _metric_row_html(campo: str, m, unidade: str = "") -> str:
    rotulo = _ROTULO.get(campo, campo)
    if not m.has_data:
        return (f'<div class="metric-row"><div class="label">{_esc(rotulo)}</div>'
                f'<div class="bar-wrap"></div><div class="val">sem dado</div><div class="status"></div></div>')
    baseline = f"normal {m.mean_90d:g}{unidade}" if m.mean_90d is not None else "sem baseline"
    z = m.z if m.z is not None else 0.0
    pct = min(100.0, abs(z) * 33.0)
    lado = "right:50%;" if z < 0 else "left:50%;"
    cls = "z-warn" if m.status in ("atencao", "alerta") else "z-ok"
    dot = "dot-warn" if m.status in ("atencao", "alerta") else "dot-ok"
    return (
        f'<div class="metric-row"><div class="label"><span class="dot {dot}"></span>{_esc(rotulo)}</div>'
        f'<div class="bar-wrap"><div class="bar {cls}" style="{lado}width:{pct / 2:.1f}%;"></div></div>'
        f'<div class="val">{_esc(baseline)} &middot; z {z:+.2f}</div>'
        f'<div class="status mono">{m.value:g}{_esc(unidade)}</div></div>'
    )


def _fmt_km(v) -> str:
    return f"{v:.1f}" if v is not None else "—"


_LABEL_DOMINIO = {"ok": ("NORMAL", "🟢", "pill-ok"), "atencao": ("BORDERLINE", "🟡", "pill-warn"),
                   "alerta": ("ALERTA", "🔴", "pill-crit"), "sem_dado": ("SEM DADO", "⚪", "pill-warn")}


def _idade(birth_date: str | None, on_date: str) -> int | None:
    if not birth_date:
        return None
    b = _date.fromisoformat(str(birth_date)[:10])
    d = _date.fromisoformat(on_date)
    return d.year - b.year - ((d.month, d.day) < (b.month, b.day))


# ------------------------------------------------------------------- template

_HEAD = """<!doctype html>
<title>Farol</title>
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Fraunces:opsz,wght@9..144,440;9..144,560;9..144,650&family=Public+Sans:wght@400;500;600;700&family=IBM+Plex+Mono:wght@400;500;600&display=swap">
<style>
  :root{
    --bg:#f5f1e6; --surface:#fffdf8; --surface-2:#ece3d0; --border:#ddd0ae;
    --ink:#211a10; --ink-dim:#6d6250;
    --accent:#b6572a; --accent-strong:#8a3f18; --accent-tint:#f2ded0;
    --ok:#2f7d5b; --ok-tint:#dfeee3; --warn:#a3701c; --warn-tint:#f4e6c9;
    --critical:#a83c32; --critical-tint:#f5dcd6;
    --shadow: 0 1px 2px rgba(33,26,16,.06), 0 8px 24px -12px rgba(33,26,16,.18);
  }
  @media (prefers-color-scheme: dark){
    :root:not([data-theme="light"]){
      --bg:#11161c; --surface:#171e26; --surface-2:#1e2731; --border:#2d3945;
      --ink:#eee9de; --ink-dim:#9aa3ab;
      --accent:#e0964c; --accent-strong:#f2b06c; --accent-tint:#3a2a1c;
      --ok:#5cb98c; --ok-tint:#1c3128; --warn:#e3bb5e; --warn-tint:#3a301a;
      --critical:#e37868; --critical-tint:#3a2320;
      --shadow: 0 1px 2px rgba(0,0,0,.3), 0 8px 24px -12px rgba(0,0,0,.5);
    }
  }
  :root[data-theme="dark"]{
    --bg:#11161c; --surface:#171e26; --surface-2:#1e2731; --border:#2d3945;
    --ink:#eee9de; --ink-dim:#9aa3ab;
    --accent:#e0964c; --accent-strong:#f2b06c; --accent-tint:#3a2a1c;
    --ok:#5cb98c; --ok-tint:#1c3128; --warn:#e3bb5e; --warn-tint:#3a301a;
    --critical:#e37868; --critical-tint:#3a2320;
    --shadow: 0 1px 2px rgba(0,0,0,.3), 0 8px 24px -12px rgba(0,0,0,.5);
  }
  *{box-sizing:border-box;}
  body{background:var(--bg);color:var(--ink);font-family:"Public Sans",ui-sans-serif,system-ui,sans-serif;font-size:15px;line-height:1.55;}
  h1,h2,h3,.display{font-family:"Fraunces",Georgia,serif;text-wrap:balance;font-weight:650;letter-spacing:-0.01em;}
  .mono, table, .tabular{font-family:"IBM Plex Mono",ui-monospace,"SFMono-Regular",monospace;font-variant-numeric:tabular-nums;}
  a{color:var(--accent-strong);}
  .wrap{max-width:900px;margin:0 auto;padding:0 20px 80px;}
  header.hero{position:relative;overflow:hidden;padding:56px 20px 40px;
    background:radial-gradient(620px 420px at 14% -10%, color-mix(in srgb, var(--accent) 30%, transparent), transparent 70%), var(--surface);
    border-bottom:1px solid var(--border);}
  header.hero .wrap{padding:0;}
  .eyebrow{font-family:"IBM Plex Mono",monospace;font-size:11.5px;letter-spacing:.14em;text-transform:uppercase;color:var(--accent-strong);
    display:flex;align-items:center;gap:10px;margin-bottom:14px;}
  .eyebrow::before{content:"";width:22px;height:1px;background:var(--accent-strong);display:inline-block;}
  h1.title{font-size:clamp(34px,6vw,50px);margin:0 0 6px;}
  .subtitle{color:var(--ink-dim);font-size:16.5px;max-width:60ch;margin:0 0 28px;}
  .hero-meta{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:1px;background:var(--border);
    border:1px solid var(--border);border-radius:10px;overflow:hidden;}
  .hero-meta div{background:var(--surface);padding:14px 16px;}
  .hero-meta .k{font-size:11px;text-transform:uppercase;letter-spacing:.08em;color:var(--ink-dim);margin-bottom:5px;}
  .hero-meta .v{font-family:"IBM Plex Mono",monospace;font-size:17px;font-weight:600;}
  section{padding:44px 0 4px;border-bottom:1px solid var(--border);}
  section:last-of-type{border-bottom:none;}
  .section-head{display:flex;align-items:baseline;justify-content:space-between;gap:16px;margin-bottom:20px;flex-wrap:wrap;}
  .section-head h2{font-size:23px;margin:0;}
  .section-note{color:var(--ink-dim);font-size:13px;max-width:46ch;}
  .callout{border:1px solid var(--border);border-left:4px solid var(--accent);background:var(--surface);border-radius:8px;
    padding:18px 20px;box-shadow:var(--shadow);margin-bottom:16px;}
  .callout h3{font-size:16px;margin:0 0 6px;font-family:"Public Sans";font-weight:700;}
  .callout p{margin:0 0 8px;font-size:14px;color:var(--ink-dim);}
  .callout p:last-child{margin-bottom:0;}
  .tag{display:inline-block;font-family:"IBM Plex Mono";font-size:10.5px;letter-spacing:.06em;text-transform:uppercase;
    padding:2px 8px;border-radius:999px;margin-bottom:8px;}
  .tag-warn{background:var(--warn-tint);color:var(--warn);}
  .tag-ok{background:var(--ok-tint);color:var(--ok);}
  .tag-info{background:var(--accent-tint);color:var(--accent-strong);}
  .tiles{display:grid;grid-template-columns:repeat(auto-fit,minmax(190px,1fr));gap:12px;}
  .tile{background:var(--surface);border:1px solid var(--border);border-radius:10px;padding:16px 18px;box-shadow:var(--shadow);}
  .tile .k{font-size:11.5px;text-transform:uppercase;letter-spacing:.06em;color:var(--ink-dim);margin-bottom:8px;}
  .tile .v{font-family:"Fraunces",serif;font-size:28px;font-weight:650;line-height:1;}
  .tile .u{font-size:13px;color:var(--ink-dim);margin-left:3px;font-family:"Public Sans";}
  .tile .sub{font-size:12.5px;color:var(--ink-dim);margin-top:6px;font-family:"IBM Plex Mono";}
  .domains{display:grid;grid-template-columns:repeat(auto-fit,minmax(220px,1fr));gap:12px;margin-top:6px;}
  .domain{background:var(--surface);border:1px solid var(--border);border-radius:10px;padding:14px 16px;box-shadow:var(--shadow);
    display:flex;align-items:center;justify-content:space-between;gap:10px;}
  .domain .name{font-weight:600;font-size:14.5px;}
  .domain .streak{font-size:11.5px;color:var(--ink-dim);font-family:"IBM Plex Mono";}
  .pill{font-family:"IBM Plex Mono";font-size:11.5px;font-weight:600;letter-spacing:.03em;padding:4px 10px;border-radius:999px;white-space:nowrap;}
  .pill-ok{background:var(--ok-tint);color:var(--ok);}
  .pill-warn{background:var(--warn-tint);color:var(--warn);}
  .pill-crit{background:var(--critical-tint);color:var(--critical);}
  .card{background:var(--surface);border:1px solid var(--border);border-radius:10px;padding:18px 20px;box-shadow:var(--shadow);}
  .card + .card{margin-top:12px;}
  .metric-grid{display:grid;grid-template-columns:1fr;gap:2px;}
  .metric-row{display:grid;grid-template-columns:130px 1fr 170px 90px;align-items:center;gap:12px;padding:9px 0;
    border-top:1px solid var(--border);font-size:13.5px;}
  .metric-row:first-child{border-top:none;}
  .metric-row .label{font-weight:600;font-size:13px;}
  .metric-row .val{font-family:"IBM Plex Mono";text-align:right;color:var(--ink-dim);font-size:12px;}
  .metric-row .status{text-align:right;}
  .metric-row .bar-wrap{position:relative;height:8px;background:var(--surface-2);border-radius:5px;overflow:hidden;}
  .metric-row .bar{position:absolute;top:0;bottom:0;border-radius:5px;opacity:.7;}
  .metric-row .bar.z-ok{background:var(--ok);} .metric-row .bar.z-warn{background:var(--warn);}
  .dot{width:6px;height:6px;border-radius:50%;display:inline-block;margin-right:6px;}
  .dot-ok{background:var(--ok);} .dot-warn{background:var(--warn);}
  @media (max-width:560px){.metric-row{grid-template-columns:100px 1fr 40px;} .metric-row .val{display:none;}}
  .chart-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(280px,1fr));gap:12px;}
  .chart-card{background:var(--surface);border:1px solid var(--border);border-radius:10px;padding:16px 18px 12px;box-shadow:var(--shadow);}
  .chart-card .head{display:flex;justify-content:space-between;align-items:baseline;margin-bottom:6px;}
  .chart-card .title{font-weight:600;font-size:13.5px;}
  .chart-card .now{font-family:"IBM Plex Mono";font-size:15px;font-weight:600;}
  .chart-card svg{display:block;width:100%;height:auto;overflow:visible;}
  .chart-card .foot{display:flex;justify-content:space-between;font-size:10.5px;color:var(--ink-dim);margin-top:4px;font-family:"IBM Plex Mono";}
  .bars{display:flex;align-items:flex-end;gap:8px;height:130px;margin:10px 0 6px;}
  .bar-col{flex:1;display:flex;flex-direction:column;align-items:center;justify-content:flex-end;height:100%;position:relative;}
  .bar-col .bar-el{width:100%;max-width:34px;background:var(--surface-2);border-radius:4px 4px 2px 2px;position:relative;height:100px;}
  .bar-col .bar-el .fill{position:absolute;bottom:0;left:0;right:0;background:var(--accent);border-radius:4px 4px 2px 2px;opacity:.85;}
  .bar-col.current .fill{background:var(--accent-strong);}
  .bar-col .val{font-family:"IBM Plex Mono";font-size:10.5px;color:var(--ink-dim);margin-bottom:4px;}
  .bar-col .lbl{font-size:9.5px;color:var(--ink-dim);margin-top:6px;text-align:center;line-height:1.2;font-family:"IBM Plex Mono";}
  .bar-col.current .lbl{color:var(--accent-strong);font-weight:600;}
  table.data{width:100%;border-collapse:collapse;font-size:12.5px;}
  table.data th,table.data td{text-align:left;padding:8px 10px;border-bottom:1px solid var(--border);}
  table.data th{font-family:"Public Sans";font-weight:600;text-transform:uppercase;font-size:10.5px;letter-spacing:.05em;color:var(--ink-dim);}
  table.data td.num{text-align:right;}
  table.data tr.current td{background:var(--accent-tint);}
  .table-scroll{overflow-x:auto;}
  blockquote.read{margin:0;padding:16px 20px;background:var(--surface-2);border-radius:8px;font-family:"Fraunces",serif;
    font-size:16px;font-weight:440;line-height:1.5;border-left:3px solid var(--accent);}
  pre.report{white-space:pre-wrap;font-family:"IBM Plex Mono";font-size:12px;line-height:1.6;color:var(--ink-dim);margin:0;}
  .badge-row{display:flex;flex-wrap:wrap;gap:8px;margin-top:10px;}
  .badge{font-family:"IBM Plex Mono";font-size:11px;padding:5px 10px;border-radius:6px;background:var(--surface-2);
    border:1px solid var(--border);color:var(--ink-dim);}
  .badge b{color:var(--ink);font-weight:600;}
  .readiness{display:grid;grid-template-columns:repeat(auto-fit,minmax(145px,1fr));gap:1px;background:var(--border);border:1px solid var(--border);border-radius:10px;overflow:hidden;}
  .readiness > div{background:var(--surface);padding:15px 16px;}
  .readiness .label,.form-label{font-size:10.5px;letter-spacing:.07em;text-transform:uppercase;color:var(--ink-dim);font-weight:600;}
  .readiness .value{font-family:"Fraunces",serif;font-size:22px;font-weight:650;line-height:1.2;margin-top:4px;}
  .readiness .detail{font-size:11.5px;color:var(--ink-dim);margin-top:4px;}
  .performance-grid{display:grid;grid-template-columns:1.3fr 1fr;gap:12px;}
  .performance-grid svg{width:100%;height:auto;min-height:220px;display:block;}
  .legend{display:flex;gap:12px;flex-wrap:wrap;font-size:11.5px;color:var(--ink-dim);margin-top:10px;}
  .legend i{display:inline-block;width:9px;height:9px;border-radius:50%;margin-right:4px;vertical-align:baseline;}
  .form-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(140px,1fr));gap:12px;margin:14px 0;}
  .form-grid label{display:grid;gap:4px;font-size:12px;color:var(--ink-dim);}
  input,select{width:100%;background:var(--surface-2);color:var(--ink);border:1px solid var(--border);border-radius:6px;padding:8px;font:12px "IBM Plex Mono",monospace;}
  .nutrition-summary{display:flex;gap:12px;flex-wrap:wrap;font-family:"IBM Plex Mono",monospace;font-size:12px;color:var(--ink-dim);margin:8px 0 14px;}
  .notice{font-size:12.5px;color:var(--ink-dim);margin:12px 0 0;}
  .alert-manual{border-left-color:var(--critical);}
  .injury-history{margin-top:14px;font-size:12.5px;color:var(--ink-dim);}
  @media (max-width:650px){.performance-grid{grid-template-columns:1fr;}}
  footer{padding:32px 0 0;color:var(--ink-dim);font-size:12px;line-height:1.7;}
  footer a{color:var(--ink-dim);text-decoration:underline;}
</style>
"""

_SCRIPT_RUNTIME = """
function farolSpark(svgId, points, baseline, fmt, nowId){
  var svg = document.getElementById(svgId);
  if (!svg || !points.length) return;
  var W = 280, H = 90, padL = 4, padR = 4, padT = 10, padB = 4;
  var values = points.map(function(p){ return p[1]; });
  var min = Math.min.apply(null, values), max = Math.max.apply(null, values);
  if (baseline !== null) { min = Math.min(min, baseline); max = Math.max(max, baseline); }
  var span = (max - min) || 1;
  min -= span * 0.15; max += span * 0.15;
  var n = values.length;
  var x = function(i){ return padL + (W - padL - padR) * (n === 1 ? 0 : i / (n - 1)); };
  var y = function(v){ return padT + (H - padT - padB) * (1 - (v - min) / (max - min)); };
  var ns = "http://www.w3.org/2000/svg";
  var accent = getComputedStyle(document.documentElement).getPropertyValue('--accent').trim();
  var dim = getComputedStyle(document.documentElement).getPropertyValue('--ink-dim').trim();
  if (baseline !== null){
    var bl = document.createElementNS(ns,'line');
    bl.setAttribute('x1', padL); bl.setAttribute('x2', W - padR);
    bl.setAttribute('y1', y(baseline)); bl.setAttribute('y2', y(baseline));
    bl.setAttribute('stroke', dim); bl.setAttribute('stroke-width','1');
    bl.setAttribute('stroke-dasharray','3,3'); bl.setAttribute('opacity','0.6');
    svg.appendChild(bl);
  }
  var linePts = values.map(function(v,i){ return x(i)+","+y(v); }).join(' ');
  var areaPts = "M"+x(0)+","+H+" L"+values.map(function(v,i){ return x(i)+","+y(v); }).join(' L') + " L"+x(n-1)+","+H+" Z";
  var gradId = svgId+"-grad";
  var defs = document.createElementNS(ns,'defs');
  var grad = document.createElementNS(ns,'linearGradient');
  grad.setAttribute('id', gradId); grad.setAttribute('x1','0'); grad.setAttribute('y1','0'); grad.setAttribute('x2','0'); grad.setAttribute('y2','1');
  var s1 = document.createElementNS(ns,'stop'); s1.setAttribute('offset','0%'); s1.setAttribute('stop-color', accent); s1.setAttribute('stop-opacity','0.35');
  var s2 = document.createElementNS(ns,'stop'); s2.setAttribute('offset','100%'); s2.setAttribute('stop-color', accent); s2.setAttribute('stop-opacity','0');
  grad.appendChild(s1); grad.appendChild(s2); defs.appendChild(grad); svg.appendChild(defs);
  var area = document.createElementNS(ns,'path');
  area.setAttribute('d', areaPts); area.setAttribute('fill', 'url(#'+gradId+')');
  svg.appendChild(area);
  var poly = document.createElementNS(ns,'polyline');
  poly.setAttribute('points', linePts); poly.setAttribute('fill','none');
  poly.setAttribute('stroke', accent); poly.setAttribute('stroke-width','2');
  poly.setAttribute('stroke-linejoin','round'); poly.setAttribute('stroke-linecap','round');
  svg.appendChild(poly);
  var dot = document.createElementNS(ns,'circle');
  dot.setAttribute('cx', x(n-1)); dot.setAttribute('cy', y(values[n-1])); dot.setAttribute('r','3.5');
  dot.setAttribute('fill', accent);
  svg.appendChild(dot);
  if (nowId){
    var el = document.getElementById(nowId);
    if (el) el.textContent = fmt(values[n-1]);
  }
}
function farolPace(v){ var m=Math.floor(v), s=Math.round((v-m)*60); if(s===60){m++;s=0;} return m+':' + String(s).padStart(2,'0')+'/km'; }
function farolSvg(tag, attrs){ var e=document.createElementNS('http://www.w3.org/2000/svg',tag); Object.keys(attrs||{}).forEach(function(k){e.setAttribute(k,attrs[k]);}); return e; }
function farolPerformance(points, prediction){
  var line=document.getElementById('performance-pace'), scatter=document.getElementById('performance-hr');
  if(!line || !scatter || !points.length) return;
  var css=getComputedStyle(document.documentElement), accent=css.getPropertyValue('--accent').trim(), ink=css.getPropertyValue('--ink-dim').trim(), race='#a3701c', active='#2f7d5b';
  var W=520,H=240,p={l:42,r:14,t:18,b:28}, pace=points.map(function(x){return x.pace;}), lo=Math.min.apply(null,pace), hi=Math.max.apply(null,pace);
  if(prediction){lo=Math.min(lo,prediction.min);hi=Math.max(hi,prediction.max);} var span=(hi-lo)||1; lo-=span*.15;hi+=span*.15;
  var x=function(i){return p.l+(W-p.l-p.r)*(points.length===1?0:i/(points.length-1));}; var y=function(v){return p.t+(H-p.t-p.b)*(v-lo)/(hi-lo);};
  [0,.5,1].forEach(function(q){var yy=p.t+(H-p.t-p.b)*q;line.appendChild(farolSvg('line',{x1:p.l,x2:W-p.r,y1:yy,y2:yy,stroke:ink,'stroke-width':.5,opacity:.35}));line.appendChild(Object.assign(farolSvg('text',{x:2,y:yy+4,fill:ink,'font-size':10}),{textContent:farolPace(lo+(hi-lo)*q)}));});
  if(prediction){var top=y(prediction.min), bottom=y(prediction.max);line.appendChild(farolSvg('rect',{x:p.l,y:top,width:W-p.l-p.r,height:Math.max(2,bottom-top),fill:accent,opacity:.12}));var label=farolSvg('text',{x:W-p.r,y:top-4,fill:accent,'font-size':10,'text-anchor':'end'});label.textContent='faixa prevista 21,1 km';line.appendChild(label);}
  if(points.length>1){var meanX=(points.length-1)/2,meanY=pace.reduce(function(a,b){return a+b;},0)/points.length, num=0,den=0;points.forEach(function(v,i){num+=(i-meanX)*(v.pace-meanY);den+=(i-meanX)*(i-meanX);});var slope=den?num/den:0;line.appendChild(farolSvg('line',{x1:x(0),x2:x(points.length-1),y1:y(meanY-slope*meanX),y2:y(meanY+slope*((points.length-1)-meanX)),stroke:ink,'stroke-width':1.5,'stroke-dasharray':'4 3',opacity:.8}));}
  points.forEach(function(point,i){var c=point.race?race:(point.scope==='segmentos ativos'?active:accent), dot=farolSvg('circle',{cx:x(i),cy:y(point.pace),r:point.race?5:3.5,fill:c});var title=farolSvg('title');title.textContent=point.date+' · '+point.name+' · '+farolPace(point.pace)+' · '+point.scope+(point.hr?' · '+Math.round(point.hr)+' bpm':'');dot.appendChild(title);line.appendChild(dot);});
  var hp=points.filter(function(x){return x.hr!==null;}); if(!hp.length) return; var hrs=hp.map(function(x){return x.hr;}), hmin=Math.min.apply(null,hrs),hmax=Math.max.apply(null,hrs), hspan=(hmax-hmin)||1;hmin-=hspan*.12;hmax+=hspan*.12; var sx=function(v){return p.l+(W-p.l-p.r)*(v-hmin)/(hmax-hmin);};
  [0,.5,1].forEach(function(q){var xx=p.l+(W-p.l-p.r)*q;scatter.appendChild(farolSvg('line',{x1:xx,x2:xx,y1:p.t,y2:H-p.b,stroke:ink,'stroke-width':.5,opacity:.35}));var t=farolSvg('text',{x:xx,y:H-8,fill:ink,'font-size':10,'text-anchor':'middle'});t.textContent=Math.round(hmin+(hmax-hmin)*q)+' bpm';scatter.appendChild(t);});
  hp.forEach(function(point){var c=point.race?race:(point.scope==='segmentos ativos'?active:accent),dot=farolSvg('circle',{cx:sx(point.hr),cy:y(point.pace),r:point.race?5:3.5,fill:c});var title=farolSvg('title');title.textContent=point.date+' · '+farolPace(point.pace)+' · '+Math.round(point.hr)+' bpm · '+point.scope;dot.appendChild(title);scatter.appendChild(dot);});
}
function farolNutrition(defaults){
  var ids=['nut-distance','nut-duration','nut-start','nut-carbs','nut-interval','nut-caffeine','nut-pre','nut-cutoff','nut-coffee']; var get=function(id){return document.getElementById(id);};
  function draw(){var duration=Number(get('nut-duration').value)||1, carbs=Number(get('nut-carbs').value)||0, interval=Math.max(1,Number(get('nut-interval').value)||30), pre=Math.max(0,Number(get('nut-pre').value)||0), cutoff=Math.max(0,Number(get('nut-cutoff').value)||0), cafeAt=Math.max(0,Number(get('nut-caffeine').value)||0), start=get('nut-start').value, events=[[-pre,'Gel regular',carbs,0]], used=false; for(var minute=interval;minute<=duration-cutoff;minute+=interval){var cafe=!used&&minute>=cafeAt;events.push([minute,cafe?'Ultra Gel':'Gel regular',carbs,cafe?defaults.gel_caffeine_mg:0]);used=used||cafe;} var total=events.reduce(function(s,e){return s+e[2];},0), caffeine=events.reduce(function(s,e){return s+e[3];},0), base=start?new Date('2000-01-01T'+start+':00'):null; get('nutrition-summary').textContent=events.length+' gel(is) · '+total.toFixed(0)+' g carboidrato · '+(total/duration*60).toFixed(1)+' g/h · '+caffeine.toFixed(0)+' mg cafeina'; get('nutrition-body').innerHTML=events.map(function(e){var clock='—';if(base){var d=new Date(base.getTime()+e[0]*60000);clock=String(d.getHours()).padStart(2,'0')+':'+String(d.getMinutes()).padStart(2,'0');}return '<tr><td>'+ (e[0]<0?Math.abs(e[0])+' min antes':e[0]+' min')+'</td><td>'+clock+'</td><td>'+e[1]+'</td><td class="num">'+e[2].toFixed(0)+' g</td><td class="num">'+e[3].toFixed(0)+' mg</td><td>agua junto</td></tr>';}).join(''); get('nutrition-coffee').textContent=get('nut-coffee').checked?'Cafe opcional antes: apenas se ja e habitual e tolerado.':'Sem cafe no planejamento.';}
  ids.forEach(function(id){var e=get(id);if(e)e.addEventListener('input',draw);if(e)e.addEventListener('change',draw);});draw();
}
"""


def _hero_html(ctx: dict) -> str:
    profile = ctx["profile"]
    athlete = profile.get("athlete") or {}
    idade = _idade(athlete.get("birth_date"), ctx["on_date"])
    dias_prova = ctx["prova"]["dias"] if ctx["prova"] else None
    nome = athlete.get("name") or "atleta"
    return f"""
<header class="hero"><div class="wrap">
  <div class="eyebrow">Farol</div>
  <h1 class="title">Estado do atleta</h1>
  <p class="subtitle">Recuperacao, carga de treino e previsao de prova de {_esc(nome)}, a partir dos seus dados do Garmin -- comparado sempre contra o seu proprio padrao, nunca uma populacao generica.</p>
  <div class="hero-meta">
    <div><div class="k">Atleta</div><div class="v" style="font-size:14px;font-family:'Public Sans';font-weight:600;">{_esc(nome)}</div></div>
    <div><div class="k">Data</div><div class="v">{_esc(ctx['on_date'])}</div></div>
    <div><div class="k">Idade</div><div class="v">{_esc(idade) if idade is not None else '—'}</div></div>
    <div><div class="k">Prova em</div><div class="v">{_esc(dias_prova) + ' dias' if dias_prova is not None else '—'}</div></div>
  </div>
</div></header>
"""


def _resumo_html(ctx: dict) -> str:
    indice = ctx["indice"]
    if indice is not None:
        tile_recuperacao = (f'<div class="tile"><div class="k">Indice de recuperacao</div>'
                             f'<div class="v">{indice:g}<span class="u">/10</span></div>'
                             f'<div class="sub">{_esc(recovery_label(indice))}</div></div>')
    else:
        tile_recuperacao = ('<div class="tile"><div class="k">Indice de recuperacao</div>'
                             '<div class="v">—</div><div class="sub">sem dado suficiente</div></div>')

    race = ctx["race"]
    if race and race.get("predicao"):
        p = race["predicao"]
        tile_prova = (f'<div class="tile"><div class="k">{_esc(race["race"].get("name", "Prova"))}</div>'
                      f'<div class="v" style="font-size:22px;">{race_prediction._fmt_tempo(p["min"])}'
                      f'&ndash;{race_prediction._fmt_tempo(p["max"])}</div>'
                      f'<div class="sub">Riegel/Cameron/VDOT sobre {_esc(race["ancora"]["date"])}</div></div>')
    else:
        tile_prova = ('<div class="tile"><div class="k">Previsao de prova</div>'
                      '<div class="v">—</div><div class="sub">sem prova observada declarada</div></div>')

    tsb_m = ctx["metricas"].get("tsb")
    tile_tsb = (f'<div class="tile"><div class="k">TSB hoje</div>'
                f'<div class="v">{tsb_m.value:+.1f}</div>'
                f'<div class="sub">alvo conceitual p/ prova: positivo</div></div>'
                if tsb_m and tsb_m.has_data else
                '<div class="tile"><div class="k">TSB hoje</div><div class="v">—</div>'
                '<div class="sub">sem dado</div></div>')

    atividades = ctx["atividades"]
    if atividades:
        primeira = atividades[0]
        km = f"{primeira['distance_km']:.2f} km" if primeira.get("distance_km") else ""
        tile_treino = (f'<div class="tile"><div class="k">Treino de hoje</div>'
                       f'<div class="v" style="font-size:19px;">{_esc(km) or _esc(primeira.get("type") or "atividade")}</div>'
                       f'<div class="sub">{_esc(today.resumo_atividades(atividades))}</div></div>')
    else:
        tile_treino = ('<div class="tile"><div class="k">Treino de hoje</div>'
                       '<div class="v" style="font-size:19px;">—</div><div class="sub">nenhum registrado</div></div>')

    domains_html = ""
    leitura_html = ""
    if ctx["state"]:
        blocos = []
        for nome, d in ctx["state"]["dominios"].items():
            rotulo, emoji, cls = _LABEL_DOMINIO[d["status"]]
            streak = d.get("streak") or {"dias": 0}
            streak_txt = f"{streak['dias']} dia(s) de streak" if streak["dias"] else "—"
            blocos.append(
                f'<div class="domain"><div><div class="name">{nome.capitalize()}</div>'
                f'<div class="streak">{_esc(streak_txt)}</div></div>'
                f'<span class="pill {cls}">{emoji} {rotulo}</span></div>'
            )
        domains_html = f'<div class="domains">{"".join(blocos)}</div>'

    if ctx["leitura"]:
        leitura = ctx["leitura"]
        leitura_html = f"""
    <div class="card" style="margin-top:16px;">
      <blockquote class="read">&ldquo;{_esc(leitura['estado_fisiologico'])}. Sinal dominante: {_esc(leitura['sinal_dominante'])}.
      Convergencia: {_esc(leitura['convergencia'])}. {_esc(leitura['conclusao'])}.&rdquo;</blockquote>
      <div style="font-size:11.5px;color:var(--ink-dim);margin-top:8px;">&mdash; farol.interpretation, sobre a classificacao de state.gather(). Descreve; nao prescreve treino.</div>
    </div>"""

    return f"""
<section>
  <div class="section-head"><h2>Voce, agora</h2><div class="section-note">Indice de recuperacao, previsao de prova e carga.</div></div>
  <div class="tiles">{tile_recuperacao}{tile_prova}{tile_tsb}{tile_treino}</div>
  {domains_html}
  {leitura_html}
</section>
"""


def _prontidao_html(ctx: dict) -> str:
    race = ctx["race"]
    if not race:
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


def _desempenho_html(ctx: dict) -> str:
    points = ctx["performance_series"]
    if not points:
        return ""
    return """
<section>
  <div class="section-head"><h2>Desempenho recente</h2><div class="section-note">90 dias de corridas; passe o cursor pelos pontos para ler a sessao.</div></div>
  <div class="performance-grid">
    <div class="card"><div class="form-label">Pace ao longo das sessoes</div><svg id="performance-pace" viewBox="0 0 520 240" role="img" aria-label="Pace por sessao"></svg></div>
    <div class="card"><div class="form-label">Pace x frequencia cardiaca</div><svg id="performance-hr" viewBox="0 0 520 240" role="img" aria-label="Pace por frequencia cardiaca"></svg></div>
  </div>
  <div class="legend"><span><i style="background:var(--accent)"></i>sessao corrida</span><span><i style="background:var(--ok)"></i>segmentos ativos Garmin</span><span><i style="background:var(--warn)"></i>prova declarada</span><span>linha tracejada: tendencia linear</span></div>
  <div class="callout" style="margin-top:14px;"><span class="tag tag-info">Regra explicita</span><h3>Caminhadas e recuperacoes nao entram na analise dos tiros.</h3><p>Quando existem splits tipados, o ponto usa exclusivamente os segmentos <code class="mono">INTERVAL_ACTIVE</code> que o Garmin marcou como trabalho. Aquecimento, recuperacao e caminhada ficam fora. Sem splits tipados, o tooltip identifica o valor como sessao corrida.</p></div>
</section>
"""


def _nutricao_html(ctx: dict) -> str:
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
    <p class="notice">Fonte gravavel: <code class="mono">python -m farol.injuries add --area "area" --pain 0 --swelling nenhum --shoe "{_esc(shoe_hint)}" --walk sim --run sim</code>. Acrescente <code class="mono">--worse</code> se houve piora.</p>
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
        f'python -m farol.race_log add --date {race_date} '
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


def _charts_html(ctx: dict) -> str:
    return """
<section>
  <div class="section-head"><h2>Ultimos 30 dias</h2><div class="section-note">Linha tracejada = sua media movel de 90 dias (o "normal" pessoal).</div></div>
  <div class="chart-grid">
    <div class="chart-card"><div class="head"><div class="title">HRV</div><div class="now" id="now-hrv"></div></div><svg id="c-hrv" viewBox="0 0 280 90"></svg></div>
    <div class="chart-card"><div class="head"><div class="title">FC de repouso</div><div class="now" id="now-rhr"></div></div><svg id="c-rhr" viewBox="0 0 280 90"></svg></div>
    <div class="chart-card"><div class="head"><div class="title">Sleep score</div><div class="now" id="now-sleep"></div></div><svg id="c-sleep" viewBox="0 0 280 90"></svg></div>
    <div class="chart-card"><div class="head"><div class="title">TSB</div><div class="now" id="now-tsb"></div></div><svg id="c-tsb" viewBox="0 0 280 90"></svg></div>
  </div>
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
    linhas = "".join(_metric_row_html(c, m[c], unidades.get(c, "")) for c in _METRICAS_RECUPERACAO if c in m)
    cv = ctx["hrv_cv"]
    cv_txt = ""
    if cv is not None:
        ref = f'{cv["cv_30d"]:.1f}%' if cv["cv_30d"] is not None else "sem dado"
        cv_txt = (f'<div style="font-size:12px;color:var(--ink-dim);margin-top:10px;">'
                  f'HRV CV {cv["cv_7d"]:.1f}% (7d) &middot; referencia 30d {ref}</div>')
    return f"""
<section>
  <div class="section-head"><h2>Recuperacao</h2><div class="section-note">z-score contra sua janela movel de 90 dias.</div></div>
  <div class="card"><div class="metric-grid">{linhas}</div>{cv_txt}</div>
</section>
"""


def _sono_html(ctx: dict) -> str:
    m = ctx["metricas"]
    campos = ("sleep_score", "total_sleep_minutes", "avg_sleep_stress")
    unidades = {"sleep_score": "/100", "total_sleep_minutes": " min", "avg_sleep_stress": ""}
    linhas = "".join(_metric_row_html(c, m[c], unidades.get(c, "")) for c in campos if c in m)
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
<section>
  <div class="section-head"><h2>Calibracao automatica do modelo</h2></div>
  <div class="card"><pre class="report">{_esc(ctx['calibration_md'].strip())}</pre></div>
</section>
"""


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
      <div style="font-family:'IBM Plex Mono';font-size:14px;">{_esc(athlete.get('name', '—'))}</div></div>
      <div><div class="k" style="font-size:11px;text-transform:uppercase;color:var(--ink-dim);">Altura</div>
      <div style="font-family:'IBM Plex Mono';font-size:14px;">{_esc(athlete.get('height_cm', '—'))} cm</div></div>
      <div><div class="k" style="font-size:11px;text-transform:uppercase;color:var(--ink-dim);">Prova-alvo</div>
      <div style="font-family:'IBM Plex Mono';font-size:14px;">{_esc(race.get('name', '—'))} ({_esc(race.get('date', '—'))})</div></div>
    </div>
  </div>
  <div class="card"><div class="badge-row">{zonas}</div></div>
</section>
"""


def render_html(ctx: dict) -> str:
    sparklines = ctx["sparklines"]
    race_data = ctx["race"] or {}
    race_prediction_data = race_data.get("predicao")
    race_distance = race_data.get("alvo_km")
    prediction_pace = ({
        "min": race_prediction_data["min"] / race_distance,
        "max": race_prediction_data["max"] / race_distance,
    } if race_prediction_data and race_distance else None)
    dados_js = json.dumps({
        "hrv": sparklines["hrv_avg"], "rhr": sparklines["rhr"],
        "sleep": sparklines["sleep_score"], "tsb": sparklines["tsb"],
        "baselines": {
            "hrv": ctx["metricas"]["hrv_avg"].mean_90d if ctx["metricas"]["hrv_avg"].has_data else None,
            "rhr": ctx["metricas"]["rhr"].mean_90d if ctx["metricas"]["rhr"].has_data else None,
            "sleep": ctx["metricas"]["sleep_score"].mean_90d if ctx["metricas"]["sleep_score"].has_data else None,
            "tsb": ctx["metricas"]["tsb"].mean_90d if ctx["metricas"]["tsb"].has_data else None,
        },
        "performance": ctx["performance_series"],
        "nutrition": ctx["nutrition_config"],
        # O eixo do grafico e pace; tempo total de prova seria uma unidade
        # incompativel e destruiria a escala. A faixa e convertida uma unica
        # vez aqui, da previsao oficial do modulo race_prediction.
        "prediction": prediction_pace,
    })
    script = (
        "<script>" + _SCRIPT_RUNTIME +
        f"var FAROL_DATA = {dados_js};\n"
        "farolSpark('c-hrv', FAROL_DATA.hrv, FAROL_DATA.baselines.hrv, function(v){return v.toFixed(0)+' ms';}, 'now-hrv');\n"
        "farolSpark('c-rhr', FAROL_DATA.rhr, FAROL_DATA.baselines.rhr, function(v){return v.toFixed(0)+' bpm';}, 'now-rhr');\n"
        "farolSpark('c-sleep', FAROL_DATA.sleep, FAROL_DATA.baselines.sleep, function(v){return v.toFixed(0);}, 'now-sleep');\n"
        "farolSpark('c-tsb', FAROL_DATA.tsb, FAROL_DATA.baselines.tsb, function(v){return (v>0?'+':'')+v.toFixed(1);}, 'now-tsb');\n"
        "farolPerformance(FAROL_DATA.performance, FAROL_DATA.prediction);\n"
        "farolNutrition(FAROL_DATA.nutrition);\n"
        "</script>"
    )
    corpo = "".join([
        _hero_html(ctx),
        '<div class="wrap">',
        _resumo_html(ctx),
        _prontidao_html(ctx),
        _charts_html(ctx),
        _desempenho_html(ctx),
        _carga_html(ctx),
        _recuperacao_html(ctx),
        _sono_html(ctx),
        _prova_html(ctx),
        _nutricao_html(ctx),
        _lesoes_html(ctx),
        _registro_pos_prova_html(ctx),
        _analises_html(ctx),
        _perfil_html(ctx),
        f'<footer>Gerado em {_esc(ctx["on_date"])} por <code class="mono">python -m farol.dashboard</code>. '
        "Le apenas o banco SQLite local; nao consulta Garmin nem clima durante a geracao. "
        "A atualizacao Garmin e separada via farol.ingest; clima do percurso usa consulta externa. "
        "Descreve o que os dados mostram; nao prescreve treino nem diagnostica (PROFILE.md, regras 7 e 9).</footer>",
        "</div>",
    ])
    return _HEAD + corpo + script


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
