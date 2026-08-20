from pcs.models.decision import Action


def close(reason: str):
    return Action.CLOSE, reason

