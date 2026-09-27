"""Answers for recruiter questionnaires on Naukri's Apply flow.

Answers come only from real data: your resume, your years of experience, the answers profile you fill
in once (notice period, CTC, location...), and answers you gave earlier. Questions it can't answer are
saved as "pending" so you can answer them once in the web app; the job is left for you to finish.

Stored in data/answers.json:
  profile  - notice_period, current_ctc, expected_ctc, current_location, preferred_location,
             willing_to_relocate, linkedin, phone, skill_years {skill: years}
  saved    - {normalized question: answer} you gave earlier
  pending  - questions that couldn't be answered yet
"""

import json
import re
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).parent
ANSWERS_FILE = ROOT / "data" / "answers.json"

PROFILE_FIELDS = {
    "notice_period": "Notice period (e.g. Immediate, 15 days, 30 days, 60 days)",
    "current_ctc": "Current CTC in lakhs per year (e.g. 25)",
    "expected_ctc": "Expected CTC in lakhs per year (e.g. 32)",
    "current_location": "Current city",
    "preferred_location": "Preferred cities (e.g. Remote, Bengaluru)",
    "willing_to_relocate": "Willing to relocate? (Yes / No)",
    "linkedin": "LinkedIn profile URL",
    "phone": "Phone number",
}

YES_WORDS = ("comfortable", "willing", "okay", "ok with", "ready to", "able to", "open to", "available to",
             "agree", "fine with", "can you work", "can you join", "work from office", "work in shifts",
             "night shift", "rotational", "hybrid", "remote", "travel")
EXPERIENCE_WORDS = ("do you have", "have you worked", "have you used", "hands-on", "hands on", "knowledge of",
                    "familiar with", "experience in", "experience with", "experience on", "worked on", "proficient")


def normalize(text: str) -> str:
    text = re.sub(r"\s+", " ", (text or "").lower()).strip()
    return re.sub(r"[^\w\s+#./-]", "", text).strip()


def load() -> dict:
    data = json.loads(ANSWERS_FILE.read_text()) if ANSWERS_FILE.exists() else {}
    data.setdefault("profile", {})
    data.setdefault("saved", {})
    data.setdefault("pending", [])
    data["profile"].setdefault("skill_years", {})
    return data


def save(data: dict) -> None:
    ANSWERS_FILE.parent.mkdir(parents=True, exist_ok=True)
    ANSWERS_FILE.write_text(json.dumps(data, indent=2))


def update(profile: dict | None = None, saved: dict | None = None, remove: list[str] | None = None) -> dict:
    """Merge answers from the web app; answering a pending question removes it from pending."""
    data = load()
    if profile:
        for key, value in profile.items():
            if key == "skill_years" and isinstance(value, dict):
                data["profile"]["skill_years"] = {k.strip().lower(): str(v).strip() for k, v in value.items()
                                                  if k.strip() and str(v).strip()}
            elif key in PROFILE_FIELDS:
                data["profile"][key] = str(value).strip()
    for question, answer in (saved or {}).items():
        if str(answer).strip():
            data["saved"][normalize(question)] = str(answer).strip()
    for question in remove or []:
        data["saved"].pop(normalize(question), None)
    answered = set(data["saved"])
    data["pending"] = [p for p in data["pending"] if normalize(p["question"]) not in answered]
    save(data)
    return data


def _number(text: str) -> float | None:
    m = re.search(r"\d+(?:\.\d+)?", text or "")
    return float(m.group()) if m else None


def choose_option(answer: str, options: list[str]) -> str | None:
    """Pick the option that matches an answer: exact text, yes/no, or the numeric range containing it."""
    if not options:
        return answer
    a = normalize(answer)
    for opt in options:
        if normalize(opt) == a:
            return opt
    for opt in options:
        o = normalize(opt)
        if a in ("yes", "no") and (o == a or o.startswith(a + " ")):
            return opt
    for opt in options:
        o = normalize(opt)
        if a and a not in ("yes", "no") and (a in o or o in a):
            return opt
    value = _number(answer)
    if value is None:
        return None
    in_days = "day" in a
    for opt in options:
        o = normalize(opt)
        nums = [float(n) for n in re.findall(r"\d+(?:\.\d+)?", o)]
        if in_days and "month" in o:
            nums = [n * 30 for n in nums]
        if len(nums) >= 2 and nums[0] <= value <= nums[1]:
            return opt
        if len(nums) == 1:
            n = nums[0]
            if ("+" in o or "more" in o or "above" in o or "greater" in o) and value >= n:
                return opt
            if ("less" in o or "below" in o or "under" in o or "within" in o or "upto" in o or "up to" in o) \
                    and value <= n:
                return opt
            if value == n:
                return opt
    return None


