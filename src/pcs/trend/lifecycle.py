"""Small shared causal lifecycle primitives, independent of setup family."""


def pending_sessions(sessions, start, through, committed):
    """Display start never skips sessions after an existing commit boundary."""
    return [s for s in sessions if s <= through and
            (s > committed if committed else s >= start)]


def committed_days(days, boundary):
    return [d for d in days if boundary and d.session <= boundary]


def required_conjunction(conditions):
    values = [c.predicate_value for c in conditions if c.role != 'DIAGNOSTIC']
    return False if False in values else None if None in values else True


def requested_applicability(*, requested, evidence, semantics, eligible, entry_end):
    """An expired window is known; otherwise a different EOD needs evidence."""
    if semantics == 'HISTORICAL':
        return eligible
    if entry_end and requested > entry_end:
        return False
    if requested != evidence:
        return None
    return eligible
