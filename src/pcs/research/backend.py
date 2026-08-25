"""Single research option-backend resolution point."""
VALID_BACKENDS={"csv","duckdb"}
DEFAULT_RESEARCH_BACKEND="duckdb"
def resolve_option_backend(requested_backend=None):
    backend=(requested_backend or DEFAULT_RESEARCH_BACKEND).lower()
    if backend not in VALID_BACKENDS: raise ValueError(f"unsupported research backend: {backend}")
    return backend
