"""Local web app: every job-hunter action in your browser.  Start with:  ./run.sh web

Serves web/index.html on http://127.0.0.1:<port> (this computer only). Browser work (search, apply,
contacts, login) runs one task at a time in a background thread; its output is streamed to the page.
"""

import io
import json
import re
import sys
import threading
import traceback
import urllib.request
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

import hunt
from hunt import (APPLIED_FILE, DATA, DEFAULT_PROFILE, ROOT, apply_jobs, can_auto_apply, config_path,
                  latest_file, list_profiles, load_config, load_json, run_search, save_json)
from matcher import find_resume, read_resume
from naukri import Job, Naukri, credentials
from options import JOB_TYPE_LABELS, JOB_TYPES, WORK_MODES, describe
from preferences import RESUMES_DIR, apply_overrides, set_pref, summary, use_resume
from setup_wizard import _copy_examples, save_credentials
from skills import detect_experience, detect_roles, detect_skills

APP_ID = "naukri-job-hunter"
DEFAULT_PORT = 8765
INDEX = ROOT / "web" / "index.html"
MAX_UPLOAD = 20 * 1024 * 1024


class Busy(Exception):
    pass


class Task:
    """The single background task (search/apply/contacts/login) and its captured output."""

    def __init__(self):
        self.lock = threading.Lock()
        self.thread: threading.Thread | None = None
        self.id = 0
        self.name = ""
        self.running = False
        self.lines: list[str] = []
        self.result = None
        self.error = ""

    def start(self, name: str, fn) -> int:
        with self.lock:
            if self.running:
                raise Busy(f"Please wait: '{self.name}' is still running.")
            self.id += 1
            self.name, self.running, self.lines, self.result, self.error = name, True, [], None, ""
            self.thread = threading.Thread(target=self._run, args=(fn,), daemon=True)
            self.thread.start()
            return self.id

    def _run(self, fn) -> None:
        try:
            self.result = fn()
        except SystemExit as e:
            self.error = str(e.code) if e.code not in (None, 0) else "Stopped."
        except Exception as e:
            self.error = str(e).splitlines()[0] if str(e) else type(e).__name__
            self.write(traceback.format_exc())
        finally:
            self.running = False

    def write(self, text: str) -> None:
        with self.lock:
            for line in text.replace("\r", "\n").split("\n"):
                if line.strip():
                    self.lines.append(line.rstrip())
            del self.lines[:-500]

    def snapshot(self) -> dict:
        return {"id": self.id, "name": self.name, "running": self.running, "log": self.lines[-200:],
                "result": self.result, "error": self.error}


TASK = Task()


class _TaskOutput(io.TextIOBase):
    """stdout/stderr replacement: output of the task thread also goes to the web page."""

    def __init__(self, original):
        self.original = original

    def write(self, s: str) -> int:
        if threading.current_thread() is TASK.thread:
            TASK.write(s)
        self.original.write(s)
        return len(s)

    def flush(self) -> None:
        self.original.flush()

    def fileno(self) -> int:
        return self.original.fileno()


# ---------------------------------------------------------------- data for the page

def _job_dict(cfg: dict, j: Job, applied: dict) -> dict:
    via = "Naukri (auto)" if can_auto_apply(cfg, j) else ("Company site" if j.external_apply else "Naukri + questions")
    return {
        "job_id": j.job_id, "score": j.score, "title": j.title, "company": j.company, "rating": j.rating,
        "experience": j.experience, "salary": j.salary, "location": j.location, "posted": j.posted,
        "type": describe(j.job_types, JOB_TYPE_LABELS, empty="Full-time"), "via": via,
        "auto": can_auto_apply(cfg, j), "applied": j.job_id in applied, "url": j.url,
        "skills": j.matched_skills, "signals": j.gig_signals,
    }


def _resume_status(cfg: dict) -> dict:
    try:
        path = find_resume(ROOT, cfg.get("resume_path"))
        return {"ok": True, "name": path.name}
    except FileNotFoundError:
        return {"ok": False, "name": ""}


