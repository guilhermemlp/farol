"""Camada de interpretacao: transforma a classificacao ja calculada pelo
modelo congelado em uma leitura coerente do estado -- sem recalcular nada e
sem criar metrica ou limiar novo.

Fonte unica de entrada: `farol.domain.state.gather()` (a mesma classificacao por
dominio por tras de DAILY_STATE.md e DAILY_ANALYSIS.md). Este modulo so
agrupa, nomeia e reconcilia o que ja foi classificado: quantos sinais puxam
cada dominio, se o nucleo autonomico esta preservado, se o sinal esta
concentrado na noite ou espalhado, se a convergencia e fraca ou forte.

Principio (PROFILE.md): Farol descreve, treinador prescreve. Nenhuma funcao
aqui recomenda acao -- leitura retrospectiva, nunca prescricao.

Vistoria de 2026-09-04 (docs/archive/VISTORIA_2026-09-04.md) que motivou esta camada:
a saida tratava metricas como dominios, o RESUME chamava de "deterioracao
sistemica" um dia com 2 dominios borderline puxados por 1 campo cada, e
conclusao/interpretacao se contradiziam. Tudo isto se resolve com a mesma
classificacao -- nenhuma formula mudou.
"""
from __future__ import annotations

# Rotulos de terminal usados na leitura (sem acento, convencao do projeto).
_ROTULO = {
    "hrv_avg": "HRV", "rhr": "FC repouso", "night_hr_avg": "FC noturna",
    "stress_avg": "Stress", "body_battery_max": "Body Battery",
    "respiration_avg": "Respiracao", "spo2_avg": "SpO2 media", "spo2_min": "SpO2 minima",
    "tsb": "TSB", "sleep_total_min": "Duracao do sono", "awake_minutes": "Tempo acordado",
    "deep_sleep_min": "Sono profundo", "rem_sleep_min": "Sono REM", "sleep_score": "Sleep score",
}

# Nucleo autonomico do dominio RECUPERACAO: as metricas mais diretamente
# ligadas a modulacao vagal/simpatica. SpO2, Body Battery e respiracao sao
# complementares -- um campo periferico isolado fora do normal nao e' o mesmo
# que o nucleo comprometido (regra usada na vistoria para ler 04/09).
_NUCLEO_RECUPERACAO = ("hrv_avg", "rhr", "night_hr_avg", "stress_avg")

# Comprometimento do nucleo exige 2+ campos ao mesmo tempo (mesmo criterio
# que estado_fisiologico ja aplicava). Um unico campo do nucleo em atencao e'
# sinal isolado, nao alteracao sistemica -- ver alteracao_sistemica().
_MIN_NUCLEO_COMPROMETIDO = 2


def _nucleo_fora(metricas: dict) -> list[str]:
    """Campos do nucleo autonomico com dado e status atencao/alerta, na ordem
    fixa de _NUCLEO_RECUPERACAO. Fonte unica do calculo -- nao reclassifica
    nada, so' le o status que farol.domain.state ja produziu."""
    return [c for c in _NUCLEO_RECUPERACAO
            if (m := metricas.get(c)) is not None and m.has_data
            and m.status in ("atencao", "alerta")]


def nucleo_comprometido(dominios: dict) -> list[str]:
    """Campos do nucleo autonomico fora do padrao pessoal hoje.

    Publico porque `farol.reports.daily_analysis` precisa da MESMA definicao usada
    por `estado_fisiologico`/`conclusao`; antes repetia o calculo acessando
    `_NUCLEO_RECUPERACAO` privado e, no caminho, usava `any` (1 campo ja'
    contava como nucleo comprometido), contradizendo a leitura em
    linguagem. Devolve lista vazia se o dominio RECUPERACAO nao existir.
    """
    metricas = (dominios.get("RECUPERACAO") or {}).get("metricas", {})
    return _nucleo_fora(metricas)


