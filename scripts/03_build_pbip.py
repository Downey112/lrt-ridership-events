"""Generate the Power BI project (PBIP: TMDL semantic model + PBIR report) under powerbi/.

The report reads the CSVs written by 02_export_bi.py from the folder in the DataFolder parameter.
Re-run this script after changing the layout; edits made in Power BI Desktop are overwritten.
Lineage tags and logical ids are uuid5 values, so regenerating gives identical files.
Helpers adapted from the pricecatcher-anomaly report generator; the LRT photo is added as
registered image resources (assets/hero_lrt.jpg, assets/strip_lrt.jpg).
"""
import json
import os
import shutil
import uuid
from pathlib import Path

NAME = "LRTRidership"
ROOT = Path(__file__).resolve().parents[1]
PBI = ROOT / "powerbi"
# the DataFolder parameter must be absolute; PBI_DATA_FOLDER overrides it (e.g. when building from a worktree)
DATA = Path(os.environ.get("PBI_DATA_FOLDER", PBI / "data"))
NS = uuid.UUID("6a0f3c2e-4b7d-4e1a-9c58-2d7e1b9f0a31")
SCHEMA = "https://developer.microsoft.com/json-schemas/fabric"

# station-signage palette: dark carriage navy, platform grey, white, plus line colours
NAVY, INK, MUTED, PLATFORM, RULE = "#101828", "#101828", "#5B6475", "#EEF1F5", "#DDE2EA"
SIGNAL = "#D50032"   # accent red, used for "clear" verdicts and the title rule
LINE_COLOURS = {     # approximations of the line colours on the Klang Valley rail map
    "LRT Kelana Jaya": "#D50032",
    "LRT Ampang/Sri Petaling": "#E57200",
    "MRT Kajang": "#047940",
    "KL Monorail": "#7AB800",
    "MRT Putrajaya": "#D9A400",
    "LRT Shah Alam": "#0096A6",
    "KTM Komuter (Klang Valley)": "#1F4E9C",
    "KTM Komuter Utara": "#8B95A5",   # comparison line, kept grey
}
SERIES = list(LINE_COLOURS.values()) + ["#5B6475", "#98A2B3"]
IMAGES = {"hero": "hero_lrt.jpg", "strip": "strip_lrt.jpg"}
CREDIT = ("Photo: Kelana Jaya Line LRT Train by Anthonytyc, Wikimedia Commons, CC BY-SA 4.0 (cropped, darkened). "
          "Independent analysis, not affiliated with Prasarana or Rapid KL.")


def tag(*parts):
    return str(uuid.uuid5(NS, "/".join(parts)))


# ---------------------------------------------------------------- semantic model (TMDL)

def q(name):
    return f"'{name}'" if any(ch in name for ch in " -()/%") else name


def measure_lines(table, name, expr, fmt=None, folder=None):
    lines = expr.strip().splitlines()
    out = ([f"\tmeasure {q(name)} = {lines[0]}"] if len(lines) == 1
           else [f"\tmeasure {q(name)} ="] + [f"\t\t\t{ln}" for ln in lines])
    if fmt:
        out.append(f"\t\tformatString: {fmt}")
    if folder:
        out.append(f"\t\tdisplayFolder: {folder}")
    return out + [f"\t\tlineageTag: {tag(table, 'm', name)}", ""]


def csv_table(name, cols, measures=(), sort_by=None, hidden=()):
    """cols: [(column, tmdl type, M type, format or None)]; sort_by: {column: column it sorts by}"""
    sort_by = sort_by or {}
    out = [f"table {name}", f"\tlineageTag: {tag(name)}", ""]
    for m in measures:
        out += measure_lines(name, *m)
    for c, typ, _, fmt in cols:
        out += [f"\tcolumn {q(c)}", f"\t\tdataType: {typ}"]
        if fmt:
            out.append(f"\t\tformatString: {fmt}")
        if c in hidden:
            out.append("\t\tisHidden")
        out += [f"\t\tlineageTag: {tag(name, c)}", "\t\tsummarizeBy: none", f"\t\tsourceColumn: {c}"]
        if c in sort_by:
            out.append(f"\t\tsortByColumn: {sort_by[c]}")
        out.append("")
    types = ",\n".join(f'\t\t\t\t        {{"{c}", {mt}}}' for c, _, mt, _ in cols)
    out += [f"\tpartition {name} = m", "\t\tmode: import", "\t\tsource =", "\t\t\t\tlet",
            f'\t\t\t\t    Source = Csv.Document(File.Contents(DataFolder & "\\{name}.csv"), '
            '[Delimiter = ",", Encoding = 65001, QuoteStyle = QuoteStyle.Csv]),',
            "\t\t\t\t    Promoted = Table.PromoteHeaders(Source, [PromoteAllScalars = true]),",
            "\t\t\t\t    Typed = Table.TransformColumnTypes(Promoted, {", types, '\t\t\t\t    }, "en-US")',
            "\t\t\t\tin", "\t\t\t\t    Typed", ""]
    return "\n".join(out)


