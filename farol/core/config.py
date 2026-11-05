import os
from pathlib import Path

import yaml
from dotenv import load_dotenv

# farol/core/config.py -> sobe tres niveis ate a raiz do repositorio (onde
# vivem pyproject.toml, data/ e reports/).
ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = ROOT / "data"
REPORTS_DIR = ROOT / "reports"
load_dotenv(ROOT / ".env")

DB_PATH = os.environ.get("FAROL_DB_PATH", str(DATA_DIR / "farol.db"))
SHEET_PATH = os.environ.get("FAROL_SHEET_PATH", str(DATA_DIR / "garmin_health.xlsx"))
INJURY_LOG_PATH = os.environ.get("FAROL_INJURY_LOG_PATH", str(DATA_DIR / "injury_log.json"))
RACE_LOG_PATH = os.environ.get("FAROL_RACE_LOG_PATH", str(DATA_DIR / "race_log.json"))
GARMIN_EMAIL = os.environ.get("GARMIN_EMAIL")
GARMIN_PASSWORD = os.environ.get("GARMIN_PASSWORD")
TOKEN_STORE = os.environ.get("FAROL_TOKEN_STORE", str(ROOT / ".garmin_token"))

# sleep_start/sleep_end (Garmin) vem em epoch ms UTC, sem conversao de fuso
# em nenhum lugar do pipeline ate agora. -3 (America/Sao_Paulo, sem horario
# de verao desde 2019) e o fuso do perfil documentado em PROFILE.md.
# Ajustavel via env var para quem rodar o farol em outro fuso -- deliberado
# nao usar zoneinfo/tzdata aqui: o Windows nao traz a base IANA por padrao
# (exigiria nova dependencia) e o Brasil nao tem transicao de horario de
# verao para complicar um offset fixo.
TZ_OFFSET_HOURS = int(os.environ.get("FAROL_TZ_OFFSET_HOURS", "-3"))

_PROFILE_PATH = DATA_DIR / "profile.yaml"


def load_profile() -> dict:
    if not _PROFILE_PATH.exists():
        raise FileNotFoundError(
            f"profile.yaml não encontrado em {_PROFILE_PATH}. "
            "Ver PROFILE.md para os dados e o formato esperado."
        )
    return yaml.safe_load(_PROFILE_PATH.read_text(encoding="utf-8")) or {}