def alteracao_sistemica(dominios: dict) -> bool:
    """True somente quando 2+ campos do nucleo autonomico estao fora do
    padrao ao mesmo tempo -- o criterio documentado em estado_fisiologico.
    Um campo isolado e' atencao concentrada, nao alteracao sistemica."""
    return len(nucleo_comprometido(dominios)) >= _MIN_NUCLEO_COMPROMETIDO

_STATUS_TXT = {"ok": "normal/estavel", "atencao": "atencao", "alerta": "alerta"}


def _fora_do_padrao(dominios: dict) -> list[str]:
    """Dominios com status atencao/alerta (mesmo criterio de
    state.convergence -- regra 8 do PROFILE.md)."""
    return [n for n, d in dominios.items()
            if d["status"] in ("atencao", "alerta")]


def sinais_por_dominio(dominios: dict) -> dict[str, list[str]]:
    """Por dominio, quais campos DETERMINANTES estao em atencao/alerta.

    E' aqui que a leitura ganha profundidade: "RECUPERACAO em atencao" pode
    significar 1 campo periferico isolado (SpO2 minima) ou varios campos do
    nucleo ao mesmo tempo -- duas historias fisiologicas diferentes com o
    mesmo estado de dominio. So reagrupa o que farol.domain.state ja classificou.

    C2.2 (auditoria de 2026-09-08): so' conta campos em `d['determinantes']`
    -- o indice que state._dominios() ja deriva de domain_status() (via
    `primario`). Antes desta correcao, esta funcao percorria TODAS as
    metricas do dominio (`d['metricas']`), inclusive as contextuais de
    CARGA (trimp_7d/trimp_28d/ctl/atl/atl_ctl_ratio) -- entao TSB podia
    estar 'ok' (CARGA oficial normal) e CTL/ATL aparecerem aqui como sinal,
    contradizendo o proprio status do dominio. `state` e' a fonte de
    verdade para o que e' evidencia; esta funcao nunca reclassifica."""
    saida = {}
    for nome, d in dominios.items():
        metricas = d.get("metricas", {})
        for campo in d["determinantes"]:
            m = metricas[campo]
            if m.has_data and m.status in ("atencao", "alerta"):
                saida.setdefault(nome, []).append(_ROTULO.get(campo, campo))
    return saida


def estado_fisiologico(dominios: dict) -> str:
    """Leitura de fundo do dia, so a partir do estado agregado dos dominios.

    Regras (nenhuma metrica nova, nenhum limiar novo):
    - nucleo autonomico comprometido (2+ campos de _NUCLEO_RECUPERACAO fora do
      padrao) -> o corpo inteiro esta respondendo: alerta de alteracao
      sistemica. Sem dominio alerta, "atencao" ampla.
    - um dominio isolado em atencao -> atencao concentrada naquele dominio.
    - 2+ dominios em atencao, mas cada um puxado por poucos campos -> atencao
      concentrada em uma regiao (noite, por exemplo) -- nao e' o mesmo que
      deterioracao sistemica (vistoria, 04/09).
    - nada fora do padrao -> normal/estavel.
    """
    nucleo_fora = nucleo_comprometido(dominios)
    alerta = [n for n, d in dominios.items() if d["status"] == "alerta"]
    atencao = [n for n, d in dominios.items() if d["status"] == "atencao"]

    if alerta:
        return ("alerta -- dominio em ALERTA"
                + (f" ({', '.join(alerta)})" if len(alerta) > 1 else f" ({alerta[0]})")
                + ": resposta fora do padrao pessoal")
    if len(nucleo_fora) >= _MIN_NUCLEO_COMPROMETIDO:
        base = ("alteracao sistemica em curso -- nucleo autonomico comprometido"
                f" ({', '.join(nucleo_fora)} fora do padrao ao mesmo tempo)")
        if len(atencao) >= 2:
            base += ", com convergencia entre dominios"
        return base
    if len(atencao) >= 2:
        sinais = sinais_por_dominio(dominios)
        total = sum(len(v) for v in sinais.values())
        return (f"atencao em {len(atencao)} dominios"
                f" ({', '.join(atencao)}), puxados por {total} sinais"
                + (" -- sinal concentrado, nao deterioracao ampla"
                   if total <= len(atencao) else " -- ver convergencia"))
    if atencao:
        campos = sinais_por_dominio(dominios).get(atencao[0], [])
        return (f"atencao concentrada em {atencao[0]}"
                + (f", puxada por {', '.join(campos)}" if campos else ""))
    if any(d["status"] == "sem_dado" for d in dominios.values()):
        return "indefinido -- sem dado suficiente em algum dominio"
    return "normal/estavel dentro do padrao pessoal de 90 dias"