def recovery_for(line):
    return f'CALCULATE ( MAX ( line_recovery[recovery] ), line_recovery[line] = "{line}" )'


RECOVERY_MEASURES = [(f"Recovery {k}", recovery_for(v), "0%", "KPI") for k, v in
                     [("KJ", "rail_lrt_kj"), ("Ampang", "rail_lrt_ampang"), ("MRT", "rail_mrt_kajang"),
                      ("Monorail", "rail_monorail")]]

EFFECT_MEASURES = [
    ("Title Effects",
     '"What changed during: " & SELECTEDVALUE ( events[event], "pick an event" )', None, "Titles"),
    ("Title Daily",
     'SELECTEDVALUE ( lines[line_name], "Pick a line" ) & " · actual vs expected without the event"',
     None, "Titles"),
    ("Event Note", 'SELECTEDVALUE ( events[note], "" ) & "  ·  " & SELECTEDVALUE ( events[dates], "" )',
     None, "Titles"),
]

DAILY_MEASURES = [
    ("Actual Riders", "SUM ( event_daily[actual] )", "#,0", "Base"),
    ("Expected Riders", "SUM ( event_daily[predicted] )", "#,0", "Base"),
    ("Expected via Komuter Utara", "SUM ( event_daily[predicted_ctrl] )", "#,0", "Base"),
]

TABLES = ["lines", "events", "event_effects", "event_daily", "monthly", "recovery_monthly", "my50_context",
          "line_recovery", "new_lines_monthly", "lrt3_phases", "monorail_share"]

RELATIONSHIPS = [("event_effects", "line", "lines", "line"), ("event_daily", "line", "lines", "line"),
                 ("monthly", "line", "lines", "line"), ("recovery_monthly", "line", "lines", "line"),
                 ("my50_context", "line", "lines", "line"), ("event_effects", "event_id", "events", "event_id"),
                 ("event_daily", "event_id", "events", "event_id"), ("new_lines_monthly", "line", "lines", "line")]


def model_files():
    files = {"definition/database.tmdl": "database\n\tcompatibilityLevel: 1567\n"}
    files["definition/model.tmdl"] = "\n".join([
        "model Model", "\tculture: en-US", "\tdefaultPowerBIDataSourceVersion: powerBI_V3",
        "\tsourceQueryCulture: en-US", "\tdataAccessOptions", "\t\tlegacyRedirects", "\t\treturnErrorValuesAsNull", "",
        "annotation __PBI_TimeIntelligenceEnabled = 0", "",
        f"annotation PBI_QueryOrder = {json.dumps(['DataFolder'] + TABLES)}", "",
        'annotation PBI_ProTooling = ["DevMode"]', "", *[f"ref table {q(t)}" for t in TABLES], ""])
    files["definition/expressions.tmdl"] = (
        f'expression DataFolder = "{DATA}" meta [IsParameterQuery=true, Type="Text", IsParameterQueryRequired=true]\n'
        f"\tlineageTag: {tag('DataFolder')}\n\n\tannotation PBI_ResultType = Text\n")
    rels = []
    for fc, fcol, tt, tcol in RELATIONSHIPS:
        rels += [f"relationship {tag('rel', fc, fcol, tt)}", f"\tfromColumn: {fc}.{fcol}", f"\ttoColumn: {tt}.{tcol}", ""]
    files["definition/relationships.tmdl"] = "\n".join(rels)

    txt, i64, dbl = ("string", "type text", None), ("int64", "Int64.Type", "#,0"), ("double", "type number", None)
    dt = ("dateTime", "type date", "d mmm yyyy")
    pct, spct = "0%", "+0%;-0%;0%"

    def cols(*spec):
        return [(n, t[0], t[1], fmt if fmt is not None else t[2]) for n, t, fmt in spec]

    T = {
        "lines": csv_table("lines", cols(("line", txt, None), ("line_name", txt, None), ("line_order", i64, "0")),
                           sort_by={"line_name": "line_order"}, hidden=("line", "line_order")),
        "events": csv_table("events", cols(
            ("event_id", txt, None), ("event", txt, None), ("start", dt, None), ("end", dt, None), ("kind", txt, None),
            ("note", txt, None), ("dates", txt, None), ("source", txt, None), ("event_order", i64, "0")),
            sort_by={"event": "event_order"}, hidden=("event_id", "event_order")),
        "event_effects": csv_table("event_effects", cols(
            ("event_id", txt, None), ("mode", txt, None), ("line", txt, None), ("effect", dbl, spct),
            ("band_lo", dbl, spct), ("band_hi", dbl, '"±"0%'), ("p_value", dbl, "0.00"), ("pre_resid", dbl, spct),
            ("effect_vs_pre", dbl, spct), ("effect_ctrl", dbl, spct), ("band_ctrl", dbl, '"±"0%'),
            ("p_ctrl", dbl, "0.00"), ("verdict", txt, None), ("failed_checks", txt, None),
            ("placebo_n", i64, None), ("train_days", i64, None)),
            EFFECT_MEASURES, hidden=("event_id", "line")),
        "event_daily": csv_table("event_daily", cols(
            ("event_id", txt, None), ("line", txt, None), ("date", dt, None), ("actual", dbl, "#,0"),
            ("predicted", dbl, "#,0"), ("predicted_ctrl", dbl, "#,0")), DAILY_MEASURES, hidden=("event_id", "line")),
        "monthly": csv_table("monthly", cols(("month", dt, "mmm yyyy"), ("line", txt, None), ("mean_daily", dbl, "#,0")),
                             [("Riders per Day", "AVERAGE ( monthly[mean_daily] )", "#,0", "Base")], hidden=("line",)),
        "recovery_monthly": csv_table("recovery_monthly", cols(
            ("month", dt, "mmm yyyy"), ("line", txt, None), ("mean_daily", dbl, "#,0"),
            ("mean_2019_same_month", dbl, "#,0"), ("recovery", dbl, pct)),
            [("Recovery vs 2019", "AVERAGE ( recovery_monthly[recovery] )", pct, "Base")], hidden=("line",)),
        "my50_context": csv_table("my50_context", cols(("week", dt, None), ("line", txt, None), ("mean_daily", dbl, "#,0")),
                                  [("Weekly Riders per Day", "AVERAGE ( my50_context[mean_daily] )", "#,0", "Base")],
                                  hidden=("line",)),
        "line_recovery": csv_table("line_recovery", cols(("line", txt, None), ("recovery", dbl, pct)),
                                   RECOVERY_MEASURES, hidden=("line",)),
        "new_lines_monthly": csv_table("new_lines_monthly", cols(
            ("month", dt, "mmm yyyy"), ("line", txt, None), ("mean_daily", dbl, "#,0")),
            [("New Line Riders per Day", "AVERAGE ( new_lines_monthly[mean_daily] )", "#,0", "Base")],
            hidden=("line",)),
        "lrt3_phases": csv_table("lrt3_phases", cols(
            ("phase", txt, None), ("phase_order", i64, "0"), ("days", i64, None), ("mean_weekday", dbl, "#,0")),
            [("LRT3 Weekday Riders", "AVERAGE ( lrt3_phases[mean_weekday] )", "#,0", "Base")],
            sort_by={"phase": "phase_order"}, hidden=("phase_order",)),
        "monorail_share": csv_table("monorail_share", cols(("month", dt, "mmm yyyy"), ("share", dbl, "0.0%")),
                                    [("Monorail Share", "AVERAGE ( monorail_share[share] )", "0.0%", "Base")]),
    }
    for name, body in T.items():
        files[f"definition/tables/{name}.tmdl"] = body
    files[".platform"] = platform("SemanticModel")
    files["definition.pbism"] = json.dumps({
        "$schema": f"{SCHEMA}/item/semanticModel/definitionProperties/1.0.0/schema.json",
        "version": "4.2", "settings": {}}, indent=2)
    return files


