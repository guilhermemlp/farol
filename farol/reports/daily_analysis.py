"""CLI: gera o diagnostico completo do dia (DAILY_ANALYSIS.md).

Enquanto `farol.domain.state` produz o resumo executivo de 1 pagina (DAILY_STATE.md),
este modulo produz a analise detalhada: evidencia por metrica, relacao entre
os campos de CARGA, agregacao de dominio explicada, descontinuidades e uma
interpretacao que separa EVIDENCIA / INTERPRETACAO / LIMITACAO.

Nao existe uma segunda fonte de verdade aqui: toda classificacao (status,
z-score, confianca, convergencia) vem de `farol.domain.state.gather`, que le
farol_daily_state e aplica as mesmas regras ja validadas em farol.domain.state (TSB
como unica evidencia de CARGA, pior-status/pior-confianca em RECUPERACAO e
SONO, RHR vindo de `daily` para respeitar a quebra de fonte de 2026-06-11).
Este arquivo so adiciona texto -- nao recalcula nada.

Uso: python -m farol.reports.daily_analysis [--date YYYY-MM-DD] [--out CAMINHO]
"""
import argparse
import sys
from datetime import date as _date
from datetime import datetime

from ..core import db
from ..core.labels import STATUS_LABEL
from ..domain import interpretation, state
from ..records import injuries, race_log
from ..core.config import REPORTS_DIR
from ..core.metrics import Metric

_TENDENCIA_TXT = {
    "subindo": "subindo", "caindo": "caindo",
    "estavel": "estavel", "indefinido": "sem tendencia definida",
}

# farol.domain.longitudinal.trend() -> texto (direcao da SEVERIDADE do dominio, nao
# de uma metrica individual -- nao confundir com _TENDENCIA_TXT acima).
_DIRECAO_TXT = {
    "WORSENING": "piorando", "IMPROVING": "melhorando",
    "STABLE": "estavel", "INSUFFICIENT_DATA": "dados insuficientes para avaliar",
}


def _persistencia_txt(dominio: dict) -> str | None:
    """'Ha quanto tempo' (streak) e 'para onde' (trend) de um dominio, a
    partir do que state.gather ja calculou via farol.domain.longitudinal. None
    quando o dominio nao esta ativo hoje -- persistencia so faz sentido
    descrever enquanto o sinal continua ligado.

    C5 (VISTORIA V7): streak (janela consecutiva a partir de hoje) e trend
    (janela fixa de 7 dias) sao RECORTES diferentes; quando o episodio
    comecou hoje (streak = 1) e o trend varre dias anteriores ao episodio, a
    direcao e' do periodo, nao do episodio -- o texto diz isso em vez de
    amarrar os dois como se fossem o mesmo recorte."""
    streak, trend = dominio.get("streak"), dominio.get("trend")
    if not streak or streak["dias"] < 1:
        return None
    dias = streak["dias"]
    plural = "dia" if dias == 1 else "dias"
    base = f"Ativo ha {dias} {plural} consecutivos (desde {streak['data_inicio']})"
    direcao = _DIRECAO_TXT.get(trend["direcao"]) if trend else None
    if direcao:
        if dias == 1 and streak.get("data_inicio") and trend.get("data_inicio"):
            # Episodio comecou hoje; a direcao compara extremos de uma janela
            # fixa que comeca ANTES do episodio (ex.: 29/08 alerta -> hoje
            # atencao = "melhorando" para a janela, nao para o episodio).
            base += (f", tendencia {direcao} na janela de {trend['janela_dias']} dias"
                     f" (janela comeca em {trend['data_inicio']}, antes deste episodio,"
                     f" que comecou hoje -- direcao e do periodo, nao do episodio)")
        else:
            base += f", tendencia {direcao} (janela de {trend['janela_dias']} dias)"
    return base + "."


def _fmt(v) -> str:
    if v is None:
        return "sem dado"
    if isinstance(v, float):
        return f"{v:g}"
    return str(v)


