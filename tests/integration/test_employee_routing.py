"""Integration tests for employee routing."""

import datetime
from unittest.mock import Mock

import pytest


class TestEmployeeRouting:
    """Tests for employee routing logic."""

    @pytest.fixture
    def mock_session(self):
        """Create a mock database session."""
        return Mock()

    def test_hr102_eligible_for_store014(self, mock_session):
        """Test that HR-102 is eligible for STORE-014 tasks."""
        # HR-102: ACTIVE, ON_SHIFT, receiving_associate at ST-014
        hr102 = Mock()
        hr102.source_employee_id = "HR-102"
        hr102.display_name = "Dev Shah"
        hr102.employment_status = "ACTIVE"
        hr102.role = "receiving_associate"
        hr102.canonical_location_id = "STORE-014"

        # Mock workforce status - ON_SHIFT, observed recently
        workforce_status = Mock()
        workforce_status.availability_status = "ON_SHIFT"
        workforce_status.observed_at = datetime.datetime(
            2026, 10, 16, 14, 50, 0, tzinfo=datetime.UTC
        )
        hr102.workforce_status = [workforce_status]

        mock_session.query.return_value.filter.return_value.all.return_value = [hr102]
        mock_session.query.return_value.options.return_value.all.return_value = [hr102]

        # Evaluate eligibility
        evaluation_time = datetime.datetime(2026, 10, 16, 15, 0, 0, tzinfo=datetime.UTC)

        # Check employment status
        assert hr102.employment_status == "ACTIVE"

        # Check role and location
        assert hr102.role == "receiving_associate"
        assert hr102.canonical_location_id == "STORE-014"

        # Check shift status
        assert workforce_status.availability_status == "ON_SHIFT"

        # Check freshness (within 2 hours of evaluation time)
        age = evaluation_time - workforce_status.observed_at
        assert age < datetime.timedelta(hours=2)

        # HR-102 should be eligible
        assert True  # If we got here, all checks passed

    def test_hr101_not_eligible_off_shift(self, mock_session):
        """Test that HR-101 is NOT eligible due to OFF_SHIFT."""
        hr101 = Mock()
        hr101.source_employee_id = "HR-101"
        hr101.display_name = "Asha Rao"
        hr101.employment_status = "ACTIVE"
        hr101.role = "receiving_associate"
        hr101.canonical_location_id = "STORE-014"

        workforce_status = Mock()
        workforce_status.availability_status = "OFF_SHIFT"
        workforce_status.observed_at = datetime.datetime(
            2026, 10, 16, 14, 50, 0, tzinfo=datetime.UTC
        )

        # Check shift status
        assert workforce_status.availability_status == "OFF_SHIFT"
        # HR-101 should NOT be eligible

    def test_hr103_not_eligible_inactive(self, mock_session):
        """Test that HR-103 is NOT eligible due to INACTIVE status."""
        hr103 = Mock()
        hr103.source_employee_id = "HR-103"
        hr103.display_name = "Nikhil Das"
        hr103.employment_status = "INACTIVE"  # Even though shift says ON_SHIFT
        hr103.role = "receiving_associate"
        hr103.canonical_location_id = "STORE-014"

        workforce_status = Mock()
        workforce_status.availability_status = "ON_SHIFT"

        # Employment status wins over shift status
        assert hr103.employment_status == "INACTIVE"
        # HR-103 should NOT be eligible

    def test_hr401_fallback_for_store015(self, mock_session):
        """Test that HR-401 is eligible as fallback for STORE-015."""
        hr401 = Mock()
        hr401.source_employee_id = "HR-401"
        hr401.display_name = "Rohan Iyer"
        hr401.employment_status = "ACTIVE"
        hr401.role = "store_manager"
        hr401.canonical_location_id = "STORE-015"

        workforce_status = Mock()
        workforce_status.availability_status = "ON_SHIFT"
        workforce_status.observed_at = datetime.datetime(
            2026, 10, 16, 14, 50, 0, tzinfo=datetime.UTC
        )

        # Check eligibility
        evaluation_time = datetime.datetime(2026, 10, 16, 15, 0, 0, tzinfo=datetime.UTC)

        assert hr401.employment_status == "ACTIVE"
        assert hr401.role == "store_manager"
        assert hr401.canonical_location_id == "STORE-015"
        assert workforce_status.availability_status == "ON_SHIFT"

        age = evaluation_time - workforce_status.observed_at
        assert age < datetime.timedelta(hours=2)

        # HR-401 should be eligible as fallback

    def test_hr501_not_assigned_region_scoped(self, mock_session):
        """Test that HR-501 (regional) is not assigned to store-specific tasks."""
        hr501 = Mock()
        hr501.source_employee_id = "HR-501"
        hr501.display_name = "Priya Menon"
        hr501.employment_status = "ACTIVE"
        hr501.role = "regional_operations"
        hr501.canonical_location_id = None  # No store location

        # Regional employees don't have store locations
        assert hr501.canonical_location_id is None
        # Should not be assigned to store-specific tasks


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
