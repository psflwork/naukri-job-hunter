"""Job types, work modes and their aliases / detection keywords."""

JOB_TYPES = ["full_time", "part_time", "freelance", "contract"]
GIG_TYPES = ["part_time", "freelance", "contract"]
WORK_MODES = ["remote", "hybrid", "office"]

JOB_TYPE_LABELS = {"full_time": "Full-time", "part_time": "Part-time", "freelance": "Freelance", "contract": "Contract"}

# Search-keyword prefix used when hunting only gig types, e.g. "freelance python developer".
JOB_TYPE_QUERY_PREFIX = {"part_time": "part time", "freelance": "freelance", "contract": "contract"}

_JOB_TYPE_ALIASES = {
    "full_time": "full_time", "full-time": "full_time", "full time": "full_time", "fulltime": "full_time",
    "permanent": "full_time", "regular": "full_time",
    "part_time": "part_time", "part-time": "part_time", "part time": "part_time", "parttime": "part_time",
    "freelance": "freelance", "freelancer": "freelance", "freelancing": "freelance",
    "contract": "contract", "contractual": "contract", "c2h": "contract",
}
_WORK_MODE_ALIASES = {
    "remote": "remote", "wfh": "remote", "work from home": "remote",
    "hybrid": "hybrid",
    "office": "office", "onsite": "office", "on-site": "office", "on site": "office", "wfo": "office",
    "work from office": "office",
}

# Phrases that mark a job as a given type (searched in title, tags and description).
DEFAULT_JOB_TYPE_KEYWORDS = {
    "part_time": [
        "part time", "part-time", "few hours", "weekends only", "flexible hours", "flexible timings",
        "secondary income", "side income", "second job", "2nd job", "moonlighting",
    ],
    "freelance": [
        "freelance", "freelancer", "freelancing", "independent consultant", "project based",
        "project-based", "hourly", "per hour",
    ],
    "contract": [
        "contractual", "contract role", "contract position", "contract basis", "contract opportunity",
        "contract job", "contract engagement", "contract duration", "contract rate", "type contract",
        "month contract", "months contract", "month engagement", "months engagement", "contract to hire", "c2h",
    ],
}
# Words that only count when they appear in the job title (too noisy in descriptions).
DEFAULT_TITLE_KEYWORDS = {"contract": "contract", "weekend": "part_time"}
# Removed before detection so they don't create false signals.
DEFAULT_IGNORE_PHRASES = [
    "contract testing", "smart contract", "smart contracts", "if on contract", "contract management",
    "weekend work", "weekend support", "no freelancers", "not freelance", "no part time",
]


def _as_list(values) -> list[str]:
    if values is None:
        return []
    if isinstance(values, str):
        values = values.split(",")
    return [str(v).strip().lower() for v in values if str(v).strip()]


def normalize_job_types(values) -> list[str]:
    """['Full-time', 'gig'] -> ['full_time', 'part_time', 'freelance', 'contract']; 'any' -> []."""
    result: list[str] = []
    for v in _as_list(values):
        if v in ("any", "all"):
            return []
        mapped = GIG_TYPES if v in ("gig", "gigs", "side") else [_JOB_TYPE_ALIASES.get(v)]
        if None in mapped:
            raise ValueError(f"Unknown job type '{v}'. Use: full-time, part-time, freelance, contract, gig, any")
        result += [m for m in mapped if m not in result]
    return result


def normalize_work_modes(values) -> list[str]:
    """['WFH', 'hybrid'] -> ['remote', 'hybrid']; 'any' -> []."""
    result: list[str] = []
    for v in _as_list(values):
        if v in ("any", "all"):
            return []
        mapped = _WORK_MODE_ALIASES.get(v)
        if not mapped:
            raise ValueError(f"Unknown work mode '{v}'. Use: remote, hybrid, office, any")
        if mapped not in result:
            result.append(mapped)
    return result


def describe(values: list[str], labels: dict | None = None, empty: str = "any") -> str:
    if not values:
        return empty
    return ", ".join((labels or {}).get(v, v) for v in values)