def get_state() -> dict:
    profiles = []
    for p in list_profiles():
        cfg = load_config(p)
        profiles.append({"id": p, "name": cfg.get("profile_name", p), "prefs": summary(cfg),
                         "has_results": latest_file(cfg).exists(), "resume": _resume_status(cfg)})
    return {
        "app": APP_ID, "profiles": profiles, "has_login": credentials() is not None,
        "job_types": [{"id": t, "label": JOB_TYPE_LABELS[t]} for t in JOB_TYPES],
        "work_modes": [{"id": m, "label": m.capitalize()} for m in WORK_MODES],
        "task": TASK.snapshot(),
    }


def get_jobs(profile: str) -> dict:
    cfg = load_config(profile)
    applied = load_json(APPLIED_FILE, {})
    jobs = [_job_dict(cfg, Job(**d), applied) for d in load_json(latest_file(cfg), [])]
    return {"profile": profile, "jobs": jobs, "max_per_run": cfg.get("apply", {}).get("max_per_run", 10)}


# ---------------------------------------------------------------- background tasks

def task_search(profile: str, include_seen: bool, limit: int | None):
    cfg = load_config(profile)
    hunt.print_search_plan(cfg)
    r = run_search(cfg, include_seen=include_seen, limit=limit)
    print(f"Done: scanned {r['scanned']} jobs, {r['total_matches']} matches.")
    return {"kind": "search", "profile": profile, "scanned": r["scanned"], "matches": r["total_matches"],
            "shown": len(r["results"]), "report": r["html"].name}


MANUAL_REASONS = {
    "external": "applies on the company's website",
    "needs_manual": "needs answers to recruiter questions",
    "failed": "couldn't be applied automatically",
}
MAX_TABS = 10


def task_apply(profile: str, job_ids: list[str]):
    cfg = load_config(profile)
    applied = load_json(APPLIED_FILE, {})
    chosen = [Job(**d) for d in load_json(latest_file(cfg), []) if d["job_id"] in job_ids]
    auto = [j for j in chosen if can_auto_apply(cfg, j) and j.job_id not in applied]
    cap = cfg.get("apply", {}).get("max_per_run", 10)
    skipped = auto[cap:]

    def manual_item(j: Job, status: str) -> dict:
        return {"title": j.title, "company": j.company, "url": j.url, "reason": MANUAL_REASONS[status]}

    manual = [manual_item(j, "external" if j.external_apply else "needs_manual")
              for j in chosen if not can_auto_apply(cfg, j)]
    print(f"You ticked {len(chosen)} job(s): {len(auto[:cap])} can be applied automatically, "
          f"{len(manual)} need you to finish them yourself.")

    results = []
    if auto[:cap]:
        for j, status in apply_jobs(auto[:cap]):
            results.append({"title": j.title, "company": j.company, "url": j.url, "status": status})
            if status in MANUAL_REASONS:
                manual.append(manual_item(j, status))

    if manual:
        print(f"Opening {min(len(manual), MAX_TABS)} job(s) in your browser so you can finish applying:")
        for m in manual[:MAX_TABS]:
            print(f"  {m['title']} @ {m['company']} ({m['reason']})")
            webbrowser.open_new_tab(m["url"])
    return {"kind": "apply", "profile": profile, "results": results, "manual": manual,
            "applied": sum(1 for r in results if r["status"] == "applied"),
            "opened": min(len(manual), MAX_TABS), "over_limit": len(skipped), "limit": cap}


def task_contacts(profile: str, top: int):
    from contacts import build_rows, fetch_details, resume_name, write_contacts

    cfg = load_config(profile)
    jobs = [Job(**d) for d in load_json(latest_file(cfg), [])][:top]
    if not jobs:
        raise SystemExit("No matches yet. Run a search first.")
    cache_file = DATA / "job_details.json"
    details = fetch_details(jobs, load_json(cache_file, {}), headless=cfg["search"].get("headless", False))
    save_json(cache_file, details)
    name = (cfg.get("outreach") or {}).get("name") or resume_name(find_resume(ROOT, cfg.get("resume_path")))
    rows = build_rows(jobs, details, cfg, name)
    prefix = "contacts" if profile == DEFAULT_PROFILE else f"{profile}_contacts"
    _, html_path = write_contacts(rows, hunt.OUTPUT, prefix)
    return {"kind": "contacts", "profile": profile, "rows": rows, "report": html_path.name}


