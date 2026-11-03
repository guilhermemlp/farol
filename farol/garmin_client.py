"""Login e extração completa do Garmin Connect.

Padrão de login (token salvo, com fallback para email/senha na primeira vez)
portado de pulse/garmin_standalone.py. Cada endpoint é isolado com `_safe`
para que uma falha pontual da API do Garmin não derrube a sincronização
inteira — mesmo padrão usado em StrideHealth/AthletIQ/projeto.

fetch_day/fetch_activities retornam dicts JÁ NO VOCABULÁRIO das abas da
planilha (mesmos nomes de coluna de farol/sheets.py) — pedido explícito do
usuário para não perder nenhuma métrica que a planilha já suporta. Mapeamento
de campo bruto do Garmin -> coluna: portado de pulse/garmin_standalone.py
(`adapt_stats`/`adapt_sleep`/`adapt_hrv`/`adapt_activity`).
"""
import json
import os
import sys

from garminconnect import Garmin

from .config import GARMIN_EMAIL, GARMIN_PASSWORD, TOKEN_STORE


def login() -> Garmin:
    g = Garmin()
    try:
        g.login(TOKEN_STORE)
        return g
    except Exception:
        pass
    if not GARMIN_EMAIL or not GARMIN_PASSWORD:
        print(
            "Defina GARMIN_EMAIL e GARMIN_PASSWORD no .env "
            "(necessário só na primeira sincronização, para criar o token salvo)."
        )
        sys.exit(1)
    g = Garmin(email=GARMIN_EMAIL, password=GARMIN_PASSWORD)
    g.login()
    os.makedirs(TOKEN_STORE, exist_ok=True)
    g.garth.dump(TOKEN_STORE)
    return g


def _safe(fn, *args, erros: list[str] | None = None):
    """Chama `fn(*args)` e devolve None se falhar (isolamento de endpoint).

    F9 (auditoria): quando `erros` e' passado, o nome do endpoint que falhou
    entra na lista -- a falha deixa de ser silenciosa e chega a `daily.errors`,
    para o relatorio poder dizer "hoje veio sem SpO2: endpoint tal falhou" em
    vez de mostrar apenas um campo vazio sem explicacao."""
    try:
        return fn(*args)
    except Exception:
        if erros is not None:
            erros.append(fn.__name__)
        return None


def _daily_row(date_str: str, stats: dict, hrv_summary: dict, status: dict, readiness: dict, resp: dict, erros: list[str]) -> dict:
    moderate = stats.get("moderateIntensityMinutes")
    vigorous = stats.get("vigorousIntensityMinutes")
    intensity = (moderate or 0) + (vigorous or 0) if (moderate or vigorous) else None
    return {
        "date": date_str,
        "steps": stats.get("totalSteps"),
        "floors": stats.get("floorsAscended"),
        "calories_total": stats.get("totalKilocalories"),
        "calories_active": stats.get("activeKilocalories"),
        "resting_hr": stats.get("restingHeartRate"),
        "max_hr": stats.get("maxHeartRate"),
        "avg_hr": None,  # Garmin não expõe FC média do dia inteiro fora de atividades
        "stress_avg": stats.get("averageStressLevel"),
        "stress_duration_minutes": round(stats["stressDuration"] / 60, 1) if stats.get("stressDuration") else None,
        "body_battery_max": stats.get("bodyBatteryHighestValue"),
        "body_battery_min": stats.get("bodyBatteryLowestValue"),
        "spo2_avg": stats.get("averageSpo2"),
        "spo2_min": stats.get("lowestSpo2"),
        "respiration_avg": resp.get("avgWakingRespirationValue"),
        "intensity_minutes": intensity,
        "hydration_ml": stats.get("hydrationValueInML") or stats.get("hydrationIntake"),
        "hrv_avg": hrv_summary.get("lastNightAvg"),
        "hrv_status": hrv_summary.get("status"),
        "training_readiness_score": readiness[0].get("score") if readiness else None,
        "training_status": status.get("training_status"),
        "vo2max": status.get("vo2_max"),
        # F9: endpoints que falharam no _safe, separados por '; ' -- legivel
        # na planilha e parseavel no relatorio. None quando a extracao foi
        # completa (nada a registrar).
        "errors": "; ".join(erros) if erros else None,
        "recovery_time_minutes": readiness[0].get("recoveryTime") if readiness else None,
    }


