"""
Analyse the impact of fare and network events on Klang Valley rail ridership.

Counterfactual per event and line: OLS on log riders (day of week, KL public holidays, holiday eves,
CNY/Hari Raya festive weeks, Ramadan, linear trend) fitted on the weeks around the event, with Duan
smearing. Noise band: symmetric +/- q, q = 95th percentile of |effect| at placebo dates (Jun 2023 - May 2026).
Robustness: the same effect measured against the level of the 14 days just before the event.
"""

from pathlib import Path
import holidays
import numpy as np
import pandas as pd

# Paths relative to repository root
ROOT = Path(__file__).resolve().parents[1]
DATA_FILE = ROOT / "data" / "raw" / "ridership_headline.csv"
EVENTS_FILE = ROOT / "data" / "events.csv"
RESULTS_DIR = ROOT / "results"

LINES = ["rail_lrt_ampang", "rail_lrt_kj", "rail_mrt_kajang", "rail_monorail"]
LINE_NAMES = {
    "rail_lrt_ampang": "LRT Ampang/Sri Petaling",
    "rail_lrt_kj": "LRT Kelana Jaya",
    "rail_mrt_kajang": "MRT Kajang",
    "rail_monorail": "KL Monorail",
}

# Load headline ridership and event calendar
df_raw = pd.read_csv(DATA_FILE)
df_raw["date"] = pd.to_datetime(df_raw["date"])
df = df_raw.set_index("date").sort_index()

events = pd.read_csv(EVENTS_FILE)
events["start"] = pd.to_datetime(events["start"])
events["end"] = pd.to_datetime(events["end"])

# Federal Territory of Kuala Lumpur public holidays
h_kl = holidays.Malaysia(subdiv="KUL", years=range(2018, 2028))
holiday_dates = set(pd.to_datetime(list(h_kl.keys())))


def first_day(year, keys):
    """First day of a festival in `year`: the holiday whose name has a key but no 'second day'/'replacement' tag."""
    hits = [d for d, name in sorted(h_kl.items()) if d.year == year
            and any(k in name.lower() for k in keys)
            and not any(x in name.lower() for x in ("hari kedua", "cuti", "pergantian"))]
    assert len(hits) == 1, (year, keys, hits)
    return pd.Timestamp(hits[0])


cny_first_days = [first_day(y, ["tahun baharu cina"]) for y in range(2019, 2027)]
raya_first_days = [first_day(y, ["hari raya puasa", "hari raya aidilfitri"]) for y in range(2019, 2027)]
# festive week: balik kampung travel, 3 days before to 7 days after the first day
festive_dates = {d for f in cny_first_days + raya_first_days
                 for d in pd.date_range(f - pd.Timedelta(days=3), f + pd.Timedelta(days=7))}
# Ramadan approximated as the 30 days before Hari Raya (true length is 29 or 30)
ramadan_dates = {d for r in raya_first_days for d in pd.date_range(r - pd.Timedelta(days=30), r - pd.Timedelta(days=1))}

# Precompute exclusion mask: all event windows plus 14 days post-event buffer
excluded_dates = set()
for _, ev in events.iterrows():
    for d in pd.date_range(ev["start"], ev["end"] + pd.Timedelta(days=14)):
        excluded_dates.add(d)


def build_features(dates: pd.DatetimeIndex, event_start: pd.Timestamp) -> np.ndarray:
    """Build design matrix: intercept, 6 day-of-week dummies, holiday, eve, festive week, Ramadan, trend."""
    intercept = np.ones(len(dates))
    dow = dates.day_of_week
    dow_dummies = [np.where(dow == i, 1.0, 0.0) for i in range(1, 7)]  # drop 0 (Monday)
    is_hol = np.array([1.0 if d in holiday_dates else 0.0 for d in dates])
    is_eve = np.array([
        1.0 if (d + pd.Timedelta(days=1) in holiday_dates and d not in holiday_dates) else 0.0
        for d in dates
    ])
    is_festive = np.array([1.0 if d in festive_dates else 0.0 for d in dates])
    is_ramadan = np.array([1.0 if d in ramadan_dates else 0.0 for d in dates])
    trend = (dates - event_start).days.values.astype(float)
    return np.column_stack([intercept] + dow_dummies + [is_hol, is_eve, is_festive, is_ramadan, trend])


