"""Fecha o loop de calibracao: le CALIBRACAO_LOG.txt e mede concordancia
entre o indice do farol e a percepcao real preenchida a mao. Com notas
numericas opcionais suficientes, tambem mostra associacoes exploratorias
entre subscores e percepcao, sem editar pesos.

Recomendacao 10 do parecer tecnico de 2026-09-04. BATEU COM A REALIDADE
continua a alimentar o resumo depois de 14 entradas; uma sugestao de revisao
de pesos exige 30 dias pareados com nota numerica e resposta manual, alem de
discordancia predominante. Nunca altera o modelo por conta propria.
Este modulo e' so leitura de texto -- nunca altera CALIBRACAO_LOG.txt, o
banco ou farol_daily_state.

Uso: python -m farol.calibration_review [--log CAMINHO]
"""
import argparse
import re
import sys
from dataclasses import dataclass
from statistics import StatisticsError, correlation

from .config import REPORTS_DIR

_LOG_PATH = REPORTS_DIR / "CALIBRACAO_LOG.txt"

# Mesmo numero que CALIBRACAO.md e o docstring de calibration.py ja citam --
# nao um limiar novo inventado aqui.
_MIN_ENTRADAS = 14
# Peso exploratorio so e' sugerido com amostra maior que o minimo usado para
# a revisao manual. O limite coincide com o minimo do modulo correlations;
# ainda assim, as observacoes diarias podem ser autocorrelacionadas.
_MIN_NOTAS_NUMERICAS = 30
_MARGEM_SUGESTAO = 0.20

_RE_ENTRADA = re.compile(
    r"={60}\nDATA: (\d{4}-\d{2}-\d{2})\n={60}\n(.*?)(?=\n={60}\nDATA: |\Z)",
    re.DOTALL,
)
_RE_BATEU = re.compile(
    r"BATEU COM A REALIDADE\?[^\n]*\n-[ \t]*(\S[^\n]*)?$", re.MULTILINE,
)


def _campo(bloco: str, rotulo: str) -> str | None:
    m = re.search(rf"^- {re.escape(rotulo)}: *(.*)$", bloco, re.MULTILINE)
    valor = m.group(1).strip() if m else None
    return valor or None


def _bateu_com_a_realidade(bloco: str) -> str | None:
    """Texto cru da resposta (ex.: 'sim', 'nao', 'parcial', ou uma frase
    livre), ou None se a entrada ainda esta em branco (so '-')."""
    m = _RE_BATEU.search(bloco)
    if not m or not m.group(1):
        return None
    return m.group(1).strip()


def _classifica(bateu: str) -> str:
    """Primeira palavra, normalizada -- CALIBRACAO.md permite texto livre
    ('sim, mas...'), entao agregamos pela primeira palavra em vez de exigir
    resposta exata. 'outro' para qualquer coisa fora de sim/nao/parcial,
    nunca descartado da contagem."""
    primeira = bateu.strip().lower().split()[0].rstrip(",.:;")
    return primeira if primeira in ("sim", "nao", "não", "parcial") else "outro"


@dataclass(frozen=True)
class Entrada:
    date: str
    indice_txt: str | None
    convergencia_txt: str | None
    concordancia_garmin: str | None
    bateu: str | None
    componentes: dict[str, float]
    percepcao_numerica: float | None


def _componentes(bloco: str) -> dict[str, float]:
    """Le subscores persistidos no log; entradas antigas simplesmente nao os tem."""
    encontrados = re.findall(
        r"^  - ([a-z_]+): ([0-9]+(?:\.[0-9]+)?)$", bloco, re.MULTILINE,
    )
    return {nome: float(valor) for nome, valor in encontrados}


def _percepcao_numerica(bloco: str) -> float | None:
    m = re.search(
        r"^- Nota numerica de recuperacao \(0-10, opcional\):\s*([0-9]+(?:[.,][0-9]+)?)\s*$",
        bloco, re.MULTILINE,
    )
    if not m:
        return None
    valor = float(m.group(1).replace(",", "."))
    return valor if 0 <= valor <= 10 else None