def platform(kind):
    return json.dumps({
        "$schema": f"{SCHEMA}/gitIntegration/platformProperties/2.0.0/schema.json",
        "metadata": {"type": kind, "displayName": NAME},
        "config": {"version": "2.0", "logicalId": tag("platform", kind)}}, indent=2)


# ---------------------------------------------------------------- report (PBIR)

def lit(v):
    return {"expr": {"Literal": {"Value": v}}}


def s(text):
    return lit("'" + text.replace("'", "''") + "'")


def color(hexv):
    return {"solid": {"color": lit(f"'{hexv}'")}}


def measure_ref(entity, prop):
    return {"Measure": {"Expression": {"SourceRef": {"Entity": entity}}, "Property": prop}}


def col_ref(entity, prop):
    return {"Column": {"Expression": {"SourceRef": {"Entity": entity}}, "Property": prop}}


def proj(entity, prop, display=None, measure=False, active=None):
    p = {"field": measure_ref(entity, prop) if measure else col_ref(entity, prop),
         "queryRef": f"{entity}.{prop}", "nativeQueryRef": prop}
    if display:
        p["displayName"] = display
    if active:
        p["active"] = True
    return p


def M(entity, prop, display=None):
    return proj(entity, prop, display, measure=True)


def C(entity, prop, display=None, active=None):
    return proj(entity, prop, display, active=active)


def padding(t, b, l, r):
    return [{"properties": {"top": lit(f"{t}D"), "bottom": lit(f"{b}D"), "left": lit(f"{l}D"), "right": lit(f"{r}D")}}]


def line_colour_points():
    """Per-series colours keyed on lines[line_name], so each line keeps its map colour on every chart."""
    return [{"properties": {"fill": color(hexv)},
             "selector": {"data": [{"scopeId": {"Comparison": {
                 "ComparisonKind": 0, "Left": col_ref("lines", "line_name"),
                 "Right": {"Literal": {"Value": f"'{name}'"}}}}}]}}
            for name, hexv in LINE_COLOURS.items()]


