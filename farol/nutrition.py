"""Plano configuravel de ingestao para prova, sem inferir tolerancia individual."""
from datetime import datetime, timedelta


def _clock(start_time: str | None, minute_offset: int) -> str | None:
    if not start_time:
        return None
    try:
        base = datetime.strptime(start_time, "%H:%M")
    except ValueError:
        return None
    return (base + timedelta(minutes=minute_offset)).strftime("%H:%M")


def config_from_profile(profile: dict) -> dict:
    raw = profile.get("race_nutrition") or {}
    return {
        "gel_carbohydrate_g": float(raw.get("gel_carbohydrate_g", 27)),
        "gel_caffeine_mg": float(raw.get("gel_caffeine_mg", 75)),
        "interval_minutes": int(raw.get("interval_minutes", 30)),
        "pre_start_minutes": int(raw.get("pre_start_minutes", 15)),
        "caffeine_at_minutes": int(raw.get("caffeine_at_minutes", 60)),
        "last_gel_cutoff_minutes": int(raw.get("last_gel_cutoff_minutes", 15)),
        "coffee_before_race": bool(raw.get("coffee_before_race", False)),
    }


def build_plan(duration_min: float, config: dict, start_time: str | None = None) -> dict:
    """Monta agenda por tempo estimado, portanto serve a qualquer distancia.

    O gel pre-largada e contado no total. Um gel cafeinado substitui o gel
    regular no marco configurado; cafe opcional fica apenas como lembrete de
    um habito que o atleta ja tolera, sem estimar miligramas desconhecidos.
    """
    duration = max(1, int(round(float(duration_min))))
    carbs = float(config["gel_carbohydrate_g"])
    interval = max(1, int(config["interval_minutes"]))
    pre = max(0, int(config["pre_start_minutes"]))
    caffeine_at = max(0, int(config["caffeine_at_minutes"]))
    cutoff = max(0, int(config["last_gel_cutoff_minutes"]))
    events = [{
        "minute": -pre, "clock": _clock(start_time, -pre), "type": "gel regular",
        "carbs_g": carbs, "caffeine_mg": 0, "label": f"{pre} min antes",
    }]
    minute = interval
    caffeine_used = False
    while minute <= duration - cutoff:
        caffeinated = not caffeine_used and minute >= caffeine_at
        events.append({
            "minute": minute, "clock": _clock(start_time, minute),
            "type": "Ultra Gel" if caffeinated else "gel regular",
            "carbs_g": carbs, "caffeine_mg": float(config["gel_caffeine_mg"]) if caffeinated else 0,
            "label": f"{minute} min de prova",
        })
        caffeine_used = caffeine_used or caffeinated
        minute += interval
    return {
        "duration_min": duration, "events": events,
        "total_carbs_g": round(sum(event["carbs_g"] for event in events), 1),
        "total_caffeine_mg": round(sum(event["caffeine_mg"] for event in events), 1),
        "carbs_per_hour": round(sum(event["carbs_g"] for event in events) / duration * 60, 1),
        "coffee_before_race": bool(config["coffee_before_race"]),
    }