def _linha_metrica_detalhada(rotulo: str, m: Metric) -> str:
    if not m.has_data:
        return f"| {rotulo} | sem dado | — | — | SEM DADO | — |"
    baseline = _fmt(m.mean_90d)
    z = f"{m.z:+.2f}" if m.z is not None else "—"
    status_rotulo, _ = STATUS_LABEL[m.status]
    return (f"| {rotulo} | {_fmt(m.value)} | {baseline} | {z} | {status_rotulo} | "
            f"{_TENDENCIA_TXT[m.trend]} |")


def _piores_campos(metricas: dict, status_alvo: str) -> list[str]:
    """Quais campos do dominio empataram no pior status -- para dizer o que
    sustenta ou puxa o dominio para baixo, sem inventar novo criterio."""
    return [state._ROTULOS.get(c, c) for c, m in metricas.items()
            if m.has_data and m.status == status_alvo]


def _tabela_dominio(dominio: dict) -> list[str]:
    linhas = ["| Metrica | Hoje | Baseline 90d | z | Estado | Tendencia (7d vs 30d) |",
              "|---------|------|---------------|---|--------|------------------------|"]
    for campo, m in dominio["metricas"].items():
        linhas.append(_linha_metrica_detalhada(state._ROTULOS.get(campo, campo), m))
    return linhas


def _secao_carga(dominio: dict) -> list[str]:
    m = dominio["metricas"]
    tsb = m["tsb"]
    linhas = ["## 3. CARGA", ""]
    linhas += _tabela_dominio(dominio)
    linhas.append("")
    status_rotulo, _ = STATUS_LABEL[dominio["status"]]
    linhas.append(
        f"TSB = CTL − ATL. Hoje: {_fmt(m['ctl'].value)} − {_fmt(m['atl'].value)} = "
        f"{_fmt(m['tsb'].value)}. CTL acumula a carga cronica (media movel de "
        "42 dias) e ATL a carga aguda (media movel de 7 dias); ATL/CTL > 1 "
        "significa que a carga recente esta acima do que o corpo vem "
        "absorvendo em media -- fase de pico, nao necessariamente problema."
    )
    linhas.append(
        f"\n**TSB e a unica evidencia usada para o estado do dominio** "
        f"(z={_fmt(tsb.z) if tsb.z is not None else 'sem dado'}, "
        f"confianca {dominio['confianca']}): {status_rotulo}. CTL, ATL e "
        "ATL/CTL sao contexto de como esse TSB foi construido, nao evidencias "
        "adicionais -- os tres vem da mesma serie de TRIMP que gera o proprio "
        "TSB, e conta-los separadamente inflaria artificialmente o numero de "
        "sinais (MODEL_V1.0.md, dominio CARGA)."
    )
    persistencia = _persistencia_txt(dominio)
    if persistencia:
        linhas.append(f"\n{persistencia}")
    return linhas


def _secao_dominio_simples(nome_secao: str, dominio: dict, nota_extra: str = "") -> list[str]:
    linhas = [nome_secao, ""]
    linhas += _tabela_dominio(dominio)
    linhas.append("")
    status_rotulo, _ = STATUS_LABEL[dominio["status"]]

    piores = _piores_campos(dominio["metricas"], dominio["status"])
    if dominio["status"] == "sem_dado":
        explicacao = "Nenhuma metrica do dominio tem dado suficiente hoje para classificar."
    elif dominio["status"] == "ok" or not piores:
        explicacao = "Nenhuma metrica do dominio esta fora do padrao pessoal de 90 dias."
    else:
        explicacao = (f"Estado puxado por: {', '.join(piores)} "
                       f"({'unica metrica' if len(piores) == 1 else 'metricas'} em {status_rotulo.lower()}).")

    linhas.append(
        f"**Estado do dominio: {status_rotulo}** (confianca {dominio['confianca']} -- "
        "a mais baixa entre as metricas com dado; o dominio usa o pior status "
        f"entre suas metricas, nao uma media). {explicacao}"
    )
    persistencia = _persistencia_txt(dominio)
    if persistencia:
        linhas.append(f"\n{persistencia}")
    if nota_extra:
        linhas.append(f"\n{nota_extra}")
    return linhas


