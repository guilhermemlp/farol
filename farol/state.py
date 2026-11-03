"""CLI: gera o resumo de 1 pagina do estado do atleta a partir de
farol_daily_state, seguindo o "Fluxo de Interpretacao" do MODEL_V1.0.md:

    1. Para cada dominio, monta a Metric (farol.metrics) de cada campo contra
       a janela movel de 90 dias do proprio atleta -- nenhum limiar
       fisiologico fixo mora aqui.
    2. Classifica cada dominio como NORMAL / BORDERLINE / ALERTA / SEM DADO.
    3. Aplica a regra de convergencia (PROFILE.md, regra 8): so vira "padrao"
       quando 2+ dominios pioram ao mesmo tempo.

Uso: python -m farol.state [--date YYYY-MM-DD] [--out CAMINHO]
Sem --date, usa hoje. Sem --out, grava em DAILY_STATE.md na raiz do projeto.
"""
import argparse
import json
import sys
from datetime import date as _date

from . import db, injuries
from .config import REPORTS_DIR
from .labels import ROTULOS
from .metrics import Metric, daily_metric, from_daily

# CARGA usa "farol_daily_state" porque trimp_7d/28d e atl_ctl_ratio so existem
# ali (training_load so tem ctl/atl/tsb). RHR e excecao: le de `daily` via
# daily_metric para respeitar a quebra de fonte de 2026-06-11 (MODEL_V1.0.md,
# dominio RECUPERACAO) -- farol_daily_state nao tem coluna `source`.
_TABLE = "farol_daily_state"

# CARGA: CTL/ATL/TSB/atl_ctl_ratio vem todos da mesma serie de TRIMP.
# MODEL_V1.0.md e explicito: nao contar como evidencias separadas -- o status
# do dominio sai so do TSB (>0 = descansado, <0 = carregado).
_CARGA = ["trimp_7d", "trimp_28d", "ctl", "atl", "tsb", "atl_ctl_ratio"]
_CARGA_PRIMARIO = "tsb"

_RECUPERACAO = ["hrv_avg", "rhr", "night_hr_avg", "stress_avg", "body_battery_max",
                "respiration_avg", "spo2_avg", "spo2_min"]
# body_battery_min fica FORA de _RECUPERACAO de proposito: sd_90d=15,1 sobre
# media 20,4 (CV ~74%, contra 0-64 do campo) -- muito mais volatil dia a dia
# que qualquer outro campo do dominio (todos entre 16-29% de dias marcados
# atencao/alerta; body_battery_min sozinho marca 55%). Nao e' dado ruim, e'
# um campo genuinamente mais ruidoso, e o limiar fixo de -0.5 SD (calibrado
# implicitamente para campos menos volateis) dispara nele com o dobro da
# frequencia -- inflava o dominio inteiro (achado ao rodar o pipeline real
# de ponta a ponta, 2026-09-04). Mesmo tratamento de vo2max/
# sleep_regularity_min: guardado em farol_daily_state, exibido em
# farol.today, fora de domain_status/streak/convergencia.
_SONO = ["sleep_total_min", "awake_minutes", "deep_sleep_min", "rem_sleep_min", "sleep_score"]
# sleep_regularity_min (farol_daily_state) fica FORA de _SONO de proposito:
# e' uma janela movel de 14 dias, 13 deles compartilhados com o dia
# anterior -- fortemente autocorrelacionada. Alimentar isso em
# domain_status()/longitudinal.streak() inflaria "dias consecutivos ruins"
# de forma artificial (uma unica mudanca de padrao de sono vira uma sequencia
# de ~14 dias "ruins" por construcao da janela, nao por evidencia
# independente repetida -- achado ao rodar contra o historico real, ver
# tests/test_longitudinal.py::test_caso_real_2026_08_21_a_2026_09_03).
# Exposto so como informativo em farol.today, sem entrar em streak/trend/
# convergencia.

# Direcao fisiologica dos campos de farol_daily_state. Nomes divergem de
# farol.metrics.DIRECTION porque a tabela usa outros nomes de coluna
# (sleep_total_min em vez de total_sleep_minutes etc. -- ver schema.sql).
_DIRECTION = {
    "trimp_7d": True, "trimp_28d": True, "ctl": True, "atl": True,
    "atl_ctl_ratio": True,  # nao usados para status -- so exibidos
    "tsb": True,
    "hrv_avg": True, "rhr": False, "night_hr_avg": False,
    "stress_avg": False, "body_battery_max": True,
    "respiration_avg": False,
    "spo2_avg": True, "spo2_min": True,
    "sleep_total_min": True, "awake_minutes": False,
    "deep_sleep_min": True, "rem_sleep_min": True, "sleep_score": True,
}