class Page:
    def __init__(self, name, display):
        self.name, self.display, self.visuals, self.z, self.interactions = name, display, {}, 1000, []

    def add(self, name, pos, visual, title=None, subtitle=None, alt=None, background=True, pad=None):
        x, y, w, h = pos
        self.z += 1000
        vco = {"title": [{"properties": {"show": lit("true" if title else "false")}}],
               "background": [{"properties": {"show": lit("true" if background else "false")}}]}
        if title:
            vco["title"][0]["properties"]["text"] = title if isinstance(title, dict) else s(title)
        if subtitle:
            vco["subTitle"] = [{"properties": {"show": lit("true"), "text": s(subtitle)}}]
        if alt:
            vco["general"] = [{"properties": {"altText": s(alt)}}]
        if pad:
            vco["padding"] = padding(*pad)
        self.visuals[name] = {
            "$schema": f"{SCHEMA}/item/report/definition/visualContainer/2.3.0/schema.json",
            "name": name, "position": {"x": x, "y": y, "z": self.z, "height": h, "width": w, "tabOrder": self.z},
            "visual": {**visual, "visualContainerObjects": vco, "drillFilterOtherVisuals": True}}

    def text(self, name, pos, paragraphs, background=False, pad=None):
        runs = [{"textRuns": [{"value": v, "textStyle": {"fontFamily": f, "fontSize": sz, "color": c}}]}
                for v, f, sz, c in paragraphs]
        self.add(name, pos, {"visualType": "textbox", "objects": {"general": [{"properties": {"paragraphs": runs}}]}},
                 background=background, pad=pad or ((0, 0, 0, 0) if not background else (12, 12, 16, 16)))

    def image(self, name, pos, key, alt):
        self.add(name, pos, {"visualType": "image", "objects": {
            "general": [{"properties": {"imageUrl": {"expr": {"ResourcePackageItem": {
                "PackageName": "RegisteredResources", "PackageType": 1, "ItemName": IMAGES[key]}}}}}],
            "imageScaling": [{"properties": {"imageScalingType": s("Fill")}}]}},
            alt=alt, background=False, pad=(0, 0, 0, 0))

    def strip_header(self, title, subtitle):
        """Dark roof-photo strip across the top with the page question in white."""
        self.image("header_strip", (0, 0, 1280, 80), "strip", "Decorative strip: station roof above an LRT platform.")
        self.text("header", (24, 10, 1232, 64), [(title, "Segoe UI Semibold", "18pt", "#FFFFFF"),
                                                 (subtitle, "Segoe UI", "9pt", "#D0D5DD")])

    def card(self, key, pos, entity, value, title, sub, accent):
        x, y, w, h = pos
        self.add(f"kpi_{key}_tile", pos, {"visualType": "textbox",
                                          "objects": {"general": [{"properties": {"paragraphs": []}}]}},
                 pad=(0, 0, 0, 0))
        self.add(f"kpi_{key}_bar", (x, y, 6, h), {"visualType": "textbox",
                                                  "objects": {"general": [{"properties": {"paragraphs": []}}]}},
                 pad=(0, 0, 0, 0))  # a coloured empty textbox: line-colour accent strip
        self.visuals[f"kpi_{key}_bar"]["visual"]["visualContainerObjects"]["background"][0]["properties"].update(
            {"color": color(accent), "transparency": lit("0D")})
        self.add(f"kpi_{key}_value", (x + 6, y, w - 6, h - 22),
                 {"visualType": "card", "query": {"queryState": {"Values": {"projections": [M(entity, value)]}}},
                  "objects": {"labels": [{"properties": {"fontSize": lit("26D"), "color": color(INK)}}]}},
                 title=title, alt=f"{title}: {sub}.", background=False, pad=(8, 0, 14, 14))
        t = self.visuals[f"kpi_{key}_value"]["visual"]["visualContainerObjects"]["title"][0]["properties"]
        t.update({"fontSize": lit("10D"), "fontColor": color(MUTED), "alignment": s("left")})
        self.text(f"kpi_{key}_sub", (x + 20, y + h - 26, w - 26, 20), [(sub, "Segoe UI", "8pt", MUTED)])

    def slicer(self, name, pos, entity, column, display, default=None, alt=None):
        objects = {"data": [{"properties": {"mode": s("Dropdown")}}],
                   "header": [{"properties": {"show": lit("true"), "text": s(display)}}],
                   "selection": [{"properties": {"singleSelect": lit("true")}}]}
        if default is not None:
            objects["general"] = [{"properties": {"filter": {"filter": {
                "Version": 2, "From": [{"Name": "p", "Entity": entity, "Type": 0}],
                "Where": [{"Condition": {"In": {
                    "Expressions": [{"Column": {"Expression": {"SourceRef": {"Source": "p"}}, "Property": column}}],
                    "Values": [[{"Literal": {"Value": f"'{default}'"}}]]}}}]}}}}]
        self.add(name, pos, {"visualType": "slicer",
                             "query": {"queryState": {"Values": {"projections": [C(entity, column, display, True)]}}},
                             "objects": objects}, alt=alt, pad=(4, 4, 12, 12))

    def chart(self, name, pos, vtype, query, title, subtitle=None, alt=None, objects=None, sort=None, pad=None):
        v = {"visualType": vtype, "query": {"queryState": query}, "objects": objects or {}}
        if sort:
            v["query"]["sortDefinition"] = {"sort": [{"field": sort[0], "direction": sort[1]}]}
        self.add(name, pos, v, title=title, subtitle=subtitle, alt=alt, pad=pad)

    def table(self, name, pos, projections, title, subtitle=None, alt=None, sort=None, widths=None):
        objects = {"total": [{"properties": {"totals": lit("false")}}],
                   "columnHeaders": [{"properties": {"wordWrap": lit("true")}}],
                   "values": [{"properties": {"wordWrap": lit("true")}}]}
        if widths:
            objects["columnWidth"] = [{"properties": {"value": lit(f"{w}D")}, "selector": {"metadata": pr["queryRef"]}}
                                      for pr, w in zip(projections, widths)]
        self.chart(name, pos, "tableEx", {"Values": {"projections": projections}}, title, subtitle, alt,
                   objects=objects, sort=sort, pad=(8, 8, 16, 16))

    def files(self):
        bg = {"color": color(PLATFORM), "transparency": lit("0D")}
        out = {f"definition/pages/{self.name}/page.json": {
            "$schema": f"{SCHEMA}/item/report/definition/page/2.0.0/schema.json",
            "name": self.name, "displayName": self.display, "displayOption": "FitToPage", "height": 720, "width": 1280,
            "objects": {"background": [{"properties": bg}], "outspace": [{"properties": bg}]},
            **({"visualInteractions": self.interactions} if self.interactions else {})}}
        for vn, v in self.visuals.items():
            out[f"definition/pages/{self.name}/visuals/{vn}/visual.json"] = v
        return out


