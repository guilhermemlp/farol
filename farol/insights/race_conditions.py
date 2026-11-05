"""Contexto externo de percurso e clima para uma prova.

Esta camada nao alimenta CARGA, RECUPERACAO, SONO ou convergencia. Ela guarda
condicoes observaveis para interpretar e calibrar uma prova, preservando a
distincao entre dados Garmin, percurso oficial e modelos meteorologicos.

Uso:
    python -m farol.insights.race_conditions percurso.gpx --start 2026-09-19T18:00
"""
import argparse
import json
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import urlopen

from .course import CoursePoint, _distance_m, load_gpx

_ELEVATION_URL = "https://api.open-meteo.com/v1/elevation"
_FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
_ARCHIVE_URL = "https://archive-api.open-meteo.com/v1/archive"
_ELEVATION_BATCH_SIZE = 100


@dataclass(frozen=True)
class RouteSample:
    distance_km: float
    latitude: float
    longitude: float


@dataclass(frozen=True)
class WeatherPoint:
    label: str
    latitude: float
    longitude: float
    time: str
    temperature_c: float | None
    apparent_temperature_c: float | None
    humidity_pct: float | None
    dew_point_c: float | None
    precipitation_mm: float | None
    wind_speed_kmh: float | None
    wind_direction_deg: float | None
    wind_gusts_kmh: float | None
    cloud_cover_pct: float | None
    shortwave_radiation_wm2: float | None


def sample_route(points: list[CoursePoint], every_m: float = 500) -> list[RouteSample]:
    """Amostra o traco em distancias regulares, incluindo exatamente inicio/fim.

    Interpolacao linear entre dois pontos GPX consecutivos e' adequada para
    pontos a cada poucas centenas de metros; o GPX original continua sendo a
    fonte de geometria, nao a amostra derivada.
    """
    if every_m <= 0:
        raise ValueError("O intervalo de amostragem precisa ser positivo.")

    segment_lengths = [_distance_m(first, second) for first, second in zip(points, points[1:])]
    total_m = sum(segment_lengths)
    targets = [0.0]
    next_target = every_m
    while next_target < total_m:
        targets.append(next_target)
        next_target += every_m
    targets.append(total_m)

    samples: list[RouteSample] = []
    segment_index = 0
    segment_start_m = 0.0
    for target in targets:
        while (segment_index < len(segment_lengths) - 1
               and target > segment_start_m + segment_lengths[segment_index]):
            segment_start_m += segment_lengths[segment_index]
            segment_index += 1
        length = segment_lengths[segment_index]
        fraction = 0.0 if length == 0 else (target - segment_start_m) / length
        first, second = points[segment_index], points[segment_index + 1]
        samples.append(RouteSample(
            distance_km=round(target / 1000, 3),
            latitude=first.latitude + (second.latitude - first.latitude) * fraction,
            longitude=first.longitude + (second.longitude - first.longitude) * fraction,
        ))
    return samples


def _get_json(url: str) -> dict:
    with urlopen(url, timeout=30) as response:  # noqa: S310 -- URL e' constante do modulo
        return json.loads(response.read().decode("utf-8"))


def fetch_route_elevation(samples: list[RouteSample], fetcher=_get_json) -> list[float]:
    """Busca elevacao do terreno Open-Meteo para os pontos, em lotes de 100."""
    elevations: list[float] = []
    for start in range(0, len(samples), _ELEVATION_BATCH_SIZE):
        batch = samples[start:start + _ELEVATION_BATCH_SIZE]
        query = urlencode({
            "latitude": ",".join(f"{point.latitude:.6f}" for point in batch),
            "longitude": ",".join(f"{point.longitude:.6f}" for point in batch),
        })
        payload = fetcher(f"{_ELEVATION_URL}?{query}")
        values = payload.get("elevation")
        if not isinstance(values, list) or len(values) != len(batch):
            raise ValueError("Resposta de elevacao nao corresponde aos pontos solicitados.")
        elevations.extend(float(value) for value in values)
    return elevations


def _hourly_value(hourly: dict, key: str, timestamp: str) -> float | None:
    times = hourly.get("time", [])
    try:
        index = times.index(timestamp)
    except ValueError:
        return None
    values = hourly.get(key, [])
    return float(values[index]) if index < len(values) and values[index] is not None else None


