"""Serial permanent-shares Stage B2 delta surface on frozen B1 DTE regions."""
import argparse
import json
import subprocess
import sys
import tempfile
import os
from pathlib import Path

TIMING = {"NVDA": "ALWAYS_SELL", "SPY": "TREND", "QQQ": "TREND_OVEREXTENSION",
          "AMD": "STRONG_UPTREND_NO_SELL"}
# Representative points from the broad B1 regions; B1 remains the authority
# for the region, this surface tests delta without a DTE/strategy cross-product.
REPRESENTATIVE_DTE = {"NVDA": 14, "SPY": 14, "QQQ": 21, "AMD": 21}
DELTAS = (0.05, 0.075, 0.10, 0.125, 0.15, 0.20, 0.25, 0.30)
OUT = Path(os.environ.get("PCS_STAGE_B2_OUT", "data/staging/covered_call_stage_b2"))

def _worker(symbol, delta, path):
    sys.path.insert(0, str(Path(__file__).parent))
    from run_covered_call_stage_a1_non_iv import main
    report = main(tickers=(symbol,), target_dte=REPRESENTATIVE_DTE[symbol],
                  target_delta=delta, families=(TIMING[symbol],))
    path.write_text(json.dumps(report["tickers"][symbol], default=str), encoding="utf-8")

def run(symbols=("NVDA", "SPY", "QQQ", "AMD")):
    OUT.mkdir(parents=True, exist_ok=True)
    checkpoint = OUT / "delta_surface_permanent_shares.json"
    try:
        result = json.loads(checkpoint.read_text(encoding="utf-8")) if checkpoint.exists() else {}
    except (OSError, json.JSONDecodeError):
        result = {}
    for symbol in symbols:
        cells = result.setdefault(symbol, {})
        for delta in DELTAS:
            key = str(delta)
            if key in cells:
                continue
            with tempfile.TemporaryDirectory(prefix="pcs_b2_", dir=str(OUT)) as temp:
                cell = Path(temp) / "cell.json"
                completed = subprocess.run(
                    [sys.executable, str(Path(__file__).resolve()), "--worker",
                     symbol, str(delta), str(cell)], cwd=Path.cwd(), check=False)
                if completed.returncode != 0 or not cell.exists():
                    raise RuntimeError(f"B2_CELL_FAILED:{symbol}:{delta}:exit={completed.returncode}")
                cells[key] = json.loads(cell.read_text(encoding="utf-8"))
            checkpoint.write_text(json.dumps(result, indent=2, default=str), encoding="utf-8")
    checkpoint.write_text(json.dumps(result, indent=2, default=str), encoding="utf-8")
    return result

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--worker", nargs=3)
    args = parser.parse_args()
    if args.worker:
        _worker(args.worker[0], float(args.worker[1]), Path(args.worker[2]))
    else:
        print(json.dumps(run(), indent=2, default=str))
