"""Saved preferences (resume, skills, roles, locations, work mode, job type) layered over the YAML configs.

Stored in data/preferences.json:
  common   - resume_path, skills, experience_years (shared by every hunt profile)
  profiles - per profile: roles, locations, work_modes, job_types
"""

import json
import os
import shutil
from pathlib import Path
from typing import Callable

from matcher import find_resume, read_resume, wanted_job_types, wanted_work_modes
from options import JOB_TYPE_LABELS, JOB_TYPES, WORK_MODES, describe, normalize_job_types, normalize_work_modes
from skills import detect_skills, edit_list

ROOT = Path(__file__).parent
PREFS_FILE = ROOT / "data" / "preferences.json"
RESUMES_DIR = ROOT / "resumes"
COMMON_KEYS = ("resume_path", "skills", "experience_years")
PROFILE_KEYS = ("roles", "locations", "work_modes", "job_types")


def load_prefs() -> dict:
    data = json.loads(PREFS_FILE.read_text()) if PREFS_FILE.exists() else {}
    data.setdefault("common", {})
    data.setdefault("profiles", {})
    return data


def save_prefs(data: dict) -> None:
    PREFS_FILE.parent.mkdir(parents=True, exist_ok=True)
    PREFS_FILE.write_text(json.dumps(data, indent=2))


def set_pref(profile: str, key: str, value) -> None:
    data = load_prefs()
    section = data["common"] if key in COMMON_KEYS else data["profiles"].setdefault(profile, {})
    if value is None:
        section.pop(key, None)
    else:
        section[key] = value
    save_prefs(data)


def reset_profile(profile: str) -> None:
    data = load_prefs()
    data["profiles"].pop(profile, None)
    save_prefs(data)


def apply_overrides(cfg: dict, *, resume_path=None, skills=None, roles=None, locations=None,
                    work_modes=None, job_types=None, experience_years=None) -> dict:
    """Overlay preference values on a loaded config (None = keep the config's value)."""
    search = cfg.setdefault("search", {})
    if resume_path is not None:
        cfg["resume_path"] = resume_path
    if skills is not None:
        cfg["skills"] = list(skills)
    if roles is not None:
        cfg["queries"] = list(roles)
    if locations is not None:
        search["locations"] = list(locations)
    if work_modes is not None:
        search["work_modes"] = normalize_work_modes(work_modes)
    if job_types is not None:
        cfg["job_types"] = normalize_job_types(job_types)
    if experience_years is not None:
        cfg["experience_years"] = int(experience_years)
    return cfg


def apply_saved(cfg: dict, profile: str) -> dict:
    data = load_prefs()
    return apply_overrides(cfg, **data["common"], **data["profiles"].get(profile, {}))


def clean_path(raw: str) -> Path:
    """Path typed or dragged into a terminal: strips quotes and (Mac/Linux) backslash-escaped spaces."""
    raw = raw.strip().strip("'\"")
    if os.name != "nt":
        raw = raw.replace("\\ ", " ")
    return Path(raw).expanduser().resolve()


def import_resume(path_text: str) -> Path:
    """Copy a resume PDF into resumes/ and return its new path."""
    src = clean_path(path_text)
    if src.suffix.lower() != ".pdf" or not src.exists():
        raise ValueError(f"Not a PDF file: {src}")
    RESUMES_DIR.mkdir(exist_ok=True)
    dest = RESUMES_DIR / src.name
    if src != dest:
        shutil.copy(src, dest)
    return dest


def use_resume(path_text: str, *, update_skills: bool) -> tuple[Path, list[str]]:
    """Make a resume the active one; optionally replace saved skills with ones detected from it."""
    dest = import_resume(path_text)
    set_pref("", "resume_path", str(dest.relative_to(ROOT)))
    detected = detect_skills(read_resume(dest))
    if update_skills and detected:
        set_pref("", "skills", detected)
    return dest, detected


def summary(cfg: dict) -> dict:
    s = cfg.get("search", {})
    return {
        "resume": cfg.get("resume_path") or "(first PDF in folder)",
        "skills": cfg.get("skills", []),
        "roles": cfg.get("queries", []),
        "locations": s.get("locations") or [],
        "work_modes": wanted_work_modes(cfg),
        "job_types": wanted_job_types(cfg),
        "experience_years": cfg.get("experience_years"),
    }