def _secao_performance(performance: dict, carga_status: str) -> list[str]:
    linhas = ["## 6. PERFORMANCE", "",
              "Regra de leitura: caminhadas, aquecimento e recuperacoes nao entram nos tiros; "
              "a analise intervalada usa somente segmentos `INTERVAL_ACTIVE`.", ""]
    op, dd = performance.get("output_preserved"), performance.get("deterioration_detected")
    if op is None and dd is None:
        linhas.append(
            "- **output_preserved**: sem dado (sem treino intervalado reconhecido no dia)\n"
            "- **deterioration_detected**: sem dado (sem treino intervalado reconhecido no dia)\n\n"
            "Sem sessao intervalada estruturada, nao ha base para avaliar se a "
            "capacidade de output foi preservada ou se houve degradacao tecnica "
            "nesse dia especifico."
        )
        return linhas

    linhas.append(f"- **output_preserved**: {op}")
    linhas.append(f"- **deterioration_detected**: {dd}")
    linhas.append("")
    if op == "yes" and dd == "no":
        contexto = ("Combinado com o estado de CARGA "
                    f"({carga_status}): a capacidade de sustentar o pace/output do treino "
                    "intervalado e a ausencia de degradacao tecnica entre tiros sao "
                    "evidencia de que a carga atual esta sendo absorvida, nao de "
                    "sobrecarga.")
    elif op == "no" or dd == "yes":
        contexto = ("Combinado com o estado de CARGA "
                    f"({carga_status}): queda de output e/ou sinal de degradacao tecnica "
                    "no mesmo periodo em que a carga esta fora do padrao pesa a favor "
                    "de investigar sobrecarga, em vez de apenas pico de treino normal.")
    else:
        contexto = "Sinal indeterminado -- dados insuficientes para uma leitura direcional."
    linhas.append(contexto)
    return linhas


def _secao_convergencia(dominios: dict, convergencia_streak: dict | None = None,
                         convergencia_trend: dict | None = None) -> list[str]:
    linhas = ["## 7. CONVERGENCIA", ""]
    fora = {n: d["status"] for n, d in dominios.items() if d["status"] in ("atencao", "alerta")}
    preservados = [n for n, d in dominios.items() if d["status"] == "ok"]
    msg = state.convergence({n: d["status"] for n, d in dominios.items()})

    linhas.append(f"- Dominios em atencao/alerta: {len(fora)} "
                   f"({', '.join(fora) if fora else 'nenhum'})")
    linhas.append(f"- Dominios preservados: {', '.join(preservados) if preservados else 'nenhum'}")
    if msg:
        linhas.append(f"- **Convergencia sinalizada**: {msg}. Dois ou mais dominios fora "
                       "do padrao ao mesmo tempo reduz a chance de ser ruido isolado em "
                       "uma unica metrica.")
        if convergencia_streak and convergencia_streak["dias"] > 1:
            direcao = (_DIRECAO_TXT.get(convergencia_trend["direcao"])
                       if convergencia_trend else None)
            texto = (f"- **Persistencia**: convergencia ativa ha {convergencia_streak['dias']} "
                     f"dias consecutivos (desde {convergencia_streak['data_inicio']})")
            if direcao:
                texto += f", tendencia {direcao}"
            linhas.append(texto + ".")
    else:
        linhas.append("- **Sem convergencia sistemica**: no maximo um dominio fora do "
                       "padrao hoje. Um sinal isolado, por definicao, nao caracteriza "
                       "convergencia (PROFILE.md, regra 8).")
    return linhas