def task_login():
    with Naukri(headless=False) as n:
        ok = n.login()
    if not ok:
        raise SystemExit("Not logged in. Try again and finish the login in the Chrome window.")
    return {"kind": "login", "logged_in": True}


# ---------------------------------------------------------------- actions (quick, no browser)

def save_prefs(body: dict) -> dict:
    profile = body.get("profile") or DEFAULT_PROFILE
    values = {k: body[k] for k in ("skills", "roles", "locations", "work_modes", "job_types", "experience_years")
              if k in body}
    if "roles" in values and not values["roles"]:
        raise ValueError("Add at least one job title to search for.")
    if values.get("experience_years") in ("", None):
        values.pop("experience_years", None)
    apply_overrides({"search": {}}, **values)  # validates before anything is saved
    for key, value in values.items():
        set_pref(profile, key, int(value) if key == "experience_years" else value)
    return {"ok": True, "prefs": summary(load_config(profile))}


def upload_resume(filename: str, data: bytes, update_skills: bool) -> dict:
    name = re.sub(r"[^\w.\- ]", "_", filename.replace("\\", "/").rsplit("/", 1)[-1]).strip() or "resume.pdf"
    if not name.lower().endswith(".pdf") or not data.startswith(b"%PDF"):
        raise ValueError("Please choose a PDF file.")
    RESUMES_DIR.mkdir(exist_ok=True)
    dest = RESUMES_DIR / name
    dest.write_bytes(data)
    _, detected = use_resume(str(dest), update_skills=update_skills)
    return {"ok": True, "resume": name, "detected": detected}


def resume_suggestions(profile: str) -> dict:
    """Skills, experience and job titles read from the resume; nothing is saved."""
    cfg = load_config(profile)
    text = read_resume(find_resume(ROOT, cfg.get("resume_path")))
    return {"skills": detect_skills(text), "experience_years": detect_experience(text), "roles": detect_roles(text)}


def detect_resume_skills(profile: str) -> dict:
    cfg = load_config(profile)
    detected = detect_skills(read_resume(find_resume(ROOT, cfg.get("resume_path"))))
    if not detected:
        raise ValueError("No known skills found in the resume.")
    set_pref(profile, "skills", detected)
    return {"ok": True, "skills": detected}


def save_login(body: dict) -> dict:
    email, password = (body.get("email") or "").strip(), body.get("password") or ""
    if not email or not password:
        raise ValueError("Enter both your Naukri email and password.")
    save_credentials(email, password)
    return {"ok": True}


# ---------------------------------------------------------------- HTTP

