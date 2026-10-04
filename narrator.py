#!/usr/bin/env python3
"""
Monthly Performance Report Narrator
CSV exports (Google Analytics / Google Ads / anything similar)
  -> what changed / why / next 3 actions -> branded PDF.

Usage:
  python narrator.py --client "Acme Dental" \
      --inputs sample_data/ga.csv sample_data/ads.csv \
      --names "Website (GA4)" "Google Ads" \
      --brand brand.json --out report.pdf
"""
import argparse
import json
import os
import re
import sys
import tempfile
from pathlib import Path

import pandas as pd

# ----------------------------------------------------------------------------
# 1. READING THE CSVs
#    Different exports call the same thing different names ("Spend", "Cost").
#    We map them to one standard name so the rest of the tool works on any file.
# ----------------------------------------------------------------------------
ALIASES = {
    # Google (GA4 / Ads), Meta (Facebook/Instagram) Ads Manager and LinkedIn Campaign Manager names
    "cost": ["spend", "amount_spent", "ad_spend", "cost", "total_spent"],
    "conversions": ["conversions", "key_events", "goal_completions", "leads", "results",
                    "purchases", "website_purchases", "total_conversions", "one_click_leads"],
    "revenue": ["revenue", "purchase_revenue", "total_revenue", "conversion_value",
                "purchases_conversion_value", "website_purchases_conversion_value",
                "purchase_conversion_value"],
    "sessions": ["sessions", "visits"],
    "users": ["users", "total_users", "active_users"],
    "clicks": ["clicks", "link_clicks", "clicks_all"],
    "impressions": ["impressions", "impr"],
}
DATE_NAMES = ["date", "day", "month", "week", "start_date", "reporting_starts"]
RATIO_HINTS = ["rate", "avg", "average", "ratio", "duration", "per_session",
               "ctr", "roas", "cpc", "cpm", "cpa", "frequency", "cost_per"]
CAMPAIGN_HINTS = ["campaign", "channel", "source", "medium", "page", "ad_set", "ad_group"]
LOWER_IS_BETTER = {"cpc", "cpa", "bounce_rate"}
MONEY = {"cost", "revenue", "cpc", "cpa"}
PERCENT = {"ctr", "conv_rate", "bounce_rate", "engagement_rate"}
KEY_METRIC_PRIORITY = ["conversions", "revenue", "sessions", "clicks", "users", "impressions"]


def norm(name: str) -> str:
    n = re.sub(r"[^a-z0-9]+", "_", str(name).strip().lower()).strip("_")
    # "Amount spent (USD)" -> amount_spent ; "Start Date (in UTC)" -> start_date
    n = re.sub(r"_(in_utc|usd|eur|gbp|ngn|cad|aud|zar|kes)$", "", n)
    return n


def read_any_csv(path: str) -> pd.DataFrame:
    """Reads normal CSVs and also the odd formats some platforms export
    (tab-separated, UTF-16 text, or a few title lines above the header)."""
    last_err = None
    for enc in ("utf-8-sig", "utf-16", "latin-1"):
        try:
            raw = Path(path).read_text(encoding=enc)
        except (UnicodeError, UnicodeDecodeError) as e:
            last_err = e
            continue
        lines = raw.splitlines()
        # the header row is the first line that has several separators
        sep = "\t" if raw.count("\t") > raw.count(",") else ","
        start = next((i for i, l in enumerate(lines) if l.count(sep) >= 2), 0)
        from io import StringIO
        return pd.read_csv(StringIO("\n".join(lines[start:])), sep=sep)
    sys.exit(f"{path}: could not read this file ({last_err})")


def load_csv(path: str) -> pd.DataFrame:
    df = read_any_csv(path)
    df.columns = [norm(c) for c in df.columns]
    # daily "reach" can't be added up across days (the same person is counted again), so skip it
    df = df.drop(columns=[c for c in df.columns if c == "reach"])

    # standardise metric names
    rename = {}
    for std, options in ALIASES.items():
        for col in df.columns:
            if col in options and std not in df.columns and col not in rename:
                rename[col] = std
                break
    df = df.rename(columns=rename)

    # find the date column
    date_col = next((c for c in df.columns if c in DATE_NAMES), None)
    if date_col is None:
        sys.exit(f"{path}: no date column found. Add a column named 'date' (or day/month/week).")
    df["_date"] = pd.to_datetime(df[date_col], errors="coerce")
    df = df.dropna(subset=["_date"])
    df["_month"] = df["_date"].dt.to_period("M")

    # turn "$1,234" or "12%" text into real numbers
    for col in df.columns:
        if col.startswith("_") or col == date_col:
            continue
        if not pd.api.types.is_numeric_dtype(df[col]):
            cleaned = pd.to_numeric(
                df[col].astype(str).str.replace(r"[$,%\s]", "", regex=True), errors="coerce"
            )
            if cleaned.notna().mean() > 0.8:
                df[col] = cleaned
    df.attrs["date_col"] = date_col
    return df