def leitura_carga(dominios: dict, row) -> str:
    """CARGA descrita com contexto, sem mudar o criterio (TSB e' a unica
    evidencia do estado do dominio; CTL/ATL sao contexto -- MODEL_V1.0)."""
    dominio = dominios["CARGA"]
    if dominio["status"] == "sem_dado":
        return "sem dado suficiente para classificar"
    base = f"{_STATUS_TXT.get(dominio['status'], dominio['status'])}"
    if dominio["status"] != "ok":
        return f"fora do padrao pessoal ({base})"
    # Contexto: carga aguda recente acima da cronica indica volume em
    # construcao -- descricao, nunca alerta novo (ATL/CTL nao e' evidencia).
    atl_ctl = row["atl_ctl_ratio"] if row is not None else None
    ctl = row["ctl"] if row is not None else None
    if atl_ctl is not None and ctl is not None and atl_ctl > 1:
        return ("elevada em relacao a media recente, mas nao excepcional "
                f"(ATL/CTL {atl_ctl:.2f} -- carga aguda acima da cronica, CTL {ctl:g})")
    return "normal/estavel"


def leitura_recuperacao(dominios: dict) -> str:
    """RECUPERACAO descrita pelo que a puxa -- nucleo autonomico vs. campo
    periferico isolado."""
    d = dominios["RECUPERACAO"]
    if d["status"] == "sem_dado":
        return "sem dado suficiente para classificar"
    if d["status"] == "ok":
        return "preservada -- nucleo e campos complementares dentro do padrao"
    nucleo_fora = _nucleo_fora(d["metricas"])
    periferico_fora = [c for c, m in d["metricas"].items()
                       if c not in _NUCLEO_RECUPERACAO and m.has_data
                       and m.status in ("atencao", "alerta")]
    partes = []
    if nucleo_fora:
        partes.append("nucleo autonomico fora do padrao"
                      f" ({', '.join(_ROTULO.get(c, c) for c in nucleo_fora)})")
    if periferico_fora:
        partes.append("campo(s) complementar(es) isolado(s)"
                      f" ({', '.join(_ROTULO.get(c, c) for c in periferico_fora)})")
    if not partes:
        return f"{d['status']} (sem campo individual detalhado na leitura)"
    prefixo = "comprometida" if d["status"] == "alerta" else "em atencao"
    return f"{prefixo} -- {'; '.join(partes)}"


def leitura_sono(dominios: dict) -> str:
    """SONO descrito por dimensao: duracao, arquitetura ou score -- cada uma
    com interpretacao propria (MODEL_V1.1)."""
    d = dominios["SONO"]
    if d["status"] == "sem_dado":
        return "sem dado suficiente para classificar"
    if d["status"] == "ok":
        return "dentro do padrao (duracao, arquitetura e score normais)"
    campos = sinais_por_dominio({"SONO": d}).get("SONO", [])
    dimensoes = []
    for c in campos:
        if c in ("Duracao do sono", "Tempo acordado"):
            dimensoes.append(f"duracao ({c})")
        elif c in ("Sono profundo", "Sono REM"):
            dimensoes.append(f"arquitetura ({c})")
        else:
            dimensoes.append(f"score ({c})")
    unicas = list(dict.fromkeys(dimensoes)) or ["campos individuais"]
    return (f"{'comprometido' if d['status'] == 'alerta' else 'em atencao'}"
            f" -- dimensao afetada: {', '.join(unicas)}")


