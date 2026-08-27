import pandas as pd

from pcs.research.stage4a_replay import audit_inputs


def test_empty_stage4a_artifact_is_never_ready():
    result = audit_inputs(pd.DataFrame())
    assert result.rows == 0
    assert result.lookahead_safe is False
    assert result.can_run_decision_engine is False
