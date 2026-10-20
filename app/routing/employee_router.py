"""Employee routing service for task assignment."""

import datetime

from sqlalchemy import and_
from sqlalchemy.orm import Session, joinedload

from app.config import settings
from app.models.employee import Employee
from app.models.exception import Exception as ExceptionModel
from app.models.task import Task
from app.models.workforce_status import WorkforceStatus


class EmployeeRouter:
    """Routes tasks to eligible employees based on business rules."""

    def __init__(
        self,
        session: Session,
        evaluation_time: datetime.datetime | None = None,
        freshness_hours: int | None = None,
    ):
        """
        Initialize the router.

        Args:
            session: Database session
            evaluation_time: Time to use for shift status checks
            freshness_hours: Hours within which workforce status is considered fresh
        """
        self.session = session

        # Use provided evaluation time or frozen time
        if evaluation_time:
            self.evaluation_time = evaluation_time
        else:
            from dateutil.parser import parse

            self.evaluation_time = parse(settings.frozen_evaluation_time)

        # Freshness window
        self.freshness_hours = (
            freshness_hours if freshness_hours is not None else settings.shift_freshness_hours
        )

    def find_eligible_assignee(
        self,
        exception: ExceptionModel,
        preferred_role: str = "receiving_associate",
        fallback_roles: list[str] | None = None,
    ) -> tuple[Employee | None, str | None, str | None]:
        """
        Find an eligible employee to assign a task to.

        Assignment rules:
        1. Employee must be ACTIVE (employment_status)
        2. If using workforce status: must be ON_SHIFT at evaluation time
        3. Workforce status must be fresh enough
        4. For location-based tasks: employee must be at the same canonical location
        5. Preferred role is receiving_associate at the location
        6. Fallback to store manager if no receiving associate available

        Args:
            exception: Exception to create task for
            preferred_role: Preferred role for assignment
            fallback_roles: List of fallback roles in order of preference

        Returns:
            Tuple of (employee, reason, error_message)
        """
        if fallback_roles is None:
            fallback_roles = ["store_manager"]

        location_id = exception.canonical_location_id

        # Try to find employee at the same location
        eligible_employee = self._find_employee_at_location(
            location_id=location_id,
            role=preferred_role,
        )

        if eligible_employee:
            return (
                eligible_employee,
                f"Selected {eligible_employee.display_name} "
                f"({eligible_employee.source_employee_id}) "
                f"as eligible {preferred_role} at {location_id}",
                None,
            )

        # If no employee found at location, try fallback roles
        for fallback_role in fallback_roles:
            eligible_employee = self._find_employee_at_location(
                location_id=location_id,
                role=fallback_role,
            )

            if eligible_employee:
                return (
                    eligible_employee,
                    f"No {preferred_role} available at {location_id}; "
                    f"falling back to {fallback_role}: {eligible_employee.display_name} "
                    f"({eligible_employee.source_employee_id})",
                    None,
                )

        # No eligible employee found
        return (
            None,
            None,
            f"No eligible {preferred_role} or fallback roles found at {location_id}",
        )

    def is_eligible_for_exception(self, employee: Employee, exception: ExceptionModel) -> bool:
        """Check whether an employee may be assigned work for an exception.

        Manual reassignment must enforce the same policy as automatic routing:
        the employee has to be ACTIVE, have a fresh ON_SHIFT observation, and
        hold an approved role at the exception's canonical location. Without
        this check an off-shift or region-scoped employee could be assigned
        work that automatic routing would never give them.
        """
        if employee.canonical_location_id != exception.canonical_location_id:
            return False

        if employee.role not in ("receiving_associate", "store_manager"):
            return False

        return self._is_employee_eligible(employee)

    def _find_employee_at_location(
        self,
        location_id: str,
        role: str,
    ) -> Employee | None:
        """
        Find an eligible employee at a specific location with a specific role.

        Args:
            location_id: Canonical location ID
            role: Employee role to find

        Returns:
            Eligible employee or None
        """
        # Find employees with the role at the location
        candidates = (
            self.session.query(Employee)
            .filter(
                and_(
                    Employee.role == role,
                    Employee.canonical_location_id == location_id,
                    Employee.employment_status == "ACTIVE",
                )
            )
            .options(joinedload(Employee.workforce_status))
            .all()
        )

        for employee in candidates:
            if self._is_employee_eligible(employee):
                return employee

        return None

    def _is_employee_eligible(self, employee: Employee) -> bool:
        """
        Check if an employee is eligible for task assignment.

        Rules:
        1. Must be ACTIVE (employment_status)
        2. Must have fresh workforce status
        3. Must be ON_SHIFT at evaluation time

        Args:
            employee: Employee to check

        Returns:
            True if eligible, False otherwise
        """
        # Check employment status
        if employee.employment_status != "ACTIVE":
            return False

        # Get workforce status for this employee
        workforce_status = (
            self.session.query(WorkforceStatus)
            .filter(
                and_(
                    WorkforceStatus.source_employee_id == employee.source_employee_id,
                    WorkforceStatus.observed_at <= self.evaluation_time,
                )
            )
            .order_by(WorkforceStatus.observed_at.desc())
            .first()
        )

        if not workforce_status:
            # No workforce status available - cannot determine eligibility
            # This is a data quality issue, not an automatic rejection
            return False

        # Check if status is fresh enough
        observation_age = self.evaluation_time - workforce_status.observed_at
        max_age = datetime.timedelta(hours=self.freshness_hours)

        if observation_age > max_age:
            # Status is too old
            return False

        # Check if employee is ON_SHIFT
        return workforce_status.availability_status == "ON_SHIFT"

    def get_assignment_history(self, task: Task) -> list[dict]:
        """
        Get assignment history for a task.

        Args:
            task: Task to get history for

        Returns:
            List of assignment events
        """
        # This would query audit events
        # For now, return basic info
        history = []

        if task.assigned_by_employee_id:
            assigner = (
                self.session.query(Employee)
                .filter(Employee.source_employee_id == task.assigned_by_employee_id)
                .first()
            )

            history.append(
                {
                    "event": "ASSIGNED",
                    "assignee": task.assignee_employee_id,
                    "assigner": task.assigned_by_employee_id,
                    "assigner_name": assigner.display_name if assigner else None,
                    "reason": task.assignment_reason,
                    "timestamp": task.created_at.isoformat(),
                }
            )

        return history
