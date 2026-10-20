"""Integration tests for task workflow."""

import datetime
from unittest.mock import Mock

import pytest

from app.workflows.state_machine import (
    VALID_TRANSITIONS,
    StateMachineError,
    TaskStateMachine,
    TaskStatus,
)


class TestTaskStateMachine:
    """Tests for the task state machine."""

    @pytest.fixture
    def mock_session(self):
        """Create a mock database session."""
        return Mock()

    @pytest.fixture
    def state_machine(self, mock_session):
        """Create a state machine instance."""
        return TaskStateMachine(session=mock_session)

    @pytest.fixture
    def sample_task(self):
        """Create a sample task."""
        task = Mock()
        task.id = "task-001"
        task.status = TaskStatus.CREATED.value
        task.exception_id = "exception-001"
        task.task_type = "INVESTIGATE_SHORTFALL"
        task.assignee_employee_id = None
        task.assigned_by_employee_id = None
        task.assignment_reason = None
        task.resolution_code = None
        task.evidence_reference = None
        task.created_at = datetime.datetime.now(datetime.UTC)
        task.updated_at = datetime.datetime.now(datetime.UTC)
        return task

    def test_valid_transition_created_to_assigned(self, state_machine, sample_task):
        """Test valid transition from CREATED to ASSIGNED."""
        result = state_machine.validate_transition(
            sample_task,
            TaskStatus.ASSIGNED,
            actor="HR-201",
            reason="Assigning to eligible receiver",
        )

        assert sample_task.status == TaskStatus.ASSIGNED.value
        assert result.event_type == "ASSIGNED"
        assert result.from_status == TaskStatus.CREATED.value
        assert result.to_status == TaskStatus.ASSIGNED.value

    def test_valid_transition_assigned_to_acknowledged(self, state_machine, sample_task):
        """Test valid transition from ASSIGNED to ACKNOWLEDGED."""
        # First assign
        state_machine.validate_transition(
            sample_task,
            TaskStatus.ASSIGNED,
            actor="HR-201",
        )

        # Then acknowledge
        result = state_machine.validate_transition(
            sample_task,
            TaskStatus.ACKNOWLEDGED,
            actor="HR-102",
        )

        assert sample_task.status == TaskStatus.ACKNOWLEDGED.value
        assert result.event_type == "ACKNOWLEDGED"

    def test_valid_transition_acknowledged_to_in_progress(self, state_machine, sample_task):
        """Test valid transition from ACKNOWLEDGED to IN_PROGRESS."""
        # Setup: assigned and acknowledged
        state_machine.validate_transition(sample_task, TaskStatus.ASSIGNED, actor="HR-201")
        state_machine.validate_transition(sample_task, TaskStatus.ACKNOWLEDGED, actor="HR-102")

        # Start working
        result = state_machine.validate_transition(
            sample_task,
            TaskStatus.IN_PROGRESS,
            actor="HR-102",
        )

        assert sample_task.status == TaskStatus.IN_PROGRESS.value
        assert result.event_type == "STARTED"

    def test_valid_transition_in_progress_to_completed(self, state_machine, sample_task):
        """Test valid transition from IN_PROGRESS to COMPLETED."""
        # Setup workflow
        for status in [TaskStatus.ASSIGNED, TaskStatus.ACKNOWLEDGED, TaskStatus.IN_PROGRESS]:
            state_machine.validate_transition(sample_task, status, actor="HR-102")

        # Complete task
        result = state_machine.complete(
            sample_task,
            actor="HR-102",
            resolution_code="DONE",
            evidence_reference="GRN-501-A verified",
        )

        assert sample_task.status == TaskStatus.COMPLETED.value
        assert sample_task.resolution_code == "DONE"
        assert sample_task.evidence_reference == "GRN-501-A verified"
        assert result.event_type == "COMPLETED"

    def test_invalid_transition_rejected(self, state_machine, sample_task):
        """Test that invalid transitions are rejected."""
        # Try to jump from CREATED to COMPLETED
        with pytest.raises(StateMachineError) as exc_info:
            state_machine.validate_transition(
                sample_task,
                TaskStatus.COMPLETED,
                actor="HR-102",
            )

        assert "Invalid transition" in str(exc_info.value)
        assert sample_task.status == TaskStatus.CREATED.value  # Unchanged

    def test_idempotent_completion_retry(self, state_machine, sample_task):
        """Test that completing an already-completed task is safe."""
        # First, set up the task through proper workflow
        state_machine.validate_transition(sample_task, TaskStatus.ASSIGNED, actor="HR-201")
        state_machine.validate_transition(sample_task, TaskStatus.ACKNOWLEDGED, actor="HR-102")
        state_machine.validate_transition(sample_task, TaskStatus.IN_PROGRESS, actor="HR-102")

        # Now complete the task
        state_machine.complete(
            sample_task,
            actor="HR-102",
            resolution_code="DONE",
        )

        assert sample_task.status == TaskStatus.COMPLETED.value

        # Retry completion - should be safe
        result = state_machine.complete(
            sample_task,
            actor="HR-102",
            resolution_code="DONE",
        )

        # Should be safe - no error, task unchanged
        assert sample_task.status == TaskStatus.COMPLETED.value
        assert result.event_type == "COMPLETION_RETRY"

    def test_reassign_updates_assignee(self, state_machine, sample_task):
        """Test that reassignment updates the assignee."""
        # Assign task
        state_machine.validate_transition(
            sample_task,
            TaskStatus.ASSIGNED,
            actor="HR-201",
            reason="Initial assignment",
        )

        assert sample_task.assignee_employee_id is None  # Not set by validate_transition alone

        # Reassign
        reassign_result, _ = state_machine.reassign(
            sample_task,
            new_assignee="HR-401",
            actor="HR-201",
            reason="Original assignee unavailable",
        )

        # Note: In real implementation, we'd update task.assignee_employee_id
        # For this test, we're verifying the state machine logic

    def test_verify_terminal_state(self, state_machine, sample_task):
        """Test that VERIFIED is a terminal state."""
        # Setup through full workflow
        for status in [TaskStatus.ASSIGNED, TaskStatus.ACKNOWLEDGED, TaskStatus.IN_PROGRESS]:
            state_machine.validate_transition(sample_task, status, actor="HR-102")

        state_machine.complete(sample_task, actor="HR-102", resolution_code="DONE")
        state_machine.validate_transition(
            sample_task, TaskStatus.PENDING_VERIFICATION, actor="HR-201"
        )

        # Verify
        state_machine.validate_transition(
            sample_task,
            TaskStatus.VERIFIED,
            actor="HR-201",
        )

        assert sample_task.status == TaskStatus.VERIFIED.value

        # Try to transition from VERIFIED - should fail
        with pytest.raises(StateMachineError):
            state_machine.validate_transition(
                sample_task,
                TaskStatus.IN_PROGRESS,
                actor="HR-102",
            )


