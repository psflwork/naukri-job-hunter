"""Interactive first-run setup: configs from examples/, resume, experience, target roles, login."""

import getpass
import os
import re
import shutil
from pathlib import Path

ROOT = Path(__file__).parent
EXAMPLES = ROOT / "examples"
ENV_FILE = ROOT / ".env"


def _ask(prompt: str, default: str = "") -> str:
    suffix = f" [{default}]" if default else ""
    answer = input(f"{prompt}{suffix}: ").strip()
    return answer or default


def _set_scalar(path: Path, key: str, value: str) -> None:
    text = path.read_text()
    text = re.sub(rf"(?m)^{key}:.*$", f"{key}: {value}", text, count=1)
    path.write_text(text)


def _set_list(path: Path, key: str, items: list[str]) -> None:
    text = path.read_text()
    block = f"{key}:\n" + "".join(f"  - {item}\n" for item in items)
    text = re.sub(rf"(?m)^{key}:\n(?:  - .*\n)+", block, text, count=1)
    path.write_text(text)


def _clean_path(raw: str) -> Path:
    # Terminals quote or backslash-escape dragged-in paths (backslashes are separators on Windows).
    raw = raw.strip().strip("'\"")
    if os.name != "nt":
        raw = raw.replace("\\ ", " ")
    return Path(os.path.expanduser(raw)).resolve()


def _copy_examples() -> list[Path]:
    created = []
    for example in sorted(EXAMPLES.glob("config*.yaml")):
        target = ROOT / example.name
        if not target.exists():
            shutil.copy(example, target)
            created.append(target)
    return created


def _choose_resume() -> str:
    local = sorted(ROOT.glob("*.pdf"))
    if local:
        print(f"Found resume: {local[0].name}")
        return local[0].name
    while True:
        raw = _ask("Your resume PDF: drag the file into this window (or type its full path), then press Enter")
        path = _clean_path(raw)
        if path.suffix.lower() == ".pdf" and path.exists():
            return str(path)
        print(f"  Not a PDF file: {path}")


def _detect_skills(resume: str) -> None:
    from matcher import read_resume
    from preferences import set_pref
    from skills import detect_skills

    try:
        detected = detect_skills(read_resume(ROOT / resume))
    except Exception as e:
        print(f"  Couldn't read skills from the resume ({e}); keeping the skills in config.yaml")
        return
    if not detected:
        return
    print(f"\nSkills found in your resume ({len(detected)}): {', '.join(detected)}")
    if _ask("Use these for matching jobs? [Y/n]", "y").lower() in ("y", "yes"):
        set_pref("", "skills", detected)
        print("  Saved. Add or remove skills any time from the menu (Change what I'm looking for).")


def _write_env() -> None:
    if ENV_FILE.exists() and re.search(r"(?m)^NAUKRI_EMAIL=.+", ENV_FILE.read_text()):
        print("Naukri login already saved in .env")
        return
    print("\nNaukri login (saved only on this computer in .env; leave empty to log in by hand in the browser)")
    email = _ask("Naukri email")
    if not email:
        return
    password = getpass.getpass("Naukri password (nothing shows while you type; press Enter when done): ")
    save_credentials(email, password)
    print("Saved to .env")


def save_credentials(email: str, password: str) -> None:
    """Write the Naukri login to .env (owner-only permissions) and use it in this process right away."""
    old_umask = os.umask(0o077)
    try:
        ENV_FILE.write_text(
            "# Naukri login used by hunt.py and the MCP server. Keep this file private.\n"
            f"NAUKRI_EMAIL={email}\nNAUKRI_PASSWORD={password}\n"
        )
    finally:
        os.umask(old_umask)
    ENV_FILE.chmod(0o600)
    os.environ["NAUKRI_EMAIL"], os.environ["NAUKRI_PASSWORD"] = email, password


def run_setup() -> None:
    print("=== Naukri Job Hunter setup ===\n")
    created = _copy_examples()
    configs = [p for p in (ROOT / "config.yaml", ROOT / "config.side.yaml") if p.exists()]
    if created:
        print("Created " + ", ".join(p.name for p in created) + " from examples/")

    resume = _choose_resume()
    years = _ask("Total years of experience", "8")
    while not years.isdigit():
        years = _ask("Please enter a whole number of years", "8")
    for cfg in configs:
        _set_scalar(cfg, "resume_path", "'" + resume.replace("'", "''") + "'")
        _set_scalar(cfg, "experience_years", years)
    _detect_skills(resume)

    roles = _ask("\nJob titles to search for, separated by commas (just press Enter to keep the suggested ones)\n"
                 "  e.g. senior python developer, full stack developer, solution architect\n ")
    if roles:
        _set_list(ROOT / "config.yaml", "queries", [r.strip() for r in roles.split(",") if r.strip()])

    _write_env()

    print("\nSetup done! You can change your resume, skills, location or job type any time from the menu.")
    print("(Advanced: titles, exclusions and scoring live in config.yaml / config.side.yaml)\n")
