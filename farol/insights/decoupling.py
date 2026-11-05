"""Decoupling aerobico classico (Pa:Hr): compara velocidade:FC da 1a metade
contra a 2a metade de UMA UNICA corrida continua.

Diferente de analysis.aerobic_efficiency/continuous_mechanics, que
comparam FC~pace ENTRE corridas ao longo da historia (tendencia de meses,
um ponto por corrida). Decoupling e' um sinal POR SESSAO: FC subindo mais
que o esperado pelo pace dentro do mesmo treino indica fadiga
cardiovascular/termica acumulando durante a corrida -- pergunta diferente
de "minha economia aerobica esta melhorando com o tempo".

Recomendacao 6 do parecer tecnico de 2026-09-04, destravada em
2026-09-04 apos confirmar que farol.pipeline.splits.fetch_lap_splits
(get_activity_splits) devolve laps reais por km para qualquer corrida
continua -- a chamada usada ate entao (get_activity_typed_splits) so
cobre treino intervalado estruturado, por isso o recurso ficou congelado
antes.

Metodo (TrainingPeaks/Uphill Athlete, Pa:Hr): divide a corrida em duas
metades por DISTANCIA acumulada (nao contagem de laps -- laps podem variar
de tamanho), calcula o Efficiency Factor (velocidade / FC media,
ponderada por distancia) de cada metade, e reporta a variacao percentual.
<5% de variacao e' comumente citado como boa base aerobica -- citado aqui
como contexto da literatura, NAO validado contra o historico deste
usuario (diferente do limiar de today.signature_doenca, que foi calibrado
contra 214 dias reais antes de virar codigo).

Uso: python -m farol.insights.decoupling [--activity-id ID]
Sem --activity-id, usa a corrida continua mais recente com >=5km no banco.
"""
import argparse
import sys
from dataclasses import dataclass

from ..core import db
from ..pipeline.garmin_client import login
from ..pipeline.splits import fetch_lap_splits

# Abaixo disso, dividir ao meio nao sobra lap suficiente por lado para o EF
# significar algo -- mesmo piso de _MIN_PONTOS_JANELA em farol/insights/changepoint.py.
_MIN_LAPS = 4

# Laps muito curtos (o ultimo lap de uma corrida costuma ser um resto
# parcial, ex.: 6m em 2s) tem pace instavel e distorceriam a metade que os
# contem -- excluidos antes de dividir.
_DISTANCIA_MIN_LAP_M = 200


@dataclass(frozen=True)
class Decoupling:
    ef_primeira_metade: float
    ef_segunda_metade: float
    decoupling_pct: float
    n_laps_primeira: int
    n_laps_segunda: int
    dist_total_km: float


def _lap_valido(lap: dict) -> bool:
    return (lap.get("distance") or 0) >= _DISTANCIA_MIN_LAP_M and lap.get("averageHR")


def _efficiency_factor(bloco: list[dict]) -> float:
    """Velocidade media (m/s) / FC media, ponderada por distancia -- um lap
    mais longo pesa mais na media de FC do bloco, nao todos os laps
    igualmente."""
    dist = sum(lap["distance"] for lap in bloco)
    dur = sum(lap["duration"] for lap in bloco)
    hr_medio = sum(lap["averageHR"] * lap["distance"] for lap in bloco) / dist
    velocidade = dist / dur
    return velocidade / hr_medio


def compute_decoupling(laps: list[dict]) -> Decoupling | None:
    """`laps` no formato bruto de get_activity_splits (lapDTOs), em ordem
    cronologica: precisa de `distance` (m), `duration` (s), `averageHR`.

    Divide por METADE DA DISTANCIA ACUMULADA (primeiro lap cujo fim ja
    passa da metade do percurso fecha a primeira metade). None com menos
    de _MIN_LAPS laps validos, ou menos de 2 laps de cada lado depois da
    divisao."""
    validos = [lap for lap in laps if _lap_valido(lap)]
    if len(validos) < _MIN_LAPS:
        return None

    dist_total = sum(lap["distance"] for lap in validos)
    meio_dist = dist_total / 2

    acumulado = 0.0
    ponto_corte = len(validos)
    for i, lap in enumerate(validos):
        acumulado += lap["distance"]
        if acumulado >= meio_dist:
            ponto_corte = i + 1
            break

    primeira, segunda = validos[:ponto_corte], validos[ponto_corte:]
    if len(primeira) < 2 or len(segunda) < 2:
        return None

    ef1, ef2 = _efficiency_factor(primeira), _efficiency_factor(segunda)
    if ef1 == 0:
        return None

    return Decoupling(
        ef_primeira_metade=round(ef1, 5),
        ef_segunda_metade=round(ef2, 5),
        decoupling_pct=round((ef1 - ef2) / ef1 * 100, 1),
        n_laps_primeira=len(primeira),
        n_laps_segunda=len(segunda),
        dist_total_km=round(dist_total / 1000, 2),
    )


def _corrida_continua_mais_recente(conn) -> tuple[int, str] | None:
    """Corrida >=5km mais recente -- corridas curtas nao tem laps
    suficientes para a divisao ao meio significar algo (ver _MIN_LAPS)."""
    row = conn.execute(
        "SELECT activity_id, date FROM activities WHERE type = 'running' AND source = 'garmin' "
        "AND activity_id > 0 AND distance_km >= 5 ORDER BY date DESC, activity_id DESC LIMIT 1"
    ).fetchone()
    return (row["activity_id"], row["date"]) if row else None


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Decoupling aerobico classico (Pa:Hr) de uma corrida continua.")
    parser.add_argument("--activity-id", type=int, default=None,
                         help="ID da atividade (padrao: corrida continua mais recente >=5km)")
    args = parser.parse_args()

    conn = db.connect_readonly()
    activity_id = args.activity_id
    activity_date = None
    if activity_id is None:
        found = _corrida_continua_mais_recente(conn)
        if not found:
            print("Nenhuma corrida >=5km no banco. Rode farol.pipeline.ingest primeiro.")
            sys.exit(1)
        activity_id, activity_date = found
    conn.close()

    g = login()
    laps = fetch_lap_splits(g, activity_id)
    resultado = compute_decoupling(laps)

    rotulo_atividade = f"atividade {activity_id}" + (f" ({activity_date})" if activity_date else "")
    if resultado is None:
        print(f"{rotulo_atividade} nao tem laps suficientes (minimo {_MIN_LAPS}) para calcular decoupling.")
        return

    print(f"\nDecoupling aerobico -- {rotulo_atividade}, {resultado.dist_total_km} km")
    print(f"  1a metade: EF {resultado.ef_primeira_metade} ({resultado.n_laps_primeira} laps)")
    print(f"  2a metade: EF {resultado.ef_segunda_metade} ({resultado.n_laps_segunda} laps)")
    print(f"  Decoupling: {resultado.decoupling_pct:+.1f}%")
    print("  (<5% e' comumente citado na literatura como boa base aerobica -- "
          "nao validado contra o seu historico ainda)")


if __name__ == "__main__":
    main()