# "Sleep score" fica local de proposito: aqui rotula a linha da tabela do
# dominio SONO, junto de "Duracao do sono"/"Sono profundo" -- precisa ser
# especifico. Ver farol/labels.py (mesma chave vira "Sono" em today.py, que
# rotula a fatia do indice, nao o campo bruto).
_ROTULOS = {
    "trimp_7d": "TRIMP 7d", "trimp_28d": "TRIMP 28d", "ctl": "CTL",
    "atl": "ATL", "atl_ctl_ratio": "ATL/CTL",
    "sleep_total_min": "Duracao do sono", "awake_minutes": "Tempo acordado",
    "deep_sleep_min": "Sono profundo", "rem_sleep_min": "Sono REM",
    "sleep_score": "Sleep score",
    **ROTULOS,
}

_LABEL = {"ok": ("NORMAL", "\U0001F7E2"), "atencao": ("BORDERLINE", "\U0001F7E1"),
          "alerta": ("ALERTA", "\U0001F534"), "sem_dado": ("SEM DADO", "⚪")}

# Severidade para agregar o pior status entre varias metricas de um dominio.
_SEVERIDADE = {"sem_dado": 0, "ok": 1, "atencao": 2, "alerta": 3}
_CONFIANCA_ORDEM = {"alta": 3, "media": 2, "baixa": 1}

# F10 (auditoria): saida estruturada em maquina (--json) e exit codes. O
# nivel global do dia e' o PIOR status entre os dominios, na mesmissima
# escala _SEVERIDADE que domain_status() ja usa -- nenhum limiar, score ou
# heuristica nova: so a classificacao que farol.state ja produz, traduzida
# (PROFILE.md regra 8: convergencia de 2+ dominios ja esta coberta pelos
# status de cada dominio; o exit code nao soma nada a isso).
#
# Mapeamento (documentado tambem no relatorio --json):
#   0 = OK        (todos os dominios ok, ou sem_dado)
#   1 = ATTENTION (pelo menos um dominio em atencao, nenhum em alerta)
#   2 = ALERT     (pelo menos um dominio em alerta)
#
# erro tecnico de execucao NAO vira estado fisiologico: vai para stderr e
# sai com codigo de processo (1), sem JSON no stdout.
_EXIT_OK = 0
_EXIT_ATENCAO = 1
_EXIT_ALERTA = 2

# Versao do contrato JSON. Nao e' a versao do modelo (v1.1 vive em
# docs/MODEL_V1.1.md, sem constante no codigo) -- e' a versao deste schema
# de saida, para um consumidor saber se o formato mudou.
#
# Politica de compatibilidade (C2.1, 2026-09-08): a versao so sobe em
# mudanca INCOMPATIVEL -- remover uma chave, mudar o tipo/significado de
# uma existente, ou renomear algo que ja era publico. Adicionar uma chave
# nova a um dict que ja existe NAO e' incompativel: todo teste deste
# projeto que valida o payload (tests/test_state_json.py) usa
# assertIn/checagem de chaves especificas, nunca igualdade estrita do dict
# inteiro -- um consumidor que so le o que conhece continua funcionando.
# Por isso `evidencias`/`determinantes`/`contextuais`/`ausentes`, novos em
# dominio_to_json(), entraram sem subir a versao.
_JSON_SCHEMA = "farol.state.json.v1"


def global_status(dominios: dict[str, dict]) -> str:
    """Pior status entre os dominios -- mesma ordem de _SEVERIDADE que
    domain_status() usa para agregar metricas dentro de um dominio."""
    return max((d["status"] for d in dominios.values()),
               key=lambda s: _SEVERIDADE.get(s, _SEVERIDADE["sem_dado"]))


def exit_code(status_global: str) -> int:
    """F10: traduz o status global (ja calculado) para 0/1/2. Nao calcula
    nem reclassifica nada -- so mapeia a escala interna para a de maquina."""
    return {
        "ok": _EXIT_OK, "sem_dado": _EXIT_OK,
        "atencao": _EXIT_ATENCAO, "alerta": _EXIT_ALERTA,
    }[status_global]

