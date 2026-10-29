"""Consistency checks on the analysis outputs (run scripts 01 and 02 first)."""
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]
RES, BI = ROOT / "results", ROOT / "powerbi" / "data"
LINES = ["rail_lrt_ampang", "rail_lrt_kj", "rail_mrt_kajang", "rail_monorail"]


@pytest.fixture(scope="module")
def raw():
    return pd.read_csv(ROOT / "data" / "raw" / "ridership_headline.csv", parse_dates=["date"]).set_index("date")


@pytest.fixture(scope="module")
def effects():
    return pd.read_csv(RES / "event_effects.csv")


def test_raw_lines_complete(raw):
    assert raw.index.is_unique and raw.index.is_monotonic_increasing
    assert (raw.index[-1] - raw.index[0]).days + 1 == len(raw)
    assert raw[LINES].notna().all().all() and (raw[LINES] > 0).all().all()


def test_events_inside_data(raw):
    ev = pd.read_csv(ROOT / "data" / "events.csv", parse_dates=["start", "end"])
    assert (ev["start"] <= ev["end"]).all()
    assert ev["start"].min() - pd.Timedelta(days=56) >= raw.index[0]
    assert ev["end"].max() + pd.Timedelta(days=70) <= raw.index[-1]


def test_one_row_per_event_and_line(effects):
    assert len(effects) == 3 * len(LINES)
    assert not effects.duplicated(["event_id", "line"]).any()
    assert (effects["placebo_n"] >= 100).all() and (effects["train_days"] >= 60).all()


def test_band_symmetric_and_verdict_consistent(effects):
    assert np.allclose(effects["band_lo"], -effects["band_hi"])
    inside = effects["effect"].abs() <= effects["band_hi"]
    assert (effects.loc[inside, "verdict"] == "within normal noise").all()
    clear = effects["verdict"].str.startswith("clear")
    assert (effects.loc[clear, "effect_vs_pre"].abs() > effects.loc[clear, "band_hi"]).all()
    assert np.allclose(effects["effect_vs_pre"], (1 + effects["effect"]) / (1 + effects["pre_resid"]) - 1)


def test_effect_matches_daily_sums(effects):
    daily = pd.read_csv(RES / "event_daily.csv")
    sums = daily.groupby(["event_id", "line"])[["actual", "predicted"]].sum()
    joined = effects.set_index(["event_id", "line"]).join(sums)
    assert np.allclose(joined["effect"], joined["actual"] / joined["predicted"] - 1)


def test_daily_actuals_are_raw_values(raw):
    daily = pd.read_csv(RES / "event_daily.csv", parse_dates=["date"])
    looked_up = [raw.at[d, ln] for d, ln in zip(daily["date"], daily["line"])]
    assert np.allclose(daily["actual"], looked_up)


def test_recovery_uses_same_month_2019(raw):
    rec = pd.read_csv(RES / "recovery_monthly.csv", parse_dates=["month"])
    row = rec[(rec["month"] == "2024-03-01") & (rec["line"] == "rail_lrt_kj")].iloc[0]
    assert row["mean_daily"] == pytest.approx(raw.loc["2024-03", "rail_lrt_kj"].mean())
    assert row["mean_2019_same_month"] == pytest.approx(raw.loc["2019-03", "rail_lrt_kj"].mean())
    assert row["recovery"] == pytest.approx(row["mean_daily"] / row["mean_2019_same_month"])


def test_headline_recovery_same_span(raw):
    rec = pd.read_csv(BI / "line_recovery.csv").set_index("line")["recovery"]
    last = raw.index[-1]
    expect = raw.loc[f"{last.year}-01-01":last, LINES].mean() / raw.loc[f"2019-01-01":f"2019-{last:%m-%d}", LINES].mean()
    assert np.allclose(rec[LINES], expect[LINES])
