#!/usr/bin/env python3
"""MCP server exposing Naukri job-hunting tools (search, details, resume, apply)."""

import threading
from datetime import date

import anyio
from mcp.server.mcpserver import MCPServer

from hunt import (APPLIED_FILE, DEFAULT_PROFILE, ROOT, latest_file, list_profiles, load_config, load_json,
                  record_applied, run_search)
from matcher import find_resume, read_resume
from naukri import Job, Naukri, credentials

mcp = MCPServer(
    name="naukri-job-hunter",
    instructions=(
        "Tools for hunting jobs on naukri.com for the user. Typical flow: get_candidate_profile -> "
        "search_jobs -> get_job_details on promising jobs -> present a shortlist -> apply_to_job only "
        "after the user explicitly approves specific jobs. Hunt profiles: 'default' (regular full-time "
        "roles, config.yaml) and 'side' (remote part-time/freelance/contract gigs, config.side.yaml). "
        "Every browser tool opens a visible Chrome window; Naukri blocks headless browsers."
    ),
)

# Only one Chrome instance can use the persistent profile at a time.
_browser_lock = threading.Lock()


async def _in_browser(fn):
    def run():
        with _browser_lock:
            cfg = load_config()
            with Naukri(headless=cfg["search"].get("headless", False)) as n:
                return fn(n)
    return await anyio.to_thread.run_sync(run)


def _apply_type(j: Job) -> str:
    if j.external_apply:
        return "company_site"
    return "naukri_with_questions" if j.has_questionnaire else "naukri"


def _summary(j: Job) -> dict:
    return {
        "job_id": j.job_id, "score": j.score, "title": j.title, "company": j.company,
        "rating": j.rating, "experience": j.experience, "salary": j.salary, "location": j.location,
        "posted": j.posted, "apply_type": _apply_type(j), "matched_skills": j.matched_skills,
        "gig_signals": j.gig_signals, "url": j.url,
    }


def _latest(profile: str) -> list[Job]:
    return [Job(**d) for d in load_json(latest_file(load_config(profile)), [])]


def _find_job(job_id: str) -> Job | None:
    """Look the job up in the latest results of every profile."""
    for profile in list_profiles():
        job = next((j for j in _latest(profile) if j.job_id == job_id), None)
        if job:
            return job
    return None


@mcp.tool()
async def get_candidate_profile(profile: str = DEFAULT_PROFILE) -> dict:
    """Return the candidate's resume text and the search preferences of a hunt profile.

    profile: "default" (regular roles) or "side" (part-time/freelance/contract gigs).
    """
    cfg = load_config(profile)
    path = find_resume(ROOT, cfg.get("resume_path"))
    return {
        "profile": profile,
        "profile_name": cfg.get("profile_name", profile),
        "available_profiles": list_profiles(),
        "gig_keywords": cfg.get("gig_keywords"),
        "resume_file": path.name,
        "resume_text": read_resume(path),
        "experience_years": cfg.get("experience_years"),
        "queries": cfg.get("queries"),
        "target_titles": cfg.get("target_titles"),
        "skills": cfg.get("skills"),
        "exclude_title_keywords": cfg.get("exclude_title_keywords"),
        "exclude_companies": cfg.get("exclude_companies"),
        "search": cfg.get("search"),
        "min_score": cfg.get("min_score"),
    }


@mcp.tool()
async def search_jobs(
    profile: str = DEFAULT_PROFILE,
    queries: list[str] | None = None,
    remote_only: bool = True,
    job_age_days: int | None = None,
    max_pages: int | None = None,
    min_score: int | None = None,
    include_seen: bool = False,
    limit: int = 25,
) -> dict:
    """Search Naukri, score every job against the resume, and return the best matches.

    profile: "default" for regular roles, "side" for remote part-time/freelance/contract gigs.
    queries: search keywords (defaults to the profile's queries).
    job_age_days: only jobs posted in the last N days (1, 3, 7, 15, 30).
    max_pages: result pages per query, 20 jobs each.
    include_seen: also return jobs already returned by earlier searches.
    Writes an HTML/CSV report and replaces the profile's "latest results" used by list_matches.
    """
    cfg = load_config(profile)

    def run():
        with _browser_lock:
            return run_search(cfg, include_seen=include_seen, queries=queries, remote_only=remote_only,
                              job_age_days=job_age_days, max_pages=max_pages, min_score=min_score)

    r = await anyio.to_thread.run_sync(run)
    return {
        "profile": profile,
        "scanned_unique_jobs": r["scanned"],
        "matches": len(r["results"]),
        "min_score": r["min_score"],
        "report_html": str(r["html"]),
        "jobs": [_summary(j) for j in r["results"][:limit]],
    }


