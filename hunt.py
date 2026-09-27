#!/usr/bin/env python3
"""Naukri remote job hunter.

  ./run.sh menu                      # simple numbered menu (what the START-HERE launchers open)
  ./run.sh setup                     # first-run wizard (run.sh starts it automatically)
  ./run.sh                           # everything: login, search, report, offer to apply
  ./run.sh side                      # same, using the side-gig profile (config.side.yaml)
  ./run.sh --apply                   # apply without asking (for scheduled runs)
  ./run.sh login                     # log in, session is saved (needed for apply)
  ./run.sh search [--profile side]   # search, score against resume, write report (new jobs only)
  ./run.sh search --all              # include jobs already seen in previous runs
  ./run.sh open --top 10             # open the top matches from the latest report in your browser
  ./run.sh apply --top 10            # dry run: show which jobs would be auto-applied
  ./run.sh apply --top 10 --confirm  # actually apply (Naukri-native apply only)
  ./run.sh prefs                     # change resume, skills, roles, location, work mode, job type
  ./run.sh resume ~/cv.pdf           # switch resume (skills are re-detected from it)
  ./run.sh --location Pune --work-mode hybrid --job-type contract   # one-off choices for this run
  ./run.sh pick                      # numbered list of matches; choose which to apply to
  ./run.sh contacts                  # emails / phones / links to send your resume manually
  ./run.sh side pick                 # any command for the side-gig profile

Profiles: "default" uses config.yaml; any other name uses config.<name>.yaml.
"""

import argparse
import csv
import html
import json
import sys
import webbrowser
from dataclasses import asdict
from datetime import datetime
from pathlib import Path

import yaml

from matcher import Matcher, find_resume, read_resume, wanted_job_types, wanted_work_modes
from naukri import Job, Naukri, credentials
from options import GIG_TYPES, JOB_TYPE_LABELS, JOB_TYPE_QUERY_PREFIX, describe
from preferences import apply_overrides, apply_saved, edit_preferences, summary, use_resume
from setup_wizard import run_setup
from skills import edit_list

ROOT = Path(__file__).parent
DATA = ROOT / "data"
OUTPUT = ROOT / "output"
APPLIED_FILE = DATA / "applied.json"
DEFAULT_PROFILE = "default"


def load_json(path: Path, default):
    return json.loads(path.read_text()) if path.exists() else default


def save_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2))


def config_path(profile: str) -> Path:
    return ROOT / ("config.yaml" if profile == DEFAULT_PROFILE else f"config.{profile}.yaml")


def list_profiles() -> list[str]:
    return [DEFAULT_PROFILE] + sorted(p.name.split(".")[1] for p in ROOT.glob("config.*.yaml"))


def load_config(profile: str = DEFAULT_PROFILE) -> dict:
    path = config_path(profile)
    if not path.exists():
        raise SystemExit(f"Unknown profile '{profile}'. Available: {', '.join(list_profiles())}")
    cfg = yaml.safe_load(path.read_text())
    cfg["_profile"] = profile
    return apply_saved(cfg, profile)


def build_queries(cfg: dict, queries: list[str]) -> list[str]:
    """When hunting only gig job types, turn 'python developer' into 'freelance python developer' etc."""
    job_types = wanted_job_types(cfg)
    if not job_types or not set(job_types) <= set(GIG_TYPES):
        return queries
    gig_words = ("part time", "part-time", "freelance", "freelancing", "contract", "contractual")
    result: list[str] = []
    for q in queries:
        variants = [q] if any(w in q.lower() for w in gig_words) else \
            [f"{JOB_TYPE_QUERY_PREFIX[t]} {q}" for t in job_types]
        result += [v for v in variants if v not in result]
    return result


def _state_file(cfg: dict, name: str) -> Path:
    profile = cfg.get("_profile", DEFAULT_PROFILE)
    return DATA / (f"{name}.json" if profile == DEFAULT_PROFILE else f"{name}.{profile}.json")


