"""Run the already frozen 474 AMD candidates against onboarded v2."""
from pathlib import Path
exec(Path("scripts/run_vendor_txt_full_replay_regression.py").read_text(encoding="utf-8").replace(
    'TICKERS = ("AMD", "HOOD", "META")', 'TICKERS = ("AMD",)').replace(
    'PILOT = Path("data/parquet/options_v2_pilot_vendor_txt_20260820_run2")', 'PILOT = Path("data/parquet/options_v2_onboarding_amd_20260820")').replace(
    'OUT = Path("data/parquet/research/vendor_txt_full_replay_20260820")', 'OUT = Path("data/parquet/research/amd_onboarding_replay_20260820")').replace(
    'data/manifests/vendor_txt_full_replay_20260820.json', 'data/manifests/amd_onboarding_replay_20260820.json'))
