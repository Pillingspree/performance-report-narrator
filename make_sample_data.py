"""Creates fake Google Analytics and Google Ads exports (Aug + Sep 2026) for testing."""
import random
from pathlib import Path

import pandas as pd

random.seed(7)
out = Path("sample_data")
out.mkdir(exist_ok=True)

days = pd.date_range("2026-08-01", "2026-09-30")

# Google Analytics: sessions and conversions by channel
ga = []
for d in days:
    sep = d.month == 9
    for ch, base, cr in [("Organic Search", 120 * (1.25 if sep else 1), 0.03),
                         ("Paid Search", 90 * (1.1 if sep else 1), 0.045 * (0.8 if sep else 1)),
                         ("Direct", 60, 0.02), ("Social", 40 * (0.7 if sep else 1), 0.01)]:
        s = int(base * random.uniform(0.8, 1.2))
        ga.append({"Date": d.date(), "Channel": ch, "Sessions": s,
                   "Total users": int(s * 0.8), "Key events": round(s * cr),
                   "Bounce rate": f"{random.uniform(38, 55):.1f}%"})
pd.DataFrame(ga).to_csv(out / "ga.csv", index=False)

# Google Ads: spend ramps up on one campaign that converts poorly
ads = []
for d in days:
    sep = d.month == 9
    for camp, spend, cpc, cr in [("Brand", 25, 0.9, 0.12), ("Services - Generic", 60 * (1.5 if sep else 1), 2.2, 0.05 * (0.6 if sep else 1)),
                                 ("Retargeting", 20, 1.1, 0.09)]:
        sp = spend * random.uniform(0.85, 1.15)
        clicks = int(sp / cpc)
        ads.append({"Day": d.date(), "Campaign": camp, "Impressions": int(clicks * random.uniform(14, 20)),
                    "Clicks": clicks, "Cost": f"${sp:,.2f}", "Conversions": round(clicks * cr),
                    "Conversion value": f"${clicks * cr * 85:,.2f}"})
pd.DataFrame(ads).to_csv(out / "ads.csv", index=False)
print("Sample data written to sample_data/")

# Meta (Facebook/Instagram) Ads Manager style export
meta = []
for d in days:
    sep = d.month == 9
    for camp, spend, cpc, cr in [("Lead Gen - Lookalike", 40 * (1.4 if sep else 1), 0.8, 0.06 * (0.65 if sep else 1)),
                                 ("Retargeting - Site Visitors", 18, 0.6, 0.10)]:
        sp = spend * random.uniform(0.85, 1.15)
        clicks = int(sp / cpc)
        res = round(clicks * cr)
        meta.append({"Reporting starts": d.date(), "Reporting ends": d.date(), "Campaign name": camp,
                     "Amount spent (USD)": round(sp, 2), "Impressions": int(clicks * 30),
                     "Reach": int(clicks * 22), "Link clicks": clicks,
                     "CTR (all)": round(random.uniform(1.5, 3.5), 2), "Results": res,
                     "Purchase ROAS (return on ad spend)": round(random.uniform(2, 4), 2),
                     "Website purchases conversion value": round(res * 60, 2)})
pd.DataFrame(meta).to_csv(out / "meta_ads.csv", index=False)

# LinkedIn Campaign Manager style export: title lines, tab-separated, UTF-16
li = []
for d in days:
    sep = d.month == 9
    for camp, spend, cpc, cr in [("Decision Makers - Whitepaper", 70 * (1.2 if sep else 1), 6.5, 0.15 * (0.7 if sep else 1)),
                                 ("Brand Awareness", 30, 3.0, 0.05)]:
        sp = spend * random.uniform(0.85, 1.15)
        clicks = int(sp / cpc)
        li.append({"Start Date (in UTC)": d.strftime("%m/%d/%Y"), "Campaign Name": camp,
                   "Total Spent": f"{sp:.2f}", "Impressions": int(clicks * 60), "Clicks": clicks,
                   "Leads": round(clicks * cr)})
header = "Campaign Performance Report\nExported 2026-10-01\n"
(out / "linkedin_ads.csv").write_text(header + pd.DataFrame(li).to_csv(index=False, sep="\t"), encoding="utf-16")
print("Meta and LinkedIn samples written too")
