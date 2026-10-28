from .base import IssuingClient, AirwallexError
from .mock import MockIssuingClient


def make_client(backend: str | None = None) -> IssuingClient:
    import os
    backend = (backend or os.environ.get("PASSGATE_BACKEND") or "mock").lower()
    if backend == "rest":
        from .rest import RestIssuingClient
        return RestIssuingClient.from_env()
    return MockIssuingClient()
