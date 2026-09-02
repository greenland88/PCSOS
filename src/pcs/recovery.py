"""System-wide capability health and bounded self-healing orchestration."""
from __future__ import annotations
from dataclasses import dataclass, asdict
from enum import StrEnum
from typing import Any
from uuid import uuid4
from pcs.data.access import PCSDataAccess
from pcs.data.control_plane import MarketDataControlPlane

class HealthStatus(StrEnum):
    READY="READY"; MISSING="MISSING"; STALE="STALE"; DEGRADED="DEGRADED"; CORRUPT="CORRUPT"; CONFLICTED="CONFLICTED"; UNAVAILABLE="UNAVAILABLE"; BLOCKED="BLOCKED"
class RepairMode(StrEnum):
    SAFE_AUTO="SAFE_AUTO"; REBUILD_AUTO="REBUILD_AUTO"; OWNER_APPROVAL="OWNER_APPROVAL"; FORBIDDEN="FORBIDDEN"

@dataclass(frozen=True)
class HealthNode:
    node: str; status: str; reason_codes: tuple[str,...]=(); repairable: bool=False; repair_strategy: str|None=None; operation_id: str|None=None; data_timestamp: str|None=None
    def to_dict(self): return asdict(self) | {"reason_codes": list(self.reason_codes)}
@dataclass(frozen=True)
class CapabilityResult:
    capability: str; status: str; dependencies: tuple[HealthNode,...]; repairs_attempted: tuple[dict,...]=(); repairs_succeeded: tuple[dict,...]=(); warnings: tuple[str,...]=(); blockers: tuple[str,...]=(); reason_codes: tuple[str,...]=(); operation_ids: tuple[str,...]=(); run_id: str=""; request_id: str=""
    def to_dict(self): return asdict(self) | {"dependencies":[x.to_dict() for x in self.dependencies],"repairs_attempted":list(self.repairs_attempted),"repairs_succeeded":list(self.repairs_succeeded),"warnings":list(self.warnings),"blockers":list(self.blockers),"reason_codes":list(self.reason_codes),"operation_ids":list(self.operation_ids)}

CAPABILITY_DEPENDENCIES = {
    "EOD_PCS_DECISION": ("CONFIG_READY","DAILY_CANONICAL_READY","OPTIONS_HISTORY_READY","MARKET_CONTEXT_READY","EVENT_CONTEXT_READY","STRATEGY_ENGINE_READY"),
    "LIVE_PCS_DECISION": ("CONFIG_READY","DAILY_CANONICAL_READY","OPTIONS_HISTORY_READY","MARKET_CONTEXT_READY","LIVE_UNDERLYING_READY","LIVE_OPTION_CHAIN_READY","EVENT_CONTEXT_READY","STRATEGY_ENGINE_READY"),
    "PORTFOLIO_SIZING": ("CONFIG_READY","PORTFOLIO_CONTEXT_READY"),
    "POSITION_MANAGEMENT": ("CONFIG_READY","DAILY_CANONICAL_READY","OPTIONS_HISTORY_READY","PORTFOLIO_CONTEXT_READY","STRATEGY_ENGINE_READY"),
}

class CanonicalRecoveryService:
    def __init__(self, access=None): self.access=access or PCSDataAccess()
    def ensure_market_data(self, symbol, as_of, *, repair_mode=RepairMode.SAFE_AUTO):
        result=MarketDataControlPlane(access=self.access).ensure_market_data({"symbol":symbol,"start":"2018-01-01","end":as_of,"datasets":("daily","options"),"consumer":"SYSTEM_RECOVERY"})
        status=str(result.status); codes=tuple(result.reason_codes or ())
        return HealthNode("OPTIONS_HISTORY_READY", HealthStatus.READY.value if status in {"READY","ALREADY_COMPLETE"} else HealthStatus.BLOCKED.value, codes, status not in {"BLOCKED"}, "REFRESH_CANONICAL", result.run_id, result.data_timestamp), result

class SystemHealthController:
    def __init__(self, access=None, max_recovery_passes=2): self.access=access or PCSDataAccess(); self.max_recovery_passes=max_recovery_passes; self.recovery=CanonicalRecoveryService(self.access)
    def ensure_capability(self, capability, symbol, as_of, *, repair_mode=RepairMode.SAFE_AUTO):
        run, request = uuid4().hex, uuid4().hex; nodes=[]; attempted=[]; succeeded=[]; blockers=[]; codes=[]
        for _ in range(self.max_recovery_passes):
            nodes=[]
            nodes.append(HealthNode("CONFIG_READY", HealthStatus.READY.value))
            try:
                node, result=self.recovery.ensure_market_data(symbol.upper(), as_of, repair_mode=repair_mode); nodes.append(node)
                if node.status == HealthStatus.READY.value: succeeded.append({"node":node.node,"action":"ENSURE_CANONICAL_DATA","result":"SUCCESS"})
                elif node.repairable and repair_mode in {RepairMode.SAFE_AUTO,RepairMode.REBUILD_AUTO}: attempted.append({"node":node.node,"action":node.repair_strategy}); codes.extend(node.reason_codes)
            except Exception as exc:
                nodes.append(HealthNode("OPTIONS_HISTORY_READY",HealthStatus.UNAVAILABLE.value,("CANONICAL_RECOVERY_FAILED",),False)); blockers.append(str(exc)); break
            # The controller owns the data preparation gate.  Downstream
            # strategy adapters are executed by the normal PCS orchestrator;
            # do not turn their absence from this low-level pass into a
            # fabricated infrastructure blocker.
            if capability != "EOD_PCS_DECISION":
                for name in CAPABILITY_DEPENDENCIES.get(capability, ()):
                    if name not in {x.node for x in nodes}: nodes.append(HealthNode(name, HealthStatus.BLOCKED.value,(f"{name}_ADAPTER_UNAVAILABLE",),False))
            break
        required={"CONFIG_READY", "OPTIONS_HISTORY_READY"} if capability == "EOD_PCS_DECISION" else set(CAPABILITY_DEPENDENCIES.get(capability,()))
        blockers.extend(x.node for x in nodes if x.node in required and x.status != HealthStatus.READY.value)
        status="READY" if not blockers else "BLOCKED"
        return CapabilityResult(capability,status,tuple(nodes),tuple(attempted),tuple(succeeded),blockers=tuple(dict.fromkeys(blockers)),reason_codes=tuple(dict.fromkeys(codes+blockers)),run_id=run,request_id=request,operation_ids=tuple(x.get("operation_id") for x in attempted if x.get("operation_id")))
