#!/usr/bin/env python3
"""
Web page for the Report Narrator.
Run:  python app.py      then open  http://localhost:5000
A freelancer uploads CSV exports, types the client name, and downloads the PDF.
This file only handles the web page; all the real work is done by narrator.py.
"""
import json
import re
import subprocess
import sys
import tempfile
from pathlib import Path

from flask import Flask, render_template_string, request, send_file

HERE = Path(__file__).parent
app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 15 * 1024 * 1024  # refuse uploads over 15 MB in total

PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Monthly Report Narrator</title>
<style>
 :root{--ink:#1d2433;--muted:#5d6678;--line:#dfe3ea;--bg:#f6f7f9;--card:#fff;--brand:#1F3A5F;--accent:#E07A1F}
 @media (prefers-color-scheme:dark){:root{--ink:#e8ebf1;--muted:#9aa3b5;--line:#2c3447;--bg:#12151c;--card:#1a1f2b}}
 *{box-sizing:border-box} body{margin:0;background:var(--bg);color:var(--ink);font:16px/1.5 system-ui,sans-serif}
 main{max-width:640px;margin:0 auto;padding:32px 16px 64px}
 h1{font-size:26px;margin:0 0 4px} p.lead{color:var(--muted);margin:0 0 24px}
 form{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:20px}
 label{display:block;font-weight:600;margin:16px 0 6px} label:first-of-type{margin-top:0}
 small{color:var(--muted);font-weight:400}
 input[type=text],input[type=month],input[type=file]{width:100%;padding:10px;border:1px solid var(--line);border-radius:8px;background:transparent;color:inherit;font:inherit}
 .row{display:grid;grid-template-columns:1fr 1fr;gap:12px} @media(max-width:520px){.row{grid-template-columns:1fr}}
 .file{border:1px dashed var(--line);border-radius:8px;padding:10px;margin-bottom:8px}
 details{margin-top:18px} summary{cursor:pointer;font-weight:600}
 button{margin-top:22px;width:100%;padding:12px;border:0;border-radius:8px;background:var(--brand);color:#fff;font:inherit;font-weight:600;cursor:pointer}
 button:hover{filter:brightness(1.15)} button:disabled{opacity:.6;cursor:wait}
 .err{background:#fde8e8;color:#8a1c1c;border-radius:8px;padding:12px;margin-bottom:16px;white-space:pre-wrap}
 .note{color:var(--muted);font-size:14px;margin-top:14px}
</style></head><body><main>
<h1>Monthly Report Narrator</h1>
<p class="lead">Upload your Google Analytics / Ads, Meta or LinkedIn exports. Get a branded PDF with what changed, why, and the next 3 actions.</p>
{% if error %}<div class="err">{{ error }}</div>{% endif %}
<form method="post" action="/report" enctype="multipart/form-data" onsubmit="b.disabled=true;b.textContent='Building your report...'">
 <label for="client">Client name</label>
 <input type="text" id="client" name="client" required placeholder="e.g. Acme Dental">

 <label>CSV exports <small>(1 to 4 files, each needs a Date/Day column and ideally a Campaign or Channel column)</small></label>
 {% for i in range(1,5) %}
 <div class="file"><input type="file" name="file{{i}}" accept=".csv,.txt"{% if i==1 %} required{% endif %}>
  <input type="text" name="name{{i}}" placeholder="Label for this file, e.g. Google Ads" style="margin-top:6px"></div>
 {% endfor %}

 <label for="month">Month to report on <small>(optional; defaults to the latest month in the files)</small></label>
 <input type="month" id="month" name="month">

 <details><summary>Your branding (optional)</summary>
  <label for="company">Agency / your name</label><input type="text" id="company" name="company" placeholder="Your Agency Name">
  <div class="row"><div><label for="c1">Main colour</label><input type="text" id="c1" name="primary" placeholder="#1F3A5F"></div>
  <div><label for="c2">Accent colour</label><input type="text" id="c2" name="accent" placeholder="#E07A1F"></div></div>
  <label for="cur">Currency symbol</label><input type="text" id="cur" name="currency" placeholder="$" maxlength="3">
 </details>
 <button id="b" type="submit">Create PDF report</button>
 <p class="note">Your files are used only to build this report and are deleted straight afterwards. Always skim the PDF before sending it to a client.</p>
</form></main></body></html>"""


def clean_colour(value, default):
    value = (value or "").strip()
    return value if re.fullmatch(r"#[0-9a-fA-F]{6}", value) else default


@app.get("/")
def home():
    return render_template_string(PAGE, error=None)


@app.post("/report")
def report():
    client = (request.form.get("client") or "").strip()[:80]
    if not client:
        return render_template_string(PAGE, error="Please type the client's name."), 400

    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        inputs, names = [], []
        for i in range(1, 5):
            f = request.files.get(f"file{i}")
            if f and f.filename:
                path = tmp / f"input{i}.csv"
                f.save(path)
                inputs.append(str(path))
                names.append((request.form.get(f"name{i}") or "").strip()[:40] or Path(f.filename).stem[:40])
        if not inputs:
            return render_template_string(PAGE, error="Please upload at least one CSV file."), 400

        # start from brand.json, then apply anything typed into the branding box
        brand = json.loads((HERE / "brand.json").read_text())
        company = (request.form.get("company") or "").strip()[:60]
        if company:
            brand["company_name"] = company
            brand["footer"] = f"Prepared by {company}"
        brand["primary_color"] = clean_colour(request.form.get("primary"), brand["primary_color"])
        brand["accent_color"] = clean_colour(request.form.get("accent"), brand["accent_color"])
        cur = (request.form.get("currency") or "").strip()[:3]
        if cur:
            brand["currency"] = cur
        brand_path = tmp / "brand.json"
        brand_path.write_text(json.dumps(brand))

        out_pdf = tmp / "report.pdf"
        cmd = [sys.executable, str(HERE / "narrator.py"), "--client", client, "--inputs", *inputs,
               "--names", *names, "--brand", str(brand_path), "--out", str(out_pdf)]
        month = (request.form.get("month") or "").strip()
        if re.fullmatch(r"\d{4}-\d{2}", month):
            cmd += ["--month", month]
        try:
            run = subprocess.run(cmd, capture_output=True, text=True, timeout=180)
        except subprocess.TimeoutExpired:
            return render_template_string(PAGE, error="That took too long. Try smaller files."), 500
        if run.returncode != 0 or not out_pdf.exists():
            msg = (run.stderr or run.stdout or "Something went wrong.").strip().splitlines()[-1]
            msg = re.sub(r"/\S*input(\d)\.csv", lambda m: f"File {m.group(1)}", msg)  # hide temp file paths
            return render_template_string(PAGE, error=f"Couldn't build the report: {msg}"), 400

        data = out_pdf.read_bytes()  # read before the temp folder (and the client's data) is deleted

    from io import BytesIO
    safe = re.sub(r"[^A-Za-z0-9_-]+", "-", client).strip("-") or "client"
    return send_file(BytesIO(data), mimetype="application/pdf", as_attachment=True,
                     download_name=f"{safe}-report.pdf")


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5000)
