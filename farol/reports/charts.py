"""Primitivas SVG do dashboard -- camada de visualizacao, nada mais.

Entram series ja lidas do banco, sai string SVG. Estas funcoes nao abrem
conexao, nao recalculam metrica e nao classificam nada: valor, baseline,
direcao e status chegam prontos de farol.domain. Desenhar no servidor
deixa o HTML imprimivel e legivel sem JavaScript.

As escalas aqui sao puramente visuais (min/max da serie no sparkline,
media +/- 2,5 sd no bullet) -- nenhuma delas e' limiar fisiologico e
nenhuma decide cor ou status.
"""
import html
from datetime import date as _date


def _esc(v) -> str:
    return html.escape("" if v is None else str(v))


def _c(v: float) -> str:
    """Coordenada SVG: 1 casa, sem '-0'."""
    texto = f"{v:.1f}"
    return "0" if texto == "-0" else texto


def _values(serie) -> list[float]:
    """Aceita [(data, valor), ...] ou [valor, ...]; ignora buracos."""
    if not serie:
        return []
    if isinstance(serie[0], (tuple, list)):
        return [float(item[1]) for item in serie if item[1] is not None]
    return [float(v) for v in serie if v is not None]


def _pace(valor: float) -> str:
    minutos = int(valor)
    segundos = round((valor - minutos) * 60)
    if segundos == 60:
        minutos += 1
        segundos = 0
    return f"{minutos}:{segundos:02d}"


def sparkline_svg(serie, baseline=None, *, width=280, height=90, title="",
                  desc="", unit="", digits=0) -> str:
    """Linha de tendencia de uma metrica, com a baseline pessoal tracejada.

    `serie` e' a janela curta (30 dias no dashboard) e `baseline` a media
    movel que ja veio do Metric -- a funcao so posiciona os dois na mesma
    escala visual. Serie vazia devolve "" (quem chama decide o fallback).
    """
    valores = _values(serie)
    if not valores:
        return ""

    pad_l, pad_r, pad_t, pad_b = 4.0, 4.0, 10.0, 8.0
    lo, hi = min(valores), max(valores)
    if baseline is not None:
        lo, hi = min(lo, float(baseline)), max(hi, float(baseline))
    span = hi - lo
    if span <= 0:
        lo, hi, span = lo - 1.0, hi + 1.0, 2.0
    margem = span * 0.15
    lo, hi = lo - margem, hi + margem

    n = len(valores)

    def x(i: int) -> float:
        return pad_l + (width - pad_l - pad_r) * (0.0 if n == 1 else i / (n - 1))

    def y(v: float) -> float:
        return pad_t + (height - pad_t - pad_b) * (1 - (v - lo) / (hi - lo))

    pontos = " ".join(f"{_c(x(i))},{_c(y(v))}" for i, v in enumerate(valores))
    area = ("M" + _c(x(0)) + "," + _c(height) + " L"
            + " L".join(f"{_c(x(i))},{_c(y(v))}" for i, v in enumerate(valores))
            + " L" + _c(x(n - 1)) + "," + _c(height) + " Z")

    linha_base = ""
    if baseline is not None:
        linha_base = (
            f'<line class="spark-base" x1="{_c(pad_l)}" x2="{_c(width - pad_r)}" '
            f'y1="{_c(y(float(baseline)))}" y2="{_c(y(float(baseline)))}"/>'
        )

    ultimo = f"{valores[-1]:.{digits}f}{unit}"
    titulo = title or "Serie recente"
    descricao = desc or (f"{n} ponto(s); minimo {min(valores):.{digits}f}, "
                         f"maximo {max(valores):.{digits}f}; ultimo {ultimo}")
    return "".join([
        f'<svg class="spark" viewBox="0 0 {width} {height}" role="img" focusable="false">',
        f"<title>{_esc(titulo)}</title><desc>{_esc(descricao)}</desc>",
        f'<path class="spark-area" d="{area}"/>',
        linha_base,
        f'<polyline class="spark-line" points="{pontos}"/>',
        f'<circle class="spark-dot" cx="{_c(x(n - 1))}" cy="{_c(y(valores[-1]))}" r="3.5"/>',
        "</svg>",
    ])


