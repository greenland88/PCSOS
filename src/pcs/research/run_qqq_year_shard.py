import os
from .qqq_entry_discovery_v1 import run
year=os.environ.get('QQQ_YEAR_SHARD')
if year not in {'2020','2021','2022','2023'}: raise SystemExit('QQQ_YEAR_SHARD must be 2020-2023')
print(run(f'research_outputs/qqq_entry_discovery_agent_v1/rounds/phase_a_year_shards/year={year}', f'{year}-01-01', f'{year}-12-31'))
