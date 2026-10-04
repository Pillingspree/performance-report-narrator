# Monthly Performance Report Narrator

Turns CSV exports (Google Analytics, Google Ads, or similar) into a branded PDF with:
**what changed / why / next 3 actions**, a trend chart and a numbers table.

## One-time setup
```
pip install -r requirements.txt
export ANTHROPIC_API_KEY="your-key"     # optional; without it you get a simpler offline narrative
```

## Try it with fake data
```
python make_sample_data.py
python narrator.py --client "Acme Dental" \
  --inputs sample_data/ga.csv sample_data/ads.csv \
  --names "Website (GA4)" "Google Ads" --out acme-sept.pdf
```

## Use it as a web page (no commands needed after setup)
```
python app.py
```
Then open http://localhost:5000 in your browser, type the client name, upload the CSVs, and download the PDF.
You can also type your own agency name, colours and currency symbol on the page. For the AI-written
narrative, set `ANTHROPIC_API_KEY` in the same window *before* running `python app.py`.

## Supported exports
Google Analytics 4, Google Ads, Meta (Facebook/Instagram) Ads Manager and LinkedIn Campaign Manager.
LinkedIn-style files (tab-separated, UTF-16, title lines above the header) are handled automatically.

## Real client reports
1. Export a CSV from each platform with a **daily** row (needs a `Date`/`Day` column) covering this month AND last month.
2. Include a text column like `Campaign` or `Channel` so the report can explain *why* things moved.
3. Edit `brand.json` (name, colours, logo path, footer).
4. Run the command above with the client's name.

Options: `--month 2026-09` to pick a month, `--no-ai` to force the offline narrative.

## Safety notes
- All numbers are calculated by code. The AI only writes the explanation from those numbers.
- Always skim the PDF before sending it to a client.
