from pathlib import Path
import ast
import yaml


def _provider_imports(source):
    """Inspect imports, including aliases, without matching comments/docstrings."""
    providers = {"MassiveCompatibleClient", "PCSClickHouseClient", "YahooDailyFetcher"}
    modules = {"yfinance", "pcs.data.massive_client", "pcs.data.clickhouse"}
    found = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            found.extend(alias.name for alias in node.names
                         if alias.name in modules or alias.name.startswith("yfinance."))
        elif isinstance(node, ast.ImportFrom):
            if node.module == "yfinance" or (node.module or "").startswith("yfinance."):
                found.append(node.module)
            else:
                found.extend(alias.name for alias in node.names if alias.name in providers)
    return found


def test_non_adapter_consumers_do_not_directly_bind_providers():
    repository = Path(__file__).parents[2]
    root = repository / "src"
    registry = yaml.safe_load((repository / "config/market_data_source_registry.yaml").read_text())
    allowed = {source["adapter"].rsplit(".", 1)[0]
               for sources in registry["sources"].values() for source in sources}
    # Explicit orchestration/re-export boundaries, not a blanket pcs.data exemption.
    allowed.update({"pcs.data.control_plane", "pcs.data.__init__", "pcs.data.market_data_service"})
    violations = []
    for path in (root / "pcs").rglob("*.py"):
        module = ".".join(path.relative_to(root).with_suffix("").parts)
        if module in allowed:
            continue
        for provider in _provider_imports(path.read_text(encoding="utf-8")):
            violations.append(f"{module}:{provider}")
    assert not violations, "direct provider binding outside pcs.data adapters: " + "; ".join(violations)


def test_boundary_detects_aliased_imports_without_comment_false_positives():
    assert _provider_imports("from pcs.data.clickhouse import PCSClickHouseClient as Client")
    assert _provider_imports("import yfinance as yf")
    assert _provider_imports("from yfinance import download as get_prices")
    assert not _provider_imports('# import yfinance\n"PCSClickHouseClient"')
