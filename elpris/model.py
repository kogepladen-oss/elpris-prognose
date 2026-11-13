"""Kvantil-model pr. horisont, walk-forward backtest og læring af fejl."""
from __future__ import annotations

import logging
from dataclasses import dataclass

import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingRegressor

from . import features as F

log = logging.getLogger(__name__)

MODEL_VERSION = "hgb-q-v1"
SCALE = 20.0  # øre/kWh i asinh-transformationen; tåler negative priser


def fwd(y):
    return np.arcsinh(np.asarray(y, dtype=float) / SCALE)


def inv(z):
    return SCALE * np.sinh(np.asarray(z, dtype=float))


@dataclass
class HorizonModel:
    h: int
    cols: list[str]
    models: dict

    def predict(self, X: pd.DataFrame) -> np.ndarray:
        z = np.column_stack([self.models[q].predict(X[self.cols]) for q in sorted(self.models)])
        return np.sort(z, axis=1)  # undgå krydsende kvantiler (z-rum)


def training_frame(prices, wx_hist, h, start, last_target_date) -> pd.DataFrame:
    """Alle måltimer med kendt udfald til og med last_target_date."""
    idx = prices.index[(prices.index >= start)]
    f = F.build(idx, h, prices, wx_hist, by_lead=True)
    f["y"] = prices["DK1"].reindex(idx).to_numpy()
    ok = (f["target_date"] <= last_target_date) & f["y"].notna() & f["dk1_mean"].notna() \
        & (f["dk1_n_K"] >= 22) & f["wind_dk"].notna()
    return f[ok.to_numpy()].reset_index(drop=True)


def fit(train: pd.DataFrame, h: int, cfg: dict) -> HorizonModel:
    m = cfg["model"]
    cols = F.feature_columns(train)
    age = (train["target_date"].max() - train["target_date"]).dt.days.to_numpy()
    w = 0.5 ** (age / m["recency_half_life_days"])
    # Nyere data vægtes højest ved at gentage rækker (1-3 gange). sample_weight gør
    # kvantil-træningen ca. 6 gange langsommere, så det er et bevidst valg.
    rep = np.rint(1 + 2 * w).astype(int)
    train = train.loc[train.index.repeat(rep)]
    z = fwd(train["y"])
    models = {}
    for q in m["quantiles"]:
        est = HistGradientBoostingRegressor(loss="quantile", quantile=q, max_iter=200,
                                            learning_rate=0.07, max_leaf_nodes=24, max_bins=127,
                                            min_samples_leaf=40, l2_regularization=1.0,
                                            early_stopping=False, random_state=0)
        est.fit(train[cols], z)
        models[q] = est
    return HorizonModel(h, cols, models)


# ---------------------------------------------------------------- læring

def corrections(hist: pd.DataFrame, prices: pd.DataFrame, upto_date: pd.Timestamp, h: int,
                cfg: dict) -> tuple[float, float]:
    """Bias (z-rum) og båndfaktor ud fra tidligere prognoser, hvis udfald var kendt senest upto_date."""
    m = cfg["model"]
    if hist is None or hist.empty:
        return 0.0, 1.0
    d = hist[(hist["h"] == h) & (hist["target_date"] <= upto_date)
             & (hist["target_date"] > upto_date - pd.Timedelta(days=m["calibration_window_days"]))]
    if len(d) < 48:
        return 0.0, 1.0
    y = prices["DK1"].reindex(pd.DatetimeIndex(d["target_utc"])).to_numpy()
    ok = ~np.isnan(y)
    if ok.sum() < 48:
        return 0.0, 1.0
    d, y = d[ok], y[ok]
    z, z10, z50, z90 = fwd(y), fwd(d["raw_p10"]), fwd(d["raw_p50"]), fwd(d["raw_p90"])
    recent = (d["target_date"] > upto_date - pd.Timedelta(days=m["bias_window_days"])).to_numpy()
    bias = 0.5 * float(np.median((z - z50)[recent])) if recent.sum() >= 24 else 0.0
    zc = z50 + bias
    lo, hi = np.maximum(z50 - z10, 1e-6), np.maximum(z90 - z50, 1e-6)
    r = np.where(z >= zc, (z - zc) / hi, (zc - z) / lo)  # skaleret afstand; 1 = båndkant
    factor = float(np.clip(np.quantile(r, m["target_coverage"]), 0.7, 2.5))
    return bias, factor


def apply_corr(zq: np.ndarray, bias: float, factor: float) -> np.ndarray:
    z10, z50, z90 = zq[:, 0], zq[:, 1], zq[:, 2]
    c = z50 + bias
    return inv(np.column_stack([c - factor * (z50 - z10), c, c + factor * (z90 - z50)]))


def predict_frame(model: HorizonModel, f: pd.DataFrame, bias: float, factor: float) -> pd.DataFrame:
    zq = model.predict(f)
    raw = inv(zq)
    fin = apply_corr(zq, bias, factor)
    return pd.DataFrame({
        "target_utc": f["target_utc"].to_numpy(), "target_date": f["target_date"].to_numpy(),
        "h": f["h"].to_numpy(), "lead": f["lead"].to_numpy(),
        "p10": fin[:, 0], "p50": fin[:, 1], "p90": fin[:, 2],
        "raw_p10": raw[:, 0], "raw_p50": raw[:, 1], "raw_p90": raw[:, 2],
        "bias": bias, "band_factor": factor,
    })