class Handler(BaseHTTPRequestHandler):
    server_version = "NaukriJobHunter"

    def log_message(self, *args) -> None:
        pass

    def _allowed_host(self) -> bool:
        # Blocks DNS-rebinding: only answer requests addressed to this computer.
        host = (self.headers.get("Host") or "").rsplit(":", 1)[0]
        return host in ("127.0.0.1", "localhost")

    def _send(self, code: int, body: bytes, ctype: str) -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, data, code: int = 200) -> None:
        self._send(code, json.dumps(data, default=str).encode(), "application/json")

    def do_GET(self) -> None:
        if not self._allowed_host():
            return self._send(403, b"Forbidden", "text/plain")
        url = urlparse(self.path)
        query = {k: v[0] for k, v in parse_qs(url.query).items()}
        try:
            if url.path in ("/", "/index.html"):
                return self._send(200, INDEX.read_bytes(), "text/html; charset=utf-8")
            if url.path == "/api/ping":
                return self._json({"app": APP_ID})
            if url.path == "/api/state":
                return self._json(get_state())
            if url.path == "/api/task":
                return self._json(TASK.snapshot())
            if url.path == "/api/jobs":
                return self._json(get_jobs(query.get("profile", DEFAULT_PROFILE)))
            if url.path.startswith("/output/"):
                name = url.path.removeprefix("/output/")
                path = hunt.OUTPUT / name
                if re.fullmatch(r"[\w.\-]+\.(html|csv)", name) and path.is_file():
                    ctype = "text/html; charset=utf-8" if name.endswith(".html") else "text/csv; charset=utf-8"
                    return self._send(200, path.read_bytes(), ctype)
            self._send(404, b"Not found", "text/plain")
        except Exception as e:
            self._json({"error": str(e)}, 500)

    def do_POST(self) -> None:
        # Custom header = browsers refuse to send this cross-site without a CORS preflight we never allow.
        if not self._allowed_host() or self.headers.get("X-Requested-With") != APP_ID:
            return self._send(403, b"Forbidden", "text/plain")
        url = urlparse(self.path)
        query = {k: v[0] for k, v in parse_qs(url.query).items()}
        length = int(self.headers.get("Content-Length") or 0)
        if length > MAX_UPLOAD:
            return self._json({"error": "File too large (max 20 MB)."}, 413)
        raw = self.rfile.read(length) if length else b""
        try:
            if url.path == "/api/resume":
                return self._json(upload_resume(query.get("name", ""), raw, query.get("update_skills") == "1"))
            body = json.loads(raw or b"{}")
            profile = body.get("profile") or DEFAULT_PROFILE
            if url.path == "/api/prefs":
                return self._json(save_prefs(body))
            if url.path == "/api/skills/detect":
                return self._json(detect_resume_skills(profile))
            if url.path == "/api/resume/sync":
                return self._json(resume_suggestions(profile))
            if url.path == "/api/login-details":
                return self._json(save_login(body))
            if url.path == "/api/search":
                limit = int(body["limit"]) if body.get("limit") else None
                return self._json({"task": TASK.start("Searching Naukri", lambda: task_search(
                    profile, bool(body.get("include_seen")), limit))})
            if url.path == "/api/apply":
                ids = [str(i) for i in body.get("job_ids", [])]
                if not ids:
                    raise ValueError("Tick the jobs you want to apply to first.")
                return self._json({"task": TASK.start("Applying", lambda: task_apply(profile, ids))})
            if url.path == "/api/contacts":
                top = int(body.get("top") or 25)
                return self._json({"task": TASK.start("Collecting contacts", lambda: task_contacts(profile, top))})
            if url.path == "/api/login":
                return self._json({"task": TASK.start("Logging in to Naukri", task_login)})
            if url.path == "/api/shutdown":
                self._json({"ok": True})
                threading.Thread(target=self.server.shutdown, daemon=True).start()
                return
            self._json({"error": "Unknown action"}, 404)
        except Busy as e:
            self._json({"error": str(e)}, 409)
        except (ValueError, FileNotFoundError, KeyError) as e:
            self._json({"error": str(e)}, 400)
        except Exception as e:
            self._json({"error": str(e)}, 500)


def _already_running(port: int) -> bool:
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/ping", timeout=1) as r:
            return json.loads(r.read()).get("app") == APP_ID
    except Exception:
        return False


def serve(port: int = DEFAULT_PORT, open_browser: bool = True) -> None:
    if not config_path(DEFAULT_PROFILE).exists():
        _copy_examples()
    if _already_running(port):
        print(f"Already running: http://127.0.0.1:{port}")
        if open_browser:
            webbrowser.open(f"http://127.0.0.1:{port}/")
        return
    server = None
    for candidate in range(port, port + 20):
        try:
            server = ThreadingHTTPServer(("127.0.0.1", candidate), Handler)
            break
        except OSError:
            continue
    if server is None:
        raise SystemExit(f"No free port between {port} and {port + 19}.")
    url = f"http://127.0.0.1:{server.server_address[1]}/"
    sys.stdout, sys.stderr = _TaskOutput(sys.stdout), _TaskOutput(sys.stderr)
    print(f"\nNaukri Job Hunter is open in your browser: {url}")
    print("Keep this window open while you use it. To stop: close this window or press Ctrl+C.\n")
    if open_browser:
        threading.Timer(0.8, webbrowser.open, args=(url,)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        sys.stdout, sys.stderr = sys.stdout.original, sys.stderr.original
        print("Stopped.")
