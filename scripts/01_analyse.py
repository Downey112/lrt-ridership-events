"""
Analyse the impact of fare and network events on Klang Valley rail ridership (v2).

Counterfactual per event and line: OLS on log riders (day of week, KL/Selangor public holidays,
holiday eves, festive weeks, Ramadan, school holidays, linear trend) fitted on clean training windows
around each event (or pre-only for permanent changes), with Duan smearing. Noise band: symmetric +/- q
from placebos (Jun 2023 - May 2026), filtering step dates. Robustness: pre-event level & comparison line.
"""

from pathlib import Path
import holidays
import numpy as np
import pandas as pd

# Paths relative to repository root
ROOT = Path(__file__).resolve().parents[1]
DATA_FILE = ROOT / "data" / "raw" / "ridership_headline.csv"
EVENTS_FILE = ROOT / "data" / "events.csv"
SCHOOL_HOLIDAYS_FILE = ROOT / "data" / "school_holidays.csv"
RESULTS_DIR = ROOT / "results"

CONTROL = "rail_komuter_utara"
BREAKS = [pd.Timestamp("2022-06-16"), pd.Timestamp("2024-02-17"), pd.Timestamp("2026-06-29")]
LINES = ["rail_lrt_ampang", "rail_lrt_kj", "rail_mrt_kajang", "rail_monorail"]
LINE_NAMES = {
    "rail_lrt_ampang": "LRT Ampang/Sri Petaling",
    "rail_lrt_kj": "LRT Kelana Jaya",
    "rail_mrt_kajang": "MRT Kajang",
    "rail_monorail": "KL Monorail",
    "rail_mrt_pjy": "MRT Putrajaya",
    "rail_lrt_shah_alam": "LRT Shah Alam",
    "rail_komuter": "KTM Komuter (Klang Valley)",
    "rail_komuter_utara": "KTM Komuter Utara",
}

# Load headline ridership and event calendar
df_raw = pd.read_csv(DATA_FILE)
df_raw["date"] = pd.to_datetime(df_raw["date"])
df = df_raw.set_index("date").sort_index()

events = pd.read_csv(EVENTS_FILE, parse_dates=["start", "end"])

# Federal Territory of KL and Selangor public holidays
h_kl = holidays.Malaysia(subdiv="KUL", years=range(2018, 2028))
h_sgr = holidays.Malaysia(subdiv="SGR", years=range(2018, 2028))
h_jhr = holidays.Malaysia(subdiv="JHR", years=range(2018, 2028))
holiday_dates = set(pd.to_datetime(list(h_kl.keys()))).union(pd.to_datetime(list(h_sgr.keys())))


def first_day(year, keys):
    """First day of a festival in `year`: holiday whose name has key but no second day/replacement tag."""
    hits = [d for d, name in sorted(h_kl.items()) if d.year == year
            and any(k in name.lower() for k in keys)
            and not any(x in name.lower() for x in ("hari kedua", "cuti", "pergantian"))]
    assert len(hits) == 1, (year, keys, hits)
    return pd.Timestamp(hits[0])


cny_first_days = [first_day(y, ["tahun baharu cina"]) for y in range(2019, 2027)]
raya_first_days = [first_day(y, ["hari raya puasa", "hari raya aidilfitri"]) for y in range(2019, 2027)]
festive_dates = {d for f in cny_first_days + raya_first_days
                 for d in pd.date_range(f - pd.Timedelta(days=3), f + pd.Timedelta(days=7))}

ramadan_dates, ramadan_matches = set(), []
for y in range(2019, 2027):
    awal_hits = [pd.Timestamp(d) for d, n in sorted(h_jhr.items())
                 if d.year == y and "awal ramadan" in n.lower() and "cuti" not in n.lower()]
    assert len(awal_hits) == 1, f"Expected 1 Awal Ramadan date in {y}, got {len(awal_hits)}"
    awal, raya = awal_hits[0], first_day(y, ["hari raya puasa", "hari raya aidilfitri"])
    ramadan_matches.append((y, awal, raya))
    ramadan_dates.update(pd.date_range(awal, raya - pd.Timedelta(days=1)))

school_holidays_df = pd.read_csv(SCHOOL_HOLIDAYS_FILE, parse_dates=["start", "end"])
school_holiday_dates = {d for _, r in school_holidays_df.iterrows() for d in pd.date_range(r["start"], r["end"])}