def seen_file(cfg: dict) -> Path:
    return _state_file(cfg, "seen_jobs")


def latest_file(cfg: dict) -> Path:
    return _state_file(cfg, "latest_results")


def cmd_login(cfg: dict, args) -> None:
    with Naukri(headless=False) as n:
        n.login()


def run_search(cfg: dict, *, include_seen: bool = False, queries: list[str] | None = None,
               job_age_days: int | None = None, max_pages: int | None = None,
               min_score: int | None = None, limit: int | None = None) -> dict:
    s = cfg["search"]
    limit = limit or cfg.get("max_results")
    queries = build_queries(cfg, queries or cfg["queries"])
    work_modes = wanted_work_modes(cfg)
    locations = s.get("locations") or [None]
    job_age_days = job_age_days or s.get("job_age_days", 7)
    max_pages = max_pages or s.get("max_pages_per_query", 3)
    min_score = cfg.get("min_score", 0) if min_score is None else min_score
    experience = s.get("experience_filter", cfg["experience_years"])
    matcher = Matcher(read_resume(find_resume(ROOT, cfg.get("resume_path"))), cfg)

    found: dict[str, Job] = {}
    with Naukri(headless=s.get("headless", False)) as n:
        if credentials():
            n.ensure_logged_in()
        for location in locations:
            for query in queries:
                for job in n.search(
                    query,
                    experience=experience,
                    work_modes=work_modes,
                    job_age=job_age_days,
                    max_pages=max_pages,
                    delay=(s.get("min_delay_seconds", 3), s.get("max_delay_seconds", 7)),
                    location=location,
                ):
                    found.setdefault(job.job_id, job)

    seen = set(load_json(seen_file(cfg), []))
    applied = load_json(APPLIED_FILE, {})
    results = []
    for job in found.values():
        if matcher.is_excluded(job) or job.job_id in applied:
            continue
        if not include_seen and job.job_id in seen:
            continue
        if matcher.score(job) >= min_score:
            results.append(job)
    results.sort(key=lambda j: j.score, reverse=True)
    total_matches = len(results)
    held_back = {j.job_id for j in results[limit:]} if limit else set()
    results = results[:limit] if limit else results

    # Matches cut by the limit stay unseen so they can show up in a later run.
    save_json(seen_file(cfg), sorted((seen | set(found)) - held_back))
    save_json(latest_file(cfg), [asdict(j) for j in results])
    csv_path, html_path = write_reports(results, cfg)
    return {"scanned": len(found), "min_score": min_score, "results": results, "total_matches": total_matches,
            "limit": limit, "csv": csv_path, "html": html_path}


def print_search_plan(cfg: dict) -> None:
    cur = summary(cfg)
    queries = build_queries(cfg, cfg["queries"])
    searches = len(queries) * max(1, len(cur["locations"]))
    print(f"Profile   : {cfg.get('profile_name', cfg['_profile'])}")
    print(f"Resume    : {cur['resume']}")
    print(f"Roles     : {', '.join(queries)}")
    print(f"Locations : {describe(cur['locations'])}   Work mode: {describe(cur['work_modes'])}   "
          f"Job type: {describe(cur['job_types'], JOB_TYPE_LABELS)}")
    print(f"Skills    : {len(cur['skills'])} ({', '.join(cur['skills'][:8])}{' ...' if len(cur['skills']) > 8 else ''})")
    print(f"Searches  : {searches} (roles x locations)   Change with: ./run.sh prefs\n")


def cmd_search(cfg: dict, args) -> None:
    print_search_plan(cfg)
    r = run_search(cfg, include_seen=args.all, limit=args.limit)
    results = r["results"]
    shown = f", showing top {len(results)}" if len(results) < r["total_matches"] else ""
    print(f"\nScanned {r['scanned']} unique jobs -> {r['total_matches']} matches (score >= {r['min_score']}){shown}")
    for j in results[:15]:
        print(f"  {j.score:3d}  {j.title[:55]:55s}  {j.company[:25]:25s}  {j.experience}")
    print(f"\nReport: {r['html']}\nCSV:    {r['csv']}")
    if results and not args.no_open:
        webbrowser.open(r["html"].as_uri())


