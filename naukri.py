"""Naukri search + apply via a real Chromium browser (Playwright)."""

import os
import random
import re
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from urllib.parse import quote

from playwright.sync_api import BrowserContext, Page, sync_playwright

BASE_URL = "https://www.naukri.com"
PROFILE_DIR = Path(__file__).parent / "data" / "browser_profile"
PAGE_SIZE = 20


def log(*args) -> None:
    # stdout is reserved for the MCP stdio protocol
    print(*args, file=sys.stderr, flush=True)


def load_env(path: Path = Path(__file__).parent / ".env") -> None:
    """Load KEY=VALUE lines from .env; real environment variables take precedence."""
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip().strip("'\""))


load_env()


def credentials() -> tuple[str, str] | None:
    email, password = os.environ.get("NAUKRI_EMAIL"), os.environ.get("NAUKRI_PASSWORD")
    return (email, password) if email and password else None


def strip_html(text: str) -> str:
    text = re.sub(r"<(br|/p|/li)\s*/?>", "\n", text or "", flags=re.I)
    text = re.sub(r"<[^>]+>", " ", text)
    return re.sub(r"[ \t]+", " ", re.sub(r"\n\s*\n+", "\n", text)).strip()


@dataclass
class Job:
    job_id: str
    title: str
    company: str
    url: str
    experience: str = ""
    salary: str = ""
    location: str = ""
    posted: str = ""
    posted_at: str = ""
    tags: list[str] = field(default_factory=list)
    description: str = ""
    min_exp: int | None = None
    max_exp: int | None = None
    external_apply: bool = False
    has_questionnaire: bool = False
    rating: str = ""
    query: str = ""
    score: int = 0
    matched_skills: list[str] = field(default_factory=list)
    gig_signals: list[str] = field(default_factory=list)

    @classmethod
    def from_api(cls, d: dict, query: str) -> "Job":
        ph = {p.get("type"): p.get("label", "") for p in d.get("placeholders", [])}
        created = d.get("createdDate")
        return cls(
            job_id=str(d.get("jobId", "")),
            title=d.get("title", "").strip(),
            company=d.get("companyName", "").strip(),
            url=BASE_URL + d.get("jdURL", "") if d.get("jdURL", "").startswith("/") else d.get("jdURL", ""),
            experience=ph.get("experience", d.get("experienceText", "")),
            salary=ph.get("salary", ""),
            location=ph.get("location", ""),
            posted=d.get("footerPlaceholderLabel", ""),
            posted_at=datetime.fromtimestamp(created / 1000).strftime("%Y-%m-%d") if created else "",
            tags=[t.strip() for t in (d.get("tagsAndSkills") or "").split(",") if t.strip()],
            description=strip_html(d.get("jobDescription") or ""),
            min_exp=_to_int(d.get("minimumExperience")),
            max_exp=_to_int(d.get("maximumExperience")),
            external_apply=bool(d.get("companyApplyJob")),
            has_questionnaire=bool(d.get("questionnaireIdPresent")),
            rating=(d.get("ambitionBoxData") or {}).get("AggregateRating", ""),
            query=query,
        )


def _to_int(v) -> int | None:
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


def search_url(query: str, page_no: int, experience: int | None, remote_only: bool, job_age: int) -> str:
    slug = f"{_slug(query)}-jobs" + (f"-{page_no}" if page_no > 1 else "")
    params = [f"k={quote(query)}", f"jobAge={job_age}"]
    if experience is not None:
        params.append(f"experience={experience}")
    if remote_only:
        params.append("wfhType=2")
    return f"{BASE_URL}/{slug}?{'&'.join(params)}"


