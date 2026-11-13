"""Skriver docs/forecast.json, som den statiske side i docs/index.html læser."""
from __future__ import annotations

import json
import math

import numpy as np
import pandas as pd

from .config import SITE
from .timeutil import local_date

DAYS_BEFORE = [2, 3, 5, 7]


def _arr(x, nd=2):
    return [None if (v is None or (isinstance(v, float) and math.isnan(v))) else round(float(v), nd)
            for v in x]


def build_json(now, prices, comp, live, res, metrics7, metrics30, warnings, cfg, meta) -> dict:
    start = (now - pd.Timedelta(days=7)).floor("h")
    end = (now + pd.Timedelta(days=7)).ceil("h")
    idx = pd.date_range(start, end, freq="h")
    actual = prices["DK1"].reindex(idx)
    c = comp.reindex(idx)
    out = {
        "generated_at": now.isoformat(),
        "title": cfg["site"]["title"],
        "model_version": meta.get("model_version"),
        "anchor_date": meta.get("anchor_date"),
        "warnings": warnings,
        "wind_source": meta.get("wind_source"),
        "vat": cfg["pricing"]["vat_rate"],
        "pays_grid": cfg["pricing"]["pays_grid_tariffs_and_tax"],
        "charging": cfg["charging"],
        "t": [t.isoformat() for t in idx],
        "actual": _arr(actual),
        "grid": _arr(c["grid"]), "energinet": _arr(c["energinet"]),
        "elafgift": _arr(c["elafgift"]), "addon": _arr(c["addon"]),
        "metrics7": metrics7, "metrics30": metrics30, "past": {},
    }
    if live is not None and not live.empty:
        lv = live.set_index(pd.DatetimeIndex(live["target_utc"])).reindex(idx)
        out["live"] = {q: _arr(lv[q]) for q in ("p10", "p50", "p90")}
        out["live_issued_at"] = str(live["issued_at"].iloc[0])
    if res is not None and not res.empty:
        # også ikke-afgjorte prognoser for timer, hvor prisen allerede kendes, er med i res
        for db in DAYS_BEFORE:
            r = res[res["days_before"] == db]
            r = r.drop_duplicates("target_utc").set_index(pd.DatetimeIndex(r["target_utc"])).reindex(idx)
            out["past"][str(db)] = {q: _arr(r[q]) for q in ("p10", "p50", "p90")}
            out["past"][str(db)]["kind"] = [None if pd.isna(k) else k for k in r["kind"]]
    return out


def write(data: dict) -> None:
    SITE.mkdir(parents=True, exist_ok=True)
    with open(SITE / "forecast.json", "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, separators=(",", ":"))