def line_chart(p, name, pos, x, y, title, subtitle, alt, series=True, y_fmt=None):
    query = {"Category": {"projections": [x]}, "Y": {"projections": [y]}}
    if series:
        query["Series"] = {"projections": [C("lines", "line_name", "Line")]}
    objects = {"dataPoint": line_colour_points(),
               "legend": [{"properties": {"show": lit("true"), "position": s("Top")}}]}
    if y_fmt:
        objects["valueAxis"] = [{"properties": {"start": lit(y_fmt)}}]
    p.chart(name, pos, "lineChart", query, title, subtitle, alt, objects=objects,
            sort=(x["field"], "Ascending"))


def page_overview():
    p = Page("overview", "Overview")
    p.image("hero", (0, 0, 336, 720), "hero", "Photo of a Kelana Jaya Line LRT train arriving at an elevated station.")
    p.text("hero_title", (24, 430, 296, 270), [
        ("KLANG VALLEY RAIL", "Segoe UI Semibold", "9pt", "#FDA29B"),
        ("Did fares and network changes move rail riders?", "Segoe UI Semibold", "20pt", "#FFFFFF"),
        ("Daily ridership on Klang Valley rail lines, Jan 2019 – Aug 2026, from data.gov.my.", "Segoe UI", "10pt",
         "#D0D5DD"),
        ("Six events, each compared with what the same line would normally have carried, then checked three ways.",
         "Segoe UI", "10pt", "#D0D5DD"),
    ])
    cards = [("kj", "Recovery KJ", "LRT Kelana Jaya"), ("ampang", "Recovery Ampang", "LRT Ampang/Sri Petaling"),
             ("mrt", "Recovery MRT", "MRT Kajang"), ("mono", "Recovery Monorail", "KL Monorail")]
    for i, (key, measure, line) in enumerate(cards):
        p.card(key, (360 + i * 228, 24, 220, 104), "line_recovery", measure, line,
               "riders vs 2019, Jan–Aug 2026", LINE_COLOURS[line])
    line_chart(p, "chart_monthly", (360, 144, 896, 272), C("monthly", "month", active=True),
               M("monthly", "Riders per Day"), "Average riders per day, by month",
               "COVID lockdowns 2020–21, then a steady climb · the sharp yearly dips are Chinese New Year and Hari Raya",
               "Line chart of average daily riders per month for each line, 2019 to 2026.")
    p.table("table_events", (360, 432, 588, 264),
            [C("events", "event", "Event"), C("events", "dates", "Dates")],
            "Events tested", "Dates checked against news reports and the daily data itself",
            "Table of the six events tested, with dates.",
            sort=(col_ref("events", "event_order"), "Ascending"), widths=[360, 180])
    p.text("notes_my50", (956, 432, 300, 264), [
        ("What changed riders?", "Segoe UI Semibold", "12pt", INK),
        ("Free fares did: +19% to +34% on three lines in Jun–Jul 2022, and LRT Shah Alam lost 4 in 10 weekday "
         "riders when its free rides ended.", "Segoe UI", "9pt", INK),
        ("Disruptions moved riders: the Monorail gained 16% while Kelana Jaya was shut.", "Segoe UI", "9pt", INK),
        ("My50 still can't be measured from this data.", "Segoe UI", "9pt", INK),
        (CREDIT, "Segoe UI", "7pt", MUTED),
    ], background=True)
    return p