def bullet_svg(value, mean=None, sd=None, *, status="ok", width=200, height=22,
               title="", unit="", digits=1) -> str:
    """Bullet chart: faixa normal (+/- 1 sd), linha da media e marcador de hoje.

    Mostra magnitude e posicao na propria variacao, o que uma barra de
    z-score a partir do centro nao mostra. Sem media/sd, cai para um trilho
    com o marcador centralizado -- cobertura ausente nao vira alerta.
    """
    if value is None:
        return ""
    v = float(value)
    m = float(mean) if mean is not None else None
    s = float(sd) if sd is not None else None
    centro = m if m is not None else v
    if s is not None and s > 0:
        lo, hi = centro - 2.5 * s, centro + 2.5 * s
        if v < lo:
            lo = v - 0.5 * s
        if v > hi:
            hi = v + 0.5 * s
    else:
        margem = max(abs(centro) * 0.05, 0.5)
        lo, hi = min(centro, v) - margem, max(centro, v) + margem

    def x(val: float) -> float:
        return 5.0 + (width - 10.0) * (val - lo) / (hi - lo)

    meio = height / 2.0
    trilho = f'<rect class="bullet-track" x="0" y="{_c(meio - 3)}" width="{_c(width)}" height="6" rx="3"/>'
    faixa = ""
    if m is not None and s is not None and s > 0:
        x0, x1 = x(centro - s), x(centro + s)
        faixa = (f'<rect class="bullet-band" x="{_c(x0)}" y="{_c(meio - 6)}" '
                 f'width="{_c(max(0.0, x1 - x0))}" height="12" rx="2"/>')
    media = ""
    if m is not None:
        media = (f'<line class="bullet-mean" x1="{_c(x(centro))}" x2="{_c(x(centro))}" '
                 f'y1="{_c(meio - 8)}" y2="{_c(meio + 8)}"/>')

    classe = status if status in ("ok", "atencao", "alerta") else "ok"
    rotulo = f"{v:.{digits}f}{unit}"
    if m is not None and s is not None:
        contexto = f"normal {m:.{digits}f}{unit} +/- {s:.{digits}f}"
    elif m is not None:
        contexto = f"normal {m:.{digits}f}{unit}"
    else:
        contexto = "sem baseline"
    titulo = title or "Metrica"
    descricao = f"{titulo}: hoje {rotulo}; {contexto}"

    return "".join([
        f'<svg class="bullet" viewBox="0 0 {width} {height}" role="img" focusable="false">',
        f"<title>{_esc(descricao)}</title><desc>{_esc(contexto)}</desc>",
        trilho, faixa, media,
        f'<circle class="bullet-dot bullet-{classe}" cx="{_c(x(v))}" cy="{_c(meio)}" r="4.5"/>',
        "</svg>",
    ])


def _pace_domain(points: list[dict], prediction=None) -> tuple[float, float]:
    valores = [float(p["pace"]) for p in points]
    lo, hi = min(valores), max(valores)
    if prediction:
        lo = min(lo, float(prediction["min"]))
        hi = max(hi, float(prediction["max"]))
    span = (hi - lo) or 0.5
    return lo - span * 0.15, hi + span * 0.15


def _classe_ponto(point: dict) -> str:
    if point.get("race"):
        return "dot-race"
    if point.get("scope") == "segmentos ativos":
        return "dot-active"
    return "dot-normal"


def _ponto_desc(point: dict) -> str:
    partes = [str(point.get("date") or ""), str(point.get("name") or "Corrida"),
              _pace(float(point["pace"])), str(point.get("scope") or "")]
    if point.get("hr") is not None:
        partes.append(f"{round(float(point['hr']))} bpm")
    return " - ".join(p for p in partes if p)


def _performance_desc(points: list[dict]) -> str:
    datas = [str(p.get("date") or "") for p in points if p.get("date")]
    periodo = f" entre {datas[0]} e {datas[-1]}" if len(datas) > 1 else (f" em {datas[0]}" if datas else "")
    paces = [float(p["pace"]) for p in points]
    return (f"{len(points)} sessao(oes){periodo}; pace de {_pace(min(paces))} a "
            f"{_pace(max(paces))} por km")


