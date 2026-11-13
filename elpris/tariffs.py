"""Priskomponenter pr. time. Alt uden moms; moms lægges på præcis én gang i total_price()."""
from __future__ import annotations

import numpy as np
import pandas as pd

from .timeutil import to_local

WINTER_MONTHS = {10, 11, 12, 1, 2, 3}


def _band(hour: np.ndarray) -> np.ndarray:
    """0: 00-06, 1: 06-17, 2: 17-21, 3: 21-24 (dansk tid)."""
    return np.select([hour < 6, hour < 17, hour < 21], [0, 1, 2], 3)


def grid_fallback(idx: pd.DatetimeIndex, cfg: dict) -> pd.Series:
    loc = to_local(idx)
    tab = cfg["pricing"]["grid_fallback_ore_ex_vat"]
    winter = np.array(tab["winter"])
    summer = np.array(tab["summer"])
    band = _band(loc.hour.to_numpy())
    is_winter = np.isin(loc.month.to_numpy(), list(WINTER_MONTHS))
    return pd.Series(np.where(is_winter, winter[band], summer[band]), index=idx, name="grid")


def grid_from_pricelist(idx: pd.DatetimeIndex, records: pd.DataFrame) -> pd.Series:
    """N1-tarif pr. time ud fra DatahubPricelist (DKK/kWh uden moms -> øre/kWh).

    Price1 er timen 00-01 dansk tid. Mangler en time-pris, gælder Price1 hele døgnet.
    Flere koder (fx CD og CD R) lægges sammen.
    """
    loc = to_local(idx).tz_localize(None)
    out = np.full(len(idx), np.nan)
    rec = records.copy()
    rec["ValidFrom"] = pd.to_datetime(rec["ValidFrom"])
    rec["ValidTo"] = pd.to_datetime(rec["ValidTo"]).fillna(pd.Timestamp("2100-01-01"))
    hours = loc.hour.to_numpy()
    total = np.zeros(len(idx))
    seen = np.zeros(len(idx), dtype=bool)
    for _, grp in rec.groupby("ChargeTypeCode"):
        grp = grp.sort_values("ValidFrom")
        for _, r in grp.iterrows():
            m = (loc >= r["ValidFrom"]) & (loc < r["ValidTo"])
            if not m.any():
                continue
            prices = np.array([r.get(f"Price{i}") for i in range(1, 25)], dtype=float)
            prices = np.where(np.isnan(prices), prices[0], prices)
            total[m] += prices[hours[m]] * 100.0
            seen |= m
    out[seen] = total[seen]
    return pd.Series(out, index=idx, name="grid")


def _by_year(idx: pd.DatetimeIndex, table: dict) -> pd.Series:
    years = to_local(idx).year
    known = {int(k): float(v) for k, v in table.items()}
    last = known[max(known)]
    return pd.Series([known.get(y, last) for y in years], index=idx)


def components(idx: pd.DatetimeIndex, cfg: dict, pricelist: pd.DataFrame | None) -> tuple[pd.DataFrame, list[str]]:
    """Returnerer øre/kWh uden moms: grid, energinet, elafgift, addon. Plus advarsler."""
    warnings = []
    fb = grid_fallback(idx, cfg)
    if pricelist is not None and len(pricelist):
        grid = grid_from_pricelist(idx, pricelist)
        missing = grid.isna()
        if missing.any():
            warnings.append(f"N1-tarif mangler for {int(missing.sum())} timer; bruger reservetabel.")
            grid[missing] = fb[missing]
    else:
        grid = fb
        warnings.append("N1-tariffer kunne ikke hentes; bruger reservetabellen i config.toml.")
    p = cfg["pricing"]
    df = pd.DataFrame({
        "grid": grid,
        "energinet": _by_year(idx, p["energinet_ore_ex_vat"]),
        "elafgift": _by_year(idx, p["elafgift_ore_ex_vat"]),
        "addon": float(p["spot_addon_ore_per_kwh"]),
    }, index=idx)
    return df, warnings


def price_levels(spot: pd.Series, comp: pd.DataFrame, vat: float) -> pd.DataFrame:
    """Tre visningsniveauer, alle inkl. moms (øre/kWh)."""
    return pd.DataFrame({
        "spot": spot * (1 + vat),
        "spot_grid": (spot + comp["grid"]) * (1 + vat),
        "total": (spot + comp["addon"] + comp["grid"] + comp["energinet"] + comp["elafgift"]) * (1 + vat),
        "spot_addon": (spot + comp["addon"]) * (1 + vat),
    })


def adders(comp: pd.DataFrame, vat: float) -> pd.DataFrame:
    """Det, der lægges oven i spot (uden moms) for hvert niveau; siden ganger selv moms på."""
    return pd.DataFrame({
        "spot": 0.0,
        "spot_grid": comp["grid"],
        "total": comp["addon"] + comp["grid"] + comp["energinet"] + comp["elafgift"],
        "spot_addon": comp["addon"],
    }, index=comp.index)