# ---------------------------------------------------------------- backtest

def backtest(prices, wx_hist, existing: pd.DataFrame | None, cfg: dict,
             last_known: pd.Timestamp) -> pd.DataFrame:
    """Walk-forward: én genoptræning pr. uge, prognoser for hver dag i ugen.

    Issue-dag i har K = i+1. Modeller trænes kun på udfald til og med K.
    Uger, der allerede ligger i existing for denne modelversion, springes over.
    """
    m = cfg["model"]
    start = pd.Timestamp(m["train_start"], tz="UTC")
    H = m["max_horizon_days"]
    done = set()
    if existing is not None and len(existing):
        e = existing[(existing["kind"] == "backtest") & (existing["model_version"] == MODEL_VERSION)]
        done = set(pd.to_datetime(e["issue_date"]).dt.normalize())
    last_issue = last_known - pd.Timedelta(days=2)  # nyeste issue-dag, hvor h=1 er afgjort
    first_issue = last_issue - pd.Timedelta(weeks=m["backtest_weeks"]) + pd.Timedelta(days=1)
    week_starts = pd.date_range(first_issue, last_issue, freq="7D")
    out = []
    hist = existing.copy() if existing is not None else pd.DataFrame()
    for ws in week_starts:
        issues = [ws + pd.Timedelta(days=k) for k in range(7) if ws + pd.Timedelta(days=k) <= last_issue]
        issues = [i for i in issues if i not in done]
        if not issues:
            continue
        K0 = ws + pd.Timedelta(days=1)
        log.info("Backtest: træner med udfald til %s for %d issue-dage", K0.date(), len(issues))
        week_rows = []
        for h in range(1, min(H, 6) + 1):  # efter kl. 13 rækker 7 dage frem til h=6
            tr = training_frame(prices, wx_hist, h, start, K0)
            if len(tr) < 500:
                continue
            mod = fit(tr, h, cfg)
            for i in issues:
                K = i + pd.Timedelta(days=1)
                D = K + pd.Timedelta(days=h)
                s, e = _day_bounds(D)
                tgt = prices.index[(prices.index >= s) & (prices.index < e)]
                if len(tgt) == 0:
                    tgt = pd.date_range(s, e, freq="h", inclusive="left")
                f = F.build(tgt, h, prices, wx_hist, by_lead=True)
                f = f[f["wind_dk"].notna() & f["dk1_mean"].notna()]
                if f.empty:
                    continue
                bias, fac = corrections(hist, prices, K, h, cfg)
                p = predict_frame(mod, f, bias, fac)
                p["issue_date"] = i
                p["anchor"] = K
                week_rows.append(p)
        if week_rows:
            wk = pd.concat(week_rows, ignore_index=True)
            wk["kind"] = "backtest"
            wk["model_version"] = MODEL_VERSION
            wk["issued_at"] = pd.NaT
            out.append(wk)
            hist = pd.concat([hist, wk], ignore_index=True)
    return pd.concat(out, ignore_index=True) if out else pd.DataFrame()


def _day_bounds(d: pd.Timestamp):
    s = pd.Timestamp(d).tz_localize(F.TZ).tz_convert("UTC")
    e = (pd.Timestamp(d) + pd.Timedelta(days=1)).tz_localize(F.TZ).tz_convert("UTC")
    return s, e


# ---------------------------------------------------------------- live

def live_forecast(prices, wx_hist, wx_live_wide, hist, cfg, now: pd.Timestamp) -> pd.DataFrame:
    m = cfg["model"]
    start = pd.Timestamp(m["train_start"], tz="UTC")
    K = F.last_complete_day(prices)
    issue_date = pd.Timestamp(now.tz_convert(F.TZ).date())
    s, _ = _day_bounds(K + pd.Timedelta(days=1))
    end = now + pd.Timedelta(days=7)
    tgt = pd.date_range(s, end.floor("h"), freq="h")
    rows = []
    D = pd.Series(pd.DatetimeIndex(F.local_date(tgt).values))
    hs = (D - K).dt.days.to_numpy()
    for h in sorted(set(hs)):
        if h < 1 or h > m["max_horizon_days"]:
            continue
        tr = training_frame(prices, wx_hist, h, start, K)
        mod = fit(tr, h, cfg)
        sub = tgt[hs == h]
        f = F.build(sub, h, prices, wx_live_wide, by_lead=False, anchor=K, issue=now)
        f = f[f["wind_dk"].notna()]
        if f.empty:
            continue
        bias, fac = corrections(hist, prices, K, h, cfg)
        p = predict_frame(mod, f, bias, fac)
        rows.append(p)
        log.info("Live h=%d: %d timer, bias %.3f, båndfaktor %.2f", h, len(p), bias, fac)
    if not rows:
        return pd.DataFrame()
    out = pd.concat(rows, ignore_index=True)
    out["issue_date"] = issue_date
    out["kind"] = "live"
    out["model_version"] = MODEL_VERSION
    out["issued_at"] = now
    out["anchor"] = K
    return out