def pace_trend_svg(points: list[dict], prediction=None, *, width=520, height=240,
                   title="", desc="") -> str:
    """Pace por sessao, com faixa prevista e reta de tendencia -- SVG estatico."""
    if not points:
        return ""
    pl, pr, pt, pb = 46.0, 14.0, 20.0, 30.0
    lo, hi = _pace_domain(points, prediction)
    n = len(points)

    def x(i: int) -> float:
        return pl + (width - pl - pr) * (0.0 if n == 1 else i / (n - 1))

    def y(v: float) -> float:
        return pt + (height - pt - pb) * (v - lo) / (hi - lo)

    eixos = []
    for q in (0.0, 0.5, 1.0):
        yy = pt + (height - pt - pb) * q
        eixos.append(f'<line class="grid" x1="{_c(pl)}" x2="{_c(width - pr)}" '
                     f'y1="{_c(yy)}" y2="{_c(yy)}"/>')
        eixos.append(f'<text class="axis-label" x="2" y="{_c(yy + 4)}">'
                     f'{_esc(_pace(lo + (hi - lo) * q))}</text>')

    faixa = ""
    if prediction:
        topo = y(float(prediction["min"]))
        base = y(float(prediction["max"]))
        faixa = (f'<rect class="pred-band" x="{_c(pl)}" y="{_c(topo)}" '
                 f'width="{_c(width - pl - pr)}" height="{_c(max(2.0, base - topo))}"/>'
                 f'<text class="axis-label" x="{_c(width - pr)}" y="{_c(max(10.0, topo - 4))}" '
                 f'text-anchor="end">faixa prevista</text>')

    tendencia = ""
    if n > 1:
        media_x = (n - 1) / 2.0
        media_y = sum(float(p["pace"]) for p in points) / n
        num = sum((i - media_x) * (float(p["pace"]) - media_y) for i, p in enumerate(points))
        den = sum((i - media_x) ** 2 for i in range(n))
        inclinacao = num / den if den else 0.0
        tendencia = (f'<line class="trend" x1="{_c(x(0))}" x2="{_c(x(n - 1))}" '
                     f'y1="{_c(y(media_y - inclinacao * media_x))}" '
                     f'y2="{_c(y(media_y + inclinacao * ((n - 1) - media_x)))}"/>')

    pontos = "".join(
        f'<circle class="perf-dot {_classe_ponto(p)}" cx="{_c(x(i))}" cy="{_c(y(float(p["pace"])))}" '
        f'r="{5 if p.get("race") else 3.5}"><title>{_esc(_ponto_desc(p))}</title></circle>'
        for i, p in enumerate(points)
    )
    return "".join([
        f'<svg class="perf" viewBox="0 0 {width} {height}" role="img" focusable="false">',
        f"<title>{_esc(title or 'Pace por sessao')}</title>",
        f"<desc>{_esc(desc or _performance_desc(points))}</desc>",
        *eixos, faixa, tendencia, pontos,
        "</svg>",
    ])