class Naukri:
    def __init__(self, headless: bool = False):
        self.headless = headless

    def __enter__(self) -> "Naukri":
        PROFILE_DIR.mkdir(parents=True, exist_ok=True)
        self._pw = sync_playwright().start()
        options = dict(
            headless=self.headless,
            viewport={"width": 1366, "height": 900},
            args=["--disable-blink-features=AutomationControlled"],
        )
        try:
            # Real Chrome: Naukri blocks headless and is stricter with bundled Chromium.
            self.ctx: BrowserContext = self._pw.chromium.launch_persistent_context(
                str(PROFILE_DIR), channel="chrome", **options
            )
        except Exception as e:
            log(f"Google Chrome not available ({str(e).splitlines()[0]}); using bundled Chromium.")
            self.ctx = self._pw.chromium.launch_persistent_context(str(PROFILE_DIR), **options)
        self.page: Page = self.ctx.pages[0] if self.ctx.pages else self.ctx.new_page()
        return self

    def __exit__(self, *exc):
        self.ctx.close()
        self._pw.stop()

    def login(self, timeout_minutes: int = 5) -> bool:
        """Log in with NAUKRI_EMAIL/NAUKRI_PASSWORD if set; otherwise (or on OTP/captcha) wait for manual login."""
        self.page.goto(BASE_URL, wait_until="domcontentloaded")
        if self.is_logged_in():
            log("Already logged in.")
            return True
        self.page.goto(f"{BASE_URL}/nlogin/login", wait_until="domcontentloaded")

        creds = credentials()
        if creds:
            log(f"Logging in as {creds[0]}...")
            self.page.locator("#usernameField").fill(creds[0])
            self.page.locator("#passwordField").fill(creds[1])
            self.page.get_by_role("button", name=re.compile(r"^\s*login\s*$", re.I)).first.click()
            if self._wait_for_login(30):
                return True
            log("Automatic login didn't complete (OTP, captcha or wrong password?).")

        log(f"Finish logging in to Naukri in the browser window (waiting up to {timeout_minutes} min)...")
        return self._wait_for_login(timeout_minutes * 60)

    def ensure_logged_in(self) -> bool:
        self.page.goto(BASE_URL, wait_until="domcontentloaded")
        if self.is_logged_in():
            return True
        return self.login(timeout_minutes=2) if credentials() else False

    def _wait_for_login(self, seconds: float) -> bool:
        deadline = time.time() + seconds
        while time.time() < deadline:
            if self.is_logged_in():
                self.page.wait_for_timeout(3000)
                log("Logged in. Session saved to", PROFILE_DIR)
                return True
            self.page.wait_for_timeout(1500)
        return False

    def search(self, query: str, *, experience: int | None, remote_only: bool, job_age: int,
               max_pages: int, delay: tuple[float, float]) -> list[Job]:
        jobs: list[Job] = []
        for page_no in range(1, max_pages + 1):
            payload = self._fetch_search_page(search_url(query, page_no, experience, remote_only, job_age))
            if payload is None:
                log(f"  [{query}] page {page_no}: no API response (blocked or layout changed)")
                break
            batch = payload.get("jobDetails") or []
            jobs.extend(Job.from_api(d, query) for d in batch)
            total = payload.get("noOfJobs", 0)
            log(f"  [{query}] page {page_no}: {len(batch)} jobs (total available: {total})")
            if len(batch) < PAGE_SIZE or page_no * PAGE_SIZE >= total:
                break
            time.sleep(random.uniform(*delay))
        return jobs

    def _fetch_search_page(self, url: str) -> dict | None:
        try:
            with self.page.expect_response(
                lambda r: "/jobapi/v3/search" in r.url and r.status == 200, timeout=30000
            ) as resp_info:
                self.page.goto(url, wait_until="domcontentloaded")
            return resp_info.value.json()
        except Exception as e:
            log("  search error:", e)
            return None

    def is_logged_in(self) -> bool:
        return any(c["name"] == "nauk_at" for c in self.ctx.cookies(BASE_URL))

    def job_details(self, url: str) -> dict:
        with self.page.expect_response(
            lambda r: "/jobapi/v4/job/" in r.url and r.status == 200, timeout=30000
        ) as resp_info:
            self.page.goto(url, wait_until="domcontentloaded")
        jd = resp_info.value.json().get("jobDetails", {})
        skills = jd.get("keySkills") or {}
        company = jd.get("companyDetail") or {}
        return {
            "job_id": str(jd.get("jobId", "")),
            "title": jd.get("title", ""),
            "company": company.get("name", ""),
            "url": jd.get("staticUrl") or url,
            "work_mode": jd.get("wfhLabel", ""),
            "locations": [loc.get("label", "") for loc in jd.get("locations", [])],
            "experience": jd.get("experienceText", ""),
            "salary": (jd.get("salaryDetail") or {}).get("label", ""),
            "role": jd.get("jobRole", ""),
            "role_category": jd.get("roleCategory", ""),
            "industry": jd.get("industry", ""),
            "employment_type": jd.get("employmentType", ""),
            "education": (jd.get("education") or {}).get("ug", []) + (jd.get("education") or {}).get("pg", []),
            "preferred_skills": [s.get("label") for s in skills.get("preferred", [])],
            "other_skills": [s.get("label") for s in skills.get("other", [])],
            "applicants": jd.get("applyCount"),
            "views": jd.get("viewCount"),
            "posted": jd.get("createdDate", ""),
            "summary": jd.get("shortDescription", ""),
            "description": strip_html(jd.get("description", "")),
            "about_company": strip_html(company.get("details", ""))[:1500],
        }

    def apply(self, job: Job) -> str:
        """Returns one of: applied, already_applied, external, needs_manual, failed."""
        if job.external_apply:
            return "external"
        page = self.page
        page.goto(job.url, wait_until="domcontentloaded")
        page.wait_for_timeout(3000)

        if page.get_by_role("button", name=re.compile(r"^\s*applied\s*$", re.I)).count():
            return "already_applied"
        if page.locator("#company-site-button").count() or page.get_by_role(
            "button", name=re.compile("company site", re.I)
        ).count():
            return "external"

        btn = page.locator("#apply-button")
        if not btn.count():
            btn = page.get_by_role("button", name=re.compile(r"^\s*apply\s*$", re.I))
        if not btn.count():
            return "failed"

        btn.first.click()
        page.wait_for_timeout(4000)

        if page.locator("[class*='chatbot'], [class*='Chatbot']").count():
            return "needs_manual"
        body = page.locator("body").inner_text().lower()
        if "successfully applied" in body or "applied to" in body or page.get_by_role(
            "button", name=re.compile(r"^\s*applied\s*$", re.I)
        ).count():
            return "applied"
        return "needs_manual"