def fetch_weather(samples: list[RouteSample], start: datetime, expected_duration_min: float, fetcher=_get_json) -> list[WeatherPoint]:
    """Busca clima horario no inicio, meio e fim do percurso.

    A data futura usa Forecast; uma data passada usa Archive. O horario vem
    do usuario e e' mantido sem conversao: a chamada pede timezone explicito.
    """
    if start.tzinfo is None:
        raise ValueError("Informe o horario de largada com fuso, ex.: 2026-09-19T18:00-03:00.")
    if expected_duration_min <= 0:
        raise ValueError("A duracao esperada precisa ser positiva.")
    indices = (0, len(samples) // 2, len(samples) - 1)
    labels = ("inicio", "meio", "fim")
    selected = [samples[index] for index in indices]
    # A origem recebe explicitamente America/Fortaleza. O inicio precisa vir
    # com offset -03:00 e nao depende de zoneinfo/tzdata, que nao acompanha
    # o Python deste Windows (mesma politica de farol.core.config).
    if start.utcoffset() is None or start.utcoffset().total_seconds() != -3 * 3600:
        raise ValueError("Informe a largada no fuso de Fortaleza (-03:00), ex.: 2026-09-19T18:00-03:00.")
    now = datetime.now(start.tzinfo).date()
    endpoint = _ARCHIVE_URL if start.date() < now else _FORECAST_URL
    expected_finish = start + timedelta(minutes=expected_duration_min)
    fields = ",".join((
        "temperature_2m", "apparent_temperature", "relative_humidity_2m", "dew_point_2m",
        "precipitation", "wind_speed_10m", "wind_direction_10m", "wind_gusts_10m",
        "cloud_cover", "shortwave_radiation",
    ))
    query = urlencode({
        "latitude": ",".join(f"{point.latitude:.6f}" for point in selected),
        "longitude": ",".join(f"{point.longitude:.6f}" for point in selected),
        "hourly": fields,
        "timezone": "America/Fortaleza",
        "start_date": start.date().isoformat(),
        "end_date": expected_finish.date().isoformat(),
        "wind_speed_unit": "kmh",
    })
    payload = fetcher(f"{endpoint}?{query}")
    responses = payload if isinstance(payload, list) else [payload]
    if len(responses) != len(selected):
        raise ValueError("Resposta de clima nao corresponde aos pontos solicitados.")

    passage_times = (start, start + timedelta(minutes=expected_duration_min / 2), expected_finish)
    weather: list[WeatherPoint] = []
    for label, point, passage_time, response in zip(labels, selected, passage_times, responses):
        timestamp = passage_time.replace(minute=0, second=0, microsecond=0).strftime("%Y-%m-%dT%H:00")
        hourly = response.get("hourly", {})
        weather.append(WeatherPoint(
            label=label, latitude=point.latitude, longitude=point.longitude, time=timestamp,
            temperature_c=_hourly_value(hourly, "temperature_2m", timestamp),
            apparent_temperature_c=_hourly_value(hourly, "apparent_temperature", timestamp),
            humidity_pct=_hourly_value(hourly, "relative_humidity_2m", timestamp),
            dew_point_c=_hourly_value(hourly, "dew_point_2m", timestamp),
            precipitation_mm=_hourly_value(hourly, "precipitation", timestamp),
            wind_speed_kmh=_hourly_value(hourly, "wind_speed_10m", timestamp),
            wind_direction_deg=_hourly_value(hourly, "wind_direction_10m", timestamp),
            wind_gusts_kmh=_hourly_value(hourly, "wind_gusts_10m", timestamp),
            cloud_cover_pct=_hourly_value(hourly, "cloud_cover", timestamp),
            shortwave_radiation_wm2=_hourly_value(hourly, "shortwave_radiation", timestamp),
        ))
    return weather


def format_conditions(samples: list[RouteSample], elevations: list[float], weather: list[WeatherPoint]) -> str:
    elevation_min, elevation_max = min(elevations), max(elevations)
    lines = [
        "Condicoes externas da prova",
        f"  Pontos amostrados no percurso: {len(samples)} (a cada ~{samples[1].distance_km - samples[0].distance_km:.1f} km)",
        f"  Elevacao Open-Meteo: {elevation_min:.1f}–{elevation_max:.1f} m (DEM de 90 m; nao oficial)",
        "  Clima horario Open-Meteo:",
    ]
    for point in weather:
        lines.append(
            f"    {point.label} ({point.time}): {point.temperature_c} C, sensacao {point.apparent_temperature_c} C, "
            f"UR {point.humidity_pct}%, vento {point.wind_speed_kmh} km/h @ {point.wind_direction_deg}°, "
            f"rajadas {point.wind_gusts_kmh} km/h, chuva {point.precipitation_mm} mm"
        )
    lines.append("  Nota: fontes externas sao contexto de prova, nao sinais fisiologicos.")
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description="Busca elevacao e clima externos para um percurso GPX.")
    parser.add_argument("gpx", type=Path, help="Arquivo GPX do percurso")
    parser.add_argument("--start", required=True, type=datetime.fromisoformat,
                        help="Largada com fuso ISO 8601, ex.: 2026-09-19T18:00-03:00")
    parser.add_argument("--duration-min", required=True, type=float,
                        help="Duracao esperada para associar inicio/meio/fim a horarios do clima")
    parser.add_argument("--every-m", type=float, default=500, help="Amostragem do percurso em metros (padrao: 500)")
    parser.add_argument("--snapshot", type=Path, help="Caminho opcional para salvar a consulta e seus dados brutos")
    args = parser.parse_args()

    samples = sample_route(load_gpx(args.gpx), args.every_m)
    raw_requests = []

    def captured_fetch(url: str) -> dict:
        payload = _get_json(url)
        raw_requests.append({"url": url, "payload": payload})
        return payload

    elevations = fetch_route_elevation(samples, captured_fetch)
    weather = fetch_weather(samples, args.start, args.duration_min, captured_fetch)
    print(format_conditions(samples, elevations, weather))
    if args.snapshot:
        args.snapshot.write_text(json.dumps({
            "source_gpx": str(args.gpx), "queried_at": datetime.now().astimezone().isoformat(),
            "race_start": args.start.isoformat(), "route_samples": [asdict(point) for point in samples],
            "expected_duration_min": args.duration_min, "elevation_m": elevations,
            "weather": [asdict(point) for point in weather], "raw_requests": raw_requests,
        }, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