# Direcao de farol.longitudinal.trend() -> seta para a MATRIZ RAPIDA.
_TREND_SETA = {"WORSENING": "↑", "IMPROVING": "↓", "STABLE": "→",
               "INSUFFICIENT_DATA": ""}


def _metric(conn, column: str, on_date: str) -> Metric:
    if column == "rhr":
        return daily_metric(conn, "rhr", on_date)
    return from_daily(conn, column, on_date, _DIRECTION.get(column, True),
                       table=_TABLE, respect_source=False)


def domain_metrics(conn, on_date: str, campos: list[str]) -> dict[str, Metric]:
    return {c: _metric(conn, c, on_date) for c in campos}


def domain_status(metricas: dict[str, Metric], primario: str | None = None) -> tuple[str, str]:
    """Status e confianca de um dominio.

    Com `primario`, o dominio inteiro segue essa unica metrica (CARGA: TSB).
    Sem `primario`, o status e o pior entre as metricas com dado, e a
    confianca e a mais baixa entre elas -- o dominio nao pode se dizer mais
    confiavel que seu elo mais fraco."""
    if primario is not None:
        m = metricas[primario]
        return m.status, m.confidence

    com_dado = [m for m in metricas.values() if m.has_data]
    if not com_dado:
        return "sem_dado", "baixa"
    status = max((m.status for m in com_dado), key=lambda s: _SEVERIDADE[s])
    confianca = min((m.confidence for m in com_dado), key=lambda c: _CONFIANCA_ORDEM[c])
    return status, confianca


def convergence(status_por_dominio: dict[str, str]) -> str | None:
    """PROFILE.md regra 8: 2+ dominios fora do normal ao mesmo tempo e que
    vira padrao a investigar -- um dominio isolado pode ser ruido."""
    piores = [d for d, s in status_por_dominio.items() if s in ("atencao", "alerta")]
    if len(piores) < 2:
        return None
    return f"{len(piores)} dominios simultaneamente fora do normal: {', '.join(piores)}"


def _linha_metrica(campo: str, m: Metric) -> str:
    rotulo = _ROTULOS.get(campo, campo)
    if not m.has_data:
        return f"| {rotulo} | sem dado | — | — |"
    baseline = f"{m.mean_90d:g}" if m.mean_90d is not None else "—"
    z = f"{m.z:+.2f}" if m.z is not None else "—"
    return f"| {rotulo} | {m.value:g} | {baseline} | {z} |"


def _streak_txt(streak: dict) -> str:
    if streak["dias"] == 0:
        return "—"
    return f"{streak['dias']}d"


def render_markdown(on_date: str, dominios: dict[str, dict], performance: dict, breakpoints: list[dict],
                     convergencia_streak: dict | None = None) -> str:
    linhas = [f"# ESTADO DO ATLETA — {on_date}", "",
              "Gerado automaticamente a partir de `farol_daily_state` "
              "(`python -m farol.state`). Ver MODEL_V1.1.md para a definicao dos campos.",
              "",
              "> **Leitura descritiva:** os estados comparam dados ao historico individual. "
              "Convergencia e um sinal para revisar evidencias, confianca e persistencia; "
              "nao e diagnostico nem decisao automatica de treino.",
              "",
              "> **Dados:** este relatorio le o banco SQLite local e nao consulta Garmin ou clima. "
              "Para atualizar o historico, rode `python -m farol.ingest`; o clima do percurso "
              "e uma consulta externa separada.",
              "", "## MATRIZ RAPIDA", "",
              "| Dominio | Estado | Confianca | Streak |", "|---------|--------|-----------|--------|"]
    for nome, d in dominios.items():
        rotulo, emoji = _LABEL[d["status"]]
        linhas.append(f"| **{nome}** | {emoji} {rotulo} | {d['confianca'].upper()} | "
                       f"{_streak_txt(d['streak'])} |")
    linhas.append("")

    linhas.append("## ANALISE POR DOMINIO")
    for i, (nome, d) in enumerate(dominios.items(), 1):
        linhas.append(f"\n### {i}. {nome}")
        linhas.append("| Metrica | Hoje | Baseline 90d | z |")
        linhas.append("|---------|------|---------------|---|")
        for campo, m in d["metricas"].items():
            linhas.append(_linha_metrica(campo, m))

    n = len(dominios) + 1
    linhas.append(f"\n### {n}. PERFORMANCE")
    op = performance.get("output_preserved")
    dd = performance.get("deterioration_detected")
    linhas.append(f"- **output_preserved**: {op if op else 'sem dado (sem treino intervalado no dia)'}")
    linhas.append(f"- **deterioration_detected**: {dd if dd else 'sem dado (sem treino intervalado no dia)'}")
    linhas.append("- Regra: caminhadas, aquecimento e recuperacoes nao entram na analise dos tiros; "
                  "somente `INTERVAL_ACTIVE` compoe esse output.")

    linhas.append(f"\n### {n + 1}. OBSERVACAO")
    if breakpoints:
        linhas.append("```json")
        linhas.append(json.dumps({"breakpoints": breakpoints}, indent=2, ensure_ascii=False))
        linhas.append("```")
    else:
        linhas.append("Nenhuma descontinuidade conhecida.")

    padrao = convergence({nome: d["status"] for nome, d in dominios.items()})
    linhas.append("\n## CONVERGENCIA")
    linhas.append(padrao if padrao else "Nenhum padrao: no maximo 1 dominio fora do normal hoje.")
    if padrao and convergencia_streak and convergencia_streak["dias"] > 1:
        linhas.append(f"\nHa {convergencia_streak['dias']} dias consecutivos "
                       f"(desde {convergencia_streak['data_inicio']}).")

    return "\n".join(linhas) + "\n"


