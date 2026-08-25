from dataclasses import dataclass, asdict
from datetime import datetime, timezone
import uuid

@dataclass
class AgentResponse:
    module: str
    version: str
    request_id: str
    status: str
    reason_code: str
    reason_codes: list[str] = None
    data: object = None
    symbol: str = None
    data_timestamp: str = None
    calculation_version: str = None
    as_of: str = None
    run_id: str = None

    def to_dict(self): return asdict(self)
    def to_json(self):
        import json
        return json.dumps(self.to_dict(), default=str, sort_keys=True)

def response(status, reason_code, data=None, symbol=None, calculation_version=None, as_of=None, run_id=None, reason_codes=None):
    codes = list(reason_codes or ([reason_code] if reason_code else []))
    return AgentResponse("pcs.agent", "v1", str(uuid.uuid4()), status, reason_code, codes, data, symbol, datetime.now(timezone.utc).isoformat(), calculation_version, as_of, run_id)