# ----------------------------------------------------------------------------
# 2. THE MATH (done by code, never by the AI, so numbers are always correct)
# ----------------------------------------------------------------------------
def summarise(df: pd.DataFrame, month: pd.Period) -> dict:
    sub = df[df["_month"] == month]
    if sub.empty:
        return {}
    out = {}
    for col in sub.select_dtypes("number").columns:
        out[col] = float(sub[col].mean() if any(h in col for h in RATIO_HINTS) else sub[col].sum())

    def has(*cols):
        return all(c in out and out[c] for c in cols)

    if has("clicks", "impressions"):
        out["ctr"] = out["clicks"] / out["impressions"] * 100
    if has("cost", "clicks"):
        out["cpc"] = out["cost"] / out["clicks"]
    if has("cost", "conversions"):
        out["cpa"] = out["cost"] / out["conversions"]
    if has("revenue", "cost"):
        out["roas"] = out["revenue"] / out["cost"]
    if has("conversions", "sessions"):
        out["conv_rate"] = out["conversions"] / out["sessions"] * 100
    elif has("conversions", "clicks"):
        out["conv_rate"] = out["conversions"] / out["clicks"] * 100
    return out


def pct_change(cur, prev):
    if prev in (None, 0) or cur is None:
        return None
    return (cur - prev) / abs(prev) * 100


def fmt(metric, value, cur_sym="$"):
    if value is None:
        return "n/a"
    if metric in MONEY:
        return f"{cur_sym}{value:,.2f}" if metric in ("cpc", "cpa") else f"{cur_sym}{value:,.0f}"
    if metric in PERCENT or "rate" in metric:
        return f"{value:.1f}%"
    if metric == "roas":
        return f"{value:.2f}x"
    return f"{value:,.0f}" if abs(value) >= 100 else f"{value:,.1f}"


def label(metric):
    special = {"cpc": "Cost per click", "cpa": "Cost per conversion", "ctr": "Click-through rate",
               "roas": "Return on ad spend", "conv_rate": "Conversion rate"}
    return special.get(metric, metric.replace("_", " ").capitalize())


def dimension_movers(df, cur_m, prev_m, metric):
    """Which campaign / channel / page drove the change? (the 'why')"""
    if prev_m is None:
        return None
    dims = [c for c in df.columns
            if not c.startswith("_") and c != df.attrs["date_col"]
            and c not in DATE_NAMES and "date" not in c and "ending" not in c and not c.endswith("_ends")
            and not pd.api.types.is_numeric_dtype(df[c]) and 2 <= df[c].nunique() <= 50]
    if not dims or metric not in df.columns:
        return None
    # prefer a column like "Campaign" or "Channel" over other text columns
    dims.sort(key=lambda c: next((i for i, h in enumerate(CAMPAIGN_HINTS) if h in c), 99))
    dim = dims[0]
    cur = df[df["_month"] == cur_m].groupby(dim)[metric].sum()
    prev = df[df["_month"] == prev_m].groupby(dim)[metric].sum()
    t = pd.concat([cur.rename("current"), prev.rename("previous")], axis=1).fillna(0)
    t["delta"] = t["current"] - t["previous"]
    t = t.reindex(t["delta"].abs().sort_values(ascending=False).index).head(4)
    return {"dimension": dim, "metric": metric,
            "movers": [{"name": str(i), "current": round(r.current, 2),
                        "previous": round(r.previous, 2), "change": round(r.delta, 2)}
                       for i, r in t.iterrows()]}


def analyse(sources, month_arg):
    all_months = sorted({m for _, df in sources for m in df["_month"].unique()})
    cur_m = pd.Period(month_arg, "M") if month_arg else all_months[-1]
    prev_m = cur_m - 1
    result = []
    for name, df in sources:
        cur, prev = summarise(df, cur_m), summarise(df, prev_m)
        if not cur:
            print(f"Warning: '{name}' has no data for {cur_m}, skipping.")
            continue
        metrics = {m: {"current": cur[m], "previous": prev.get(m),
                       "change_pct": pct_change(cur[m], prev.get(m))}
                   for m in cur if m in set(KEY_METRIC_PRIORITY) | MONEY | PERCENT | {"roas"} or "rate" in m}
        movers = []
        for m in [k for k in KEY_METRIC_PRIORITY if k in cur][:1] + (["cost"] if "cost" in cur else []):
            d = dimension_movers(df, cur_m, prev_m if prev else None, m)
            if d:
                movers.append(d)
        result.append({"source": name, "metrics": metrics, "breakdowns": movers})
    return {"period": cur_m.strftime("%B %Y"), "previous_period": prev_m.strftime("%B %Y"),
            "sources": result}, cur_m, prev_m