def _evidencias(metricas: dict[str, Metric], campos: list[str],
                 primario: str | None) -> list[dict]:
    """Uma entrada por campo do dominio -- a fonte semantica unica de que
    campos sustentam o status e quais sao so contexto.

    `papel` reflete exatamente o que domain_status() ja usa para calcular o
    status (via `primario`): nunca uma segunda decisao. Com `primario`, so
    ele e' 'determinante' (CARGA: TSB e' a unica evidencia -- MODEL_V1.0.md).
    Sem `primario`, TODOS os campos do dominio sao 'determinante' (o pior
    entre eles decide o status -- domain_status(), regra do pior campo).

    `determinantes`/`contextuais`/`ausentes` em _dominios() sao indices
    derivados desta lista (nunca recalculados por conta propria), para nao
    existirem dois contratos que possam divergir (achado C02 da auditoria de
    2026-09-08: interpretation.sinais_por_dominio() reclassificava contexto
    de CARGA como sinal por nao ter acesso a esta distincao)."""
    determinantes = {primario} if primario is not None else set(campos)
    return [
        {
            "campo": campo,
            "papel": "determinante" if campo in determinantes else "contextual",
            "presente": metricas[campo].has_data,
            "valor": metricas[campo].value,
            "status": metricas[campo].status if metricas[campo].has_data else None,
            "z": metricas[campo].z,
        }
        for campo in campos
    ]


def _dominios(conn, on_date: str) -> dict[str, dict]:
    """Classificacao (metricas, status, confianca, evidencias) dos 3
    dominios -- sem streak/trend. Extraido para ser reaproveitado por
    gather() (que adiciona streak/trend por cima) e por basic_status()
    (usado por farol.longitudinal para andar dia a dia): se longitudinal
    chamasse gather() completo em vez de basic_status(), cada dia
    recalcularia streak/trend dos dias anteriores, que por sua vez
    chamariam gather() de novo -- recursao infinita.

    `evidencias` (lista, uma entrada por campo do dominio) e' a fonte
    semantica principal para qualquer consumidor que precise saber O QUE
    sustentou o status -- `determinantes`/`contextuais`/`ausentes` sao so
    indices de conveniencia derivados dela, nunca uma segunda fonte."""
    dominios = {}
    for nome, campos, primario in (
        ("CARGA", _CARGA, _CARGA_PRIMARIO),
        ("RECUPERACAO", _RECUPERACAO, None),
        ("SONO", _SONO, None),
    ):
        metricas = domain_metrics(conn, on_date, campos)
        status, confianca = domain_status(metricas, primario)
        evidencias = _evidencias(metricas, campos, primario)
        dominios[nome] = {
            "metricas": metricas,
            "status": status,
            "confianca": confianca,
            "evidencias": evidencias,
            "determinantes": [e["campo"] for e in evidencias if e["papel"] == "determinante"],
            "contextuais": [e["campo"] for e in evidencias if e["papel"] == "contextual"],
            "ausentes": [e["campo"] for e in evidencias if not e["presente"]],
        }
    return dominios


