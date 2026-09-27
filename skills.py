"""Skill vocabulary used to detect skills from a resume."""

import re

SKILL_VOCABULARY = [
    # Languages
    "python", "java", "javascript", "typescript", "golang", "rust", "c++", "c#", "kotlin", "swift",
    "scala", "ruby", "php", "sql", "bash",
    # Frontend
    "react", "react.js", "reactjs", "next.js", "angular", "vue", "vue.js", "svelte", "redux", "html5", "css",
    "sass", "tailwind", "webpack", "micro frontend", "microfrontend", "module federation", "single spa",
    # Backend / frameworks
    "node.js", "nodejs", "express.js", "nestjs", "fastapi", "django", "flask", "spring boot",
    ".net", "asp.net", "laravel", "rails", "graphql", "rest api", "grpc", "microservices",
    # Mobile
    "android", "ios", "react native", "flutter",
    # Data
    "postgresql", "mysql", "mongodb", "redis", "elasticsearch", "cassandra", "dynamodb", "oracle",
    "snowflake", "databricks", "spark", "pyspark", "kafka", "airflow", "hadoop", "bigquery", "dbt",
    "tableau", "power bi", "etl", "data engineering", "data modeling",
    # Cloud / DevOps
    "aws", "azure", "gcp", "azure devops", "kubernetes", "docker", "terraform", "ansible", "jenkins",
    "github actions", "ci/cd", "devops", "linux", "serverless", "lambda", "helm", "prometheus", "grafana",
    # AI / ML
    "machine learning", "deep learning", "nlp", "computer vision", "pytorch", "tensorflow", "scikit-learn",
    "pandas", "numpy", "llm", "generative ai", "genai", "openai", "langchain", "llamaindex", "rag",
    "vector database", "ai agents", "prompt engineering", "mcp", "hugging face", "mlops",
    # Practices / architecture
    "system design", "solution architecture", "distributed systems", "event driven", "design patterns",
    "tdd", "unit testing", "selenium", "playwright", "cypress", "oauth",
    # Leadership / process
    "agile", "scrum", "kanban", "jira", "people management", "team management", "stakeholder management",
    "mentoring", "project management", "program management", "product management",
    "release management", "sdlc",
]


def _contains(text: str, term: str) -> bool:
    return re.search(rf"(?<![a-z0-9]){re.escape(term)}(?![a-z0-9+#])", text) is not None


def detect_skills(resume_text: str) -> list[str]:
    """Vocabulary skills that appear in the resume, in vocabulary order."""
    text = resume_text.lower()
    return [s for s in SKILL_VOCABULARY if _contains(text, s)]


def edit_list(current: list[str], text: str) -> list[str]:
    """Apply an edit string to a list.

    "a, b"        -> replace with [a, b]
    "+a, -b"      -> add a, remove b (entries without +/- are added too when any +/- is present)
    "" / "any"    -> []
    """
    text = text.strip()
    if text.lower() in ("", "any", "none", "clear"):
        return []
    parts = [p.strip() for p in text.split(",") if p.strip()]
    if not any(p[0] in "+-" for p in parts):
        return list(dict.fromkeys(p.lower() for p in parts))
    result = list(current)
    for p in parts:
        item = p.lstrip("+-").strip().lower()
        if p.startswith("-"):
            result = [x for x in result if x.lower() != item]
        elif item and item not in (x.lower() for x in result):
            result.append(item)
    return result
