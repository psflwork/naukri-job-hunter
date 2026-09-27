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


EMAIL_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._%+-]*@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}")
PHONE_RE = re.compile(r"(?<!\d)(?:\+91[\s-]?|0)?[6-9]\d{4}[\s-]?\d{5}(?!\d)|\+\d{1,3}[\s-]?\(?\d{2,4}\)?[\s-]?\d{3,4}[\s-]?\d{3,4}")


def extract_emails(text: str) -> list[str]:
    found = {e.strip(".").lower() for e in EMAIL_RE.findall(text or "")}
    return sorted(e for e in found if not e.endswith((".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp")))


def extract_phones(text: str) -> list[str]:
    return sorted({re.sub(r"[\s-]+", " ", p).strip() for p in PHONE_RE.findall(text or "")})


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
    job_types: list[str] = field(default_factory=list)

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


WORK_MODE_IDS = {"office": 0, "remote": 2, "hybrid": 3}


def search_url(query: str, page_no: int, experience: int | None, work_modes: list[str], job_age: int,
               location: str | None = None) -> str:
    slug = f"{_slug(query)}-jobs"
    if location:
        slug += f"-in-{_slug(location)}"
    if page_no > 1:
        slug += f"-{page_no}"
    params = [f"k={quote(query)}", f"jobAge={job_age}"]
    if location:
        params.append(f"l={quote(location)}")
    if experience is not None:
        params.append(f"experience={experience}")
    params += [f"wfhType={WORK_MODE_IDS[m]}" for m in work_modes if m in WORK_MODE_IDS]
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

    def search(self, query: str, *, experience: int | None, work_modes: list[str], job_age: int,
               max_pages: int, delay: tuple[float, float], location: str | None = None) -> list[Job]:
        jobs: list[Job] = []
        label = f"{query} @ {location}" if location else query
        for page_no in range(1, max_pages + 1):
            url = search_url(query, page_no, experience, work_modes, job_age, location)
            payload = self._fetch_search_page(url)
            if payload is None:
                log(f"  [{label}] page {page_no}: no API response (blocked or layout changed)")
                break
            batch = payload.get("jobDetails") or []
            jobs.extend(Job.from_api(d, query) for d in batch)
            total = payload.get("noOfJobs", 0)
            log(f"  [{label}] page {page_no}: {len(batch)} jobs (total available: {total})")
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
        contact_text = " ".join([jd.get("description", ""), jd.get("shortDescription", ""), company.get("details", "")])
        contact_text = re.sub(r"<[^>]+>", " ", contact_text.replace("mailto:", " "))
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
            "emails": extract_emails(contact_text),
            "phones": extract_phones(contact_text),
            "website": (company.get("websiteUrl") or "").strip(),
            "address": re.sub(r"\s+", " ", company.get("address") or "").strip(" ,"),
            "hiring_for": (company.get("hiringFor") or "").strip(),
            "recruitment_agency": bool(jd.get("consultant")),
        }

    # Naukri's recruiter questions open in a chat-style drawer after clicking Apply.
    CHATBOT = ("[class*='chatbot_Drawer'], [class*='chatbot_drawer'], [class*='chatbotDrawer'], "
               "[class*='chatbot_Wrapper'], [class*='chatbot']")
    BOT_MESSAGE = "[class*='botMsg'], [class*='bot-msg'], [class*='botItem'], li[class*='bot']"
    TEXT_INPUT = ("[contenteditable='true'], textarea, input[type='text'], input[type='number'], "
                  "input[type='tel'], input:not([type])")
    SEND_BUTTON = ("[class*='sendMsg'], [class*='send-msg'], [class*='sendBtn'], button:has-text('Save'), "
                   "button:has-text('Submit'), button:has-text('Next'), div:text-is('Save')")

    def _applied_confirmation(self) -> bool:
        page = self.page
        body = page.locator("body").inner_text().lower()
        return ("successfully applied" in body or "you have successfully" in body or
                page.get_by_role("button", name=re.compile(r"^\s*applied\s*$", re.I)).count() > 0)

    def _drawer(self):
        """Outermost chatbot element that holds the questions (chips and inputs also have 'chatbot' classes)."""
        with_messages = self.page.locator(self.CHATBOT).filter(has=self.page.locator(self.BOT_MESSAGE))
        return with_messages.first if with_messages.count() else self.page.locator(self.CHATBOT).first

    def _last_question(self, drawer) -> str:
        messages = drawer.locator(self.BOT_MESSAGE)
        if messages.count():
            return messages.last.inner_text().strip()
        lines = [ln.strip() for ln in drawer.inner_text().splitlines() if ln.strip()]
        questions = [ln for ln in lines if ln.endswith("?")]
        return questions[-1] if questions else (lines[-1] if lines else "")

    def _options(self, drawer) -> list[tuple[str, object]]:
        """Clickable answers in the drawer: radio buttons, checkboxes or chips, as (label, locator)."""
        options = []
        for kind in ("radio", "checkbox"):
            inputs = drawer.locator(f"input[type='{kind}']")
            for i in range(inputs.count()):
                item = inputs.nth(i)
                input_id = item.get_attribute("id")
                label = drawer.locator(f"label[for='{input_id}']") if input_id else None
                text = label.first.inner_text().strip() if label is not None and label.count() else \
                    (item.get_attribute("value") or "").strip()
                if text:
                    options.append((text, label.first if label is not None and label.count() else item))
            if options:
                return options
        chips = drawer.locator("[class*='chip'], [class*='Chip']")
        for i in range(chips.count()):
            text = chips.nth(i).inner_text().strip()
            if text and len(text) < 80:
                options.append((text, chips.nth(i)))
        return options

    def _save_debug(self, job: Job, reason: str) -> None:
        folder = PROFILE_DIR.parent / "questionnaire_debug"
        folder.mkdir(parents=True, exist_ok=True)
        try:
            (folder / f"{job.job_id}.html").write_text(self.page.content())
            self.page.screenshot(path=str(folder / f"{job.job_id}.png"))
            log(f"    ({reason}; saved {folder.name}/{job.job_id}.png for troubleshooting)")
        except Exception:
            pass

    def _answer_questionnaire(self, job: Job, answerer) -> str:
        page = self.page
        last_question, repeats = "", 0
        for _ in range(20):
            page.wait_for_timeout(1500)
            if self._applied_confirmation():
                return "applied"
            drawer = self._drawer()
            if not drawer.count() or not drawer.is_visible():
                page.wait_for_timeout(2000)
                return "applied" if self._applied_confirmation() else "needs_manual"
            question = self._last_question(drawer)
            if question == last_question:
                repeats += 1
                if repeats >= 2:
                    self._save_debug(job, "stuck on a question")
                    return "needs_manual"
                continue
            repeats = 0
            options = self._options(drawer)
            labels = [text for text, _ in options]
            answer = answerer.answer(question, labels)
            log(f"    Q: {question}" + (f"  [{' / '.join(labels)}]" if labels else ""))
            if answer is None:
                log("    A: (no answer from your resume/answers; added to 'Recruiter questions' in the app)")
                answerer.record_pending(question, labels, job.title, job.company)
                return "needs_manual"
            log(f"    A: {answer}")
            if options:
                next(loc for text, loc in options if text == answer).click()
            else:
                box = drawer.locator(self.TEXT_INPUT).last
                if not box.count():
                    self._save_debug(job, "no answer box found")
                    return "needs_manual"
                box.click()
                if box.get_attribute("contenteditable") == "true":
                    page.keyboard.type(answer)
                else:
                    box.fill(answer)
            page.wait_for_timeout(500)
            send = drawer.locator(self.SEND_BUTTON)
            if send.count():
                send.last.click()
            elif not options:
                page.keyboard.press("Enter")
            last_question = question
        return "needs_manual"

    def apply(self, job: Job, answerer=None) -> str:
        """Returns one of: applied, already_applied, external, needs_manual, failed.

        With an answerer, recruiter questions are answered from your resume and saved answers.
        """
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

        if page.locator(self.CHATBOT).count() or page.locator("[class*='Chatbot']").count():
            if answerer is None:
                return "needs_manual"
            return self._answer_questionnaire(job, answerer)
        body = page.locator("body").inner_text().lower()
        if "successfully applied" in body or "applied to" in body or page.get_by_role(
            "button", name=re.compile(r"^\s*applied\s*$", re.I)
        ).count():
            return "applied"
        return "needs_manual"
