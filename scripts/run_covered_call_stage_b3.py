"""Run the frozen-timing strike-distance surfaces for Stage B3."""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
import os
from pathlib import Path

TIMING = {"NVDA": "ALWAYS_SELL", "SPY": "TREND", "QQQ": "TREND_OVEREXTENSION",
          "AMD": "STRONG_UPTREND_NO_SELL"}
REPRESENTATIVE = {"NVDA": (14, .20), "SPY": (14, .20), "QQQ": (21, .15), "AMD": (21, .20)}
OTM = (1.02, 1.03, 1.05, 1.075, 1.10, 1.125, 1.15, 1.20)
ATR = (0.5, 1.0, 1.5, 2.0, 2.5, 3.0, 4.0)
OUT = Path(os.environ.get("PCS_STAGE_B3_OUT", "data/staging/covered_call_stage_b3"))


def _worker(symbol: str, kind: str, value: float, path: Path) -> None:
    sys.path.insert(0, str(Path(__file__).parent))
    from run_covered_call_stage_a1_non_iv import main
    dte, delta = REPRESENTATIVE[symbol]
    kwargs = ({"selection_method": "MONEYNESS", "target_moneyness": value}
              if kind == "otm" else
              {"selection_method": "ATR", "target_atr_distance": value})
    report = main(tickers=(symbol,), target_dte=dte, target_delta=delta,
                  families=(TIMING[symbol],), **kwargs)
    path.write_text(json.dumps(report["tickers"][symbol], default=str), encoding="utf-8")


def run(symbols=("NVDA", "SPY", "QQQ", "AMD")) -> dict:
    OUT.mkdir(parents=True, exist_ok=True)
    checkpoint = OUT / "strike_surface_permanent_shares.json"
    try:
        result = json.loads(checkpoint.read_text(encoding="utf-8")) if checkpoint.exists() else {}
    except (OSError, json.JSONDecodeError):
        result = {}
    for symbol in symbols:
        cells = result.setdefault(symbol, {})
        for kind, values in (("otm", OTM), ("atr", ATR)):
            for value in values:
                key = f"{kind}:{value}"
                if key in cells:
                    continue
                with tempfile.TemporaryDirectory(prefix="pcs_b3_", dir=str(OUT)) as temp:
                    cell = Path(temp) / "cell.json"
                    completed = subprocess.run(
                        [sys.executable, str(Path(__file__).resolve()), "--worker",
                         symbol, kind, str(value), str(cell)], cwd=Path.cwd(), check=False)
                    if completed.returncode != 0 or not cell.exists():
                        raise RuntimeError(f"B3_CELL_FAILED:{symbol}:{key}:exit={completed.returncode}")
                    cells[key] = json.loads(cell.read_text(encoding="utf-8"))
                checkpoint.write_text(json.dumps(result, indent=2, default=str), encoding="utf-8")
    checkpoint.write_text(json.dumps(result, indent=2, default=str), encoding="utf-8")
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--worker", nargs=4)
    args = parser.parse_args()
    if args.worker:
        _worker(args.worker[0], args.worker[1], float(args.worker[2]), Path(args.worker[3]))
    else:
        print(json.dumps(run(), indent=2, default=str))
