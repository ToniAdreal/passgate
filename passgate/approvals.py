"""Pending-approval store for human-in-the-loop decisions.

Pure data + state machine, no I/O. The approval *screen* (approval_server.py)
renders this store and routes approve/reject back into the agent; tests drive it
directly. Each Approval is a human-readable summary of why the agent paused; the
agent keeps the sensitive context (the proposed Intent) in memory, keyed by id.
"""
from __future__ import annotations

import time
import uuid
from dataclasses import asdict, dataclass, field


@dataclass
class Approval:
    """One item awaiting a human decision."""
    id: str
    kind: str                         # e.g. "renewal_price_rise"
    vendor: str
    summary: str                      # one-line human description
    currency: str = ""
    old_amount: str | None = None
    new_amount: str | None = None
    rise_pct: str | None = None
    detail: dict = field(default_factory=dict)
    created_ts: float = field(default_factory=lambda: round(time.time(), 3))
    status: str = "PENDING"           # PENDING | APPROVED | REJECTED
    decided_by: str | None = None
    decided_ts: float | None = None

    def to_dict(self) -> dict:
        return asdict(self)


class ApprovalStore:
    """In-memory queue of Approvals keyed by id (insertion-ordered)."""

    def __init__(self) -> None:
        self._items: dict[str, Approval] = {}

    def add(self, kind: str, vendor: str, summary: str, **fields) -> Approval:
        aid = fields.pop("id", None) or str(uuid.uuid4())
        a = Approval(id=aid, kind=kind, vendor=vendor, summary=summary, **fields)
        self._items[aid] = a
        return a

    def get(self, approval_id: str) -> Approval | None:
        return self._items.get(approval_id)

    def all(self) -> list[Approval]:
        return list(self._items.values())

    def pending(self) -> list[Approval]:
        return [a for a in self._items.values() if a.status == "PENDING"]

    def _decide(self, approval_id: str, status: str, by: str) -> Approval:
        a = self._items.get(approval_id)
        if a is None:
            raise KeyError(approval_id)
        if a.status != "PENDING":
            raise ValueError(f"approval {approval_id} already {a.status}")
        a.status = status
        a.decided_by = by
        a.decided_ts = round(time.time(), 3)
        return a

    def approve(self, approval_id: str, by: str) -> Approval:
        return self._decide(approval_id, "APPROVED", by)

    def reject(self, approval_id: str, by: str) -> Approval:
        return self._decide(approval_id, "REJECTED", by)