def write_reports(jobs: list[Job], cfg: dict) -> tuple[Path, Path]:
    OUTPUT.mkdir(exist_ok=True)
    stamp = datetime.now().strftime("%Y-%m-%d_%H%M")
    profile = cfg.get("_profile", DEFAULT_PROFILE)
    prefix = "jobs" if profile == DEFAULT_PROFILE else f"{profile}_jobs"
    csv_path = OUTPUT / f"{prefix}_{stamp}.csv"
    html_path = OUTPUT / f"{prefix}_{stamp}.html"
    heading = cfg.get("profile_name", "Remote matches")
    show_signals = any(j.gig_signals for j in jobs)

    def job_type_label(j: Job) -> str:
        return describe(j.job_types, JOB_TYPE_LABELS, empty="Full-time")

    cols = ["score", "title", "company", "job_type", "experience", "salary", "location", "posted", "rating",
            "external_apply", "has_questionnaire", "gig_signals", "matched_skills", "url"]
    with csv_path.open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(cols)
        for j in jobs:
            row = asdict(j)
            row["job_type"] = job_type_label(j)
            row["matched_skills"] = ", ".join(j.matched_skills)
            row["gig_signals"] = ", ".join(j.gig_signals)
            w.writerow([row[c] for c in cols])

    rows = []
    for j in jobs:
        apply_type = "Company site" if j.external_apply else ("Naukri + questions" if j.has_questionnaire else "Naukri")
        signals = f"<td class='g'>{html.escape(', '.join(j.gig_signals))}</td>" if show_signals else ""
        rows.append(
            f"<tr><td class='s'>{j.score}</td>"
            f"<td><a href='{html.escape(j.url)}' target='_blank'>{html.escape(j.title)}</a></td>"
            f"<td>{html.escape(j.company)}{' ★' + j.rating if j.rating else ''}</td>"
            f"<td>{html.escape(job_type_label(j))}</td>"
            f"<td>{html.escape(j.experience)}</td><td>{html.escape(j.salary)}</td>"
            f"<td>{html.escape(j.location)}</td><td>{html.escape(j.posted)}</td>"
            f"<td>{apply_type}</td>{signals}<td class='k'>{html.escape(', '.join(j.matched_skills))}</td></tr>"
        )
    signals_th = "<th>Gig signals</th>" if show_signals else ""
    html_path.write_text(f"""<!doctype html><html><head><meta charset="utf-8">
<title>{html.escape(heading)} {stamp}</title><style>
body{{font-family:-apple-system,sans-serif;margin:24px;color:#222}}
table{{border-collapse:collapse;width:100%;font-size:14px}}
th,td{{border-bottom:1px solid #e5e5e5;padding:8px;text-align:left;vertical-align:top}}
th{{background:#f6f6f6;position:sticky;top:0}} td.s{{font-weight:700}} td.k{{color:#555;font-size:12px}}
td.g{{color:#0a7d35;font-size:12px;font-weight:600}}
a{{color:#275df5;text-decoration:none}} a:hover{{text-decoration:underline}}
</style></head><body><h2>{html.escape(heading)}: {len(jobs)} matches &mdash; {stamp}</h2><table>
<tr><th>Score</th><th>Title</th><th>Company</th><th>Type</th><th>Exp</th><th>Salary</th><th>Location</th>
<th>Posted</th><th>Apply</th>{signals_th}<th>Matched skills</th></tr>{''.join(rows)}</table></body></html>""")
    return csv_path, html_path


def latest_jobs(cfg: dict, top: int | None = None) -> list[Job]:
    data = load_json(latest_file(cfg), [])
    if not data:
        profile = cfg.get("_profile", DEFAULT_PROFILE)
        hint = "" if profile == DEFAULT_PROFILE else f" --profile {profile}"
        raise SystemExit(f"No results yet. Run: ./run.sh search{hint}")
    return [Job(**d) for d in data[:top]]