def _sleep_row(date_str: str, sleep_raw: dict) -> dict:
    dto = sleep_raw.get("dailySleepDTO") or {}
    # sleepScores vem aninhado DENTRO de dailySleepDTO na API atual do Garmin
    # -- não no nível raiz como pulse/garmin_standalone.py assumia (bug real,
    # confirmado contra resposta ao vivo em 2026-09-02).
    overall = (dto.get("sleepScores") or sleep_raw.get("sleepScores") or {}).get("overall") or {}
    sleep_seconds = dto.get("sleepTimeSeconds")
    return {
        "date": date_str,
        "sleep_start": dto.get("sleepStartTimestampGMT"),
        "sleep_end": dto.get("sleepEndTimestampGMT"),
        "total_sleep_minutes": round(sleep_seconds / 60, 1) if sleep_seconds else None,
        "deep_sleep_minutes": round(dto["deepSleepSeconds"] / 60, 1) if dto.get("deepSleepSeconds") else None,
        "light_sleep_minutes": round(dto["lightSleepSeconds"] / 60, 1) if dto.get("lightSleepSeconds") else None,
        "rem_sleep_minutes": round(dto["remSleepSeconds"] / 60, 1) if dto.get("remSleepSeconds") else None,
        "awake_minutes": round(dto["awakeSleepSeconds"] / 60, 1) if dto.get("awakeSleepSeconds") else None,
        "awakenings": sleep_raw.get("restlessMomentsCount"),
        "sleep_score": overall.get("value"),
        "sleep_quality": overall.get("qualifierKey"),
        "avg_sleep_stress": dto.get("avgSleepStress"),
        "night_hr_avg": dto.get("avgHeartRate"),  # não "restingHeartRate" -- campo não existe na API atual
        "spo2_avg_sleep": dto.get("averageSpO2Value"),
        "spo2_min_sleep": dto.get("lowestSpO2Value"),
        "spo2_max_sleep": dto.get("highestSpO2Value"),
    }


def _status_fields(raw: dict) -> dict:
    if not raw:
        return {}
    mr = raw.get("mostRecentTrainingStatus") or {}
    dev_map = mr.get("latestTrainingStatusData") if isinstance(mr, dict) else None
    latest = next(iter(dev_map.values()), {}) if isinstance(dev_map, dict) and dev_map else {}
    vo2 = (raw.get("mostRecentVO2Max") or {}).get("generic") or {}
    return {
        "training_status": latest.get("trainingStatus"),
        "vo2_max": vo2.get("vo2MaxPreciseValue") or vo2.get("vo2MaxValue"),
    }


def _hrv_row(date_str: str, hrv_raw: dict) -> dict:
    import json

    summary = hrv_raw.get("hrvSummary") or {}
    return {
        "date": date_str,
        "hrv_avg": summary.get("lastNightAvg"),
        "hrv_status": summary.get("status"),
        "weekly_avg": summary.get("weeklyAvg"),
        "last_night_5min_high": summary.get("lastNight5MinHigh"),
        "baseline": json.dumps(summary.get("baseline")) if summary.get("baseline") else None,
        "raw": json.dumps(hrv_raw, default=str) if hrv_raw else None,
    }


def fetch_day(g: Garmin, date_str: str) -> dict:
    """Wellness diário completo. Retorna {"daily": {...}, "sleep": {...},
    "hrv": {...}} com as chaves já no vocabulário das abas Daily Health/
    Sleep/HRV."""
    # F9: uma lista compartilhada por todas as chamadas do dia. Cada _safe
    # que falhar registra o nome do endpoint aqui; no fim vira daily.errors.
    erros: list[str] = []
    stats = _safe(g.get_stats, date_str, erros=erros) or {}
    sleep_raw = _safe(g.get_sleep_data, date_str, erros=erros) or {}
    hrv_raw = _safe(g.get_hrv_data, date_str, erros=erros) or {}
    status_raw = _safe(g.get_training_status, date_str, erros=erros) or {}
    readiness_raw = _safe(g.get_training_readiness, date_str, erros=erros) or []
    resp_raw = _safe(g.get_respiration_data, date_str, erros=erros) or {}

    hrv_summary = hrv_raw.get("hrvSummary") or {}
    status = _status_fields(status_raw)
    readiness = readiness_raw if isinstance(readiness_raw, list) else [readiness_raw] if readiness_raw else []

    return {
        "daily": _daily_row(date_str, stats, hrv_summary, status, readiness, resp_raw, erros),
        "sleep": _sleep_row(date_str, sleep_raw),
        "hrv": _hrv_row(date_str, hrv_raw),
    }


