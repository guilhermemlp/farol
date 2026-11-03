"""Predicao de prova: estima o tempo de uma distancia-alvo (a meia de
2026-09-19, no perfil) a partir de provas observadas recentes.

Capacidade F1 da auditoria de 2026-09-04 (docs/auditoria_v1.md, secao 6):
"Predicao de prova (VDOT de Jack Daniels a partir de provas recentes +
modelo de Cameron para 21,1 km; ou regressao de pace ~ distancia do proprio
historico). A meia e 19/09 -- 15 dias; mostrar pace-alvo e TSB recomendado
para o dia (coach-alvo: TSB positivo no race day)".

TRES MODELOS independentes, nao um so (mesma etica de `farol.correlations`:
mostrar mais de um juiz em vez de fingir precisao):

1. Riegel (1981): T2 = T1 x (D2/D1)^1.06. Lei de potencia com expoente de
   fadiga unico, derivado de recordes mundiais de 3,5 min a 3h50. O 1.06 e o
   padrao recreacional (elite ~1.05, menos treinado ~1.08).
2. Cameron (1999): T2 = (T1/D1) x D2 x (f(D1)/f(D2)), com f(x) =
   13.49681 - 0.000030363x + 835.7114 / x^0.7905 (x em metros). Ajuste de
   fadiga especifico por distancia, calibrado contra os melhores tempos
   mundiais das distancias; tende a ser mais conservador que Riegel em
   distancias longas.
3. VDOT (Daniels/Gilbert): converte a prova observada num equivalente de
   VO2max (VDOT), depois acha o tempo na distancia-alvo que mantem o mesmo
   VDOT. Formula: VO2 = -4.60 + 0.182258*S + 0.000104*S^2 (S em m/min,
   custo de oxigenio da corrida) e %VO2max sustentavel =
   0.8 + 0.1894393*e^(-0.012778*t) + 0.2989558*e^(-0.1932605*t) (t em min).

Deliberadamente uma ESTIMATIVA, nunca prescricao (PROFILE.md, regras 7 e 9):
o farol mostra o pace-alvo e o estado de TSB para o dia, e para por ai --
periodizacao e do treinador. O relatorio separa o que e dado observado (a
prova no banco), o que e modelo (as tres formulas acima) e o que sera
validado contra o resultado real de 19/09 (a propria secao A VALIDAR).

Uso: python -m farol.race_prediction [--out CAMINHO]
Sem --out, grava em reports/RACE_PREDICTION.md.
"""
import argparse
import math
from datetime import date as _date

from . import db, injuries, nutrition, race_log
from .config import REPORTS_DIR, load_profile


# ------------------------------------------------------------------- modelos
# Cada funcao de tempo e pura e recebe distancia em km e tempo em minutos --
# nada de banco, nada de perfil: facil de testar contra valores publicados.

# Riegel: expoente de fadiga padrao recreacional. Elite tende a 1.05, menos
# treinado a 1.08 (runplanner/rundida). Sem calibracao no historico proprio
# deste atleta (faltam 2+ provas em distancias diferentes para ajustar k por
# regressao), o padrao 1.06 fica explicito e documentado como populacional.
_RIEGEL_K = 1.06

# Cameron: constantes do ajuste de fadiga por distancia (x em metros).
def _cameron_f(x_m: float) -> float:
    return 13.49681 - 0.000030363 * x_m + 835.7114 / (x_m ** 0.7905)


def riegel_time(d1_km: float, t1_min: float, d2_km: float,
                k: float = _RIEGEL_K) -> float:
    """Tempo predito por Riegel: T2 = T1 x (D2/D1)^k."""
    return t1_min * (d2_km / d1_km) ** k


def cameron_time(d1_km: float, t1_min: float, d2_km: float) -> float:
    """Tempo predito por Cameron: T2 = (T1/D1) x D2 x (f(D1)/f(D2))."""
    d1_m, d2_m = d1_km * 1000, d2_km * 1000
    fator = _cameron_f(d1_m) / _cameron_f(d2_m)
    return (t1_min / d1_km) * d2_km * fator


