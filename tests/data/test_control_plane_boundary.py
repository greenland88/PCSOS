from pathlib import Path


def test_non_adapter_consumers_do_not_directly_bind_providers():
    root = Path(__file__).parents[2] / "src" / "pcs"
    # The CLI is an ingestion orchestration entrypoint; it may construct an
    # adapter, while strategy/research consumers may not.
    allowed = {root / "data", root / "cli.py"}
    forbidden_tokens = ("MassiveCompatibleClient", "PCSClickHouseClient", "import yfinance", "import yfinance as")
    violations = []
    for path in root.rglob("*.py"):
        if any(path == item or item in path.parents for item in allowed):
            continue
        text = path.read_text(encoding="utf-8")
        for token in forbidden_tokens:
            if token in text:
                violations.append(f"{path}:{token}")
    assert not violations, "direct provider binding outside pcs.data adapters: " + "; ".join(violations)
