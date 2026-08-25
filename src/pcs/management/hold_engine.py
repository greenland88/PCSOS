from pcs.models.decision import Action


def hold(reason: str = "rules favor holding"):
    return Action.HOLD, reason

