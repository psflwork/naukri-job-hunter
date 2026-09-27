"""Resume parsing and job-to-resume scoring."""

import re
from pathlib import Path

from pypdf import PdfReader

from naukri import Job
from options import (DEFAULT_IGNORE_PHRASES, DEFAULT_JOB_TYPE_KEYWORDS, DEFAULT_TITLE_KEYWORDS, GIG_TYPES,
                     normalize_job_types, normalize_work_modes)


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


def wanted_job_types(cfg: dict) -> list[str]:
    """Job types to keep ([] = any). Older configs used require_gig_keywords for the gig hunt."""
    if "job_types" in cfg:
        return normalize_job_types(cfg.get("job_types"))
    return list(GIG_TYPES) if cfg.get("require_gig_keywords") else []


def wanted_work_modes(cfg: dict) -> list[str]:
    """Work modes to search ([] = any). Older configs used search.remote_only."""
    s = cfg.get("search", {})
    if "work_modes" in s:
        return normalize_work_modes(s.get("work_modes"))
    return ["remote"] if s.get("remote_only", True) else []


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
        self.type_keywords = {**DEFAULT_JOB_TYPE_KEYWORDS, **cfg.get("job_type_keywords", {})}
        self.title_keywords = cfg.get("job_type_title_keywords", DEFAULT_TITLE_KEYWORDS)
        self.ignore = [p.lower() for p in cfg.get("gig_ignore_phrases", DEFAULT_IGNORE_PHRASES)]
        self.job_types = wanted_job_types(cfg)
        s = cfg.get("search", {})
        self.work_modes = wanted_work_modes(cfg)
        self.locations = [loc.lower() for loc in s.get("locations") or []]
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
        job.job_types, job.gig_signals = self.detect_job_types(job)
        return bool(self.job_types) and not set(job.job_types) & set(self.job_types)

    def detect_job_types(self, job: Job) -> tuple[list[str], list[str]]:
        """Returns (job types, matched phrases). No gig phrase found means full-time."""
        text = re.sub(r"[:|/()\[\]\-–]+", " ", _job_text(job))
        text = re.sub(r"\s+", " ", text)
        for phrase in self.ignore:
            text = text.replace(phrase, " ")
        title = job.title.lower()
        types: list[str] = []
        signals: list[str] = []
        for job_type, phrases in self.type_keywords.items():
            hits = [p for p in phrases if _contains(text, p.lower())]
            hits += [w for w, t in self.title_keywords.items() if t == job_type and _contains(title, w)]
            if hits:
                types.append(job_type)
                signals += [h for h in hits if h not in signals]
        return (types or ["full_time"]), signals

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

        where = (job.location + " " + title).lower()
        is_remote = "remote" in where
        in_location = any(_contains(where, loc) for loc in self.locations)
        remote_ok = not self.work_modes or "remote" in self.work_modes
        remote_pts = w["remote"] if (is_remote and remote_ok) or in_location else 0

        if not job.job_types:
            job.job_types, job.gig_signals = self.detect_job_types(job)
        wanted_gigs = [t for t in self.job_types if t in GIG_TYPES] or GIG_TYPES
        gig_pts = w["gig"] if set(job.job_types) & set(wanted_gigs) else 0

        job.score = min(100, round(skill_pts + title_pts + exp_pts + remote_pts + gig_pts))
        return job.score
