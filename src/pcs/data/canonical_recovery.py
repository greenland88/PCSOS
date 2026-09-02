"""Single service boundary for canonical recovery operations."""
from __future__ import annotations
from enum import StrEnum
from typing import Any
from .access import PCSDataAccess
from .canonical_generations import RepairPlan, REPAIR_ACTION_POLICY, plan_canonical_repair, persist_repair_plan, apply_repair_plan, list_generations

class TrackingState(StrEnum): TRACKED="TRACKED"; UNTRACKED="UNTRACKED"; LEGACY="LEGACY"; ORPHAN="ORPHAN"; MISSING="MISSING"
class IntegrityState(StrEnum): VALID="VALID"; HASH_INVALID="HASH_INVALID"; SCHEMA_INVALID="SCHEMA_INVALID"; PARTITION_INVALID="PARTITION_INVALID"; PK_INVALID="PK_INVALID"
class ContentRelation(StrEnum): EXACT_EQUAL="EXACT_EQUAL"; STRICT_SUBSET="STRICT_SUBSET"; STRICT_SUPERSET="STRICT_SUPERSET"; OVERLAPPING_CONFLICT="OVERLAPPING_CONFLICT"; DISJOINT="DISJOINT"

class CanonicalRecoveryService:
    def __init__(self, access: PCSDataAccess | None = None, plan_root="data/manifests/repair_plans"): self.access=access or PCSDataAccess(); self.plan_root=plan_root
    def plan(self, **kwargs) -> RepairPlan:
        plan=plan_canonical_repair(data_access=self.access,**kwargs); persist_repair_plan(plan,self.plan_root); return plan
    def apply(self, plan_id: str) -> dict[str, Any]: return apply_repair_plan(plan_id,data_access=self.access,root=self.plan_root)
    def generations(self, **kwargs): return list_generations(data_access=self.access,**kwargs)
    def rollback(self, *, dataset, symbol, year, quarter): return self.access.rollback_generation(dataset,symbol,f"year={year}/quarter={quarter}")
