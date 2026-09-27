"""Offline tests: no browser, no Naukri account, no personal files needed."""

import json
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

import pytest

import hunt
import preferences
import webapp
from matcher import Matcher, wanted_job_types, wanted_work_modes
from naukri import Job, extract_emails, search_url
from options import GIG_TYPES, normalize_job_types, normalize_work_modes
from skills import detect_skills, edit_list


def make_job(**kw) -> Job:
    base = dict(job_id="1", title="Python Developer", company="Acme", url="https://www.naukri.com/job-1",
                experience="5-10 Yrs", salary="", location="Remote", posted="1 day ago", posted_at="",
                tags=["python", "react"], description="", min_exp=5, max_exp=10, external_apply=False,
                has_questionnaire=False, rating="", query="python developer")
    base.update(kw)
    return Job(**base)


# ---------------------------------------------------------------- options

def test_normalize_job_types():
    assert normalize_job_types(["Full-time", "freelancer"]) == ["full_time", "freelance"]
    assert normalize_job_types("gig") == GIG_TYPES
    assert normalize_job_types(["any"]) == []
    with pytest.raises(ValueError):
        normalize_job_types(["weekly"])


def test_normalize_work_modes():
    assert normalize_work_modes(["WFH", "hybrid", "remote"]) == ["remote", "hybrid"]
    assert normalize_work_modes("any") == []
    with pytest.raises(ValueError):
        normalize_work_modes(["moon"])


def test_old_config_keys_still_work():
    assert wanted_work_modes({"search": {"remote_only": True}}) == ["remote"]
    assert wanted_work_modes({"search": {"remote_only": False}}) == []
    assert wanted_job_types({"require_gig_keywords": True}) == GIG_TYPES


# ---------------------------------------------------------------- skills

def test_edit_list():
    assert edit_list(["python", "angular"], "+kafka, -angular") == ["python", "kafka"]
    assert edit_list(["python"], "go, rust") == ["go", "rust"]
    assert edit_list(["python"], "any") == []


def test_detect_skills():
    found = detect_skills("built microservices in python and react on aws with docker")
    assert {"python", "react", "aws", "docker", "microservices"} <= set(found)


# ---------------------------------------------------------------- search

def test_search_url():
    url = search_url("python developer", 2, 10, ["hybrid", "remote"], 7, "Bengaluru")
    assert url.startswith("https://www.naukri.com/python-developer-jobs-in-bengaluru-2?")
    assert "wfhType=3" in url and "wfhType=2" in url and "experience=10" in url and "l=Bengaluru" in url


def test_build_queries_adds_gig_words():
    cfg = {"job_types": ["freelance", "contract"]}
    assert hunt.build_queries(cfg, ["python developer", "freelance react"]) == [
        "freelance python developer", "contract python developer", "freelance react"]
    assert hunt.build_queries({"job_types": []}, ["python developer"]) == ["python developer"]


def test_parse_selection():
    assert hunt.parse_selection("1,3,5-7", 6) == [0, 2, 4, 5]


def test_extract_emails_skips_images_and_leading_symbols():
    assert extract_emails("mail -hr@acme.com or logo@2x.png") == ["hr@acme.com"]


# ---------------------------------------------------------------- matching

def test_job_type_detection_and_filter():
    m = Matcher("python react aws", {"job_types": ["freelance"], "skills": ["python", "react"]})
    freelance = make_job(title="Freelance Python Developer", description="paid per hour")
    contract = make_job(job_id="2", title="Python Developer - Contract", description="6 months contract")
    testing = make_job(job_id="3", title="QA Engineer", description="contract testing with pact")
    assert not m.is_excluded(freelance) and freelance.job_types == ["freelance"]
    assert m.is_excluded(contract) and contract.job_types == ["contract"]
    m.is_excluded(testing)
    assert testing.job_types == ["full_time"]


def test_score_prefers_matching_skills():
    cfg = {"skills": ["python", "react", "aws"], "experience_years": 8, "strong_titles": ["python"]}
    m = Matcher("python react aws", cfg)
    good = make_job(tags=["python", "react", "aws"])
    weak = make_job(job_id="2", title="Java Developer", tags=["java"], min_exp=1, max_exp=3)
    assert m.score(good) > m.score(weak)


# ---------------------------------------------------------------- preferences

def test_preferences_overlay(tmp_path, monkeypatch):
    monkeypatch.setattr(preferences, "PREFS_FILE", tmp_path / "preferences.json")
    preferences.set_pref("side", "locations", ["Pune"])
    preferences.set_pref("side", "skills", ["python"])
    cfg = preferences.apply_saved({"search": {}, "skills": ["java"]}, "side")
    assert cfg["search"]["locations"] == ["Pune"] and cfg["skills"] == ["python"]
    other = preferences.apply_saved({"search": {}, "skills": ["java"]}, "default")
    assert "locations" not in other["search"] and other["skills"] == ["python"]


def test_clean_path_strips_quotes(tmp_path):
    assert preferences.clean_path(f"'{tmp_path}'") == tmp_path.resolve()


# ---------------------------------------------------------------- web app

@pytest.fixture
def server():
    srv = ThreadingHTTPServer(("127.0.0.1", 0), webapp.Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_address[1]}"
    srv.shutdown()
    srv.server_close()


def _post(url: str, body: dict, headers: dict) -> tuple[int, dict | str]:
    req = urllib.request.Request(url, data=json.dumps(body).encode(), headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()


def test_web_ping_and_page(server):
    with urllib.request.urlopen(f"{server}/api/ping") as r:
        assert json.loads(r.read()) == {"app": webapp.APP_ID}
    with urllib.request.urlopen(f"{server}/") as r:
        assert b"Naukri" in r.read()


def test_web_rejects_cross_site_posts(server):
    ok_header = {"X-Requested-With": webapp.APP_ID}
    assert _post(f"{server}/api/search", {}, {})[0] == 403
    assert _post(f"{server}/api/search", {}, {**ok_header, "Host": "evil.example"})[0] == 403


def test_web_validates_input(server):
    code, body = _post(f"{server}/api/apply", {"job_ids": []}, {"X-Requested-With": webapp.APP_ID})
    assert code == 400 and "Tick" in body


def test_web_output_blocks_path_traversal(server):
    with pytest.raises(urllib.error.HTTPError) as e:
        urllib.request.urlopen(f"{server}/output/..%2Fconfig.yaml")
    assert e.value.code == 404


def test_task_runner_captures_output_and_blocks_second_task():
    import sys
    import time

    task = webapp.TASK
    out, err = sys.stdout, sys.stderr
    sys.stdout, sys.stderr = webapp._TaskOutput(out), webapp._TaskOutput(err)
    gate = threading.Event()
    try:
        def work():
            print("working")
            gate.wait(2)
            return {"kind": "test"}
        task.start("Test", work)
        with pytest.raises(webapp.Busy):
            task.start("Again", work)
        gate.set()
        for _ in range(50):
            if not task.running:
                break
            time.sleep(0.05)
        snap = task.snapshot()
        assert snap["result"] == {"kind": "test"} and "working" in snap["log"]
    finally:
        sys.stdout, sys.stderr = out, err