# ----------------------------------------------------------------------------
# 3. THE NARRATIVE (what changed / why / next 3 actions)
# ----------------------------------------------------------------------------
SYSTEM_PROMPT = """You are a senior marketing analyst writing a monthly report for a small-business client
who is NOT technical. Use ONLY the numbers in the data provided. Never invent figures, causes you cannot
see in the data, or benchmarks. If the cause is unclear, say what the data suggests and what to check.
Plain English, no jargon, short sentences. Clients want to know "what should we do?".
Reply with ONLY valid JSON in this exact shape:
{"headline": "one-sentence summary of the month",
 "what_changed": ["3-5 bullets, each with the real numbers"],
 "why": ["2-4 bullets explaining likely drivers, using the breakdowns"],
 "actions": [{"title": "short action", "detail": "1-2 sentences: what to do and why", "priority": "High|Medium|Low"}]}
Provide exactly 3 actions, most important first."""


def narrate_ai(facts):
    import anthropic
    client = anthropic.Anthropic()  # reads ANTHROPIC_API_KEY from your environment
    msg = client.messages.create(
        model=os.getenv("NARRATOR_MODEL", "claude-sonnet-5-5"),
        max_tokens=2000, system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": json.dumps(facts, default=str)}])
    text = msg.content[0].text
    data = json.loads(text[text.index("{"): text.rindex("}") + 1])
    assert {"headline", "what_changed", "why", "actions"} <= set(data) and len(data["actions"]) >= 3
    data["actions"] = data["actions"][:3]
    return data


def narrate_basic(facts, sym="$"):
    """Offline fallback: simple rule-based text. Works with no AI and no internet."""
    changes, whys = [], []
    for s in facts["sources"]:
        for m, v in s["metrics"].items():
            if v["change_pct"] is not None:
                changes.append((abs(v["change_pct"]), s["source"], m, v))
        for b in s["breakdowns"]:
            if b["movers"]:
                top = b["movers"][0]
                direction = "rose" if top["change"] > 0 else "fell"
                whys.append(f"In {s['source']}, {label(b['metric']).lower()} {direction} most for "
                            f"'{top['name']}' ({fmt(b['metric'], top['previous'], sym)} to "
                            f"{fmt(b['metric'], top['current'], sym)}).")
    changes.sort(key=lambda x: -x[0])
    bullets = [f"{src}: {label(m)} is {fmt(m, v['current'], sym)} vs {fmt(m, v['previous'], sym)} "
               f"last month ({v['change_pct']:+.1f}%)." for _, src, m, v in changes[:5]]
    up = sum(1 for c in changes[:5] if c[3]["change_pct"] > 0)
    headline = (f"{facts['period']}: " + ("mostly positive movement" if up >= 3 else "mixed results")
                + " compared with " + facts["previous_period"] + ".")
    actions = [
        {"title": "Review the biggest mover", "priority": "High",
         "detail": "Look at the campaign or channel with the largest change listed above and decide whether to scale it up or fix it."},
        {"title": "Check cost per conversion", "priority": "Medium",
         "detail": "If it rose, pause or trim the weakest campaigns and move the budget to the best performers."},
        {"title": "Set next month's target", "priority": "Medium",
         "detail": "Agree one number to improve (conversions or return on ad spend) and track it weekly."},
    ]
    return {"headline": headline, "what_changed": bullets or ["Not enough data to compare months."],
            "why": whys or ["No breakdown column (campaign/channel) was found, so drivers can't be identified."],
            "actions": actions}


# ----------------------------------------------------------------------------
# 4. THE PDF
# ----------------------------------------------------------------------------
def make_chart(sources, cur_m, prev_m, brand, tmpdir):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    picks = []
    for name, df in sources:
        for m in KEY_METRIC_PRIORITY + ["cost"]:
            if m in df.columns and not df[df["_month"] == cur_m].empty:
                picks.append((name, df, m))
                break
    if not picks:
        return None
    fig, axes = plt.subplots(1, len(picks), figsize=(7.2, 2.4), squeeze=False)
    for ax, (name, df, m) in zip(axes[0], picks):
        for period, color, lab in ((prev_m, "#B0B7C3", "Last month"), (cur_m, brand["primary_color"], "This month")):
            sub = df[df["_month"] == period]
            if sub.empty:
                continue
            daily = sub.groupby(sub["_date"].dt.day)[m].sum()
            ax.plot(daily.index, daily.values, color=color, label=lab, linewidth=2)
        ax.set_title(f"{name}: {label(m).lower()} by day", fontsize=8)
        ax.tick_params(labelsize=7)
        ax.spines[["top", "right"]].set_visible(False)
        ax.legend(fontsize=7, frameon=False)
    fig.tight_layout()
    path = os.path.join(tmpdir, "chart.png")
    fig.savefig(path, dpi=200)
    plt.close(fig)
    return path


def build_pdf(out, client, facts, narrative, chart_path, brand):
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib.units import mm
    from reportlab.platypus import (Image, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle,
                                    KeepTogether)

    sym = brand.get("currency", "$")
    primary, accent = colors.HexColor(brand["primary_color"]), colors.HexColor(brand["accent_color"])
    # Use DejaVu Sans (ships with matplotlib) so currency symbols like the naira sign (₦) display
    # properly; the built-in PDF fonts can't draw them. Falls back to Helvetica if unavailable.
    FONT, FONT_BOLD = "Helvetica", "Helvetica-Bold"
    try:
        import matplotlib
        from reportlab.pdfbase import pdfmetrics
        from reportlab.pdfbase.ttfonts import TTFont
        fdir = Path(matplotlib.get_data_path()) / "fonts" / "ttf"
        pdfmetrics.registerFont(TTFont("DejaVu", str(fdir / "DejaVuSans.ttf")))
        pdfmetrics.registerFont(TTFont("DejaVu-Bold", str(fdir / "DejaVuSans-Bold.ttf")))
        FONT, FONT_BOLD = "DejaVu", "DejaVu-Bold"
    except Exception:
        pass

    base = ParagraphStyle("b", fontName=FONT, fontSize=10, leading=14, textColor=colors.HexColor("#222"))
    h1 = ParagraphStyle("h1", parent=base, fontName=FONT_BOLD, fontSize=20, leading=24, textColor=primary)
    h2 = ParagraphStyle("h2", parent=base, fontName=FONT_BOLD, fontSize=13, leading=16,
                        textColor=primary, spaceBefore=12, spaceAfter=6)
    small = ParagraphStyle("s", parent=base, fontSize=8, textColor=colors.HexColor("#666"))
    headline = ParagraphStyle("hl", parent=base, fontSize=12, leading=17)

    def header_footer(canvas, doc):
        canvas.saveState()
        w, h = A4
        canvas.setFillColor(primary)
        canvas.rect(0, h - 14 * mm, w, 14 * mm, stroke=0, fill=1)
        x = 15 * mm
        logo = brand.get("logo")
        if logo and Path(logo).exists():
            canvas.drawImage(logo, x, h - 12 * mm, height=10 * mm, width=30 * mm,
                             preserveAspectRatio=True, mask="auto")
            x += 34 * mm
        canvas.setFillColor(colors.white)
        canvas.setFont(FONT_BOLD, 11)
        canvas.drawString(x, h - 9 * mm, brand["company_name"])
        canvas.setFont(FONT, 8)
        canvas.setFillColor(colors.HexColor("#666"))
        canvas.drawString(15 * mm, 8 * mm, brand.get("footer", ""))
        canvas.drawRightString(w - 15 * mm, 8 * mm, f"Page {doc.page}")
        canvas.restoreState()

    doc = SimpleDocTemplate(out, pagesize=A4, leftMargin=15 * mm, rightMargin=15 * mm,
                            topMargin=22 * mm, bottomMargin=16 * mm,
                            title=f"{client} - {facts['period']} performance report")
    story = [Paragraph(f"{client}: {facts['period']} Performance Report", h1), Spacer(1, 4),
             Paragraph(f"Compared with {facts['previous_period']}", small), Spacer(1, 8),
             Paragraph(narrative["headline"], headline)]

    story.append(Paragraph("What changed", h2))
    for b in narrative["what_changed"]:
        story.append(Paragraph(f"&bull; {b}", base))
        story.append(Spacer(1, 2))

    story.append(Paragraph("Why it happened", h2))
    for b in narrative["why"]:
        story.append(Paragraph(f"&bull; {b}", base))
        story.append(Spacer(1, 2))

    story.append(Paragraph("What we recommend next", h2))
    for i, a in enumerate(narrative["actions"], 1):
        pr = a.get("priority", "Medium")
        pcol = {"High": "#C0392B", "Medium": "#D68910", "Low": "#2E86C1"}.get(pr, "#666")
        card = Table([[Paragraph(f"<b>{i}</b>", ParagraphStyle("n", parent=base, fontSize=16,
                                                                 textColor=colors.white, alignment=1)),
                       [Paragraph(f"<b>{a['title']}</b> <font color='{pcol}' size=8>[{pr} priority]</font>", base),
                        Paragraph(a["detail"], base)]]],
                     colWidths=[12 * mm, 160 * mm])
        card.setStyle(TableStyle([("BACKGROUND", (0, 0), (0, 0), accent), ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                                  ("BOX", (0, 0), (-1, -1), 0.5, colors.HexColor("#DDD")),
                                  ("LEFTPADDING", (1, 0), (1, 0), 8), ("TOPPADDING", (0, 0), (-1, -1), 6),
                                  ("BOTTOMPADDING", (0, 0), (-1, -1), 6)]))
        story += [KeepTogether(card), Spacer(1, 5)]

    if chart_path:
        story += [Paragraph("Trend", h2), Image(chart_path, width=180 * mm, height=60 * mm)]

    story.append(Paragraph("The numbers", h2))
    for s in facts["sources"]:
        rows = [["Metric", "This month", "Last month", "Change"]]
        styles = [("BACKGROUND", (0, 0), (-1, 0), primary), ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                  ("FONTNAME", (0, 0), (-1, 0), FONT_BOLD), ("FONTSIZE", (0, 0), (-1, -1), 9),
                  ("ALIGN", (1, 0), (-1, -1), "RIGHT"), ("GRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#DDD")),
                  ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#F6F7F9")])]
        for r, (m, v) in enumerate(s["metrics"].items(), 1):
            ch = v["change_pct"]
            rows.append([label(m), fmt(m, v["current"], sym), fmt(m, v["previous"], sym),
                         "n/a" if ch is None else f"{ch:+.1f}%"])
            if ch is not None and m != "cost":
                good = (ch < 0) if m in LOWER_IS_BETTER else (ch > 0)
                styles.append(("TEXTCOLOR", (3, r), (3, r), colors.HexColor("#1E8449" if good else "#C0392B")))
        t = Table(rows, colWidths=[62 * mm, 38 * mm, 38 * mm, 34 * mm])
        t.setStyle(TableStyle(styles))
        story += [Paragraph(s["source"], ParagraphStyle("sn", parent=base, fontName=FONT_BOLD)),
                  Spacer(1, 3), t, Spacer(1, 10)]

    doc.build(story, onFirstPage=header_footer, onLaterPages=header_footer)


# ----------------------------------------------------------------------------
# 5. PUTTING IT TOGETHER
# ----------------------------------------------------------------------------
def main():
    ap = argparse.ArgumentParser(description="Turn CSV exports into a plain-English branded PDF report.")
    ap.add_argument("--client", required=True, help="Client name shown on the report")
    ap.add_argument("--inputs", nargs="+", required=True, help="One or more CSV files")
    ap.add_argument("--names", nargs="+", help="Friendly names for each CSV, same order")
    ap.add_argument("--brand", default="brand.json")
    ap.add_argument("--month", help="Report month as YYYY-MM (default: latest month in the data)")
    ap.add_argument("--out", default="report.pdf")
    ap.add_argument("--no-ai", action="store_true", help="Skip the AI and use the simple offline narrative")
    args = ap.parse_args()

    brand = json.loads(Path(args.brand).read_text())
    names = args.names or [Path(p).stem for p in args.inputs]
    if len(names) != len(args.inputs):
        sys.exit("--names must have the same number of items as --inputs")

    sources = [(n, load_csv(p)) for n, p in zip(names, args.inputs)]
    facts, cur_m, prev_m = analyse(sources, args.month)
    if not facts["sources"]:
        sys.exit("No data found for that month.")

    narrative = None
    if not args.no_ai and os.getenv("ANTHROPIC_API_KEY"):
        try:
            narrative = narrate_ai(facts)
            print("Narrative written by AI.")
        except Exception as e:  # never fail the whole report because the AI hiccuped
            print(f"AI step failed ({e}); using offline narrative instead.")
    elif not args.no_ai:
        print("No ANTHROPIC_API_KEY set; using the simple offline narrative.")
    if narrative is None:
        narrative = narrate_basic(facts, brand.get("currency", "$"))

    with tempfile.TemporaryDirectory() as tmp:
        chart = make_chart(sources, cur_m, prev_m, brand, tmp)
        build_pdf(args.out, args.client, facts, narrative, chart, brand)
    print(f"Done: {args.out}")


if __name__ == "__main__":
    main()