def basic_status(conn, on_date: str) -> dict[str, str]:
    """Status (ok/atencao/alerta/sem_dado) dos 3 dominios em `on_date`, sem
    streak/trend. Usado por farol.longitudinal.domain_status_by_date -- ver
    nota em _dominios() sobre por que nao usa gather()."""
    row = conn.execute(
        "SELECT 1 FROM farol_daily_state WHERE date = ?", (on_date,)
    ).fetchone()
    if row is None:
        raise ValueError(f"Sem farol_daily_state para {on_date}.")
    return {nome: d["status"] for nome, d in _dominios(conn, on_date).items()}


def gather(conn, on_date: str) -> dict:
    """Le farol_daily_state e monta a classificacao por dominio (metricas,
    status, confianca), performance e breakpoints -- sem renderizar nada.

    E a fonte de dados unica por tras de DAILY_STATE.md (`render_markdown`,
    abaixo) e de DAILY_ANALYSIS.md (`farol.daily_analysis`): os dois
    documentos derivam da mesma classificacao, nunca recalculam por conta
    propria."""
    row = conn.execute(
        "SELECT * FROM farol_daily_state WHERE date = ?", (on_date,)
    ).fetchone()
    if row is None:
        raise ValueError(
            f"Sem farol_daily_state para {on_date}. Rode 'python -m farol.ingest' "
            "ou 'python -m farol.load' primeiro."
        )

    # Import tardio: farol.longitudinal importa farol.state no nivel de
    # modulo (para basic_status()); um import no topo deste arquivo criaria
    # ciclo de importacao. gather() e o unico ponto deste modulo que precisa
    # de longitudinal, entao o import fica local a ele.
    from . import longitudinal

    dominios = _dominios(conn, on_date)
    for nome, d in dominios.items():
        # "ha quanto tempo" (streak) e "para onde" (trend) -- farol.longitudinal,
        # reaproveitados por DAILY_STATE.md e DAILY_ANALYSIS.md a partir daqui,
        # mesma razao de gather() ser fonte unica para o resto do dominio.
        d["streak"] = longitudinal.streak(conn, on_date, nome)
        d["trend"] = longitudinal.trend(conn, on_date, nome)

    performance = {
        "output_preserved": row["output_preserved"],
        "deterioration_detected": row["deterioration_detected"],
    }
    breakpoints = []
    if row["measurement_breakpoints"]:
        breakpoints = json.loads(row["measurement_breakpoints"]).get("breakpoints", [])

    return {
        "row": row, "dominios": dominios, "performance": performance, "breakpoints": breakpoints,
        "convergencia_streak": longitudinal.streak(conn, on_date, None),
        "convergencia_trend": longitudinal.trend(conn, on_date, None),
    }


def build_report(conn, on_date: str) -> str:
    dados = gather(conn, on_date)
    report = render_markdown(on_date, dados["dominios"], dados["performance"], dados["breakpoints"],
                             dados["convergencia_streak"])
    entry = injuries.latest_observation(on_date)
    alert = injuries.observation_alert(entry)
    extra = ["## OBSERVACAO MANUAL: LESOES", "",
             injuries.observation_text(entry),
             "", "Esta observacao nao altera CARGA, RECUPERACAO, SONO, TSB ou convergencia."]
    if alert:
        extra += ["", f"**Alerta do registro:** {alert}"]
    return report + "\n".join(extra) + "\n"


# --------------------------------------------------------- saida maquina (F10)

def metric_to_json(m: Metric) -> dict:
    """Uma `Metric` -> dict JSON-serializavel. Nao recalcula nada: espelha os
    campos do proprio objeto, que sao todos primitivos (float/str/int/None).
    `valor_em_relacao_ao_normal` = valor atual vs baseline 90d, na unidade
    da metrica (delta_vs_baseline do proprio Metric)."""
    return {
        "valor": m.value,
        "baseline_90d": m.mean_90d,
        "valor_em_relacao_ao_normal": m.delta_vs_baseline,
        "z": m.z,
        "status": m.status,
        "confianca": m.confidence,
        "tendencia_7d_sobre_30d": m.trend,
        "n_90d": m.n_90d,
    }


