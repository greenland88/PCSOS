"""Serial permanent-shares Stage B1 DTE surface."""
import json
import argparse
import subprocess
import sys
import tempfile
import os
from pathlib import Path
from run_covered_call_stage_a1_non_iv import main

TIMING = {"NVDA": "ALWAYS_SELL", "SPY": "TREND", "QQQ": "TREND_OVEREXTENSION",
          "AMD": "STRONG_UPTREND_NO_SELL"}
DTE = (7, 10, 14, 21, 30, 35, 45, 60)
OUT = Path(os.environ.get("PCS_STAGE_B1_OUT", "data/staging/covered_call_stage_b1"))

def _worker(symbol, dte, path):
    report = main(tickers=(symbol,), target_dte=dte, target_delta=.20,
                  families=(TIMING[symbol],))
    path.write_text(json.dumps(report["tickers"][symbol], default=str), encoding="utf-8")

def run(symbols=("NVDA", "SPY", "QQQ", "AMD")):
    OUT.mkdir(parents=True, exist_ok=True)
    checkpoint = OUT / "dte_surface_permanent_shares.json"
    try:
        result = json.loads(checkpoint.read_text(encoding="utf-8")) if checkpoint.exists() else {}
    except (OSError, json.JSONDecodeError):
        result = {}
    for symbol in symbols:
        cells = result.setdefault(symbol, {})
        for dte in DTE:
            if str(dte) in cells:
                continue
            with tempfile.TemporaryDirectory(prefix="pcs_b1_", dir=str(OUT)) as temp:
                cell = Path(temp) / "cell.json"
                completed = subprocess.run(
                    [sys.executable, str(Path(__file__).resolve()), "--worker",
                     symbol, str(dte), str(cell)], cwd=Path.cwd(), check=False)
                if completed.returncode != 0 or not cell.exists():
                    raise RuntimeError(f"B1_CELL_FAILED:{symbol}:{dte}:exit={completed.returncode}")
                cells[str(dte)] = json.loads(cell.read_text(encoding="utf-8"))
            checkpoint.write_text(json.dumps(result, indent=2, default=str), encoding="utf-8")
    checkpoint.write_text(json.dumps(result, indent=2, default=str), encoding="utf-8")
    return result

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--worker", nargs=3)
    args = parser.parse_args()
    if args.worker:
        _worker(args.worker[0], int(args.worker[1]), Path(args.worker[2]))
    else:
        print(json.dumps(run(), indent=2, default=str))