def record_applied(job: Job, status: str) -> None:
    applied = load_json(APPLIED_FILE, {})
    applied[job.job_id] = {"title": job.title, "company": job.company, "url": job.url,
                           "status": status, "at": datetime.now().isoformat(timespec="seconds")}
    save_json(APPLIED_FILE, applied)


def cmd_open(cfg: dict, args) -> None:
    for j in latest_jobs(cfg, args.top):
        webbrowser.open_new_tab(j.url)


def apply_candidates(cfg: dict, top: int) -> list[Job]:
    ac = cfg.get("apply", {})
    applied = load_json(APPLIED_FILE, {})
    candidates = []
    for j in latest_jobs(cfg, top):
        if j.job_id in applied:
            continue
        if j.external_apply:
            print(f"  skip (company site)  {j.title} @ {j.company}")
        elif j.has_questionnaire and ac.get("skip_questionnaires", True):
            print(f"  skip (questionnaire) {j.title} @ {j.company}")
        else:
            candidates.append(j)
    return candidates[:min(top, ac.get("max_per_run", 10))]


def print_candidates(jobs: list[Job]) -> None:
    print(f"\nCan auto-apply to {len(jobs)} jobs:")
    for j in jobs:
        print(f"  {j.score:3d}  {j.title} @ {j.company}\n       {j.url}")


def apply_jobs(jobs: list[Job]) -> None:
    with Naukri(headless=False) as n:
        if not (n.ensure_logged_in() or n.login()):
            raise SystemExit("Not logged in. Set NAUKRI_EMAIL/NAUKRI_PASSWORD in .env or run: ./run.sh login")
        for j in jobs:
            status = n.apply(j)
            print(f"  {status:16s} {j.title} @ {j.company}")
            if status in ("applied", "already_applied"):
                record_applied(j, status)
            n.page.wait_for_timeout(3000)


def cmd_apply(cfg: dict, args) -> None:
    candidates = apply_candidates(cfg, args.top)
    if not args.confirm:
        print_candidates(candidates)
        print("\nDry run. Re-run with --confirm to apply.")
        return
    apply_jobs(candidates)


def can_auto_apply(cfg: dict, job: Job) -> bool:
    skip_q = cfg.get("apply", {}).get("skip_questionnaires", True)
    return not job.external_apply and not (job.has_questionnaire and skip_q)


def parse_selection(text: str, count: int) -> list[int]:
    """'1,3,5-7' -> [0, 2, 4, 5, 6] (valid 0-based indexes only)."""
    picked: list[int] = []
    for part in text.replace(" ", "").split(","):
        if "-" in part:
            start, _, end = part.partition("-")
            if start.isdigit() and end.isdigit():
                picked.extend(range(int(start), int(end) + 1))
        elif part.isdigit():
            picked.append(int(part))
    return [i - 1 for i in dict.fromkeys(picked) if 1 <= i <= count]


def _ask(prompt: str) -> str:
    try:
        return input(prompt).strip()
    except EOFError:
        return ""


def cmd_pick(cfg: dict, args) -> None:
    """Show the latest matches as a numbered list and apply to the ones you choose."""
    applied = load_json(APPLIED_FILE, {})
    jobs = [j for j in latest_jobs(cfg, args.top) if j.job_id not in applied]
    if not jobs:
        print("Nothing new to apply to.")
        return
    print(f"\n{'#':>3}  {'Score':>5}  {'Apply via':18s}  Job")
    for i, j in enumerate(jobs, 1):
        via = "auto (Naukri)" if can_auto_apply(cfg, j) else ("company site" if j.external_apply else "Naukri + questions")
        print(f"{i:>3}  {j.score:>5}  {via:18s}  {j.title[:60]} @ {j.company[:30]}")
    answer = _ask("\nJobs to apply to (e.g. 1,3,5-7 | a = all auto-apply | Enter = cancel): ").lower()
    if not answer:
        print("Cancelled.")
        return
    chosen = [j for j in jobs if can_auto_apply(cfg, j)] if answer == "a" else [jobs[i] for i in parse_selection(answer, len(jobs))]
    auto = [j for j in chosen if can_auto_apply(cfg, j)]
    manual = [j for j in chosen if not can_auto_apply(cfg, j)]
    if auto:
        print(f"\nAuto-applying to {len(auto)} jobs...")
        apply_jobs(auto)
    if manual:
        print(f"\nOpening {len(manual)} jobs that need company-site or questionnaire answers in your browser:")
        for j in manual:
            print(f"  {j.title} @ {j.company}")
            webbrowser.open_new_tab(j.url)