def estimate(series: pd.Series, start, end):
    """Fit OLS on clean training windows and predict counterfactual event ridership."""
    start, end = pd.Timestamp(start), pd.Timestamp(end)
    pre = pd.date_range(start - pd.Timedelta(days=56), start - pd.Timedelta(days=1))
    post = pd.date_range(end + pd.Timedelta(days=15), end + pd.Timedelta(days=70))
    train_dates = [d for d in pre.union(post) if d not in excluded_dates and d in series.index]
    if len(train_dates) < 60:
        return None
    train_dates = pd.DatetimeIndex(sorted(train_dates))

    ev_dates = pd.date_range(start, end)
    ev_dates = pd.DatetimeIndex([d for d in ev_dates if d in series.index])
    if len(ev_dates) == 0:
        return None

    # OLS estimation on log scale
    y_train = np.log(series.loc[train_dates].values)
    X_train = build_features(train_dates, start)
    beta, _, _, _ = np.linalg.lstsq(X_train, y_train, rcond=None)

    # Duan's smearing factor to correct re-transformation bias
    residuals = y_train - X_train @ beta
    smear = np.mean(np.exp(residuals))
    # pre-trend diagnostic: how far off the baseline already was in the last 14 training days before the event
    pre_resid = float(np.exp(np.mean(residuals[train_dates < start][-14:])) - 1.0)

    # Event counterfactual prediction
    X_ev = build_features(ev_dates, start)
    predicted = np.exp(X_ev @ beta) * smear
    actual = series.loc[ev_dates].values

    daily_df = pd.DataFrame({
        "date": ev_dates.strftime("%Y-%m-%d"),
        "actual": actual,
        "predicted": predicted,
    })
    effect = float(np.sum(actual) / np.sum(predicted) - 1.0)
    return daily_df, effect, len(train_dates), pre_resid


# 1. Event Effect Estimation & Placebo Noise Bands
effects_records, daily_records = [], []
fake_starts = pd.date_range("2023-06-05", "2026-05-31", freq="7D")
ev_spans = [(ev.start, ev.end + pd.Timedelta(days=14)) for _, ev in events.iterrows()]

for _, ev in events.iterrows():
    w_len = ev.end - ev.start
    # Retain fake windows whose full span [fs - 56d, fe + 70d] never touches any event window (+14d)
    valid_fakes = [
        fs for fs in fake_starts
        if not any(
            (fs - pd.Timedelta(days=56)) <= ee and (fs + w_len + pd.Timedelta(days=70)) >= es
            for es, ee in ev_spans
        )
    ]
    for line in LINES:
        res = estimate(df[line], ev.start, ev.end)
        if res is None:
            continue
        d_df, real_effect, n_train, pre_resid = res
        d_df["event_id"] = ev.event_id
        d_df["line"] = line
        d_df["line_name"] = LINE_NAMES[line]
        daily_records.append(d_df[["event_id", "line", "line_name", "date", "actual", "predicted"]])

        runs = [estimate(df[line], fs, fs + w_len) for fs in valid_fakes]
        placebos = np.array([r[1] for r in runs if r is not None])
        q = float(np.percentile(np.abs(placebos), 95))  # symmetric band, so verdict agrees with p < 0.05
        pval = float(np.mean(np.abs(placebos) >= np.abs(real_effect)))
        effect_vs_pre = (1 + real_effect) / (1 + pre_resid) - 1
        direction = "rise" if real_effect > 0 else "fall"
        if abs(real_effect) <= q:
            verdict = "within normal noise"
        elif abs(effect_vs_pre) > q and np.sign(effect_vs_pre) == np.sign(real_effect):
            verdict = f"clear {direction}"
        else:
            verdict = f"{direction}, not robust"
        effects_records.append({
            "event_id": ev.event_id,
            "event": ev.event,
            "line": line,
            "line_name": LINE_NAMES[line],
            "effect": real_effect,
            "placebo_n": len(placebos),
            "band_lo": -q,
            "band_hi": q,
            "p_value": pval,
            "verdict": verdict,
            "train_days": n_train,
            "pre_resid": pre_resid,
            "effect_vs_pre": effect_vs_pre,
        })