def fetch_activities(g: Garmin, start: str, end: str) -> list[dict]:
    """Extrai cada atividade completa no vocabulário da aba ``Activities``.

    A listagem por data do Garmin é só um índice: varia por tipo de atividade
    e não traz de forma confiável GPS, voltas, dinâmica de corrida ou potência.
    O detalhe individual (``get_activity``) é a fonte canônica dos campos de
    resumo; ``get_activity_splits`` complementa as voltas. A resposta da
    listagem fica apenas como contingência para que uma falha pontual no
    detalhe não impeça a sincronização inteira.
    """

    def first_present(*values):
        return next((value for value in values if value is not None), None)

    def field(detail: dict, listing: dict, *names):
        summary = detail.get("summaryDTO") or {}
        for name in names:
            value = first_present(summary.get(name), listing.get(name))
            if value is not None:
                return value
        return None

    raw = _safe(g.get_activities_by_date, start, end) or []
    out = []
    for a in raw:
        activity_id = a.get("activityId")
        detail = _safe(g.get_activity, activity_id) if activity_id else None
        detail = detail or {}
        lap_data = _safe(g.get_activity_splits, activity_id) if activity_id else None
        laps = (lap_data or {}).get("lapDTOs") or []
        first_with_gps = next(
            (
                lap for lap in laps
                if lap.get("startLatitude") is not None
                and lap.get("startLongitude") is not None
            ),
            None,
        )
        start_latitude = field(detail, a, "startLatitude")
        start_longitude = field(detail, a, "startLongitude")
        gps = (
            f"{start_latitude},{start_longitude}"
            if start_latitude is not None and start_longitude is not None
            else (
                f"{first_with_gps['startLatitude']},{first_with_gps['startLongitude']}"
                if first_with_gps else None
            )
        )
        duration_s = field(detail, a, "duration")
        distance_m = field(detail, a, "distance")
        duration_min = round(duration_s / 60, 2) if duration_s else None
        distance_km = round(distance_m / 1000, 3) if distance_m else None
        avg_speed_mps = field(detail, a, "averageSpeed")
        max_speed_mps = field(detail, a, "maxSpeed")
        activity_type = (detail.get("activityTypeDTO") or {}).get("typeKey")
        activity_type = activity_type or (a.get("activityType") or {}).get("typeKey")
        out.append(
            {
                "date": (field(detail, a, "startTimeLocal") or start)[:10],
                "activity_id": activity_id,
                "type": activity_type,
                "name": first_present(detail.get("activityName"), a.get("activityName")),
                "start_time": field(detail, a, "startTimeLocal"),
                "duration_minutes": duration_min,
                "distance_km": distance_km,
                "avg_pace_min_km": round(duration_min / distance_km, 3) if duration_min and distance_km else None,
                "avg_speed_kmh": round(avg_speed_mps * 3.6, 3) if avg_speed_mps else None,
                "max_speed_kmh": round(max_speed_mps * 3.6, 3) if max_speed_mps else None,
                "calories": field(detail, a, "calories"),
                "training_effect_aerobic": field(detail, a, "trainingEffect", "aerobicTrainingEffect"),
                "training_effect_anaerobic": field(detail, a, "anaerobicTrainingEffect"),
                "avg_hr": field(detail, a, "averageHR"),
                "max_hr": field(detail, a, "maxHR"),
                "avg_cadence": field(
                    detail, a, "averageRunCadence", "averageRunningCadenceInStepsPerMinute",
                    "averageBikeCadence", "averageCadence",
                ),
                "stride_length": field(detail, a, "strideLength", "avgStrideLength"),
                "ground_contact_time": field(detail, a, "groundContactTime", "avgGroundContactTime"),
                "vertical_oscillation": field(detail, a, "verticalOscillation", "avgVerticalOscillation"),
                "elevation_gain_m": field(detail, a, "elevationGain"),
                "elevation_loss_m": field(detail, a, "elevationLoss"),
                "avg_power": field(detail, a, "averagePower", "avgPower"),
                "max_power": field(detail, a, "maxPower"),
                "gps": gps,
                "splits": json.dumps(lap_data, default=str) if lap_data else None,
            }
        )
    return out