def cmd_contacts(cfg: dict, args) -> None:
    """Build a contact list (emails, phones, websites, LinkedIn/careers links) for the latest matches."""
    from contacts import build_rows, fetch_details, resume_name, write_contacts

    jobs = latest_jobs(cfg, args.top)
    cache_file = DATA / "job_details.json"
    details = fetch_details(jobs, load_json(cache_file, {}), headless=cfg["search"].get("headless", False))
    save_json(cache_file, details)

    name = (cfg.get("outreach") or {}).get("name") or resume_name(find_resume(ROOT, cfg.get("resume_path")))
    rows = build_rows(jobs, details, cfg, name)
    profile = cfg.get("_profile", DEFAULT_PROFILE)
    csv_path, html_path = write_contacts(rows, OUTPUT, "contacts" if profile == DEFAULT_PROFILE else f"{profile}_contacts")

    print(f"\n{sum(1 for r in rows if r['emails'])} of {len(rows)} jobs publish an email:")
    for r in rows:
        if r["emails"] or r["phones"]:
            print(f"  {r['company'][:30]:30s}  {', '.join(r['emails'] + r['phones'])}")
    print(f"\nContacts: {html_path}\nCSV:      {csv_path}")
    if not getattr(args, "no_open", False):
        webbrowser.open(html_path.as_uri())


def post_search_menu(cfg: dict, args) -> None:
    while True:
        eligible = [j for j in latest_jobs(cfg, args.top) if can_auto_apply(cfg, j)
                    and j.job_id not in load_json(APPLIED_FILE, {})]
        print(f"""
What next?
  [a] Auto-apply to {len(eligible)} eligible jobs (of top {args.top})
  [p] Pick jobs to apply to
  [c] Contact list: emails / phones / links to send your resume manually
  [o] Open top {args.top} jobs in your browser
  [q] Quit""")
        choice = _ask("Choose: ").lower()
        if choice == "a":
            if eligible:
                print_candidates(eligible)
                apply_jobs(eligible)
            else:
                print("No jobs eligible for auto-apply.")
        elif choice == "p":
            cmd_pick(cfg, args)
        elif choice == "c":
            cmd_contacts(cfg, args)
        elif choice == "o":
            cmd_open(cfg, args)
        else:
            return


def cmd_run(cfg: dict, args) -> None:
    """Full pipeline: log in, search, report, then a menu to apply / pick / get contacts."""
    cmd_search(cfg, args)
    if args.no_apply or not load_json(latest_file(cfg), []):
        return
    if args.apply:
        candidates = apply_candidates(cfg, args.top)
        print_candidates(candidates)
        apply_jobs(candidates)
        return
    post_search_menu(cfg, args)