def page_events():
    p = Page("events", "Event effects")
    p.strip_header("What did each event change?",
                   "Effect = actual ÷ expected − 1 · 'clear' = beyond normal noise, AND still clear against the 2 weeks "
                   "before, AND against KTM Komuter Utara (not Rapid KL)")
    p.slicer("slicer_event", (24, 96, 400, 56), "events", "event", "Event", default="Free month on all Rapid KL services",
             alt="Choose the event to show.")
    p.slicer("slicer_line", (432, 96, 260, 56), "lines", "line_name", "Line for the daily chart",
             default="LRT Kelana Jaya", alt="Choose the line for the daily chart.")
    p.chart("event_note_card", (700, 96, 556, 56), "card",
            {"Values": {"projections": [M("event_effects", "Event Note")]}}, None,
            alt="Note on the selected event.",
            objects={"labels": [{"properties": {"fontSize": lit("10D"), "color": color(MUTED)}}]}, pad=(4, 4, 12, 12))
    p.table("table_effects", (24, 168, 640, 528),
            [C("lines", "line_name", "Line"), C("event_effects", "effect", "Effect"),
             C("event_effects", "band_hi", "Normal noise"), C("event_effects", "effect_vs_pre", "vs 2 weeks before"),
             C("event_effects", "effect_ctrl", "vs Komuter Utara"), C("event_effects", "verdict", "Verdict"),
             C("event_effects", "failed_checks", "Check failed")],
            {"expr": measure_ref("event_effects", "Title Effects")},
            "Normal noise = 95% of fake 'effects' at 120–145 ordinary dates (2023–26) fall inside ± this",
            "Table of the estimated effect on each line for the selected event, with the normal-noise band and checks.",
            sort=(col_ref("lines", "line_name"), "Ascending"), widths=[118, 54, 56, 66, 66, 98, 118])
    p.interactions.append({"source": "slicer_line", "target": "table_effects", "type": "NoFilter"})
    p.chart("chart_daily", (672, 168, 584, 528), "lineChart",
            {"Category": {"projections": [C("event_daily", "date", active=True)]},
             "Y": {"projections": [M("event_daily", "Actual Riders", "Actual"),
                                   M("event_daily", "Expected Riders", "Expected without the event"),
                                   M("event_daily", "Expected via Komuter Utara", "Expected via Komuter Utara")]}},
            {"expr": measure_ref("event_effects", "Title Daily")},
            "Expected = the line's weekday, holiday, school-break and trend pattern from the weeks around the event",
            "Line chart of actual and expected daily riders during the selected event for the selected line.",
            objects={"dataPoint": [{"properties": {"fill": color(SIGNAL)},
                                    "selector": {"metadata": "event_daily.Actual Riders"}},
                                   {"properties": {"fill": color("#98A2B3")},
                                    "selector": {"metadata": "event_daily.Expected Riders"}},
                                   {"properties": {"fill": color("#1F4E9C")},
                                    "selector": {"metadata": "event_daily.Expected via Komuter Utara"}}],
                     "lineStyles": [{"properties": {"lineStyle": s("dashed")},
                                     "selector": {"metadata": "event_daily.Expected Riders"}},
                                    {"properties": {"lineStyle": s("dotted")},
                                     "selector": {"metadata": "event_daily.Expected via Komuter Utara"}}],
                     "legend": [{"properties": {"show": lit("true"), "position": s("Top")}}]},
            sort=(col_ref("event_daily", "date"), "Ascending"))
    return p


def page_recovery():
    p = Page("recovery", "Recovery & My50")
    p.strip_header("Are riders back to 2019 levels?",
                   "Each month's average daily riders ÷ the same month in 2019 · 100% = fully recovered")
    line_chart(p, "chart_recovery", (24, 96, 1232, 280), C("recovery_monthly", "month", active=True),
               M("recovery_monthly", "Recovery vs 2019"), "Riders vs the same month in 2019",
               "First full month back at 2019: Monorail Jun 2022, MRT Kajang Feb 2023, Ampang May 2024 · "
               "Kelana Jaya still about 95%",
               "Line chart of monthly ridership as a share of the same month in 2019, by line.")
    line_chart(p, "chart_my50", (24, 392, 560, 304), C("my50_context", "week", active=True),
               M("my50_context", "Weekly Riders per Day"), "Around the My50 relaunch (1 Jan 2022)",
               "Weekly averages, Jul 2021 – Jun 2022 · the climb starts before My50, as lockdowns ended",
               "Line chart of weekly average daily riders per line from July 2021 to June 2022.")
    p.chart("chart_monorail_share", (592, 392, 320, 304), "lineChart",
            {"Category": {"projections": [C("monorail_share", "month", active=True)]},
             "Y": {"projections": [M("monorail_share", "Monorail Share")]}},
            "Monorail riders per 100 LRT riders",
            "Steps: Nov 2022 KJ shutdown, Feb 2023 Ampang cut",
            "Line chart of Monorail riders as a share of Kelana Jaya plus Ampang riders, 2019 to 2026.",
            objects={"dataPoint": [{"properties": {"fill": color(LINE_COLOURS["KL Monorail"])},
                                    "selector": {"metadata": "monorail_share.Monorail Share"}}]},
            sort=(col_ref("monorail_share", "month"), "Ascending"))
    p.text("notes_limits", (920, 392, 336, 304), [
        ("Limits", "Segoe UI Semibold", "12pt", INK),
        ("My50 still can't be measured: inside ±12–17% noise, and the comparison line is thrown off by northern "
         "holiday travel in Dec 2021 (school calendar for 2021 not found).", "Segoe UI", "9pt", INK),
        ("Monorail's 2019 base was low (fleet shortage; ~65k a day in 2015), and riders who switched during the "
         "KJ shutdown and Ampang cut partly stayed.", "Segoe UI", "9pt", INK),
        ("School holidays for 2022–26 come from unofficial calendars (data/school_holidays.csv).",
         "Segoe UI", "9pt", INK),
        ("Data: ridership_headline, data.gov.my (Prasarana / Ministry of Transport).", "Segoe UI", "8pt", MUTED),
        (CREDIT, "Segoe UI", "7pt", MUTED),
    ], background=True)
    return p


