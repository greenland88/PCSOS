from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class EntryContextResult:
    available: bool
    entry_context_state: str | None
    blocking_gates: tuple[str, ...] = ()
    positive_gates: tuple[str, ...] = ()
    reasons: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()


def build_entry_context(trend_gate_result, pullback_gate_result, strike_gate_result) -> EntryContextResult:
    gates = {
        "trend_gate": trend_gate_result,
        "pullback_gate": pullback_gate_result,
        "strike_gate": strike_gate_result,
    }
    warnings = []
    unavailable = []
    for name, result in gates.items():
        warnings.extend(getattr(result, "warnings", ()) or ())
        if result is None or not getattr(result, "available", False):
            unavailable.append(f"{name}_unavailable")
    if unavailable:
        warnings.extend(unavailable)
        return EntryContextResult(False, None, warnings=tuple(dict.fromkeys(warnings)))

    states = {name: result.__getattribute__("trend_gate_result" if name == "trend_gate" else "pullback_gate_result" if name == "pullback_gate" else "strike_gate_result") for name, result in gates.items()}
    rejected = tuple(name for name, state in states.items() if state == "REJECT")
    if rejected:
        reasons = tuple(f"{name}_reject" for name in rejected)
        return EntryContextResult(True, "REJECT", blocking_gates=rejected, reasons=reasons, warnings=tuple(dict.fromkeys(warnings)))

    waiting = tuple(name for name, state in states.items() if state in {"WATCH", "WAIT", "MARGINAL"})
    passed = tuple(name for name, state in states.items() if state == "PASS")
    if waiting:
        reasons = tuple(f"{name}_waiting" for name in waiting)
        return EntryContextResult(True, "WAIT", blocking_gates=waiting, positive_gates=passed, reasons=reasons, warnings=tuple(dict.fromkeys(warnings)))

    if all(state == "PASS" for state in states.values()):
        return EntryContextResult(True, "READY", positive_gates=tuple(states), reasons=("all_entry_context_gates_pass",), warnings=tuple(dict.fromkeys(warnings)))
    return EntryContextResult(True, "WAIT", blocking_gates=tuple(states), reasons=("unrecognized_gate_state",), warnings=tuple(dict.fromkeys(warnings)))
