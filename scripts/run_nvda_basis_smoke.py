"""Run exactly the six post-price-basis NVDA smoke cells."""
import json
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
from run_covered_call_stage_a1_non_iv import main

OUT = Path("data/staging/covered_call_stage_b3/nvda_price_basis_smoke.json")
CELLS = (("OTM_7_5", "MONEYNESS", 1.075), ("OTM_10", "MONEYNESS", 1.10),
         ("OTM_15", "MONEYNESS", 1.15), ("ATR_2_5", "ATR", 2.5),
         ("ATR_3_0", "ATR", 3.0), ("ATR_4_0", "ATR", 4.0))

def run():
    output = {"status": "RUNNING", "cells": {}}
    OUT.write_text(json.dumps(output, indent=2), encoding="utf-8")
    for name, mode, target in CELLS:
        kwargs = {"selection_method": mode}
        kwargs["target_moneyness" if mode == "MONEYNESS" else "target_atr_distance"] = target
        report = main(tickers=("NVDA",), target_dte=14, target_delta=.20,
                      families=("ALWAYS_SELL",), **kwargs)
        ticker = report["tickers"]["NVDA"]
        output["cells"][name] = ticker
        output["status"] = "RUNNING"
        OUT.write_text(json.dumps(output, indent=2, default=str), encoding="utf-8")
    output["status"] = "COMPLETED"
    OUT.write_text(json.dumps(output, indent=2, default=str), encoding="utf-8")
    return output

if __name__ == "__main__":
    print(json.dumps(run(), indent=2, default=str))
