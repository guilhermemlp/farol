"""Leitura e resumo de percurso GPX, separado de dados fisiologicos.

O modulo descreve o percurso oficial antes da prova (distancia, traçado e
elevacao do arquivo fornecido). Ele nao altera CARGA, RECUPERACAO, SONO ou
qualquer regra de convergencia do Farol.

Uso:
    python -m farol.insights.course "C:/caminho/percurso.gpx"
"""
import argparse
import math
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path


_EARTH_RADIUS_M = 6_371_000
_ELEVATION_NOISE_FLOOR_M = 3.0


@dataclass(frozen=True)
class CoursePoint:
    latitude: float
    longitude: float
    elevation_m: float | None


@dataclass(frozen=True)
class CourseProfile:
    point_count: int
    distance_km: float
    start_latitude: float
    start_longitude: float
    finish_latitude: float
    finish_longitude: float
    elevation_min_m: float | None
    elevation_max_m: float | None
    raw_gain_m: float | None
    raw_loss_m: float | None
    filtered_gain_m: float | None
    filtered_loss_m: float | None
    elevation_noise_floor_m: float


def parse_gpx_xml(xml_text: str) -> list[CoursePoint]:
    """Extrai pontos de track ou route de um GPX, preservando a ordem.

    A extensao XML/namespace nao e' usada como identidade da rota: qualquer
    elemento `trkpt` ou `rtept` e' aceito, inclusive GPX sem namespace.
    """
    root = ET.fromstring(xml_text)
    points: list[CoursePoint] = []
    for element in root.iter():
        if element.tag.rsplit("}", 1)[-1] not in {"trkpt", "rtept"}:
            continue
        try:
            latitude = float(element.attrib["lat"])
            longitude = float(element.attrib["lon"])
        except (KeyError, ValueError) as exc:
            raise ValueError("Ponto GPX sem latitude/longitude valida.") from exc

        elevation = None
        for child in element:
            if child.tag.rsplit("}", 1)[-1] == "ele" and child.text:
                elevation = float(child.text)
                break
        points.append(CoursePoint(latitude, longitude, elevation))

    if len(points) < 2:
        raise ValueError("O GPX precisa conter ao menos dois pontos de percurso.")
    return points


def load_gpx(path: Path) -> list[CoursePoint]:
    return parse_gpx_xml(path.read_text(encoding="utf-8"))


def _distance_m(first: CoursePoint, second: CoursePoint) -> float:
    lat1, lon1, lat2, lon2 = map(
        math.radians,
        (first.latitude, first.longitude, second.latitude, second.longitude),
    )
    sin_lat = math.sin((lat2 - lat1) / 2)
    sin_lon = math.sin((lon2 - lon1) / 2)
    a = sin_lat**2 + math.cos(lat1) * math.cos(lat2) * sin_lon**2
    return _EARTH_RADIUS_M * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def _elevation_totals(points: list[CoursePoint], noise_floor_m: float) -> tuple[float | None, float | None, float | None, float | None]:
    elevations = [point.elevation_m for point in points]
    if any(elevation is None for elevation in elevations):
        return None, None, None, None

    values = [float(elevation) for elevation in elevations]
    raw_gain = sum(max(current - previous, 0) for previous, current in zip(values, values[1:]))
    raw_loss = sum(max(previous - current, 0) for previous, current in zip(values, values[1:]))

    # Histerese: so acumula uma mudanca apos ela superar o piso de ruido.
    # O ganho bruto continua no relatorio para auditoria; o filtrado nao e'
    # declarado como ganho oficial do organizador.
    reference = values[0]
    filtered_gain = 0.0
    filtered_loss = 0.0
    for current in values[1:]:
        delta = current - reference
        if delta >= noise_floor_m:
            filtered_gain += delta
            reference = current
        elif delta <= -noise_floor_m:
            filtered_loss -= delta
            reference = current
    return raw_gain, raw_loss, filtered_gain, filtered_loss


def summarize_course(points: list[CoursePoint], noise_floor_m: float = _ELEVATION_NOISE_FLOOR_M) -> CourseProfile:
    if noise_floor_m <= 0:
        raise ValueError("O piso de ruido de elevacao precisa ser positivo.")

    elevations = [point.elevation_m for point in points if point.elevation_m is not None]
    raw_gain, raw_loss, filtered_gain, filtered_loss = _elevation_totals(points, noise_floor_m)
    return CourseProfile(
        point_count=len(points),
        distance_km=round(sum(_distance_m(first, second) for first, second in zip(points, points[1:])) / 1000, 2),
        start_latitude=points[0].latitude,
        start_longitude=points[0].longitude,
        finish_latitude=points[-1].latitude,
        finish_longitude=points[-1].longitude,
        elevation_min_m=round(min(elevations), 1) if elevations else None,
        elevation_max_m=round(max(elevations), 1) if elevations else None,
        raw_gain_m=round(raw_gain, 1) if raw_gain is not None else None,
        raw_loss_m=round(raw_loss, 1) if raw_loss is not None else None,
        filtered_gain_m=round(filtered_gain, 1) if filtered_gain is not None else None,
        filtered_loss_m=round(filtered_loss, 1) if filtered_loss is not None else None,
        elevation_noise_floor_m=noise_floor_m,
    )


def format_profile(profile: CourseProfile, source: Path) -> str:
    elevation = "sem elevacao no GPX"
    if profile.elevation_min_m is not None:
        elevation = (
            f"{profile.elevation_min_m:.1f}–{profile.elevation_max_m:.1f} m; "
            f"ganho/perda brutos {profile.raw_gain_m:.1f}/{profile.raw_loss_m:.1f} m; "
            f"ganho/perda filtrados {profile.filtered_gain_m:.1f}/{profile.filtered_loss_m:.1f} m "
            f"(piso de ruido {profile.elevation_noise_floor_m:.1f} m)"
        )
    return "\n".join((
        f"Perfil do percurso — {source.name}",
        f"  Pontos: {profile.point_count}",
        f"  Distancia do tracado: {profile.distance_km:.2f} km",
        f"  Inicio: {profile.start_latitude:.6f}, {profile.start_longitude:.6f}",
        f"  Chegada: {profile.finish_latitude:.6f}, {profile.finish_longitude:.6f}",
        f"  Elevacao: {elevation}",
        "  Nota: elevacao filtrada e' uma leitura do GPX, nao altimetria oficial.",
    ))


def main() -> None:
    parser = argparse.ArgumentParser(description="Resume um percurso GPX para contexto de prova.")
    parser.add_argument("gpx", type=Path, help="Arquivo GPX do percurso")
    parser.add_argument("--noise-floor-m", type=float, default=_ELEVATION_NOISE_FLOOR_M,
                        help="Mudanca minima de elevacao para acumular ganho/perda (padrao: 3 m)")
    args = parser.parse_args()
    profile = summarize_course(load_gpx(args.gpx), args.noise_floor_m)
    print(format_profile(profile, args.gpx))


if __name__ == "__main__":
    main()