def build_parser() -> argparse.ArgumentParser:
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("-p", "--profile", default=DEFAULT_PROFILE,
                        help="hunt profile: default (config.yaml) or e.g. side (config.side.yaml)")

    choices = argparse.ArgumentParser(add_help=False)
    g = choices.add_argument_group("one-off search choices (this run only; save them with: ./run.sh prefs)")
    g.add_argument("--resume", help="resume PDF to use")
    g.add_argument("--skills", help="'python, react' = replace | '+kafka, -angular' = add/remove")
    g.add_argument("--roles", help="roles to search, comma-separated")
    g.add_argument("--location", help="cities, comma-separated, or 'any'")
    g.add_argument("--work-mode", help="remote, hybrid, office (comma-separated) or any")
    g.add_argument("--job-type", help="full-time, part-time, freelance, contract, gig (comma-separated) or any")
    g.add_argument("--experience", type=int, help="total years of experience")

    p = argparse.ArgumentParser(description="Naukri remote job hunter (default command: run)")
    sub = p.add_subparsers(dest="cmd")
    rp = sub.add_parser("run", parents=[common, choices], help="login + search + report + optional apply")
    rp.add_argument("--all", action="store_true", help="include jobs seen in previous runs")
    rp.add_argument("--limit", type=int, help="max jobs in the results (overrides max_results)")
    rp.add_argument("--no-open", action="store_true", help="don't open the HTML report")
    rp.add_argument("--top", type=int, default=10, help="how many top matches to consider for applying")
    rp.add_argument("--apply", action="store_true", help="apply without asking (for scheduled runs)")
    rp.add_argument("--no-apply", action="store_true", help="search only, never apply")
    sub.add_parser("login", parents=[common])
    sp = sub.add_parser("search", parents=[common, choices])
    sp.add_argument("--all", action="store_true", help="include jobs seen in previous runs")
    sp.add_argument("--limit", type=int, help="max jobs in the results (overrides max_results)")
    sp.add_argument("--no-open", action="store_true", help="don't open the HTML report")
    op = sub.add_parser("open", parents=[common])
    op.add_argument("--top", type=int, default=10)
    ap = sub.add_parser("apply", parents=[common])
    ap.add_argument("--top", type=int, default=10)
    ap.add_argument("--confirm", action="store_true", help="actually apply (default is dry run)")
    pk = sub.add_parser("pick", parents=[common], help="numbered list of matches; choose which to apply to")
    pk.add_argument("--top", type=int, default=25)
    ct = sub.add_parser("contacts", parents=[common], help="emails/phones/links to send your resume manually")
    ct.add_argument("--top", type=int, default=25)
    ct.add_argument("--no-open", action="store_true", help="don't open the HTML contact list")
    sub.add_parser("prefs", parents=[common], help="change resume, skills, roles, location, work mode, job type")
    rs = sub.add_parser("resume", parents=[common], help="switch to a new resume PDF and detect skills from it")
    rs.add_argument("path", nargs="?", help="resume PDF (asks if omitted)")
    rs.add_argument("--keep-skills", action="store_true", help="don't replace skills with detected ones")
    sub.add_parser("setup", help="first-run wizard: resume, experience, roles, Naukri login")
    sub.add_parser("menu", help="simple numbered menu for every action (used by the START-HERE launchers)")
    return p


def main() -> None:
    parser = build_parser()
    args = parser.parse_args(sys.argv[1:] or ["run"])
    if args.cmd == "menu":
        cmd_menu(parser)
    else:
        dispatch(args)


def dispatch(args) -> None:
    if args.cmd == "setup":
        run_setup()
        return
    if not config_path(DEFAULT_PROFILE).exists():
        raise SystemExit("No config.yaml yet. Run: ./run.sh setup")
    if args.cmd == "resume":
        cmd_resume(args)
        return
    cfg = load_config(args.profile)
    try:
        apply_cli_choices(cfg, args)
    except ValueError as e:
        raise SystemExit(str(e))
    commands = {"run": cmd_run, "login": cmd_login, "search": cmd_search, "open": cmd_open, "apply": cmd_apply,
                "pick": cmd_pick, "contacts": cmd_contacts, "prefs": cmd_prefs}
    commands[args.cmd](cfg, args)


