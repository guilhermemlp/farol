"""Zonas de FC: ancoradas em LTHR (limiar de lactato, método Garmin) quando
o perfil traz `lthr`/`hr_zone_floors` — mais precisas que Karvonen, porque
vêm do próprio dispositivo calibrado, não de uma fórmula genérica. Caem
para Karvonen (% da reserva cardíaca) quando esses dados não existem.
Portado/simplificado de garmin-intervals-sheets/src/analysis/zones.py.
"""

ZONE_NAMES = ["Recuperação", "Base aeróbica", "Ritmo", "Limiar", "VO2máx"]
KARVONEN_RANGES = [0.50, 0.60, 0.70, 0.80, 0.90, 1.00]  # 5 zonas, 6 limites


def _zones_from_lthr(hr_max: float, lthr: float, floors: dict) -> list[dict]:
    edges = [floors["z1"], floors["z2"], floors["z3"], floors["z4"], lthr, hr_max]
    return [
        {"zone": f"Z{i + 1}", "name": ZONE_NAMES[i], "hr_min": round(edges[i]), "hr_max": round(edges[i + 1])}
        for i in range(5)
    ]


def _zones_from_karvonen(hr_max: float, hr_rest: float) -> list[dict]:
    reserve = hr_max - hr_rest
    edges = [hr_rest + reserve * pct for pct in KARVONEN_RANGES]
    return [
        {"zone": f"Z{i + 1}", "name": ZONE_NAMES[i], "hr_min": round(edges[i]), "hr_max": round(edges[i + 1])}
        for i in range(5)
    ]


def heart_rate_zones(profile: dict) -> list[dict]:
    """Zonas de FC a partir do perfil (dict de profile.yaml). Usa LTHR se
    `lthr` e `hr_zone_floors` (z1-z4) estiverem presentes; senão Karvonen."""
    floors = profile.get("hr_zone_floors")
    lthr = profile.get("lthr")
    if lthr and floors and all(k in floors for k in ("z1", "z2", "z3", "z4")):
        return _zones_from_lthr(profile["hr_max"], lthr, floors)
    return _zones_from_karvonen(profile["hr_max"], profile["hr_rest"])
