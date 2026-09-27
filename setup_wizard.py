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
    # Terminals quote or backslash-escape dragged-in paths.
    raw = raw.strip().strip("'\"").replace("\\ ", " ")
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
        raw = _ask("Path to your resume PDF (you can drag the file into this window)")
        path = _clean_path(raw)
        if path.suffix.lower() == ".pdf" and path.exists():
            return str(path)
        print(f"  Not a PDF file: {path}")


def _write_env() -> None:
    if ENV_FILE.exists() and re.search(r"(?m)^NAUKRI_EMAIL=.+", ENV_FILE.read_text()):
        print("Naukri login already saved in .env")
        return
    print("\nNaukri login (saved only on this computer in .env; leave empty to log in by hand in the browser)")
    email = _ask("Naukri email")
    if not email:
        return
    password = getpass.getpass("Naukri password (hidden): ")
    old_umask = os.umask(0o077)
    try:
        ENV_FILE.write_text(
            "# Naukri login used by hunt.py and the MCP server. Keep this file private.\n"
            f"NAUKRI_EMAIL={email}\nNAUKRI_PASSWORD={password}\n"
        )
    finally:
        os.umask(old_umask)
    ENV_FILE.chmod(0o600)
    print("Saved to .env")


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
        _set_scalar(cfg, "resume_path", f'"{resume}"')
        _set_scalar(cfg, "experience_years", years)

    roles = _ask("\nRoles to search for, comma-separated (Enter keeps the defaults in config.yaml)\n"
                 "  e.g. senior python developer, full stack developer, solution architect\n ")
    if roles:
        _set_list(ROOT / "config.yaml", "queries", [r.strip() for r in roles.split(",") if r.strip()])

    _write_env()

    print("\nSetup done. Fine-tune skills, titles and filters any time in config.yaml / config.side.yaml.")
    print("Next:  ./run.sh          regular remote jobs")
    print("       ./run.sh side     part-time / freelance / contract gigs\n")
