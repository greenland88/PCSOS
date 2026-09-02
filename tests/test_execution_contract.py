from pcs.execution_contract import ExecutionBlocked, execute_strategy_request


def test_recoverable_dependencies_continue_to_strategy_and_contract():
    def resolve(trace):
        trace.record("POSITION_CONTEXT", "AUTO_LOADED")
        trace.record("OPTIONS", "REFRESHED")
        trace.record("MARKET_CONTEXT", "BUILT")
        return {"ready": True, "data_status": "READY"}

    def execute(deps, trace):
        trace.record("STRATEGY", "EXECUTED")
        trace.record("CONTRACT_SELECTION", "EXECUTED")
        return {"strategy_decision": "WAIT", "strategy_evaluated": True,
                "contract_selection_evaluated": True}

    result = execute_strategy_request(strategy="PCS", symbol="HOOD", as_of="2026-08-18",
                                      dependency_resolver=resolve, strategy_executor=execute)
    assert result["system_status"] == "READY"
    assert result["strategy_evaluated"] is True
    assert result["contract_selection_evaluated"] is True


def test_missing_dependency_status_blocks_before_strategy():
    calls = []
    result = execute_strategy_request(
        strategy="PCS", symbol="HOOD", as_of="2026-08-18",
        dependency_resolver=lambda trace: {"ready": True},
        strategy_executor=lambda deps, trace: calls.append(True) or {"strategy_decision": "WAIT"},
    )
    assert result["action"] == "DATA_BLOCKED"
    assert result["strategy_decision"] == "NOT_RUN"
    assert calls == []


def test_executor_data_blocked_cannot_be_relabelled_ready():
    result = execute_strategy_request(
        strategy="PCS", symbol="HOOD", as_of="2026-08-18",
        dependency_resolver=lambda trace: {"data_status": "READY"},
        strategy_executor=lambda deps, trace: {
            "action": "DATA_BLOCKED", "data_reason": "QUOTE_STALE"
        },
    )
    assert result["system_status"] == "BLOCKED"
    assert result["strategy_decision"] == "NOT_RUN"
    assert result["strategy_evaluated"] is False


def test_external_blocker_is_not_disguised_as_strategy_result():
    result = execute_strategy_request(strategy="COVERED_CALL", symbol="NVDL", as_of="2026-08-18",
                                      dependency_resolver=lambda trace: (_ for _ in ()).throw(
                                          ExecutionBlocked("LIVE_OPTION_CHAIN_UNAVAILABLE")),
                                      strategy_executor=lambda deps, trace: {"strategy_decision": "WAIT"})
    assert result["system_status"] == "BLOCKED"
    assert result["strategy_decision"] == "NOT_RUN"
    assert result["strategy_evaluated"] is False
