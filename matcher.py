"""Resume parsing and job-to-resume scoring."""

import re
from pathlib import Path

from pypdf import PdfReader

from naukri import Job


GENERIC_TAGS = {
    "ai", "architecture", "cloud", "coding", "consulting", "development", "design", "management",
    "senior", "software", "engineering", "technology", "it", "leadership", "communication",
    "delivery", "project management", "team", "lead", "manager", "developer", "programming",
}


def find_resume(folder: Path, configured: str | None) -> Path:
    if configured:
        path = folder / configured
        if path.exists():
            return path
        raise FileNotFoundError(f"Resume not found: {path}")
    pdfs = sorted(folder.glob("*.pdf"))
    if not pdfs:
        raise FileNotFoundError(f"No PDF resume found in {folder}")
    return pdfs[0]


def read_resume(path: Path) -> str:
    return "\n".join(page.extract_text() or "" for page in PdfReader(path).pages).lower()


def _contains(text: str, term: str) -> bool:
    return re.search(rf"(?<![a-z0-9]){re.escape(term.lower())}(?![a-z0-9])", text) is not None


DEFAULT_WEIGHTS = {"skills": 50, "title": 25, "experience": 15, "remote": 10, "gig": 0}


def _job_text(job: Job) -> str:
    return " ".join([job.title, " ".join(job.tags), job.description]).lower()


class Matcher:
    def __init__(self, resume_text: str, cfg: dict):
        self.resume = resume_text
        self.skills = [s.lower() for s in cfg.get("skills", [])]
        self.target_titles = [t.lower() for t in cfg.get("target_titles", [])]
        self.strong_titles = [t.lower() for t in cfg.get("strong_titles", ["manager", "architect", "head"])]
        self.exclude_titles = [t.lower() for t in cfg.get("exclude_title_keywords", [])]
        self.exclude_companies = {c.lower() for c in cfg.get("exclude_companies", [])}
        self.gig_keywords = [k.lower() for k in cfg.get("gig_keywords", [])]
        self.gig_title_keywords = [k.lower() for k in cfg.get("gig_title_keywords", [])]
        self.gig_ignore = [p.lower() for p in cfg.get("gig_ignore_phrases", [])]
        self.require_gig = cfg.get("require_gig_keywords", False)
        self.min_skill_matches = cfg.get("min_skill_matches", 0)
        self.min_max_exp = cfg.get("skip_if_max_experience_below")
        self.weights = {**DEFAULT_WEIGHTS, **cfg.get("weights", {})}
        self.years = cfg.get("experience_years", 0)

    def is_excluded(self, job: Job) -> bool:
        title = job.title.lower()
        if job.company.lower() in self.exclude_companies or any(_contains(title, kw) for kw in self.exclude_titles):
            return True
        if self.min_max_exp is not None and job.max_exp is not None and job.max_exp < self.min_max_exp:
            return True
        job.gig_signals = self.gig_signals(job)
        return self.require_gig and not job.gig_signals

    def gig_signals(self, job: Job) -> list[str]:
        text = re.sub(r"[:|/()\[\]\-–]+", " ", _job_text(job))
        text = re.sub(r"\s+", " ", text)
        for phrase in self.gig_ignore:
            text = text.replace(phrase, " ")
        title = job.title.lower()
        found = [k for k in self.gig_keywords if _contains(text, k)]
        found += [k for k in self.gig_title_keywords if _contains(title, k) and k not in found]
        return found

    def score(self, job: Job) -> int:
        """0-100 weighted by config `weights` (defaults: skills 50, title 25, experience 15, remote 10)."""
        w = self.weights
        title = job.title.lower()
        job_text = _job_text(job)

        tags = [t for t in job.tags if t.lower() not in GENERIC_TAGS]
        matched = [s for s in self.skills if _contains(job_text, s)]
        tag_hits = [t for t in tags if _contains(self.resume, t)]
        job.matched_skills = sorted(set(matched) | {t.lower() for t in tag_hits})
        if len(matched) < self.min_skill_matches:
            job.score = 0
            return 0

        skill_pts = w["skills"] * (0.6 * min(len(matched), 8) / 8 + (0.4 * len(tag_hits) / len(tags) if tags else 0))

        title_pts = 0
        if any(_contains(title, t) for t in self.strong_titles):
            title_pts = w["title"]
        elif any(_contains(title, t) for t in self.target_titles):
            title_pts = w["title"] * 0.6

        exp_pts = 0
        if job.min_exp is not None and job.max_exp is not None:
            if job.min_exp <= self.years <= job.max_exp:
                exp_pts = w["experience"]
            elif job.min_exp - 2 <= self.years <= job.max_exp + 3:
                exp_pts = w["experience"] * 0.5

        remote_pts = w["remote"] if "remote" in (job.location + " " + title).lower() else 0

        if not job.gig_signals and (self.gig_keywords or self.gig_title_keywords):
            job.gig_signals = self.gig_signals(job)
        gig_pts = w["gig"] if job.gig_signals else 0

        job.score = min(100, round(skill_pts + title_pts + exp_pts + remote_pts + gig_pts))
        return job.score