def pace_hr_svg(points: list[dict], *, width=520, height=240,
                title="", desc="") -> str:
    """Dispersao pace x FC das sessoes com FC registrada."""
    com_fc = [p for p in points if p.get("hr") is not None]
    if not com_fc:
        return ('<p class="chart-empty">Sem frequencia cardiaca registrada nas '
                'sessoes do periodo.</p>')
    pl, pr, pt, pb = 46.0, 14.0, 20.0, 30.0
    lo, hi = _pace_domain(com_fc)
    horas = [float(p["hr"]) for p in com_fc]
    h_lo, h_hi = min(horas), max(horas)
    if h_hi - h_lo < 1:
        h_lo, h_hi = h_lo - 1.0, h_hi + 1.0
    margem = (h_hi - h_lo) * 0.12
    h_lo, h_hi = h_lo - margem, h_hi + margem
    def sx(v: float) -> float:
        return pl + (width - pl - pr) * (v - h_lo) / (h_hi - h_lo)

    def y(v: float) -> float:
        return pt + (height - pt - pb) * (v - lo) / (hi - lo)

    eixos = []
    for q in (0.0, 0.5, 1.0):
        xx = pl + (width - pl - pr) * q
        eixos.append(f'<line class="grid" x1="{_c(xx)}" x2="{_c(xx)}" '
                     f'y1="{_c(pt)}" y2="{_c(height - pb)}"/>')
        eixos.append(f'<text class="axis-label" x="{_c(xx)}" y="{_c(height - 10)}" '
                     f'text-anchor="middle">{round(h_lo + (h_hi - h_lo) * q)} bpm</text>')

    pontos = "".join(
        f'<circle class="perf-dot {_classe_ponto(p)}" cx="{_c(sx(float(p["hr"])))}" '
        f'cy="{_c(y(float(p["pace"])))}" r="{5 if p.get("race") else 3.5}">'
        f'<title>{_esc(_ponto_desc(p))}</title></circle>'
        for p in com_fc
    )
    return "".join([
        f'<svg class="perf" viewBox="0 0 {width} {height}" role="img" focusable="false">',
        f"<title>{_esc(title or 'Pace por frequencia cardiaca')}</title>",
        f"<desc>{_esc(desc or _performance_desc(com_fc))}</desc>",
        *eixos, pontos,
        "</svg>",
    ])


def _segmentos(series: list[dict], campo: str) -> list[list[tuple[_date, float]]]:
    """Quebra a serie em trechos continuos -- buraco de dia nao vira reta."""
    segmentos: list[list[tuple[_date, float]]] = []
    atual: list[tuple[_date, float]] = []
    anterior = None
    for linha in series:
        valor = linha.get(campo)
        dia = _date.fromisoformat(str(linha.get("date"))[:10])
        if valor is None:
            if atual:
                segmentos.append(atual)
                atual = []
            anterior = None
            continue
        if anterior is not None and (dia - anterior).days > 1:
            segmentos.append(atual)
            atual = []
        atual.append((dia, float(valor)))
        anterior = dia
    if atual:
        segmentos.append(atual)
    return segmentos