def sinal_dominante(dominios: dict) -> str:
    """Um resumo do sinal que sustenta o estado do dia.

    - sem dominio fora do padrao: "nenhum sinal relevante hoje".
    - 1 dominio, varios campos: o sinal e' aquele dominio.
    - 2+ dominios: nomeia a regiao comum (noite: campos ligados ao sono e a
      medicao noturna) quando todos os campos fora estao na mesma regiao.
    """
    sinais = sinais_por_dominio(dominios)
    if not sinais:
        return "nenhum sinal relevante hoje"
    # Regiao noturna: campos que so existem/medem durante o sono ou a noite.
    noturnos = {"SpO2 minima", "SpO2 media", "Sono profundo", "Sono REM",
                "Sleep score", "Duracao do sono", "Tempo acordado",
                "FC noturna", "Stress no sono"}
    todos = [c for v in sinais.values() for c in v]
    if len(sinais) >= 2 and all(c in noturnos for c in todos):
        return f"alteracao noturna ({', '.join(todos)})"
    if len(sinais) == 1:
        dominio, campos = next(iter(sinais.items()))
        return f"sinal em {dominio}: {', '.join(campos)}"
    return f"sinais em {', '.join(sinais)}: {', '.join(todos)}"


def convergencia(dominios: dict) -> str:
    """Qualifica a convergencia da regra 8: quantos dominios e quantos sinais
    estao fora. Dois dominios borderline puxados por 1 campo cada e' uma
    convergencia FRACA (quase ruido); varios campos em dominios diferentes ao
    mesmo tempo e' uma convergencia FORTE (padrao coordenado)."""
    sinais = sinais_por_dominio(dominios)
    n_dominios = len(sinais)
    if n_dominios < 2:
        return f"sem convergencia ({n_dominios} dominio fora do padrao)" \
            if n_dominios == 1 else "sem convergencia (nenhum dominio fora do padrao)"
    total = sum(len(v) for v in sinais.values())
    mult = [n for n, v in sinais.items() if len(v) > 1]
    if len(mult) >= 2 or (mult and total >= 4):
        forca = "forte"
    elif mult:
        forca = "moderada"
    else:
        forca = "fraca"
    detalhe = ", ".join(f"{n}: {len(v)} {'sinal' if len(v) == 1 else 'sinais'}"
                        for n, v in sinais.items())
    return f"{forca} -- {n_dominios} dominios, {total} {'sinal' if total == 1 else 'sinais'} no total ({detalhe})"


def leitura_performance(performance: dict) -> str:
    """Performance descrita apenas quando ha treino intervalado real no dia
    (mesmo criterio de farol.domain.analysis._work_session, >= 2 reps)."""
    op = performance.get("output_preserved")
    if op is None:
        return "sem evidencia no dia (sem treino intervalado reconhecido)"
    if op == "no":
        return "com sinal de queda de output no treino intervalado do dia"
    if op == "yes":
        dd = performance.get("deterioration_detected")
        return "preservada no treino intervalado do dia (sem degradacao tecnica)" \
            if dd == "no" else f"preservada no treino intervalado do dia (deterioration={dd})"
    return "indeterminada (sem baseline suficiente para comparar o output)"


def leitura_confianca(dominios: dict, performance: dict) -> str:
    """Confianca da leitura: a mais fraca entre os dominios com sinal + o
    estado da evidencia de performance."""
    partes = []
    ativos = {n: d for n, d in dominios.items()
              if d["status"] in ("atencao", "alerta")}
    if not ativos:
        ativos = {n: d for n, d in dominios.items() if d["status"] != "sem_dado"}
    baixas = [n for n, d in ativos.items() if d["confianca"] == "baixa"]
    if baixas:
        partes.append(f"confianca baixa em {', '.join(baixas)}")
    elif ativos and all(d["confianca"] == "alta" for d in ativos.values()):
        partes.append("confianca alta (classificacao dos dominios com sinal)")
    else:
        partes.append("confianca media")
    if performance.get("output_preserved") is None:
        partes.append("performance nao avaliada (sem treino intervalado no dia)")
    return "; ".join(partes)


