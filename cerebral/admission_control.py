from __future__ import annotations
from dataclasses import dataclass
from enum import Enum
from typing import Optional


class ProposalStatus(Enum):
    PENDING = "pending"
    APPROVED = "approved"
    DISMISSED = "dismissed"


@dataclass
class StallProposal:
    domain: str
    current_cap: int
    suggested_cap: int
    evidence: str
    status: ProposalStatus = ProposalStatus.PENDING


class ProposalQueue:
    """Existing queue mechanism: 'Felix proposes, the user decides'"""
    def __init__(self) -> None:
        self.proposals: list[StallProposal] = []

    def add(self, proposal: StallProposal) -> None:
        self.proposals.append(proposal)

    def pending(self) -> list[StallProposal]:
        return [p for p in self.proposals if p.status == ProposalStatus.PENDING]


class AdmissionController:
    STALL_THRESHOLD = 5
    HEADROOM_THRESHOLD = 10

    def __init__(self, proposal_queue: ProposalQueue, current_cap: int) -> None:
        self.proposal_queue = proposal_queue
        self.current_cap = current_cap
        self._stalls: dict[str, int] = {}
        self._headroom: dict[str, int] = {}
        self._queued: dict[str, int] = {}

    def record_stall(self, domain: str) -> None:
        self._stalls[domain] = self._stalls.get(domain, 0) + 1
        self._headroom[domain] = 0
        self._queued[domain] = 0

    def record_headroom(self, domain: str) -> None:
        self._headroom[domain] = self._headroom.get(domain, 0) + 1
        self._stalls[domain] = 0
        self._queued[domain] = 0

    def record_queueing(self, domain: str) -> None:
        self._queued[domain] = self._queued.get(domain, 0) + 1

    def evaluate_domain(self, domain: str) -> Optional[StallProposal]:
        # Threshold 1: repeated stalls at the current cap
        if self._stalls.get(domain, 0) >= self.STALL_THRESHOLD:
            self._stalls[domain] = 0
            return StallProposal(
                domain=domain,
                current_cap=self.current_cap,
                suggested_cap=self.current_cap + 2,
                evidence=f"stall_count={self.STALL_THRESHOLD}"
            )

        # Threshold 2: sustained headroom + frequent queueing
        if (self._headroom.get(domain, 0) >= self.HEADROOM_THRESHOLD and
                self._queued.get(domain, 0) >= self.HEADROOM_THRESHOLD):
            self._headroom[domain] = 0
            self._queued[domain] = 0
            return StallProposal(
                domain=domain,
                current_cap=self.current_cap,
                suggested_cap=self.current_cap + 1,
                evidence=f"headroom_ticks={self.HEADROOM_THRESHOLD}, queued_ticks={self.HEADROOM_THRESHOLD}"
            )

        return None

    def propose_cap_adjustment(self, domain: str) -> Optional[StallProposal]:
        proposal = self.evaluate_domain(domain)
        if proposal is not None:
            self.proposal_queue.add(proposal)
        return proposal