def pmc_svg(series: list[dict], annotations=None, *, inicio: str, fim: str,
            width=900, height=270, title="", desc="") -> str:
    """PMC de 180 dias: CTL (forma), ATL (fadiga), TSB (saldo) e anotacoes.

    Le a serie de `training_load` como esta' -- nenhuma constante de tempo e'
    recalculada aqui. Buraco de dia nao vira interpolacao: a linha quebra e a
    barra de TSB simplesmente nao existe naquele dia.
    """
    if not series:
        return ""
    fim_d = _date.fromisoformat(str(fim)[:10])
    inicio_d = _date.fromisoformat(str(inicio)[:10])
    total = max(1, (fim_d - inicio_d).days)
    valores = [float(linha[c]) for linha in series for c in ("ctl", "atl", "tsb")
               if linha.get(c) is not None]
    if not valores:
        return ""

    pl, pr, pt, pb = 36.0, 12.0, 28.0, 24.0
    lo, hi = min(0.0, min(valores)), max(0.0, max(valores))
    span = (hi - lo) or 1.0
    lo, hi = lo - span * 0.08, hi + span * 0.08
    interno = width - pl - pr

    def x(dia: _date) -> float:
        return pl + interno * ((dia - inicio_d).days / total)

    def y(valor: float) -> float:
        return pt + (height - pt - pb) * (hi - valor) / (hi - lo)

    eixos = []
    for q in (0.0, 0.5, 1.0):
        yy = pt + (height - pt - pb) * q
        eixos.append(f'<line class="grid" x1="{_c(pl)}" x2="{_c(width - pr)}" '
                     f'y1="{_c(yy)}" y2="{_c(yy)}"/>')
        # y() coloca o valor MAXIMO no topo; o rotulo tem que seguir a mesma
        # direcao (o contrario invertia a leitura do eixo).
        eixos.append(f'<text class="axis-label" x="2" y="{_c(yy + 4)}">'
                     f'{_esc(f"{hi - (hi - lo) * q:.0f}")}</text>')
    eixos.append(f'<line class="pmc-zero" x1="{_c(pl)}" x2="{_c(width - pr)}" '
                 f'y1="{_c(y(0.0))}" y2="{_c(y(0.0))}"/>')

    largura_barra = max(1.5, interno / total * 0.8)
    barras = []
    for linha in series:
        tsb = linha.get("tsb")
        dia = _date.fromisoformat(str(linha.get("date"))[:10])
        if tsb is None or dia < inicio_d or dia > fim_d:
            continue
        topo, base = y(float(tsb)), y(0.0)
        classe = "tsb-pos" if float(tsb) >= 0 else "tsb-neg"
        barras.append(f'<rect class="{classe}" x="{_c(x(dia) - largura_barra / 2)}" '
                      f'y="{_c(min(topo, base))}" width="{_c(largura_barra)}" '
                      f'height="{_c(abs(base - topo))}"/>')

    linhas = []
    for campo, classe in (("ctl", "pmc-ctl"), ("atl", "pmc-atl")):
        for segmento in _segmentos(series, campo):
            pontos = " ".join(f"{_c(x(d))},{_c(y(v))}" for d, v in segmento)
            linhas.append(f'<polyline class="pmc-line {classe}" points="{pontos}"/>')

    marcas = []
    usadas = 0
    # Duas linhas de rotulo. Se o texto encostaria em outro ja' desenhado na
    # mesma linha, ele nao e' desenhado -- a linha vertical e o <title> ficam,
    # e a lista de notas abaixo do grafico sempre traz todos os marcos.
    ocupadas: list[list[tuple[float, float]]] = [[], []]
    for i, anotacao in enumerate(annotations or []):
        dia = _date.fromisoformat(str(anotacao.get("date"))[:10])
        if dia < inicio_d or dia > fim_d:
            continue
        xx = x(dia)
        tipo = anotacao.get("kind") or "quebra"
        if tipo not in ("prova", "lesao", "quebra"):
            tipo = "quebra"
        rotulo_completo = str(anotacao.get("label") or "")
        rotulo = (rotulo_completo if len(rotulo_completo) <= 26
                  else rotulo_completo[:25] + "…")
        largura_texto = min(len(rotulo) * 5.6 + 8.0, width - 24.0)
        meio_texto = largura_texto / 2.0
        texto_x = min(max(xx, meio_texto), width - meio_texto)
        x0, x1 = texto_x - meio_texto, texto_x + meio_texto
        linha_livre = None
        for candidata in (i % 2, (i + 1) % 2):
            if all(x1 < a or x0 > b for a, b in ocupadas[candidata]):
                linha_livre = candidata
                break
        if linha_livre is None:
            texto = ""
        else:
            ocupadas[linha_livre].append((x0, x1))
            texto_y = 10.0 if linha_livre == 0 else 21.0
            texto = (f'<text class="anno-label anno-{tipo}" x="{_c(texto_x)}" '
                     f'y="{_c(texto_y)}" text-anchor="middle">{_esc(rotulo)}</text>')
        marcas.append(
            f'<line class="anno-line anno-{tipo}" x1="{_c(xx)}" x2="{_c(xx)}" '
            f'y1="{_c(pt)}" y2="{_c(height - pb)}">'
            f'<title>{_esc(rotulo_completo)} ({_esc(anotacao.get("date"))})</title></line>'
            f'{texto}')
        usadas += 1

    ultima = series[-1]
    resumo = ""
    if ultima.get("ctl") is not None:
        resumo = (f" ultimo dia {ultima.get('date')}: CTL {float(ultima['ctl']):.1f}, "
                  f"ATL {float(ultima['atl']):.1f}, TSB {float(ultima['tsb']):+.1f}.")
    descricao = desc or (f"{len(series)} dias de carga.{resumo} "
                         f"{usadas} marco(s): provas, lesoes e quebras de medicao.")
    return "".join([
        f'<svg class="pmc" viewBox="0 0 {width} {height}" role="img" focusable="false">',
        f"<title>{_esc(title or 'PMC de 180 dias')}</title><desc>{_esc(descricao)}</desc>",
        *eixos, *barras, *linhas, *marcas,
        "</svg>",
    ])