def parse_log(texto: str) -> list[Entrada]:
    """Uma Entrada por bloco DATA: no log, na ordem em que aparecem."""
    saida = []
    for m in _RE_ENTRADA.finditer(texto):
        data, bloco = m.group(1), m.group(2)
        saida.append(Entrada(
            date=data,
            indice_txt=_campo(bloco, "INDICE DE RECUPERACAO"),
            convergencia_txt=_campo(bloco, "CONVERGENCIA"),
            concordancia_garmin=_campo(bloco, "CONCORDANCIA COM O INDICE FAROL"),
            bateu=_bateu_com_a_realidade(bloco),
            componentes=_componentes(bloco),
            percepcao_numerica=_percepcao_numerica(bloco),
        ))
    return saida


def _ranks(values: list[float]) -> list[float]:
    ordenados = sorted(enumerate(values), key=lambda item: item[1])
    saida = [0.0] * len(values)
    inicio = 0
    while inicio < len(ordenados):
        fim = inicio + 1
        while fim < len(ordenados) and ordenados[fim][1] == ordenados[inicio][1]:
            fim += 1
        posto_medio = ((inicio + 1) + fim) / 2
        for indice, _ in ordenados[inicio:fim]:
            saida[indice] = posto_medio
        inicio = fim
    return saida


def _spearman(xs: list[float], ys: list[float]) -> float | None:
    try:
        return correlation(_ranks(xs), _ranks(ys))
    except StatisticsError:
        return None


def _revisao_pesos(entradas: list[Entrada]) -> list[str]:
    com_nota = [e for e in entradas if e.percepcao_numerica is not None]
    avaliadas = [e for e in com_nota if e.bateu is not None]
    preenchidas = [e for e in entradas if e.bateu is not None]
    linhas = [
        "## Sugestao exploratoria de revisao dos pesos",
        "",
        f"Notas numericas preenchidas: {len(com_nota)}; dias com nota e BATEU preenchidos: "
        f"{len(avaliadas)}. A analise exige {_MIN_NOTAS_NUMERICAS} dias pareados, "
        "com subscore disponivel.",
        f"BATEU preenchido no log: {len(preenchidas)} dia(s); a sugestao exige `nao`/`parcial` "
        "em maioria nos dias pareados.",
        "",
    ]
    if len(avaliadas) < _MIN_NOTAS_NUMERICAS:
        linhas.append(
            f"Ainda nao ha base para sugerir um componente. Faltam "
            f"{_MIN_NOTAS_NUMERICAS - len(avaliadas)} dia(s) com nota e BATEU preenchidos."
        )
        linhas.append("Preencha manualmente a nota numerica e BATEU COM A REALIDADE? nas entradas futuras.")
        return linhas

    componentes = sorted(set.union(*(set(e.componentes) for e in avaliadas)))
    relacoes = []
    for componente in componentes:
        pares = [(e.componentes[componente], e.percepcao_numerica)
                 for e in avaliadas if componente in e.componentes and e.percepcao_numerica is not None]
        if len(pares) < _MIN_NOTAS_NUMERICAS:
            continue
        rho = _spearman([p[0] for p in pares], [p[1] for p in pares])
        if rho is not None:
            relacoes.append((componente, rho, len(pares)))

    if not relacoes:
        linhas.append("Nao ha variacao suficiente nos subscores para estimar associacoes.")
        return linhas

    linhas.append("Associacao de Spearman entre subscore do Farol e nota percebida (descritiva, sem p-valor):")
    for componente, rho, n in sorted(relacoes, key=lambda item: item[1], reverse=True):
        linhas.append(f"- {componente}: rho={rho:+.2f}, n={n}")

    divergentes = [e for e in avaliadas if _classifica(e.bateu) in ("nao", "não", "parcial")]
    if len(divergentes) <= len(avaliadas) / 2:
        linhas += [
            "",
            "Sem sugestao de peso: o log ainda nao mostra discordancia manual predominante "
            "(`nao`/`parcial` em mais da metade das respostas preenchidas).",
        ]
        return linhas

    por_forca = sorted(relacoes, key=lambda item: abs(item[1]), reverse=True)
    mais_forte = por_forca[0]
    margem_clara = (len(por_forca) == 1
                    or abs(mais_forte[1]) - abs(por_forca[1][1]) >= _MARGEM_SUGESTAO)
    if abs(mais_forte[1]) >= _MARGEM_SUGESTAO and margem_clara:
        direcao = "aumento" if mais_forte[1] > 0 else "reducao"
        linhas += [
            "",
            f"Candidato para revisao: avaliar um pequeno {direcao} relativo do peso de "
            f"`{mais_forte[0]}` em um teste controlado do perfil.",
        ]
    else:
        linhas += ["", "As associacoes nao destacam um componente com margem clara; sem sugestao de direcao de ajuste."]

    linhas += [
        "",
        "Isto e uma pista exploratoria, nao uma estimativa causal nem uma mudanca automatica. "
        "As observacoes diarias podem ser autocorrelacionadas; revise contexto e valide qualquer teste antes de alterar pesos.",
    ]
    return linhas