# Precompute exclusion mask: all event windows plus 14 days post-event buffer
excluded_dates = {d for _, ev in events.iterrows() for d in pd.date_range(ev["start"], ev["end"] + pd.Timedelta(days=14))}


def build_features(dates: pd.DatetimeIndex, event_start: pd.Timestamp) -> np.ndarray:
    """Build design matrix: intercept, 6 day-of-week dummies, holiday, eve, festive, Ramadan, school, trend."""
    intercept = np.ones(len(dates))
    dow = dates.day_of_week
    dow_dummies = [np.where(dow == i, 1.0, 0.0) for i in range(1, 7)]  # drop 0 (Monday)
    is_hol = np.array([1.0 if d in holiday_dates else 0.0 for d in dates])
    is_eve = np.array([1.0 if (d + pd.Timedelta(days=1) in holiday_dates and d not in holiday_dates) else 0.0 for d in dates])
    is_festive = np.array([1.0 if d in festive_dates else 0.0 for d in dates])
    is_ramadan = np.array([1.0 if d in ramadan_dates else 0.0 for d in dates])
    is_school = np.array([1.0 if d in school_holiday_dates else 0.0 for d in dates])
    trend = (dates - event_start).days.values.astype(float)
    return np.column_stack([intercept] + dow_dummies + [is_hol, is_eve, is_festive, is_ramadan, is_school, trend])


def estimate(series: pd.Series, ctrl: pd.Series, start, end, mode="both"):
    """Fit OLS on clean training windows and predict counterfactual event ridership (main & comparison line)."""
    start, end = pd.Timestamp(start), pd.Timestamp(end)
    pre = pd.date_range(start - pd.Timedelta(days=56), start - pd.Timedelta(days=1))
    post = pd.date_range(end + pd.Timedelta(days=15), end + pd.Timedelta(days=70))
    train_cand = pre.union(post) if mode == "both" else pre
    min_days = 60 if mode == "both" else 40
    train_dates = [d for d in train_cand if d not in excluded_dates and d in series.index and d in ctrl.index
                   and not np.isnan(series.loc[d]) and not np.isnan(ctrl.loc[d])]
    if len(train_dates) < min_days:
        return None
    train_dates = pd.DatetimeIndex(sorted(train_dates))

    ev_dates = pd.date_range(start, end)
    ev_dates = pd.DatetimeIndex([d for d in ev_dates if d in series.index and d in ctrl.index
                                 and not np.isnan(series.loc[d]) and not np.isnan(ctrl.loc[d])])
    if len(ev_dates) == 0:
        return None

    y_train = np.log(series.loc[train_dates].values)
    y_ctrl_train = np.log(ctrl.loc[train_dates].values)
    X_train = build_features(train_dates, start)
    beta, _, _, _ = np.linalg.lstsq(X_train, y_train, rcond=None)
    residuals = y_train - X_train @ beta
    smear = np.mean(np.exp(residuals))
    pre_resid = float(np.exp(np.mean(residuals[train_dates < start][-14:])) - 1.0)

    beta_ctrl, _, _, _ = np.linalg.lstsq(X_train, y_train - y_ctrl_train, rcond=None)
    smear_ctrl = np.mean(np.exp((y_train - y_ctrl_train) - X_train @ beta_ctrl))

    X_ev = build_features(ev_dates, start)
    predicted = np.exp(X_ev @ beta) * smear
    actual = series.loc[ev_dates].values
    effect = float(np.sum(actual) / np.sum(predicted) - 1.0)

    predicted_ctrl = ctrl.loc[ev_dates].values * np.exp(X_ev @ beta_ctrl) * smear_ctrl
    effect_ctrl = float(np.sum(actual) / np.sum(predicted_ctrl) - 1.0)

    daily_df = pd.DataFrame({"date": ev_dates.strftime("%Y-%m-%d"), "actual": actual,
                             "predicted": predicted, "predicted_ctrl": predicted_ctrl})
    return daily_df, effect, len(train_dates), pre_resid, effect_ctrl


# 1. Event Effect Estimation & Placebo Noise Bands
effects_records, daily_records = [], []
fake_starts = pd.date_range("2023-06-05", "2026-05-31", freq="7D")
ev_spans = [(ev["start"], ev["end"] + pd.Timedelta(days=14)) for _, ev in events.iterrows()]

