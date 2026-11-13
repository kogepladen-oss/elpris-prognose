"""Fejlregnskab: hvor meget blev der gættet ved siden af, og slår modellen de simple metoder."""
from __future__ import annotations

import math

import numpy as np
import pandas as pd

from .timeutil import local_date


def resolved(fc: pd.DataFrame, prices: pd.DataFrame) -> pd.DataFrame:
    """Én prognose pr. (issue-dag, måltime): live foretrækkes frem for backtest. Med faktisk pris."""
    if fc is None or fc.empty:
        return pd.DataFrame()
    d = fc.copy()
    d["rank"] = (d["kind"] != "live").astype(int)
    d = d.sort_values(["issue_date", "target_utc", "rank", "issued_at"],
                      ascending=[True, True, True, False], na_position="last")
    d = d.drop_duplicates(["issue_date", "target_utc"], keep="first")
    t = pd.DatetimeIndex(d["target_utc"])
    d["y"] = prices["DK1"].reindex(t).to_numpy()
    d["naive"] = prices["DK1"].reindex(t - pd.Timedelta(days=7)).to_numpy()
    d["days_before"] = (pd.to_datetime(d["target_date"]) - pd.to_datetime(d["issue_date"])).dt.days
    return d[d["y"].notna()].drop(columns="rank")


def _block_cost(prices: np.ndarray, guide: np.ndarray, n: int) -> float:
    """Faktisk gennemsnitspris for den sammenhængende blok på n timer, der ser billigst ud i guide."""
    if len(prices) < n:
        return np.nan
    k = np.ones(n) / n
    g = np.convolve(guide, k, "valid")
    start = int(np.nanargmin(g))
    return float(prices[start:start + n].mean())


def metrics(res: pd.DataFrame, last_date: pd.Timestamp, window_days: int, cfg: dict) -> list[dict]:
    if res.empty:
        return []
    r = res[(pd.to_datetime(res["target_date"]) > last_date - pd.Timedelta(days=window_days))
            & (res["days_before"] <= 7)]
    c = cfg["charging"]
    n_hours = max(1, math.ceil(c["energy_kwh"] / c["efficiency"] / c["charger_kw"]))
    kwh_grid = c["energy_kwh"] / c["efficiency"]
    vat = cfg["pricing"]["vat_rate"]
    out = []
    for db, g in r.groupby("days_before"):
        err = g["p50"] - g["y"]
        nv = g["naive"] - g["y"]
        cover = ((g["y"] >= g["p10"]) & (g["y"] <= g["p90"])).mean()
        rho, regret_m, regret_n = [], [], []
        for _, day in g.groupby("target_date"):
            day = day.sort_values("target_utc")
            if len(day) >= 20:
                rho.append(day["p50"].rank().corr(day["y"].rank()))
                y = day["y"].to_numpy()
                best = _block_cost(y, y, n_hours)
                regret_m.append(_block_cost(y, day["p50"].to_numpy(), n_hours) - best)
                if day["naive"].notna().all():
                    regret_n.append(_block_cost(y, day["naive"].to_numpy(), n_hours) - best)
        to_kr = kwh_grid * (1 + vat) / 100.0
        out.append({
            "days_before": int(db), "n_hours": int(len(g)), "n_days": int(g["target_date"].nunique()),
            "mae": float(err.abs().mean()), "rmse": float(np.sqrt((err ** 2).mean())),
            "bias": float(err.mean()), "coverage": float(cover),
            "mae_naive": float(nv.abs().mean()) if nv.notna().any() else None,
            "rank_corr": float(np.nanmean(rho)) if rho else None,
            "regret_kr": float(np.nanmean(regret_m) * to_kr) if regret_m else None,
            "regret_naive_kr": float(np.nanmean(regret_n) * to_kr) if regret_n else None,
            "live_share": float((g["kind"] == "live").mean()),
        })
    return out


def weekly_report(res: pd.DataFrame, last_date: pd.Timestamp, metrics30: list[dict]) -> str:
    """Markdown til den ugentlige fejlanalyse. Claude læser den og foreslår én ændring ad gangen."""
    if res is None or res.empty:
        return f"# Ugerapport til og med {last_date:%Y-%m-%d}\n\nIngen afgjorte prognoser endnu.\n"
    r = res[pd.to_datetime(res["target_date"]) > last_date - pd.Timedelta(days=7)].copy()
    lines = [f"# Ugerapport til og med {last_date:%Y-%m-%d}", ""]
    if r.empty:
        return "\n".join(lines + ["Ingen afgjorte prognoser endnu."])
    r["abs_err"] = (r["p50"] - r["y"]).abs()
    day = r.groupby(["target_date", "days_before"]).agg(
        mae=("abs_err", "mean"), mean_fc=("p50", "mean"), mean_y=("y", "mean"),
        lead=("lead", "max"), kind=("kind", "first")).reset_index()
    top = day.sort_values("mae", ascending=False).head(5)
    lines += ["## De 5 dyreste fejl (dag × prognosealder)", "",
              "| Måldag | Dage før | MAE øre/kWh | Prognose snit | Faktisk snit | Kilde |",
              "| --- | --- | --- | --- | --- | --- |"]
    for _, x in top.iterrows():
        lines.append(f"| {pd.Timestamp(x.target_date):%Y-%m-%d} | {x.days_before} | {x.mae:.1f} | "
                     f"{x.mean_fc:.1f} | {x.mean_y:.1f} | {x.kind} |")
    lines += ["", "## Fejl pr. prognosealder, 30 dage", "",
              "| Dage før | MAE | Naiv MAE | Dækning P10-P90 | Ekstra ladeudgift kr |",
              "| --- | --- | --- | --- | --- |"]
    for m in metrics30:
        nv = f"{m['mae_naive']:.1f}" if m["mae_naive"] is not None else "-"
        rg = f"{m['regret_kr']:.2f}" if m["regret_kr"] is not None else "-"
        lines.append(f"| {m['days_before']} | {m['mae']:.1f} | {nv} | {m['coverage']:.0%} | {rg} |")
    lines += ["", "## Spørgsmål til analysen", "",
              "1. Skyldtes de største fejl vejrprognosen (se `scripts/explain_day.py`), modellen eller en markedshændelse?",
              "2. Er der systematisk bias på bestemte timer eller ugedage?",
              "3. Foreslå højst én ændring, og dokumentér den med walk-forward-tal før/efter."]
    return "\n".join(lines) + "\n"