def vdot_from_time(d_km: float, t_min: float) -> float:
    """VDOT (Daniels/Gilbert) equivalente a correr d_km em t_min.

    Usa o custo de oxigenio da corrida (S em m/min) dividido pela fracao de
    VO2max sustentavel no tempo t (min). Prova de 10,03 km em 59,4 min ->
    VDOT ~32,8 (validado contra tabelas publicas)."""
    s = d_km * 1000 / t_min
    vo2 = -4.60 + 0.182258 * s + 0.000104 * s * s
    fracao = (0.8 + 0.1894393 * math.exp(-0.012778 * t_min)
              + 0.2989558 * math.exp(-0.1932605 * t_min))
    return vo2 / fracao


def vdot_time(vdot: float, d_km: float) -> float:
    """Tempo predito para manter `vdot` em `d_km` (minutos).

    Busca binaria no tempo: vdot_from_time cai monotonicamente com o tempo
    (mais lento = menor exigencia), entao converge na hora. Intervalo generoso
    de 1 min a 600 min cobre de 1 km a maratona."""
    lo, hi = 1.0, 600.0
    for _ in range(200):
        mid = (lo + hi) / 2
        if vdot_from_time(d_km, mid) > vdot:
            lo = mid  # tempo rapido demais para o VDOT: sobe o tempo
        else:
            hi = mid
    return (lo + hi) / 2


def predict(d1_km: float, t1_min: float, d2_km: float) -> dict:
    """Predicoes dos tres modelos para correr d2_km a partir da prova
    observada (d1_km, t1_min). Devolve tempos em minutos e o VDOT implicado
    pela prova. A faixa [min, max] entre os tres e' a incerteza do metodo --
    quando os modelos concordam, o numero e' mais confiavel."""
    r = riegel_time(d1_km, t1_min, d2_km)
    c = cameron_time(d1_km, t1_min, d2_km)
    v = vdot_time(vdot_from_time(d1_km, t1_min), d2_km)
    return {"riegel": r, "cameron": c, "vdot": v,
            "min": min(r, c, v), "max": max(r, c, v)}


# ------------------------------------------------------------------ formatacao

def _fmt_tempo(minutos: float) -> str:
    """minutos -> 'h:mm:ss' quando >= 1h, senao 'mm:ss'."""
    total_s = round(minutos * 60)
    h, rem = divmod(total_s, 3600)
    m, s = divmod(rem, 60)
    if h:
        return f"{h}:{m:02d}:{s:02d}"
    return f"{m}:{s:02d}"


def _fmt_pace(min_km: float) -> str:
    """min/km -> 'm:ss / km'."""
    m = int(min_km)
    s = round((min_km - m) * 60)
    if s == 60:
        m, s = m + 1, 0
    return f"{m}:{s:02d} /km"


# ------------------------------------------------------------------ leitura

def reference_races(profile: dict) -> list[dict]:
    """Provas observadas declaradas no perfil (data + nome apenas). O codigo
    NAO duplica distancia/duracao aqui -- le da tabela activities, que e a
    fonte de verdade gravada pelo ingest a partir do Garmin."""
    return list((profile.get("recent_races") or []))


def _activity_for_date(conn, on_date: str) -> dict | None:
    row = conn.execute(
        "SELECT date, name, type, distance_km, duration_min, avg_hr, max_hr "
        "FROM activities WHERE date = ? AND type = 'running' AND "
        "distance_km IS NOT NULL AND duration_min IS NOT NULL "
        "ORDER BY duration_min ASC LIMIT 1",
        (on_date,),
    ).fetchone()
    return dict(row) if row else None


def observed_performances(conn, dates: list[str]) -> list[dict]:
    """Cada prova declarada no perfil, materializada com os dados reais do
    banco (distance_km, duration_min). Entradas sem corrida correspondente no
    banco sao ignoradas -- o relatorio diz o que faltou, nao inventa."""
    saida = []
    for d in dates:
        act = _activity_for_date(conn, d)
        if act is None:
            continue
        saida.append({
            "date": d,
            "name": act["name"],
            "distance_km": act["distance_km"],
            "duration_min": act["duration_min"],
            "avg_hr": act["avg_hr"],
            "max_hr": act["max_hr"],
        })
    return saida


def tsb_mais_recente(conn, antes_de: str | None = None) -> dict | None:
    """Ultima linha de training_load com TSB (contexto do dia da predicao).
    O alvo de planejamento para um dia de prova e TSB positivo (descansado) --
    aqui so mostramos o valor observado e essa meta conceitual, sem prescrever
    treino (PROFILE.md, regra 9)."""
    sql = ("SELECT date, tsb FROM training_load WHERE tsb IS NOT NULL ")
    args: list = []
    if antes_de:
        sql += "AND date <= ? "
        args.append(antes_de)
    sql += "ORDER BY date DESC LIMIT 1"
    row = conn.execute(sql, args).fetchone()
    return dict(row) if row else None