def page_new_lines():
    p = Page("new_lines", "New lines")
    p.strip_header("How are the newer lines doing?",
                   "MRT Putrajaya from Jun 2022, KTM Komuter (Klang Valley) data from Sep 2023, "
                   "LRT Shah Alam from 29 Jun 2026")
    line_chart(p, "chart_new_lines", (24, 96, 1232, 300), C("new_lines_monthly", "month", active=True),
               M("new_lines_monthly", "New Line Riders per Day"), "Average riders per day, by month",
               "MRT Putrajaya keeps climbing after Phase 2 (Mar 2023) · KTM Komuter (Klang Valley) slips through 2025–26",
               "Line chart of average daily riders per month on MRT Putrajaya, KTM Komuter Klang Valley and "
               "LRT Shah Alam.")
    p.chart("chart_lrt3", (24, 412, 600, 284), "clusteredColumnChart",
            {"Category": {"projections": [C("lrt3_phases", "phase", active=True)]},
             "Y": {"projections": [M("lrt3_phases", "LRT3 Weekday Riders")]}},
            "LRT Shah Alam: weekday riders by fare phase",
            "Mon–Fri, public holidays excluded · free to 31 Jul, extended to 14 Aug, then paid",
            "Column chart of LRT Shah Alam average weekday riders: 55,418 while free, 45,839 in the extension, "
            "33,729 once paid.",
            objects={"dataPoint": [{"properties": {"fill": color(LINE_COLOURS["LRT Shah Alam"])}}],
                     "labels": [{"properties": {"show": lit("true"), "labelDisplayUnits": lit("1D")}}]},
            sort=(col_ref("lrt3_phases", "phase"), "Ascending"))
    p.text("notes_new_lines", (632, 412, 624, 284), [
        ("What the new lines show", "Segoe UI Semibold", "12pt", INK),
        ("LRT Shah Alam lost about 4 in 10 weekday riders once fares started (55k free → 34k paid), the 2022 "
         "free month in reverse.", "Segoe UI", "9pt", INK),
        ("Its opening did not measurably move the older lines: Kelana Jaya +12% and MRT Kajang +10% in its first "
         "seven weeks, both inside the ±15% noise for a forecast that long.", "Segoe UI", "9pt", INK),
        ("Only seven weeks of LRT Shah Alam data so far (to 31 Aug 2026); paid ridership may still be settling.",
         "Segoe UI", "9pt", MUTED),
    ], background=True)
    return p


def report_files():
    pages = [page_overview(), page_events(), page_recovery(), page_new_lines()]
    images = [{"name": f, "path": f, "type": "Image"} for f in IMAGES.values()]
    files = {
        ".platform": platform("Report"),
        "definition.pbir": {"$schema": f"{SCHEMA}/item/report/definitionProperties/2.0.0/schema.json",
                            "version": "4.0", "datasetReference": {"byPath": {"path": f"../{NAME}.SemanticModel"}}},
        "definition/version.json": {"$schema": f"{SCHEMA}/item/report/definition/versionMetadata/1.0.0/schema.json",
                                    "version": "2.0.0"},
        "definition/report.json": {
            "$schema": f"{SCHEMA}/item/report/definition/report/3.0.0/schema.json",
            "themeCollection": {"customTheme": {"name": f"{NAME}.json", "reportVersionAtImport": {
                "visual": "2.3.0", "report": "3.0.0", "page": "2.0.0"}, "type": "RegisteredResources"}},
            "objects": {"outspacePane": [{"properties": {"expanded": lit("false")}}]},
            "resourcePackages": [{"name": "RegisteredResources", "type": "RegisteredResources", "items": [
                {"name": f"{NAME}.json", "path": f"{NAME}.json", "type": "CustomTheme"}, *images]}],
            "settings": {"useEnhancedTooltips": True}},
        "definition/pages/pages.json": {"$schema": f"{SCHEMA}/item/report/definition/pagesMetadata/1.0.0/schema.json",
                                        "pageOrder": [p.name for p in pages], "activePageName": pages[0].name},
        f"StaticResources/RegisteredResources/{NAME}.json": theme(),
    }
    for p in pages:
        files.update(p.files())
    return files


