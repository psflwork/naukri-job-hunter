"""Contact list for manual outreach: recruiter emails/phones from job posts, company links, mailto drafts."""

import csv
import html
import random
from datetime import datetime
from pathlib import Path
from urllib.parse import quote, quote_plus

from pypdf import PdfReader

from naukri import Job, Naukri, log

DEFAULT_SUBJECT = "Application for {title} - {name}"
DEFAULT_BODY = """Hi,

I came across the {title} role at {company} on Naukri ({job_url}) and would like to apply.
I have {experience_years} years of relevant experience; my resume is attached.

Looking forward to hearing from you.

Regards,
{name}"""


def resume_name(path: Path) -> str:
    """First line of the resume, if it looks like a name."""
    text = PdfReader(path).pages[0].extract_text() or ""
    for line in text.splitlines():
        line = line.strip()
        if line:
            words = line.split()
            return line if len(words) <= 5 and all(w.replace(".", "").isalpha() for w in words) else ""
    return ""


def fetch_details(jobs: list[Job], cache: dict, headless: bool = False) -> dict:
    """Fill `cache` (job_id -> details) for jobs not fetched yet; returns the cache."""
    missing = [j for j in jobs if j.job_id not in cache]
    if not missing:
        return cache
    log(f"Fetching contact details for {len(missing)} jobs...")
    with Naukri(headless=headless) as n:
        for i, job in enumerate(missing, 1):
            try:
                cache[job.job_id] = n.job_details(job.url)
                log(f"  [{i}/{len(missing)}] {job.company}")
            except Exception as e:
                log(f"  [{i}/{len(missing)}] {job.company}: failed ({str(e).splitlines()[0]})")
            n.page.wait_for_timeout(random.randint(1500, 3500))
    return cache


def build_rows(jobs: list[Job], details: dict, cfg: dict, name: str) -> list[dict]:
    outreach = cfg.get("outreach") or {}
    subject_t = outreach.get("subject") or DEFAULT_SUBJECT
    body_t = outreach.get("body") or DEFAULT_BODY
    rows = []
    for j in jobs:
        d = details.get(j.job_id, {})
        fields = {"title": j.title, "company": j.company, "job_url": j.url, "name": name,
                  "experience_years": cfg.get("experience_years", "")}
        subject = subject_t.format(**fields)
        body = body_t.format(**fields)
        emails = d.get("emails", [])
        rows.append({
            "score": j.score,
            "company": j.company,
            "title": j.title,
            "job_url": j.url,
            "emails": emails,
            "phones": d.get("phones", []),
            "website": d.get("website", ""),
            "address": d.get("address", ""),
            "agency": d.get("recruitment_agency", False),
            "apply_type": "Company site" if j.external_apply else ("Naukri + questions" if j.has_questionnaire else "Naukri"),
            "mailto": f"mailto:{','.join(emails)}?subject={quote(subject)}&body={quote(body)}" if emails else "",
            "linkedin": f"https://www.linkedin.com/search/results/people/?keywords={quote_plus(j.company + ' recruiter')}",
            "careers": f"https://www.google.com/search?q={quote_plus(j.company + ' careers jobs')}",
        })
    rows.sort(key=lambda r: (not r["emails"], not r["phones"], -r["score"]))
    return rows


def write_contacts(rows: list[dict], output: Path, prefix: str) -> tuple[Path, Path]:
    output.mkdir(exist_ok=True)
    stamp = datetime.now().strftime("%Y-%m-%d_%H%M")
    csv_path = output / f"{prefix}_{stamp}.csv"
    html_path = output / f"{prefix}_{stamp}.html"

    cols = ["score", "company", "title", "emails", "phones", "website", "address", "agency", "apply_type", "job_url"]
    with csv_path.open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(cols)
        for r in rows:
            w.writerow([", ".join(r[c]) if isinstance(r[c], list) else r[c] for c in cols])

    def link(url: str, text: str) -> str:
        return f"<a href='{html.escape(url)}' target='_blank'>{html.escape(text)}</a>" if url else ""

    body = []
    for r in rows:
        emails = "<br>".join(html.escape(e) for e in r["emails"])
        if r["mailto"]:
            emails += f"<br><a class='btn' href='{html.escape(r['mailto'])}'>Draft email</a>"
        site = r["website"]
        site_link = link(site if site.startswith("http") else f"https://{site}", site) if site else ""
        body.append(
            f"<tr><td class='s'>{r['score']}</td>"
            f"<td><b>{html.escape(r['company'])}</b>{' <span class=ag>agency</span>' if r['agency'] else ''}"
            f"<br><span class='m'>{html.escape(r['address'][:90])}</span></td>"
            f"<td>{link(r['job_url'], r['title'])}<br><span class='m'>{r['apply_type']}</span></td>"
            f"<td>{emails or '<span class=m>not published</span>'}</td>"
            f"<td>{'<br>'.join(link('tel:' + p.replace(' ', ''), p) for p in r['phones'])}</td>"
            f"<td>{site_link}<br>{link(r['careers'], 'Careers page')}<br>{link(r['linkedin'], 'Recruiters on LinkedIn')}</td></tr>"
        )
    with_email = sum(1 for r in rows if r["emails"])
    html_path.write_text(f"""<!doctype html><html><head><meta charset="utf-8"><title>Contacts {stamp}</title><style>
body{{font-family:-apple-system,sans-serif;margin:24px;color:#222}}
table{{border-collapse:collapse;width:100%;font-size:14px}}
th,td{{border-bottom:1px solid #e5e5e5;padding:8px;text-align:left;vertical-align:top}}
th{{background:#f6f6f6;position:sticky;top:0}} td.s{{font-weight:700}} .m{{color:#777;font-size:12px}}
a{{color:#275df5;text-decoration:none}} a:hover{{text-decoration:underline}}
.btn{{display:inline-block;margin-top:4px;padding:2px 8px;border:1px solid #275df5;border-radius:4px;font-size:12px}}
.ag{{background:#fff3cd;padding:1px 5px;border-radius:3px;font-size:11px}}
</style></head><body><h2>Contacts for manual outreach &mdash; {stamp}</h2>
<p>{with_email} of {len(rows)} jobs publish an email. "Draft email" opens your mail app with a prefilled
subject and message &mdash; attach your resume before sending. Recruiter names/phones hidden by Naukri
can often be found via the LinkedIn link.</p>
<table><tr><th>Score</th><th>Company</th><th>Job</th><th>Email</th><th>Phone</th><th>Links</th></tr>
{''.join(body)}</table></body></html>""")
    return csv_path, html_path