for _, ev in events.iterrows():
    w_len, mode = ev["end"] - ev["start"], ev["mode"]
    post_buf = pd.Timedelta(days=70) if mode == "both" else pd.Timedelta(days=0)
    valid_fakes = [
        fs for fs in fake_starts
        if not any((fs - pd.Timedelta(days=56)) <= ee and (fs + w_len + post_buf) >= es for es, ee in ev_spans)
        and not any((fs - pd.Timedelta(days=56)) <= b <= (fs + w_len + post_buf) for b in BREAKS)
    ]
    for line in ev["lines"].split(";"):
        res = estimate(df[line], df[CONTROL], ev["start"], ev["end"], mode)
        if res is None:
            continue
        d_df, real_effect, n_train, pre_resid, effect_ctrl = res
        d_df["event_id"], d_df["line"], d_df["line_name"] = ev["event_id"], line, LINE_NAMES[line]
        daily_records.append(d_df[["event_id", "line", "line_name", "date", "actual", "predicted", "predicted_ctrl"]])

        runs = [estimate(df[line], df[CONTROL], fs, fs + w_len, mode) for fs in valid_fakes]
        placebos = np.array([r[1] for r in runs if r is not None])
        placebos_ctrl = np.array([r[4] for r in runs if r is not None])
        q, pval = float(np.percentile(np.abs(placebos), 95)), float(np.mean(np.abs(placebos) >= np.abs(real_effect)))
        q_ctrl = float(np.percentile(np.abs(placebos_ctrl), 95))
        pval_ctrl = float(np.mean(np.abs(placebos_ctrl) >= np.abs(effect_ctrl)))
        effect_vs_pre = (1 + real_effect) / (1 + pre_resid) - 1
        direction = "rise" if real_effect > 0 else "fall"

        if abs(real_effect) <= q:
            verdict, failed_checks = "within normal noise", ""
        else:
            chk_pre = abs(effect_vs_pre) > q and np.sign(effect_vs_pre) == np.sign(real_effect)
            chk_ctrl = abs(effect_ctrl) > q_ctrl and np.sign(effect_ctrl) == np.sign(real_effect)
            if chk_pre and chk_ctrl:
                verdict, failed_checks = f"clear {direction}", ""
            else:
                verdict = f"{direction}, not robust"
                failed = ([] if chk_pre else ["pre-level"]) + ([] if chk_ctrl else ["comparison line"])
                failed_checks = " + ".join(failed)

        effects_records.append({
            "event_id": ev["event_id"], "event": ev["event"], "mode": mode, "line": line,
            "line_name": LINE_NAMES[line], "effect": real_effect, "band_lo": -q, "band_hi": q,
            "p_value": pval, "pre_resid": pre_resid, "effect_vs_pre": effect_vs_pre,
            "effect_ctrl": effect_ctrl, "band_ctrl": q_ctrl, "p_ctrl": pval_ctrl,
            "verdict": verdict, "failed_checks": failed_checks, "placebo_n": len(placebos), "train_days": n_train,
        })

effects_df = pd.DataFrame(effects_records)
event_daily_df = pd.concat(daily_records, ignore_index=True)

# 2. Monthly Recovery vs 2019
df_m = df_raw.copy()
df_m["month"] = df_m["date"].dt.strftime("%Y-%m-01")
df_m["m_num"] = df_m["date"].dt.month
base_2019 = df_m[df_m["date"].dt.year == 2019].groupby("m_num")[LINES].mean()
m_post = df_m[(df_m["month"] >= "2022-01-01") & (df_m["month"] <= "2026-08-01")].groupby(["month", "m_num"])[LINES].mean().reset_index()
rec_records = [
    {"month": row["month"], "line": line, "line_name": LINE_NAMES[line], "mean_daily": row[line],
     "mean_2019_same_month": base_2019.loc[row["m_num"], line], "recovery": row[line] / base_2019.loc[row["m_num"], line]}
    for _, row in m_post.iterrows() for line in LINES
]
recovery_df = pd.DataFrame(rec_records)

# 3. My50 Context (Weekly Mean 2021-07-05..2022-06-27)
df_w = df_raw.copy()
df_w["week"] = (df_w["date"] - pd.to_timedelta(df_w["date"].dt.day_of_week, unit="D")).dt.strftime("%Y-%m-%d")
sub_w = df_w[(df_w["week"] >= "2021-07-05") & (df_w["week"] <= "2022-06-27")].groupby("week")[LINES].mean().reset_index()
my50_records = [
    {"week": row["week"], "line": line, "line_name": LINE_NAMES[line], "mean_daily": row[line]}
    for _, row in sub_w.iterrows() for line in LINES
]
my50_df = pd.DataFrame(my50_records)

