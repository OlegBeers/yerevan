import json
from pathlib import Path

FIXTURES = Path(__file__).parent / "fixtures"


def fixture_text(rel: str) -> str:
    return (FIXTURES / rel).read_text(encoding="utf-8")


def fixture_json(rel: str):
    return json.loads(fixture_text(rel))