def _secao_descontinuidades(breakpoints: list[dict]) -> list[str]:
    linhas = ["## 8. DESCONTINUIDADES / QUALIDADE DOS DADOS", ""]
    if not breakpoints:
        linhas.append("Nenhuma descontinuidade de medicao conhecida.")
        return linhas

    for b in breakpoints:
        linhas.append(f"- **Campo**: `{b.get('field')}`")
        linhas.append(f"  - Detectada em: {b.get('detected_on')}")
        linhas.append(f"  - Motivo: {b.get('reason')}")
        linhas.append(f"  - Politica de baseline: {b.get('baseline_policy')}")
        if b.get("field") == "rhr":
            linhas.append(
                "  - Esta analise ja respeita essa politica: a baseline de RHR "
                "usada acima vem so de linhas com a mesma procedencia do "
                "dia analisado (farol.core.metrics.from_daily, respect_source=True)."
            )
        elif b.get("field") == "awakenings":
            linhas.append(
                "  - Esta analise nao usa a contagem de despertares (`awakenings`) "
                "-- usa `awake_minutes` (duracao acordado), um campo diferente e "
                "nao afetado por essa quebra especifica. Listada aqui para "
                "auditoria completa das descontinuidades conhecidas do modelo."
            )
        elif b.get("field") == "sleep_score":
            linhas.append(
                "  - Esta analise ja respeita essa politica: a baseline de "
                "sleep_score so inclui dias do mesmo lado da quebra de 2026-08-01 "
                "(farol.core.metrics.DATE_SPLITS, split_by_date), entao um dia de "
                "setembro nunca compara contra scores de junho/julho -- duas "
                "definicoes possivelmente diferentes."
            )
    return linhas


def _permanece(preservados: list[str]) -> str:
    if not preservados:
        return "Nenhum dominio permanece dentro do padrao."
    verbo = "permanece" if len(preservados) == 1 else "permanecem"
    return f"{', '.join(preservados)} {verbo} dentro do padrao."


def _hipotese(dominios: dict) -> tuple[str, str]:
    """Hipotese principal e contra-sinal, derivados so dos status de dominio
    ja calculados por farol.domain.state -- nenhum limiar novo."""
    fora = [n for n, d in dominios.items() if d["status"] in ("atencao", "alerta")]
    preservados = [n for n in dominios if n not in fora]

    if not fora:
        return ("Estado dentro do padrao pessoal dos ultimos 90 dias; nenhum "
                "dominio fora da faixa normal."), "Nao ha contra-sinal a avaliar."

    if len(fora) == 1:
        return (
            f"Sinal isolado em {fora[0]}, sem convergencia com os demais "
            "dominios -- mais compativel com variacao pontual do que com "
            "mudanca sistemica."
        ), _permanece(preservados)

    if "CARGA" in fora and "RECUPERACAO" not in fora:
        return (
            f"Carga de treino fora do padrao pessoal (TSB), com "
            f"{'RECUPERACAO' if 'RECUPERACAO' in preservados else 'ao menos um dominio'} "
            "preservada -- padrao mais consistente com carga elevada sendo "
            "absorvida do que com deterioracao sistemica."
        ), _permanece(preservados)

    return (
        f"Convergencia entre {', '.join(fora)}: mais de um dominio fora do "
        "padrao ao mesmo tempo, incluindo RECUPERACAO -- reduz a chance de "
        "ser ruido isolado e pesa mais a favor de investigar deterioracao "
        "sistemica."
    ), _permanece(preservados)


def _resumo_executivo(dominios: dict, performance: dict) -> list[str]:
    fora = [n for n, d in dominios.items() if d["status"] in ("atencao", "alerta")]
    msg_convergencia = state.convergence({n: d["status"] for n, d in dominios.items()})
    hipotese, contra_sinal = _hipotese(dominios)

    linhas = ["## 2. RESUMO EXECUTIVO", ""]
    if not fora:
        linhas.append("**Estado geral**: dentro do padrao pessoal -- nenhum dominio "
                       "fora da faixa normal de 90 dias.")
    else:
        linhas.append(f"**Estado geral**: {len(fora)} de {len(dominios)} dominios fora "
                       f"do padrao pessoal ({', '.join(fora)}).")
    linhas.append(f"\n**Sinal relevante?** {'Sim' if fora else 'Nao'}.")
    linhas.append(f"\n**Dominios em atencao/alerta**: {', '.join(fora) if fora else 'nenhum'}.")
    linhas.append(f"\n**Convergencia entre dominios?** "
                   f"{'Sim -- ' + msg_convergencia if msg_convergencia else 'Nao.'}")
    linhas.append(f"\n**Hipotese principal**: {hipotese}")
    linhas.append(f"\n**Principal contra-sinal**: {contra_sinal}")

    op = performance.get("output_preserved")
    if op is not None:
        linhas.append(f"\n**Performance no dia**: output_preserved={op}, "
                       f"deterioration_detected={performance.get('deterioration_detected')}.")
    return linhas