def build_report(conn, profile: dict, on_date: str | None = None) -> str:
    """Relatorio markdown da predicao para a prova-alvo do perfil, separando:
    OBSERVADO (provas usadas, do banco), ESTIMATIVA (tres modelos + faixa +
    TSB para o dia), A VALIDAR (o que sera comparado contra o resultado real
    de 19/09)."""
    hoje = _date.today().isoformat() if on_date is None else on_date
    alvo = profile.get("race") or {}
    alvo_km = alvo.get("distance_km")
    alvo_nome = alvo.get("name", "prova")
    alvo_data = alvo.get("date")
    dias_ate = None
    if alvo_data:
        try:
            dias_ate = (_date.fromisoformat(alvo_data) - _date.fromisoformat(hoje)).days
        except ValueError:
            dias_ate = None

    linhas = [f"# PREDICAO DE PROVA — {alvo_nome} ({alvo_km} km)", "",
              f"Gerado em: {hoje}. Alvo: {alvo_data}"
              + (f" ({dias_ate} dias)" if dias_ate is not None else "")
              + ". Metodo: Riegel/Cameron/VDOT sobre provas observadas.",
              "",
              "## O QUE E DADO OBSERVADO", ""]

    datas = [r["date"] for r in reference_races(profile)]
    provas = observed_performances(conn, datas)
    if not provas:
        linhas += [
            "Nenhuma prova observada no banco para as datas declaradas em "
            "`profile.yaml -> recent_races`.", "",
            "Sem ancora observada, nao ha predicao -- o farol nao chuta um "
            "tempo do nada. Rode `python -m farol.ingest` e confira as datas "
            "em `data/profile.yaml`.", ""]
    else:
        linhas.append("| Data | Prova | Distancia | Tempo | Pace | FC media |")
        linhas.append("|------|-------|-----------|-------|------|----------|")
        for p in provas:
            pace = p["duration_min"] / p["distance_km"]
            linhas.append(
                f"| {p['date']} | {p['name']} | {p['distance_km']:.2f} km | "
                f"{_fmt_tempo(p['duration_min'])} | {_fmt_pace(pace)} | "
                f"{p['avg_hr'] if p['avg_hr'] else '—'} bpm |")
        faltaram = [d for d in datas
                    if not any(p["date"] == d for p in provas)]
        if faltaram:
            linhas.append("")
            linhas.append(f"Sem corrida no banco nas datas declaradas: "
                          f"{', '.join(faltaram)} (ignoradas).")
        linhas.append("")
        linhas.append("Fonte: tabela `activities` (gravada pelo ingest a "
                      "partir do Garmin). So entram aqui provas de verdade, "
                      "declaradas em `profile.yaml -> recent_races`.")

    if provas and alvo_km:
        # Ancora = prova mais recente (forma atual chega primeiro que a melhor
        # historica -- mesma logica de baseline movel do farol).
        ancora = max(provas, key=lambda p: p["date"])
        linhas += ["", "## ESTIMATIVA", ""]
        linhas.append(f"Ancora: {ancora['name']} ({ancora['date']}, "
                      f"{ancora['distance_km']:.2f} km em "
                      f"{_fmt_tempo(ancora['duration_min'])})."[:120])
        linhas.append("")
        linhas.append("Tres modelos independentes (detalhe das formulas no "
                      "cabecalho deste arquivo); a faixa entre eles e' a "
                      "incerteza do metodo:")
        linhas.append("")
        linhas.append("| Modelo | Tempo predito | Pace |")
        linhas.append("|--------|---------------|------|")
        p = predict(ancora["distance_km"], ancora["duration_min"], alvo_km)
        for nome, chave in (("Riegel", "riegel"), ("Cameron", "cameron"),
                            ("VDOT (Daniels)", "vdot")):
            t = p[chave]
            linhas.append(f"| {nome} | {_fmt_tempo(t)} | {_fmt_pace(t / alvo_km)} |")
        linhas.append(f"| **Faixa** | **{_fmt_tempo(p['min'])} – {_fmt_tempo(p['max'])}** | "
                      f"**{_fmt_pace(p['min'] / alvo_km)} – {_fmt_pace(p['max'] / alvo_km)}** |")
        linhas.append("")
        vd = vdot_from_time(ancora["distance_km"], ancora["duration_min"])
        linhas.append(f"VDOT implicado pela ancora: {vd:.1f}.")
        linhas.append("")
        tsb = tsb_mais_recente(conn, hoje)
        if tsb is not None:
            meta = "positivo (descansado)" if tsb["tsb"] >= 0 else "positivo (hoje nao esta)"
            linhas.append(
                f"TSB para o dia: **{tsb['tsb']:+.1f}** (em {tsb['date']}). "
                f"Alvo de planejamento conceitual para dia de prova: TSB "
                f"{meta} -- decisao de como chegar la e' do treinador "
                f"(PROFILE.md, regra 9). O farol so mostra o estado, nao "
                f"prescreve treino.")
        else:
            linhas.append("TSB: sem dado em `training_load` para mostrar "
                          "contexto do dia.")
        linhas += ["", "## A VALIDAR", "",
                   f"Comparar o resultado real da {alvo_nome} de {alvo_data} "
                   f"contra a faixa acima:",
                   "- dentro da faixa -> modelo consistente com a prova real;",
                   "- acima do teto -> fadiga aguda, percurso/condicoes mais "
                   f"dificeis, ou forma abaixo da ancora de {ancora['date']};",
                   "- abaixo do piso -> forma melhor que a ancora (recalibrar).",
                   "",
                   "Depois da prova, adicionar a data em "
                   "`profile.yaml -> recent_races` e regerar este relatorio: a "
                   "nova prova vira ancora e o loop de validacao fecha "
                   "(mesma ideia do loop de calibracao de `farol.calibration`).", ""]
    elif provas and not alvo_km:
        linhas += ["", "## ESTIMATIVA", "",
                   "Perfil sem `race.distance_km` definido - nada a predizer.", ""]

    if provas and alvo_km:
        plan = nutrition.build_plan(
            (p["min"] + p["max"]) / 2, nutrition.config_from_profile(profile),
            (alvo or {}).get("start_time"))
        linhas += ["## NUTRICAO CONFIGURAVEL", "",
                   "Plano por tempo estimado; use apenas produtos e estrategia ja tolerados. Cada gel tem lembrete de agua.",
                   "", "| Momento | Produto | Carboidrato | Cafeina |",
                   "|---------|----------|-------------|---------|"]
        for event in plan["events"]:
            when = event["label"] + (f" ({event['clock']})" if event["clock"] else "")
            linhas.append(f"| {when} | {event['type']} | {event['carbs_g']:.0f} g | {event['caffeine_mg']:.0f} mg |")
        linhas += ["", f"Total: {plan['total_carbs_g']:.0f} g de carboidrato ({plan['carbs_per_hour']:.1f} g/h) e {plan['total_caffeine_mg']:.0f} mg de cafeina em gel."]
        if plan["coffee_before_race"]:
            linhas.append("Cafe antes: opcional, somente se ja e habitual e tolerado; a dose nao e estimada pelo Farol.")
        entry = injuries.latest_observation(hoje)
        alert = injuries.observation_alert(entry)
        linhas += ["", "## OBSERVACAO MANUAL: LESOES", "", injuries.observation_text(entry),
                   "", "Fora da estimativa de tempo, TSB e modelos de prova."]
        if alert:
            linhas += ["", f"**Alerta do registro:** {alert}"]

    if alvo_data and str(alvo_data)[:10] <= hoje:
        relato = race_log.entry_as_of(str(alvo_data)[:10], hoje)
        linhas += ["", "## REGISTRO POS-PROVA", "", race_log.summary(relato),
                   "", "Contexto manual da prova. Nao entra em TSB, carga, recuperacao, "
                   "classificacao ou nos modelos de previsao."]

    return "\n".join(linhas) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Estima o tempo da prova-alvo a partir de provas observadas recentes.")
    parser.add_argument("--out", default=str(REPORTS_DIR / "RACE_PREDICTION.md"),
                        help="Caminho de saida")
    args = parser.parse_args()

    try:
        profile = load_profile()
    except FileNotFoundError as exc:
        print(exc)
        raise SystemExit(1)

    conn = db.connect_readonly()
    try:
        texto = build_report(conn, profile)
    finally:
        conn.close()

    with open(args.out, "w", encoding="utf-8") as f:
        f.write(texto)
    print(f"Predicao gravada em {args.out}")


if __name__ == "__main__":
    main()