effects_df = pd.DataFrame(effects_records)
event_daily_df = pd.concat(daily_records, ignore_index=True)

# 2. Monthly Recovery vs 2019
df_m = df_raw.copy()
df_m["month"] = df_m["date"].dt.strftime("%Y-%m-01")
df_m["m_num"] = df_m["date"].dt.month
base_2019 = df_m[df_m["date"].dt.year == 2019].groupby("m_num")[LINES].mean()
m_post = (
    df_m[(df_m["month"] >= "2022-01-01") & (df_m["month"] <= "2026-08-01")]
    .groupby(["month", "m_num"])[LINES]
    .mean()
    .reset_index()
)
rec_records = [
    {
        "month": row["month"],
        "line": line,
        "line_name": LINE_NAMES[line],
        "mean_daily": row[line],
        "mean_2019_same_month": base_2019.loc[row["m_num"], line],
        "recovery": row[line] / base_2019.loc[row["m_num"], line],
    }
    for _, row in m_post.iterrows()
    for line in LINES
]
recovery_df = pd.DataFrame(rec_records)

# 3. My50 Context (Weekly Mean 2021-07-05..2022-06-27)
df_w = df_raw.copy()
df_w["week"] = (df_w["date"] - pd.to_timedelta(df_w["date"].dt.day_of_week, unit="D")).dt.strftime("%Y-%m-%d")
sub_w = df_w[(df_w["week"] >= "2021-07-05") & (df_w["week"] <= "2022-06-27")].groupby("week")[LINES].mean().reset_index()
my50_records = [
    {"week": row["week"], "line": line, "line_name": LINE_NAMES[line], "mean_daily": row[line]}
    for _, row in sub_w.iterrows()
    for line in LINES
]
my50_df = pd.DataFrame(my50_records)

# 4. Save Outputs to results/
RESULTS_DIR.mkdir(parents=True, exist_ok=True)
effects_df.to_csv(RESULTS_DIR / "event_effects.csv", index=False)
event_daily_df.to_csv(RESULTS_DIR / "event_daily.csv", index=False)
recovery_df.to_csv(RESULTS_DIR / "recovery_monthly.csv", index=False)
my50_df.to_csv(RESULTS_DIR / "my50_context.csv", index=False)

# 5. Summary Table to stdout
print("\n" + "=" * 90)
print("EVENT EFFECTS SUMMARY (KLANG VALLEY RAIL RIDERSHIP)")
print("=" * 90)
summary_tbl = effects_df[["event_id", "line_name", "effect", "band_hi", "p_value", "pre_resid", "effect_vs_pre",
                          "verdict"]].copy()
for c in ["effect", "pre_resid", "effect_vs_pre"]:
    summary_tbl[c] = summary_tbl[c].map(lambda x: f"{x:+.1%}")
summary_tbl["band_hi"] = summary_tbl["band_hi"].map(lambda x: f"±{x:.1%}")
summary_tbl["p_value"] = summary_tbl["p_value"].map(lambda x: f"{x:.3f}")
print(summary_tbl.rename(columns={"band_hi": "noise_band"}).to_string(index=False))
print("=" * 90 + "\n")