@mcp.tool()
async def list_matches(profile: str = DEFAULT_PROFILE, limit: int = 25, min_score: int = 0,
                       apply_type: str | None = None) -> dict:
    """List jobs from the profile's most recent search without hitting Naukri again.

    apply_type: optional filter - "naukri", "naukri_with_questions" or "company_site".
    """
    jobs = [j for j in _latest(profile) if j.score >= min_score and (apply_type is None or _apply_type(j) == apply_type)]
    return {"total": len(jobs), "jobs": [_summary(j) for j in jobs[:limit]]}


@mcp.tool()
async def get_job_details(job_id: str | None = None, url: str | None = None) -> dict:
    """Fetch the full job description, key skills, role, industry, applicant count and company info.

    Pass a job_id from search results, or a full naukri.com job URL.
    """
    if not url:
        job = _find_job(job_id or "")
        if not job:
            return {"error": f"job_id {job_id} not in latest results; pass the job url instead"}
        url = job.url
    return await _in_browser(lambda n: n.job_details(url))


@mcp.tool()
async def login_status() -> dict:
    """Check whether the automation browser profile is logged in to Naukri."""
    def check(n: Naukri):
        n.page.goto("https://www.naukri.com", wait_until="domcontentloaded")
        return {"logged_in": n.is_logged_in(), "credentials_configured": credentials() is not None}
    return await _in_browser(check)


@mcp.tool()
async def login(timeout_minutes: int = 5) -> dict:
    """Log in to Naukri using NAUKRI_EMAIL/NAUKRI_PASSWORD from .env, falling back to manual login
    in the opened Chrome window (e.g. for OTP or captcha)."""
    return {"logged_in": await _in_browser(lambda n: n.login(timeout_minutes))}


@mcp.tool()
async def apply_to_job(job_id: str, confirm: bool = False) -> dict:
    """Apply to one job from the latest search results via Naukri's Apply button.

    Only call with confirm=True after the user explicitly approved this specific job.
    With confirm=False it only reports whether the job can be auto-applied.
    Company-site jobs and jobs with recruiter questionnaires return needs_manual with the URL.
    """
    cfg = load_config()
    job = _find_job(job_id)
    if not job:
        return {"error": f"job_id {job_id} not in latest results"}
    applied = load_json(APPLIED_FILE, {})
    if job_id in applied:
        return {"status": "already_applied", **applied[job_id]}

    ac = cfg.get("apply", {})
    base = {"job_id": job_id, "title": job.title, "company": job.company, "url": job.url}
    if job.external_apply:
        return {**base, "status": "needs_manual", "reason": "applies on company website"}
    if job.has_questionnaire and ac.get("skip_questionnaires", True):
        return {**base, "status": "needs_manual", "reason": "recruiter questionnaire required"}

    today = date.today().isoformat()
    applied_today = sum(1 for a in applied.values() if a.get("at", "").startswith(today))
    if applied_today >= ac.get("max_per_day", 15):
        return {**base, "status": "blocked", "reason": f"daily limit reached ({applied_today})"}
    if not confirm:
        return {**base, "status": "ready", "reason": "call again with confirm=True to apply"}

    def do_apply(n: Naukri):
        if not n.ensure_logged_in():
            return "not_logged_in"
        return n.apply(job)

    status = await _in_browser(do_apply)
    if status in ("applied", "already_applied"):
        record_applied(job, status)
    return {**base, "status": status}


@mcp.tool()
async def get_contacts(profile: str = DEFAULT_PROFILE, limit: int = 15) -> dict:
    """Contact details for sending a resume manually, for the top jobs of a profile's latest search.

    Returns per job: recruiter emails/phones published in the job post, company website and address,
    a prefilled mailto draft, and LinkedIn-recruiter / careers-page search links. Also writes an HTML
    contact list. Never guess email addresses that aren't in the data.
    """
    from contacts import build_rows, fetch_details, resume_name, write_contacts
    from hunt import DATA, OUTPUT, save_json

    cfg = load_config(profile)
    jobs = _latest(profile)[:limit]
    if not jobs:
        return {"error": f"no results for profile {profile}; call search_jobs first"}
    cache_file = DATA / "job_details.json"

    def run():
        with _browser_lock:
            return fetch_details(jobs, load_json(cache_file, {}), headless=cfg["search"].get("headless", False))

    details = await anyio.to_thread.run_sync(run)
    save_json(cache_file, details)
    name = (cfg.get("outreach") or {}).get("name") or resume_name(find_resume(ROOT, cfg.get("resume_path")))
    rows = build_rows(jobs, details, cfg, name)
    _, html_path = write_contacts(rows, OUTPUT, "contacts" if profile == DEFAULT_PROFILE else f"{profile}_contacts")
    return {"contact_list_html": str(html_path), "with_email": sum(1 for r in rows if r["emails"]), "jobs": rows}


@mcp.tool()
async def list_applied() -> dict:
    """List jobs already applied to through this tool."""
    applied = load_json(APPLIED_FILE, {})
    return {"total": len(applied), "jobs": [{"job_id": k, **v} for k, v in applied.items()]}


if __name__ == "__main__":
    mcp.run("stdio")