class Answerer:
    def __init__(self, experience_years: int | float | None, skills: list[str], resume_text: str):
        data = load()
        self.profile = data["profile"]
        self.saved = data["saved"]
        self.years = experience_years
        self.skills = sorted({s.lower() for s in skills}, key=len, reverse=True)
        self.resume = (resume_text or "").lower()

    # ------------------------------------------------------------ public
    def answer(self, question: str, options: list[str] | None = None) -> str | None:
        """Answer text (or the option to pick), or None if it can't be answered from real data."""
        options = options or []
        q = normalize(question)
        raw = self.saved.get(q) or self._rule(q)
        if raw is None:
            return None
        return choose_option(raw, options) if options else raw

    def record_pending(self, question: str, options: list[str], job_title: str, company: str) -> None:
        data = load()
        if normalize(question) in data["saved"] or any(
                normalize(p["question"]) == normalize(question) for p in data["pending"]):
            return
        data["pending"].append({"question": question.strip(), "options": options, "job": job_title,
                                "company": company, "at": datetime.now().isoformat(timespec="seconds")})
        save(data)

    # ------------------------------------------------------------ rules
    def _known_skill(self, text: str) -> str | None:
        for skill in self.skills:
            if re.search(rf"(?<![a-z0-9]){re.escape(skill)}(?![a-z0-9])", text):
                return skill
        return None

    def _skill_years(self, text: str) -> str | None:
        overrides = self.profile.get("skill_years", {})
        for skill, years in sorted(overrides.items(), key=lambda kv: -len(kv[0])):
            if re.search(rf"(?<![a-z0-9]){re.escape(skill)}(?![a-z0-9])", text):
                return years
        skill = self._known_skill(text)
        if skill and self.years is not None:
            return str(int(self.years))
        return None

    def _rule(self, q: str) -> str | None:
        p = self.profile
        years = str(int(self.years)) if self.years is not None else None

        if "notice" in q:
            notice = p.get("notice_period")
            if not notice:
                return None
            if "serving" in q or "currently on" in q:
                return "Yes" if normalize(notice) not in ("immediate", "0", "0 days") else "No"
            if "days" in q and _number(notice) is None and normalize(notice) == "immediate":
                return "0"
            return notice
        if "immediate" in q and "join" in q:
            notice = normalize(p.get("notice_period", ""))
            if not notice:
                return None
            return "Yes" if notice in ("immediate", "0", "0 days") or (_number(notice) or 99) <= 15 else "No"
        if re.search(r"\b(current|present)\b.*\b(ctc|salary|package|compensation)\b", q):
            return p.get("current_ctc") or None
        if re.search(r"\b(expected|expectation)\b.*\b(ctc|salary|package|compensation)\b|\bectc\b", q):
            return p.get("expected_ctc") or None
        if "relocat" in q:
            return p.get("willing_to_relocate") or None
        if "current location" in q or "currently located" in q or "where are you based" in q:
            return p.get("current_location") or None
        if "preferred location" in q or "location preference" in q:
            return p.get("preferred_location") or None
        if "linkedin" in q:
            return p.get("linkedin") or None
        if re.search(r"\b(phone|mobile|contact number)\b", q):
            return p.get("phone") or None

        is_experience = re.search(r"\b(experience|exp|worked)\b", q)
        is_total = re.search(r"\b(total|overall)\b", q)
        if is_experience and (is_total or re.search(r"\b(how many|years|yrs|year)\b", q)):
            by_skill = self._skill_years(q)
            if by_skill is not None:
                return by_skill
            if is_total:
                return years
            # "How many years of experience do you have?" (no specific skill or role named)
            return None if re.search(r"\b(in|with|on|using|as)\s+[a-z]", q) else years

        if any(w in q for w in EXPERIENCE_WORDS):
            skill = self._known_skill(q)
            if skill:
                return "Yes"
            return None
        if any(w in q for w in YES_WORDS) and not re.search(r"\b(not|never)\b", q):
            return "Yes"
        return None