def _interpretacao(dominios: dict, performance: dict) -> list[str]:
    msg_convergencia = state.convergence({n: d["status"] for n, d in dominios.items()})
    hipotese, contra_sinal = _hipotese(dominios)

    linhas = ["## 9. INTERPRETACAO", ""]
    linhas.append("**EVIDENCIA** (o que os dados mostram):")
    for nome, d in dominios.items():
        rotulo, _ = STATUS_LABEL[d["status"]]
        linhas.append(f"- {nome}: {rotulo} (confianca {d['confianca']})")
    if performance.get("output_preserved") is not None:
        linhas.append(f"- PERFORMANCE: output_preserved={performance['output_preserved']}, "
                       f"deterioration_detected={performance['deterioration_detected']}")
    else:
        linhas.append("- PERFORMANCE: sem dado (sem treino intervalado reconhecido no dia)")

    linhas.append("\n**INTERPRETACAO** (o que os sinais combinados sugerem):")
    linhas.append(f"- {hipotese}")
    linhas.append(f"- Contra-sinal considerado: {contra_sinal}")
    if msg_convergencia:
        linhas.append(f"- {msg_convergencia}.")

    linhas.append("\n**LIMITACAO** (o que nao pode ser concluido a partir destes dados):")
    limitacoes = []
    for nome, d in dominios.items():
        if d["confianca"] == "baixa":
            limitacoes.append(f"{nome} tem confianca baixa hoje -- poucos dias com dado "
                               "na janela de 90 dias, a classificacao pode mudar com mais dado.")
        elif d["confianca"] == "media":
            limitacoes.append(f"{nome} tem confianca media -- cobertura de dado "
                               "moderada na janela de 90 dias.")
    if performance.get("output_preserved") is None:
        limitacoes.append("Sem treino intervalado no dia, PERFORMANCE nao pode ser avaliada "
                           "e nao entra na leitura de convergencia.")
    if not limitacoes:
        limitacoes.append("Nenhuma limitacao adicional alem da normal: esta analise compara "
                           "o dia contra o padrao do proprio atleta, nao contra limiares "
                           "fisiologicos absolutos, e convergencia entre dominios nao implica "
                           "causalidade entre eles.")
    for limitacao in limitacoes:
        linhas.append(f"- {limitacao}")

    return linhas


