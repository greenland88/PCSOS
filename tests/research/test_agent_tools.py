import json
from pcs.agent.models import response

def test_agent_response_is_structured_and_json_serializable():
    r=response("UNAVAILABLE","DATA_NOT_FOUND")
    assert r.module=="pcs.agent" and r.request_id and json.loads(r.to_json())["reason_code"]=="DATA_NOT_FOUND"
    payload = json.loads(r.to_json())
    assert "as_of" in payload and "run_id" in payload
