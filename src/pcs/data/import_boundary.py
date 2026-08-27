"""Single public entrypoint policy for market-data imports."""

LEGACY_IMPORT_ENTRYPOINT_MESSAGE = (
    "LEGACY_IMPORT_ENTRYPOINT_DISABLED: use `python -m pcs.cli import-market-data` "
    "or pcs.data.control_plane.ensure_market_data()"
)


def reject_legacy_import_entrypoint() -> None:
    raise SystemExit(LEGACY_IMPORT_ENTRYPOINT_MESSAGE)