def _ask(prompt: str) -> str:
    try:
        return input(prompt).strip()
    except EOFError:
        return "q"


def _choose(options: list[str], labels: dict, current: list[str]) -> list[str] | None:
    for i, opt in enumerate(options, 1):
        print(f"    {i}) {labels.get(opt, opt)}{'  *' if opt in current else ''}")
    print(f"    {len(options) + 1}) Any")
    answer = _ask("  Numbers, comma-separated (Enter = keep): ")
    if not answer:
        return None
    picked = []
    for part in answer.replace(" ", "").split(","):
        if part.isdigit() and 1 <= int(part) <= len(options):
            picked.append(options[int(part) - 1])
        elif part == str(len(options) + 1):
            return []
    return picked or None


def edit_preferences(profile: str, load_config: Callable[[str], dict]) -> None:
    """Interactive menu; every change is saved immediately."""
    while True:
        cfg = load_config(profile)
        cur = summary(cfg)
        skills = cur["skills"]
        print(f"""
Preferences for the '{profile}' hunt  (saved in data/preferences.json, overrides the YAML config)
  1) Resume      : {cur['resume']}
  2) Skills ({len(skills)}) : {', '.join(skills[:12])}{' ...' if len(skills) > 12 else ''}
  3) Roles       : {', '.join(cur['roles'])}
  4) Locations   : {describe(cur['locations'])}
  5) Work mode   : {describe(cur['work_modes'])}
  6) Job type    : {describe(cur['job_types'], JOB_TYPE_LABELS)}
  7) Experience  : {cur['experience_years']} years
  r) Reset roles/locations/work mode/job type of this hunt to the YAML config
  q) Done""")
        choice = _ask("Choose: ").lower()
        try:
            if choice == "1":
                path = _ask("  Resume PDF path (drag the file here): ")
                if path:
                    answer = _ask("  Replace your skills with the ones detected from this resume? [Y/n] ").lower()
                    dest, detected = use_resume(path, update_skills=answer in ("", "y", "yes"))
                    print(f"  Using {dest.name}; detected {len(detected)} skills: {', '.join(detected)}")
            elif choice == "2":
                print("  Current:", ", ".join(skills))
                text = _ask("  'python, react' = replace | '+kafka, -angular' = add/remove | "
                            "'detect' = from resume | Enter = keep\n  > ")
                if text.lower() == "detect":
                    detected = detect_skills(read_resume(find_resume(ROOT, cfg.get("resume_path"))))
                    set_pref(profile, "skills", detected)
                    print(f"  Detected {len(detected)} skills: {', '.join(detected)}")
                elif text:
                    set_pref(profile, "skills", edit_list(skills, text))
            elif choice == "3":
                text = _ask("  Roles to search, comma-separated (e.g. python developer, solution architect): ")
                if text:
                    set_pref(profile, "roles", [r.strip() for r in text.split(",") if r.strip()])
            elif choice == "4":
                text = _ask("  Cities, comma-separated (e.g. Bengaluru, Pune) | 'any' = no location filter: ")
                if text:
                    locations = [] if text.lower() == "any" else [c.strip() for c in text.split(",") if c.strip()]
                    set_pref(profile, "locations", locations)
            elif choice == "5":
                picked = _choose(WORK_MODES, {"remote": "Remote", "hybrid": "Hybrid", "office": "Office"},
                                 cur["work_modes"])
                if picked is not None:
                    set_pref(profile, "work_modes", picked)
            elif choice == "6":
                picked = _choose(JOB_TYPES, JOB_TYPE_LABELS, cur["job_types"])
                if picked is not None:
                    set_pref(profile, "job_types", picked)
            elif choice == "7":
                text = _ask("  Total years of experience: ")
                if text.isdigit():
                    set_pref(profile, "experience_years", int(text))
            elif choice == "r":
                reset_profile(profile)
            elif choice in ("q", ""):
                return
        except (ValueError, FileNotFoundError) as e:
            print(f"  {e}")
