"""Write the CSVs the Power BI report reads (powerbi/data/), from data/ and results/."""
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
RES, OUT = ROOT / "results", ROOT / "powerbi" / "data"
LINES = {"rail_lrt_kj": "LRT Kelana Jaya", "rail_lrt_ampang": "LRT Ampang/Sri Petaling",
         "rail_mrt_kajang": "MRT Kajang", "rail_monorail": "KL Monorail"}


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    pd.DataFrame({"line": list(LINES), "line_name": list(LINES.values()),
                  "line_order": range(1, len(LINES) + 1)}).to_csv(OUT / "lines.csv", index=False)

    ev = pd.read_csv(ROOT / "data" / "events.csv", parse_dates=["start", "end"])
    ev["dates"] = [f"{s.day} {s:%b %Y} – {e.day} {e:%b %Y}" for s, e in zip(ev["start"], ev["end"])]
    ev["event_order"] = range(1, len(ev) + 1)
    ev.drop(columns="lines").to_csv(OUT / "events.csv", index=False, date_format="%Y-%m-%d")

    for name in ["event_effects", "event_daily", "recovery_monthly", "my50_context"]:
        df = pd.read_csv(RES / f"{name}.csv")
        df.drop(columns=[c for c in ["event", "line_name"] if c in df], errors="ignore").to_csv(
            OUT / f"{name}.csv", index=False)

    raw = pd.read_csv(ROOT / "data" / "raw" / "ridership_headline.csv", parse_dates=["date"]).set_index("date")
    raw = raw[list(LINES)]
    monthly = raw.resample("MS").mean().round(0).stack().rename("mean_daily").reset_index()
    monthly.columns = ["month", "line", "mean_daily"]
    monthly.to_csv(OUT / "monthly.csv", index=False, date_format="%Y-%m-%d")

    # headline recovery: same calendar span in 2026 and 2019, so seasonality cancels
    last = raw.index.max()
    span = lambda y: raw.loc[f"{y}-01-01":f"{y}-{last:%m-%d}"].mean()
    rec = (span(last.year) / span(2019)).rename("recovery").rename_axis("line").reset_index()
    rec.to_csv(OUT / "line_recovery.csv", index=False)
    print(f"recovery Jan–{last:%b} {last.year} vs 2019:")
    print(rec.assign(line=rec["line"].map(LINES)).to_string(index=False, float_format="{:.1%}".format))
    print(f"wrote {len(list(OUT.glob('*.csv')))} CSVs to {OUT}")


if __name__ == "__main__":
    main()
