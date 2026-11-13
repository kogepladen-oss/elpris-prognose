"""Indlæsning af konfiguration og faste stier."""
from __future__ import annotations

import tomllib
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
SITE = ROOT / "docs"
REPORTS = ROOT / "reports"

AREAS = ["DK1", "DK2", "DE", "NO2", "SE3", "SE4"]
WEATHER_VARS = ["wind_speed_100m", "shortwave_radiation", "temperature_2m"]
MAX_LEAD = 7  # Open-Meteo Previous Runs giver previous_day1..7


@dataclass(frozen=True)
class Point:
    name: str
    lat: float
    lon: float


def load(path: Path | None = None) -> dict:
    path = path or ROOT / "config.toml"
    with open(path, "rb") as f:
        cfg = tomllib.load(f)
    cfg["weather"]["points"] = [Point(**p) for p in cfg["weather"]["points"]]
    return cfg