def conclusao(dominios: dict, performance: dict) -> str:
    """Conclusao da leitura, nos termos da vistoria: descreve o que existe,
    sem prescrever. Deterioracao relevante exige nucleo autonomico fora ou
    convergencia forte -- um sinal de atencao isolado e' so isso."""
    sinais = sinais_por_dominio(dominios)
    nucleo_fora = nucleo_comprometido(dominios)
    alerta = [n for n, d in dominios.items() if d["status"] == "alerta"]

    if alerta or len(nucleo_fora) >= _MIN_NUCLEO_COMPROMETIDO:
        base = ("ha evidencia de alteracao relevante: nucleo autonomico fora do "
                "padrao ou dominio em alerta")
    elif len(sinais) >= 2:
        total = sum(len(v) for v in sinais.values())
        if total >= 4 and any(len(v) > 1 for v in sinais.values()):
            base = "ha convergencia relevante entre dominios -- investigar"
        else:
            base = ("nao ha evidencia suficiente para classificar fadiga "
                    "relevante -- ha apenas um sinal de atencao (fraco, "
                    "2 dominios puxados por 1 campo cada)")
    elif len(sinais) == 1:
        dominio, campos = next(iter(sinais.items()))
        base = (f"nao ha evidencia de fadiga relevante -- apenas sinal isolado "
                f"em {dominio} ({', '.join(campos)})")
    else:
        base = "sem sinais fora do padrao -- estado dentro do esperado"

    if performance.get("output_preserved") == "no":
        base += "; atencao adicional: queda de output no treino do dia"
    return base


def interpretar(dados: dict) -> dict[str, str]:
    """Leitura completa a partir do que `farol.domain.state.gather()` ja retorna.

    `dados` tem as chaves de gather(): 'dominios' (metricas/status/confianca/
    streak/trend por dominio), 'performance' (output_preserved,
    deterioration_detected), 'row' (linha bruta de farol_daily_state, para
    contexto de carga) e 'breakpoints'/'convergencia_*'.
    """
    dominios: dict = dados["dominios"]
    performance: dict = dados.get("performance") or {}
    row = dados.get("row")
    sinais = sinais_por_dominio(dominios)

    fora = _fora_do_padrao(dominios)
    if fora:
        texto_dominios = ", ".join(fora)
        if len(fora) == 1:
            persistencia = ""
            streak = dominios[fora[0]].get("streak") or {}
            if streak.get("dias"):
                persistencia = (f", ativo ha {streak['dias']} dia(s) "
                                f"(desde {streak.get('data_inicio')})")
            dominante_txt = f"{texto_dominios}{persistencia}"
        else:
            dominante_txt = texto_dominios
    else:
        dominante_txt = "nenhum"

    leitura = {
        "estado_fisiologico": estado_fisiologico(dominios),
        "carga": leitura_carga(dominios, row),
        "recuperacao": leitura_recuperacao(dominios),
        "sono": leitura_sono(dominios),
        "sinal_dominante": sinal_dominante(dominios),
        "convergencia": convergencia(dominios),
        "performance": leitura_performance(performance),
        "confianca": leitura_confianca(dominios, performance),
        "conclusao": conclusao(dominios, performance),
        # Legado para os relatorios existentes.
        "dominios_fora": dominante_txt,
        "n_sinais": sum(len(v) for v in sinais.values()),
        "n_dominios_fora": len(fora),
        "sinais": sinais,
        "deterioracao_sistemica": bool(fora) and any(
            len(v) > 1 for v in sinais.values()),
    }
    return leitura