def dominio_to_json(d: dict) -> dict:
    """Um dominio (metricas/status/confianca/streak/trend/evidencias de
    state.gather) -> dict JSON-serializavel. streak/trend ja sao dicts de
    primitivos; as metricas passam por metric_to_json; evidencias/
    determinantes/contextuais/ausentes ja sao listas de primitivos (C2.1) --
    serializam direto, sem transformacao."""
    return {
        "status": d["status"],
        "confianca": d["confianca"],
        "streak": d.get("streak"),
        "trend": d.get("trend"),
        "metricas": {nome: metric_to_json(m) for nome, m in d["metricas"].items()},
        "evidencias": d.get("evidencias", []),
        "determinantes": d.get("determinantes", []),
        "contextuais": d.get("contextuais", []),
        "ausentes": d.get("ausentes", []),
    }


def build_state_json(conn, on_date: str) -> dict:
    """Estado do atleta em `on_date` serializado para maquina (F10).

    Fonte unica continua `gather()` -- este dict e' a MESMA classificacao do
    DAILY_STATE.md/DAILY_ANALYSIS.md, so que em forma estruturada: nenhuma
    segunda implementacao do modelo, nenhum limiar novo. O `exit_code` deriva
    do status global (pior dominio) via `exit_code()`, que so traduz.

    Contrato (farol.state.json.v1):
      data: data analisada
      status_global: ok | atencao | alerta | sem_dado (pior dos dominios)
      exit_code: 0 | 1 | 2 (traducao de status_global por exit_code())
      dominios: cada dominio com status/confianca/streak/trend/metricas
      performance: output_preserved/deterioration_detected
      convergencia: texto da regra 8 (2+ dominios) ou None
      descontinuidades: measurement_breakpoints (lista de dicts)
      schema: versao do contrato

    Sem datetime/Decimal (so float/str/int/None), determinístico: a ordem do
    dict segue a ordem fixa de gather()/dominios e as chaves das metricas
    seguem a ordem dos campos de cada dominio."""
    dados = gather(conn, on_date)
    status_global = global_status(dados["dominios"])
    return {
        "schema": _JSON_SCHEMA,
        "data": on_date,
        "status_global": status_global,
        "exit_code": exit_code(status_global),
        "dominios": {nome: dominio_to_json(d) for nome, d in dados["dominios"].items()},
        "performance": dados["performance"],
        "convergencia": convergence({nome: d["status"] for nome, d in dados["dominios"].items()}),
        "descontinuidades": dados["breakpoints"],
        "observacao_manual_lesao": {
            "registro": injuries.latest_observation(on_date),
            "alerta": injuries.observation_alert(injuries.latest_observation(on_date)),
            "nota": "Fora dos dominios fisiologicos e dos indices do Farol.",
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Gera o resumo de 1 pagina do estado do atleta.")
    parser.add_argument("--date", default=None, help="Data YYYY-MM-DD (padrao: hoje)")
    parser.add_argument("--out", default=str(REPORTS_DIR / "DAILY_STATE.md"), help="Caminho de saida")
    parser.add_argument("--json", action="store_true",
                        help="Saida estruturada JSON no stdout (exit code 0/1/2); "
                             "ignora --out. Nada de markdown/texto no stdout.")
    args = parser.parse_args()

    on_date = args.date or _date.today().isoformat()
    conn = db.connect_readonly()
    try:
        if args.json:
            payload = build_state_json(conn, on_date)
        else:
            texto = build_report(conn, on_date)
    except ValueError as exc:
        # Erro tecnico de execucao: NUNCA vira estado fisiologico. No modo
        # --json vai para stderr (stdout fica vazio, sem JSON que um script
        # pudesse interpretar como estado); no modo markdown mantem o
        # comportamento historico (mensagem na saida, sai com codigo de
        # processo 1). Em ambos os casos nao e' um estado do atleta.
        print(exc, file=(sys.stderr if args.json else sys.stdout))
        sys.exit(1)
    finally:
        conn.close()

    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
        sys.exit(payload["exit_code"])

    with open(args.out, "w", encoding="utf-8") as f:
        f.write(texto)
    print(f"Estado de {on_date} gravado em {args.out}")


if __name__ == "__main__":
    main()