def theme():
    font = "'Segoe UI', wf_segoe-ui_normal, helvetica, arial, sans-serif"
    semi = "'Segoe UI Semibold', wf_segoe-ui_semibold, helvetica, arial, sans-serif"
    axis = {"labelColor": {"solid": {"color": MUTED}}, "fontSize": 9, "fontFamily": font, "showAxisTitle": False}
    grid = {"gridlineShow": True, "gridlineColor": {"solid": {"color": RULE}}}
    legend = [{"show": True, "position": "Top", "fontSize": 9, "showTitle": False,
               "labelColor": {"solid": {"color": MUTED}}}]
    return {
        "name": NAME, "dataColors": SERIES, "background": "#FFFFFF", "foreground": INK, "tableAccent": SIGNAL,
        "good": "#047940", "neutral": MUTED, "bad": SIGNAL,
        "textClasses": {"callout": {"fontSize": 26, "fontFace": semi, "color": INK},
                        "title": {"fontSize": 12, "fontFace": semi, "color": INK},
                        "header": {"fontSize": 10, "fontFace": semi, "color": INK},
                        "label": {"fontSize": 9, "fontFace": font, "color": MUTED}},
        "visualStyles": {
            "page": {"*": {"background": [{"color": {"solid": {"color": PLATFORM}}, "transparency": 0}],
                           "outspace": [{"color": {"solid": {"color": PLATFORM}}, "transparency": 0}]}},
            "*": {"*": {"background": [{"show": True, "color": {"solid": {"color": "#FFFFFF"}}, "transparency": 0}],
                        "border": [{"show": False, "radius": 4}], "dropShadow": [{"show": False}],
                        "padding": [{"top": 12, "bottom": 12, "left": 16, "right": 16}],
                        "title": [{"show": True, "fontColor": {"solid": {"color": INK}}, "fontSize": 12,
                                   "fontFamily": semi, "alignment": "left"}],
                        "subTitle": [{"fontColor": {"solid": {"color": MUTED}}, "fontSize": 9, "fontFamily": font}]}},
            "card": {"*": {"labels": [{"color": {"solid": {"color": INK}}, "fontSize": 26, "fontFamily": semi}],
                           "categoryLabels": [{"show": False}]}},
            "lineChart": {"*": {"lineStyles": [{"strokeWidth": 2}], "categoryAxis": [{**axis, "gridlineShow": False}],
                                "valueAxis": [{**axis, **grid}], "legend": legend}},
            "clusteredColumnChart": {"*": {"categoryAxis": [axis], "valueAxis": [{**axis, "show": False}]}},
            "tableEx": {"*": {"grid": [{"gridHorizontal": True, "gridHorizontalColor": {"solid": {"color": RULE}},
                                        "gridVertical": False, "rowPadding": 4}],
                              "columnHeaders": [{"fontColor": {"solid": {"color": "#FFFFFF"}}, "fontSize": 9,
                                                 "backColor": {"solid": {"color": NAVY}}}],
                              "values": [{"fontSize": 10, "fontColorPrimary": {"solid": {"color": INK}},
                                          "backColorPrimary": {"solid": {"color": "#FFFFFF"}},
                                          "backColorSecondary": {"solid": {"color": "#F8FAFC"}}}]}},
            "slicer": {"*": {"header": [{"fontColor": {"solid": {"color": MUTED}}, "fontSize": 9}],
                             "items": [{"fontColor": {"solid": {"color": INK}}, "fontSize": 10}]}},
        }}


def write(base: Path, files: dict):
    for rel, content in files.items():
        path = base / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        text = content if isinstance(content, str) else json.dumps(content, indent=2, ensure_ascii=False)
        path.write_text(text + ("" if text.endswith("\n") else "\n"), encoding="utf-8")


def main():
    for part in ["Report", "SemanticModel"]:
        d = PBI / f"{NAME}.{part}"
        if d.exists():
            for sub in d.iterdir():  # keep Desktop's .pbi cache folder, replace the definition
                if sub.name != ".pbi":
                    shutil.rmtree(sub) if sub.is_dir() else sub.unlink()
    write(PBI / f"{NAME}.SemanticModel", model_files())
    write(PBI / f"{NAME}.Report", report_files())
    res = PBI / f"{NAME}.Report" / "StaticResources" / "RegisteredResources"
    for f in IMAGES.values():
        shutil.copyfile(ROOT / "assets" / f, res / f)
    write(PBI, {f"{NAME}.pbip": {
        "$schema": f"{SCHEMA}/pbip/pbipProperties/1.0.0/schema.json", "version": "1.0",
        "artifacts": [{"report": {"path": f"{NAME}.Report"}}], "settings": {"enableAutoRecovery": True}},
        ".gitignore": "**/.pbi/localSettings.json\n**/.pbi/cache.abf\n"})
    n = sum(1 for _ in (PBI / f"{NAME}.Report").rglob("visual.json"))
    print(f"wrote {PBI / (NAME + '.pbip')} ({n} visuals)")


if __name__ == "__main__":
    main()