def apply_cli_choices(cfg: dict, args) -> None:
    def split(text: str | None) -> list[str] | None:
        if text is None:
            return None
        return [] if text.strip().lower() == "any" else [x.strip() for x in text.split(",") if x.strip()]

    resume = getattr(args, "resume", None)
    skills = getattr(args, "skills", None)
    apply_overrides(
        cfg,
        resume_path=str(Path(resume).expanduser().resolve()) if resume else None,
        skills=edit_list(cfg.get("skills", []), skills) if skills is not None else None,
        roles=split(getattr(args, "roles", None)),
        locations=split(getattr(args, "location", None)),
        work_modes=split(getattr(args, "work_mode", None)),
        job_types=split(getattr(args, "job_type", None)),
        experience_years=getattr(args, "experience", None),
    )


def cmd_prefs(cfg: dict, args) -> None:
    edit_preferences(args.profile, load_config)


def cmd_resume(args) -> None:
    path = args.path or input("Resume PDF path (drag the file here): ").strip()
    try:
        dest, detected = use_resume(path, update_skills=not args.keep_skills)
    except ValueError as e:
        raise SystemExit(str(e))
    print(f"Now using {dest.relative_to(ROOT)} for every hunt.")
    print(f"Detected {len(detected)} skills: {', '.join(detected)}")
    print("Skills kept as before." if args.keep_skills or not detected else "Your skills were updated to these.")
    print("Fine-tune with: ./run.sh prefs")


MENU = """
============================================================
  Naukri Job Hunter
============================================================
{status}
  1) Find jobs for me
  2) Find side gigs (part-time / freelance / contract)
  3) Show my latest jobs and choose which to apply to
  4) Get recruiter emails / phone numbers (send resume myself)
  5) Change what I'm looking for (skills, location, job type...)
  6) Use a new resume
  7) Log in to Naukri again
  8) Start setup again
  q) Quit
"""


def _menu_status() -> str:
    try:
        cur = summary(load_config(DEFAULT_PROFILE))
    except (SystemExit, Exception):
        return ""
    roles = ", ".join(cur["roles"][:3]) + (" ..." if len(cur["roles"]) > 3 else "")
    return (f"  Resume : {cur['resume']}\n"
            f"  Roles  : {roles}\n"
            f"  Where  : {describe(cur['locations'], empty='anywhere')} | {describe(cur['work_modes'])}"
            f" | {describe(cur['job_types'], JOB_TYPE_LABELS)}\n")


def _which_hunt() -> str | None:
    """Ask regular vs side gigs when a side profile exists; None = cancelled."""
    if "side" not in list_profiles():
        return DEFAULT_PROFILE
    answer = _ask("  For which search?  1) Regular jobs   2) Side gigs   (Enter = 1): ")
    return {"": DEFAULT_PROFILE, "1": DEFAULT_PROFILE, "2": "side"}.get(answer)


def cmd_menu(parser: argparse.ArgumentParser) -> None:
    while True:
        print(MENU.format(status=_menu_status()))
        choice = _ask("Type a number and press Enter: ").lower()
        if choice in ("q", "quit", "exit"):
            return
        argv: list[str] | None = None
        if choice == "1":
            argv = ["run"]
        elif choice == "2":
            argv = ["run", "--profile", "side"] if "side" in list_profiles() else ["run"]
        elif choice in ("3", "4", "5"):
            profile = _which_hunt()
            if profile:
                argv = [{"3": "pick", "4": "contacts", "5": "prefs"}[choice], "--profile", profile]
        elif choice == "6":
            argv = ["resume"]
        elif choice == "7":
            argv = ["login"]
        elif choice == "8":
            argv = ["setup"]
        if argv is None:
            print("Please type one of the numbers shown, or q to quit.")
            continue
        try:
            dispatch(parser.parse_args(argv))
        except KeyboardInterrupt:
            print("\nStopped.")
        except SystemExit as e:
            if e.code not in (None, 0):
                print(f"\n{e.code}")
        except Exception as e:
            print(f"\nSomething went wrong: {e}\nTry again, or choose 7 to log in again if Naukri logged you out.")
        _ask("\nPress Enter to go back to the menu...")


if __name__ == "__main__":
    main()
