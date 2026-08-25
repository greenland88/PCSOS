import json


def analyze_thesis_stub(text: str) -> str:
    return json.dumps({"thesis_risk": 0, "summary": "No AI provider configured; deterministic rules unchanged."})

