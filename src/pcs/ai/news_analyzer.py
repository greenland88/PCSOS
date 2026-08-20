import json


def analyze_news_stub(text: str) -> str:
    return json.dumps({"news_risk": 0, "summary": "No AI provider configured; deterministic rules unchanged."})