def _conclusao(dominios: dict, performance: dict) -> list[str]:
    # C4/V5 (vistoria): a conclusao passou a derivar da camada de
    # interpretacao (farol.domain.interpretation), que distingue nucleo autonomico
    # comprometido / dominio alerta de um sinal de atencao isolado -- em vez
    # de declarar "deterioracao sistemica" por qualquer dominio BORDERLINE.
    leitura = interpretation.interpretar({"dominios": dominios,
                                          "performance": performance,
                                          "row": None})
    deterioracao = leitura["deterioracao_sistemica"]
    sinais = interpretation.sinais_por_dominio(dominios)
    # Mesma definicao de alteracao sistemica usada por
    # interpretation.estado_fisiologico/conclusao(): 2+ campos do nucleo
    # autonomico fora do padrao. Antes esta funcao refazia o calculo com
    # `any` (1 campo ja' bastava), contradizendo a leitura em linguagem.
    nucleo_comprometido = interpretation.alteracao_sistemica(dominios)
    alerta = any(d["status"] == "alerta" for d in dominios.values())

    carga_ruim = dominios["CARGA"]["status"] in ("atencao", "alerta")
    recup_ruim = dominios["RECUPERACAO"]["status"] in ("atencao", "alerta")
    sono_ruim = dominios["SONO"]["status"] in ("atencao", "alerta")
    msg_convergencia = state.convergence({n: d["status"] for n, d in dominios.items()})
    op = performance.get("output_preserved")

    linhas = ["## 10. CONCLUSAO", ""]
    # Deterioracao sistemica (C4): so quando ha alerta, nucleo autonomico
    # comprometido ou sinais espalhados -- nunca por um dominio BORDERLINE
    # puxado por 1 campo isolado (vistoria 04/09).
    if deterioracao or alerta or nucleo_comprometido:
        det_txt = "Sim"
        if alerta:
            det_txt += " -- dominio em ALERTA."
        elif nucleo_comprometido:
            det_txt += " -- nucleo autonomico fora do padrao."
        else:
            det_txt += " -- sinais espalhados entre dominios."
    else:
        det_txt = "Nao"
        if len(sinais) >= 2:
            total = sum(len(v) for v in sinais.values())
            det_txt += (f" -- apenas {len(sinais)} dominios em atencao puxados por "
                        f"{total} sinal(is) isolado(s); nucleo preservado.")
        elif sinais:
            dominio, campos = next(iter(sinais.items()))
            det_txt += f" -- sinal isolado em {dominio} ({', '.join(campos)})."
        else:
            det_txt += " -- nenhum sinal fora do padrao."
    linhas.append(f"- **Evidencia de deterioracao sistemica?** {det_txt}")
    linhas.append(f"- **Evidencia de carga elevada?** {'Sim (TSB fora do padrao).' if carga_ruim else 'Nao.'}")
    linhas.append(f"- **Recuperacao comprometida?** {'Sim.' if recup_ruim else 'Nao.'}")
    linhas.append(f"- **Sono comprometido?** {'Sim.' if sono_ruim else 'Nao.'}")
    if op == "yes":
        perf_txt = "Sim -- output preservado no treino intervalado do dia."
    elif op == "no":
        perf_txt = "Nao -- queda de output detectada no treino intervalado do dia."
    else:
        perf_txt = "Sem dado suficiente para avaliar (sem treino intervalado reconhecido)."
    linhas.append(f"- **Performance preservada?** {perf_txt}")
    linhas.append(f"- **Existe convergencia?** {'Sim -- ' + msg_convergencia + '.' if msg_convergencia else 'Nao.'}")
    linhas.append("\nEste documento e diagnostico: descreve o que os dados mostram e o que "
                  "sugerem em conjunto. Nao prescreve treino (PROFILE.md, regras 7 e 9).")
    return linhas


def _leitura_interpretacao(dominios: dict, performance: dict, row) -> list[str]:
    """A leitura em linguagem do estado -- derivada so da classificacao ja
    calculada (farol.domain.interpretation), sem metrica nem limiar novo."""
    leitura = interpretation.interpretar({"dominios": dominios,
                                          "performance": performance,
                                          "row": row})
    rotulos = [
        ("Estado fisiologico", "estado_fisiologico"),
        ("Carga", "carga"),
        ("Recuperacao", "recuperacao"),
        ("Sono", "sono"),
        ("Sinal dominante", "sinal_dominante"),
        ("Convergencia", "convergencia"),
        ("Performance", "performance"),
        ("Confianca", "confianca"),
        ("Conclusao", "conclusao"),
    ]
    linhas = ["## 11. LEITURA (INTERPRETACAO EM LINGUAGEM)", "",
              "Leitura retrospectiva derivada da classificacao acima ("
              "farol.domain.interpretation) -- descreve, nao prescreve.", ""]
    for rotulo, chave in rotulos:
        valor = leitura[chave]
        linhas.append(f"- **{rotulo}**: {valor}")
    linhas.append("")
    linhas.append("---")
    linhas.append("")
    linhas.append("Sinais fora do padrao por dominio (mesma classificacao de "
                  "farol.domain.state):")
    sinais = leitura["sinais"]
    if not sinais:
        linhas.append("- nenhum.")
    else:
        for nome, campos in sinais.items():
            linhas.append(f"- {nome}: {', '.join(campos)}")
    return linhas


