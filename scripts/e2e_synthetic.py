"""Kør hele pipelinen på syntetiske data i en midlertidig mappe over flere dage.

    python scripts/e2e_synthetic.py --days 3
"""
import argparse
import shutil
import sys
import tempfile
import time
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

from elpris import config, run_daily, store  # noqa: E402
from synthetic import World, install  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=2)
    ap.add_argument("--start", default="2026-11-10T12:30")
    ap.add_argument("--weeks", type=int, default=3, help="backtest-uger (færre = hurtigere test)")
    ap.add_argument("--keep", default=None, help="kopiér docs/ hertil bagefter")
    a = ap.parse_args()
    tmp = Path(tempfile.mkdtemp())
    # omdiriger datamapper til tmp
    config.DATA = tmp / "data"
    config.SITE = tmp / "docs"
    config.REPORTS = tmp / "reports"
    for name in ("PRICES", "WEATHER_HIST", "SNAPSHOTS", "FORECASTS", "PRICELIST", "META"):
        setattr(store, name, tmp / "data" / getattr(store, name).name)
    run_daily.REPORTS = config.REPORTS
    run_daily.site.SITE = config.SITE
    orig = config.load

    def load(path=None):
        c = orig(path)
        c["model"]["backtest_weeks"] = a.weeks
        return c
    config.load = load
    cfg = config.load()
    w = World(cfg)
    install(w, setattr)
    for d in range(a.days):
        w.now = pd.Timestamp(a.start, tz="UTC") + pd.Timedelta(days=d)
        t0 = time.time()
        rc = run_daily.main(["--force"])
        print(f"dag {d}: rc={rc} på {time.time() - t0:.0f} s")
    if a.keep:
        shutil.copytree(tmp / "docs", a.keep, dirs_exist_ok=True)
    print("data i", tmp)


if __name__ == "__main__":
    main()
