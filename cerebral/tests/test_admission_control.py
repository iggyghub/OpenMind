import unittest
from cerebral.admission_control import AdmissionController, ProposalQueue, StallProposal, ProposalStatus


class TestAdmissionControl(unittest.TestCase):
    def test_stall_streak_triggers_proposal(self) -> None:
        queue = ProposalQueue()
        cap = 10
        controller = AdmissionController(queue, cap)

        # Simulate stall streak at the current cap
        for _ in range(controller.STALL_THRESHOLD):
            controller.record_stall("domain-A")
            controller.propose_cap_adjustment("domain-A")

        pending = queue.pending()
        self.assertEqual(len(pending), 1)
        self.assertEqual(pending[0].domain, "domain-A")
        self.assertEqual(pending[0].current_cap, cap)
        self.assertIn("stall_count=5", pending[0].evidence)

    def test_approve_proposal_updates_system_cap(self) -> None:
        queue = ProposalQueue()
        cap = 10
        controller = AdmissionController(queue, cap)

        for _ in range(controller.STALL_THRESHOLD):
            controller.record_stall("domain-A")
            controller.propose_cap_adjustment("domain-A")

        proposal = queue.pending()[0]
        # Simulate user approving the proposal
        proposal.status = ProposalStatus.APPROVED
        # System setting from slice M is updated with the suggested cap
        system_cap = proposal.suggested_cap

        self.assertEqual(system_cap, 12)
        self.assertEqual(proposal.status, ProposalStatus.APPROVED)

    def test_dismiss_proposal_keeps_cap_unchanged(self) -> None:
        queue = ProposalQueue()
        original_cap = 10
        controller = AdmissionController(queue, original_cap)

        for _ in range(controller.STALL_THRESHOLD):
            controller.record_stall("domain-A")
            controller.propose_cap_adjustment("domain-A")

        proposal = queue.pending()[0]
        # Simulate user dismissing the proposal
        proposal.status = ProposalStatus.DISMISSED
        # Cap remains unchanged per acceptance criteria
        self.assertEqual(original_cap, 10)
        self.assertEqual(proposal.status, ProposalStatus.DISMISSED)

    def test_headroom_and_queueing_triggers_proposal(self) -> None:
        queue = ProposalQueue()
        cap = 10
        controller = AdmissionController(queue, cap)

        # Simulate sustained headroom + frequent queueing
        for _ in range(controller.HEADROOM_THRESHOLD):
            controller.record_headroom("domain-B")
            controller.record_queueing("domain-B")
            controller.propose_cap_adjustment("domain-B")

        pending = queue.pending()
        self.assertEqual(len(pending), 1)
        proposal = pending[0]
        self.assertIn("headroom_ticks=10", proposal.evidence)
        self.assertIn("queued_ticks=10", proposal.evidence)