def build_analysis(conn, on_date: str, gerado_em: str | None = None) -> str:
    dados = state.gather(conn, on_date)
    dominios, performance, breakpoints = dados["dominios"], dados["performance"], dados["breakpoints"]
    gerado_em = gerado_em or datetime.now().isoformat(timespec="seconds")

    linhas = [
        f"# DIAGNOSTICO DIARIO — {on_date}", "",
        f"- Data analisada: {on_date}",
        f"- Gerado em: {gerado_em}",
        "- Periodo de referencia: janela movel de 90 dias por metrica (dia-alvo "
        "excluido da propria baseline), 7d/30d para tendencia",
        "- Fonte: `farol_daily_state` (RHR obtido de `daily`, respeitando a quebra "
        "de fonte de 2026-06-11 -- ver farol.core.metrics.from_daily)",
        "- Atualizacao: esta analise le o SQLite local e nao consulta o Garmin. "
        "A ingestao Garmin e feita separadamente por `python -m farol.pipeline.ingest`; "
        "clima de percurso tambem e uma consulta externa separada.",
        "",
        "> **Leitura descritiva:** convergencia e alertas estatisticos indicam evidencias "
        "para revisar, nao diagnostico nem decisao automatica de treino.",
        "",
        "Diagnostico detalhado. Para o resumo executivo de 1 pagina, ver "
        "`DAILY_STATE.md` (`python -m farol.domain.state`). Gerado deterministicamente "
        "a partir dos mesmos dados e das mesmas regras de classificacao.",
        "",
    ]
    linhas += _resumo_executivo(dominios, performance)
    linhas.append("")
    linhas += _secao_carga(dominios["CARGA"])
    linhas.append("")
    linhas += _secao_dominio_simples("## 4. RECUPERACAO", dominios["RECUPERACAO"])
    linhas.append("")
    linhas += _secao_dominio_simples(
        "## 5. SONO", dominios["SONO"],
        nota_extra=("Sleep score e um resumo qualitativo, nao a unica medida de "
                    "qualidade -- duracao, sono profundo e REM sao avaliados "
                    "individualmente acima e podem sustentar o dominio mesmo "
                    "quando o score sozinho esta fraco."),
    )
    linhas.append("")
    linhas += _secao_performance(performance, STATUS_LABEL[dominios["CARGA"]["status"]][0])
    linhas.append("")
    linhas += _secao_convergencia(dominios, dados.get("convergencia_streak"), dados.get("convergencia_trend"))
    linhas.append("")
    entry = injuries.latest_observation(on_date)
    alert = injuries.observation_alert(entry)
    linhas += ["## OBSERVACAO MANUAL: LESOES", "",
               injuries.observation_text(entry), "",
               "Este registro e separado das metricas Garmin: nao altera carga, recuperacao, sono, TSB ou convergencia."]
    if alert:
        linhas += ["", f"**Alerta do registro:** {alert}"]
    linhas.append("")
    race_entry = race_log.entry_for_date(on_date)
    if race_entry:
        linhas += ["## CONTEXTO MANUAL DE PROVA", "", race_log.summary(race_entry),
                   "", "Relato manual separado das metricas Garmin: nao altera carga, "
                   "recuperacao, sono, TSB ou convergencia.", ""]
    linhas += _secao_descontinuidades(breakpoints)
    linhas.append("")
    linhas += _interpretacao(dominios, performance)
    linhas.append("")
    linhas += _conclusao(dominios, performance)
    linhas.append("")
    linhas += _leitura_interpretacao(dominios, performance, dados.get("row"))

    return "\n".join(linhas) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description="Gera o diagnostico diario completo do estado do atleta.")
    parser.add_argument("--date", default=None, help="Data YYYY-MM-DD (padrao: hoje)")
    parser.add_argument("--out", default=str(REPORTS_DIR / "DAILY_ANALYSIS.md"), help="Caminho de saida")
    args = parser.parse_args()

    on_date = args.date or _date.today().isoformat()
    conn = db.connect_readonly()
    try:
        texto = build_analysis(conn, on_date)
    except ValueError as exc:
        print(exc)
        sys.exit(1)
    finally:
        conn.close()

    with open(args.out, "w", encoding="utf-8") as f:
        f.write(texto)
    print(f"Diagnostico de {on_date} gravado em {args.out}")


if __name__ == "__main__":
    main()