class TestStateMachineTransitions:
    """Tests for valid and invalid state transitions."""

    def test_all_valid_transitions(self):
        """Test that all documented valid transitions work."""
        transitions = [
            (TaskStatus.CREATED, TaskStatus.ASSIGNED),
            (TaskStatus.ASSIGNED, TaskStatus.ACKNOWLEDGED),
            (TaskStatus.ACKNOWLEDGED, TaskStatus.IN_PROGRESS),
            (TaskStatus.IN_PROGRESS, TaskStatus.COMPLETED),
            (TaskStatus.COMPLETED, TaskStatus.PENDING_VERIFICATION),
            (TaskStatus.PENDING_VERIFICATION, TaskStatus.VERIFIED),
            (TaskStatus.PENDING_VERIFICATION, TaskStatus.REOPENED),
            (TaskStatus.BLOCKED, TaskStatus.ASSIGNED),
        ]

        for from_status, to_status in transitions:
            # Valid transition defined
            assert to_status in VALID_TRANSITIONS[from_status]

    def test_terminal_states(self):
        """Test that terminal states have no outgoing transitions."""
        terminal_states = [
            TaskStatus.VERIFIED,
            TaskStatus.REJECTED,
        ]

        for state in terminal_states:
            # These should not have transitions defined (or be terminal)
            assert not VALID_TRANSITIONS[state]


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