def build_report(texto: str) -> str:
    entradas = parse_log(texto)
    preenchidas = [e for e in entradas if e.bateu is not None]

    linhas = [
        "# REVISAO DE CALIBRACAO -- farol.calibration_review",
        "",
        f"{len(entradas)} entrada(s) no log, {len(preenchidas)} com "
        "'BATEU COM A REALIDADE?' preenchido.",
        "",
    ]

    if len(preenchidas) < _MIN_ENTRADAS:
        linhas += [
            f"Menos de {_MIN_ENTRADAS} entradas preenchidas -- CALIBRACAO.md e' "
            "explicito: nenhuma decisao sobre o modelo antes disso. Faltam "
            f"{_MIN_ENTRADAS - len(preenchidas)} entrada(s).",
            "",
            "Preencha PERCEPCAO REAL e BATEU COM A REALIDADE? em "
            "CALIBRACAO_LOG.txt depois de rodar 'python -m farol.calibration' "
            "no dia a dia -- este relatorio nao inventa estatistica com "
            "amostra pequena demais.",
            "",
            *_revisao_pesos(entradas),
        ]
        return "\n".join(linhas) + "\n"

    contagem: dict[str, int] = {}
    for e in preenchidas:
        c = _classifica(e.bateu)
        contagem[c] = contagem.get(c, 0) + 1
    total = len(preenchidas)

    linhas.append("## Distribuicao de BATEU COM A REALIDADE?")
    linhas.append("")
    for chave in ("sim", "parcial", "nao", "não", "outro"):
        if chave in contagem:
            n = contagem[chave]
            linhas.append(f"- {chave}: {n}/{total} ({n / total:.0%})")
    linhas.append("")

    com_garmin = [e for e in preenchidas if e.concordancia_garmin]
    if com_garmin:
        concordantes = [e for e in com_garmin if e.concordancia_garmin == "concorda"]
        c_sim = [e for e in concordantes if _classifica(e.bateu) == "sim"]
        discordantes = [e for e in com_garmin if e.concordancia_garmin != "concorda"
                         and "sem dado" not in e.concordancia_garmin]
        d_sim = [e for e in discordantes if _classifica(e.bateu) == "sim"]
        linhas.append("## Farol bate mais com a realidade quando concorda com o Garmin?")
        linhas.append("")
        if concordantes:
            linhas.append(
                f"- Farol e Garmin concordaram em {len(concordantes)} dia(s); "
                f"'sim' em {len(c_sim)}/{len(concordantes)} ({len(c_sim) / len(concordantes):.0%})."
            )
        if discordantes:
            linhas.append(
                f"- Farol e Garmin divergiram em {len(discordantes)} dia(s); "
                f"'sim' em {len(d_sim)}/{len(discordantes)} ({len(d_sim) / len(discordantes):.0%})."
            )
        if not concordantes or not discordantes:
            linhas.append("- Amostra ainda concentrada demais num dos dois grupos para comparar.")
        linhas.append("")

    linhas += _revisao_pesos(entradas)
    linhas.append("")

    linhas += [
        "## Leitura",
        "",
        "Descritivo, nao prescritivo (mesma regra de PROFILE.md que vale para "
        "o resto do farol): isto mostra ONDE olhar antes de mudar pesos do "
        "recovery_index ou limiares de detect_patterns, nunca decide sozinho. "
        "Releia as OBSERVACOES das entradas 'nao'/'parcial' antes de qualquer "
        "mudanca de codigo.",
    ]
    return "\n".join(linhas) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Revisa CALIBRACAO_LOG.txt: concordancia entre o indice do farol e a percepcao real.")
    parser.add_argument("--log", default=str(_LOG_PATH), help="Caminho do log")
    args = parser.parse_args()

    try:
        with open(args.log, encoding="utf-8") as f:
            texto = f.read()
    except FileNotFoundError:
        print(f"{args.log} nao existe ainda. Rode 'python -m farol.calibration' primeiro.")
        sys.exit(1)

    print(build_report(texto))


if __name__ == "__main__":
    main()