# 4. New Lines Monthly, LRT3 Phases, and Monorail Share
new_lines = ["rail_mrt_pjy", "rail_lrt_shah_alam", "rail_komuter", "rail_komuter_utara"]
new_lines_records = []
for line in new_lines:
    s = df[line].dropna()
    if line == "rail_lrt_shah_alam":
        s = s[s.index >= "2026-06-01"]
    for month, val in s[s > 0].groupby(s[s > 0].index.strftime("%Y-%m-01")).mean().items():
        new_lines_records.append({"month": month, "line": line, "line_name": LINE_NAMES[line], "mean_daily": float(val)})
new_lines_monthly_df = pd.DataFrame(new_lines_records)

lrt3_phases_spec = [
    ("Free (29 Jun–31 Jul)", 1, "2026-06-29", "2026-07-31"),
    ("Free, extended (1–14 Aug)", 2, "2026-08-01", "2026-08-14"),
    ("Paid (15–31 Aug)", 3, "2026-08-15", "2026-08-31"),
]
lrt3_records = []
s_lrt3 = df["rail_lrt_shah_alam"]
for name, order, p_start, p_end in lrt3_phases_spec:
    wk = [d for d in pd.date_range(p_start, p_end) if d.day_of_week < 5 and d not in holiday_dates and d in s_lrt3.index]
    lrt3_records.append({"phase": name, "phase_order": order, "days": len(wk), "mean_weekday": float(s_lrt3.loc[wk].mean())})
lrt3_phases_df = pd.DataFrame(lrt3_records)

df_span = df.loc["2019-01-01":"2026-08-31"]
m_totals = df_span.groupby(df_span.index.strftime("%Y-%m-01"))[["rail_monorail", "rail_lrt_kj", "rail_lrt_ampang"]].sum()
monorail_share_df = pd.DataFrame({"month": m_totals.index, "share": m_totals["rail_monorail"] / (m_totals["rail_lrt_kj"] + m_totals["rail_lrt_ampang"])})

# 5. Save Outputs to results/
RESULTS_DIR.mkdir(parents=True, exist_ok=True)
effects_df.to_csv(RESULTS_DIR / "event_effects.csv", index=False)
event_daily_df.to_csv(RESULTS_DIR / "event_daily.csv", index=False)
recovery_df.to_csv(RESULTS_DIR / "recovery_monthly.csv", index=False)
my50_df.to_csv(RESULTS_DIR / "my50_context.csv", index=False)
new_lines_monthly_df.to_csv(RESULTS_DIR / "new_lines_monthly.csv", index=False)
lrt3_phases_df.to_csv(RESULTS_DIR / "lrt3_phases.csv", index=False)
monorail_share_df.to_csv(RESULTS_DIR / "monorail_share.csv", index=False)

# 6. Summary Output to stdout
print("\n" + "=" * 115)
print("EVENT EFFECTS SUMMARY (KLANG VALLEY RAIL RIDERSHIP)")
print("=" * 115)
summary_tbl = effects_df[["event_id", "line_name", "effect", "band_hi", "p_value", "pre_resid", "effect_vs_pre", "effect_ctrl", "verdict", "failed_checks"]].copy()
for c in ["effect", "pre_resid", "effect_vs_pre", "effect_ctrl"]:
    summary_tbl[c] = summary_tbl[c].map(lambda x: f"{x:+.1%}")
summary_tbl["band_hi"] = summary_tbl["band_hi"].map(lambda x: f"±{x:.1%}")
summary_tbl["p_value"] = summary_tbl["p_value"].map(lambda x: f"{x:.3f}")
print(summary_tbl.rename(columns={"band_hi": "noise_band"}).to_string(index=False))
print("=" * 115)

print("\nMATCHED AWAL RAMADAN & HARI RAYA PUASA DATES (2019-2026):")
for y, awal, raya in ramadan_matches:
    print(f"  {y}: Awal Ramadan {awal.strftime('%Y-%m-%d')} -> Hari Raya {raya.strftime('%Y-%m-%d')} ({(raya - awal).days} days)")
print()
