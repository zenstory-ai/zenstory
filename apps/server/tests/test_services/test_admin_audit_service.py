"""
Tests for AdminAuditService.

Unit tests for the admin audit service, covering:
- Admin action logging with IP/user agent extraction
- Audit log querying with filters
- Pagination support
"""

from datetime import datetime
from unittest.mock import MagicMock

import pytest
from sqlmodel import Session

from models.subscription import AdminAuditLog
from services.admin_audit_service import AdminAuditService


def _create_mock_request(headers_dict: dict, client_host: str = "10.0.0.1", client_exists: bool = True):
    """Helper to create a mock Request with proper headers.get() support."""
    # Note: We don't use spec=Request because MagicMock(spec=Request) returns False for bool()
    request = MagicMock()
    # Create a headers mock that has a get method returning values from headers_dict
    headers_mock = MagicMock()
    headers_mock.get.side_effect = lambda key, default=None: headers_dict.get(key, default)
    request.headers = headers_mock
    if client_exists:
        request.client = MagicMock()
        request.client.host = client_host
    else:
        request.client = None
    return request


@pytest.mark.unit
class TestLogAction:
    """Tests for log_action method."""

    def test_log_action_basic(self, db_session: Session):
        """Test basic audit log creation."""
        service = AdminAuditService()

        log = service.log_action(
            session=db_session,
            admin_user_id="admin-123",
            action="create_code",
            resource_type="code"
        )

        assert log.id is not None
        assert log.admin_user_id == "admin-123"
        assert log.action == "create_code"
        assert log.resource_type == "code"
        assert log.resource_id is None
        assert log.old_value is None
        assert log.new_value is None
        assert log.ip_address is None
        assert log.user_agent is None
        assert log.created_at is not None

    def test_log_action_with_resource_id(self, db_session: Session):
        """Test audit log with resource ID."""
        service = AdminAuditService()

        log = service.log_action(
            session=db_session,
            admin_user_id="admin-123",
            action="update_subscription",
            resource_type="subscription",
            resource_id="sub-456"
        )

        assert log.resource_id == "sub-456"

    def test_log_action_with_old_new_values(self, db_session: Session):
        """Test audit log with old and new values."""
        service = AdminAuditService()

        old_value = {"status": "active", "plan": "free"}
        new_value = {"status": "active", "plan": "pro"}

        log = service.log_action(
            session=db_session,
            admin_user_id="admin-123",
            action="upgrade_plan",
            resource_type="subscription",
            resource_id="sub-456",
            old_value=old_value,
            new_value=new_value
        )

        assert log.old_value == old_value
        assert log.new_value == new_value

    def test_log_action_with_request_forwarded_header(self, db_session: Session):
        """Test audit log uses the proxy-appended (rightmost) X-Forwarded-For IP."""
        service = AdminAuditService()

        request = _create_mock_request({
            "X-Forwarded-For": "198.51.100.66, 203.0.113.1",
            "User-Agent": "Mozilla/5.0"
        })

        log = service.log_action(
            session=db_session,
            admin_user_id="admin-123",
            action="delete_code",
            resource_type="code",
            resource_id="code-789",
            request=request
        )

        # The client-supplied left entry is ignored
        assert log.ip_address == "203.0.113.1"
        assert log.user_agent == "Mozilla/5.0"

    def test_log_action_with_request_client_host_fallback(self, db_session: Session):
        """Test audit log uses client.host when no X-Forwarded-For header."""
        service = AdminAuditService()

        request = _create_mock_request({}, client_host="192.168.1.50")

        log = service.log_action(
            session=db_session,
            admin_user_id="admin-123",
            action="create_code",
            resource_type="code",
            request=request
        )

        assert log.ip_address == "192.168.1.50"

    def test_log_action_with_request_no_client(self, db_session: Session):
        """Test audit log handles request with no client info."""
        service = AdminAuditService()

        request = _create_mock_request({}, client_exists=False)

        log = service.log_action(
            session=db_session,
            admin_user_id="admin-123",
            action="create_code",
            resource_type="code",
            request=request
        )

        assert log.ip_address is None

    def test_log_action_with_forwarded_header_spaces(self, db_session: Session):
        """Test IP extraction handles spaces in X-Forwarded-For header."""
        service = AdminAuditService()

        request = _create_mock_request({"X-Forwarded-For": "  10.0.0.1  ,  203.0.113.1  "})

        log = service.log_action(
            session=db_session,
            admin_user_id="admin-123",
            action="create_code",
            resource_type="code",
            request=request
        )

        # Proxy-appended IP should be stripped of spaces
        assert log.ip_address == "203.0.113.1"

    def test_log_action_with_request_no_user_agent(self, db_session: Session):
        """Test audit log handles request without User-Agent header."""
        service = AdminAuditService()

        request = _create_mock_request({"X-Forwarded-For": "203.0.113.1"})

        log = service.log_action(
            session=db_session,
            admin_user_id="admin-123",
            action="create_code",
            resource_type="code",
            request=request
        )

        assert log.ip_address == "203.0.113.1"
        assert log.user_agent is None

    def test_log_action_persists_to_database(self, db_session: Session):
        """Test that log is persisted and can be queried."""
        service = AdminAuditService()

        service.log_action(
            session=db_session,
            admin_user_id="admin-123",
            action="create_code",
            resource_type="code"
        )

        # Query to verify persistence
        from sqlmodel import select
        logs = db_session.exec(select(AdminAuditLog)).all()

        assert len(logs) == 1
        assert logs[0].admin_user_id == "admin-123"
        assert logs[0].action == "create_code"

    def test_log_action_created_at_is_recent(self, db_session: Session):
        """Test that created_at timestamp is recent."""
        service = AdminAuditService()

        before = datetime.utcnow()
        log = service.log_action(
            session=db_session,
            admin_user_id="admin-123",
            action="create_code",
            resource_type="code"
        )
        after = datetime.utcnow()

        assert before <= log.created_at <= after

    def test_log_action_with_complex_values(self, db_session: Session):
        """Test audit log with complex nested values."""
        service = AdminAuditService()

        old_value = {
            "plan": "free",
            "features": {"ai_conversations": 20},
            "metadata": {"source": "signup"}
        }
        new_value = {
            "plan": "pro",
            "features": {"ai_conversations": 1000},
            "metadata": {"source": "upgrade", "promo_code": "SUMMER2024"}
        }

        log = service.log_action(
            session=db_session,
            admin_user_id="admin-123",
            action="upgrade_plan",
            resource_type="subscription",
            resource_id="sub-456",
            old_value=old_value,
            new_value=new_value
        )

        assert log.old_value["plan"] == "free"
        assert log.new_value["plan"] == "pro"
        assert log.new_value["metadata"]["promo_code"] == "SUMMER2024"

    def test_log_action_normalizes_unknown_ip_to_none(self, db_session: Session, monkeypatch: pytest.MonkeyPatch):
        service = AdminAuditService()
        request = _create_mock_request({"User-Agent": "pytest"})

        monkeypatch.setattr("services.admin_audit_service.get_client_ip", lambda req: "unknown")

        log = service.log_action(
            session=db_session,
            admin_user_id="admin-unknown-ip",
            action="create_code",
            resource_type="code",
            request=request,
        )

        assert log.ip_address is None
        assert log.user_agent == "pytest"


@pytest.mark.unit
class TestGetAuditLogs:
    """Tests for get_audit_logs method."""

    def test_get_audit_logs_no_filters(self, db_session: Session):
        """Test getting all logs without filters."""
        service = AdminAuditService()

        # Create multiple logs
        for i in range(5):
            service.log_action(
                session=db_session,
                admin_user_id=f"admin-{i}",
                action="create_code",
                resource_type="code"
            )

        logs = service.get_audit_logs(session=db_session)

        assert len(logs) == 5

    def test_get_audit_logs_filter_by_admin_user_id(self, db_session: Session):
        """Test filtering logs by admin user ID."""
        service = AdminAuditService()

        service.log_action(
            session=db_session,
            admin_user_id="admin-1",
            action="create_code",
            resource_type="code"
        )
        service.log_action(
            session=db_session,
            admin_user_id="admin-2",
            action="delete_code",
            resource_type="code"
        )
        service.log_action(
            session=db_session,
            admin_user_id="admin-1",
            action="update_code",
            resource_type="code"
        )

        logs = service.get_audit_logs(
            session=db_session,
            admin_user_id="admin-1"
        )

        assert len(logs) == 2
        for log in logs:
            assert log.admin_user_id == "admin-1"

    def test_get_audit_logs_filter_by_resource_type(self, db_session: Session):
        """Test filtering logs by resource type."""
        service = AdminAuditService()

        service.log_action(
            session=db_session,
            admin_user_id="admin-1",
            action="create",
            resource_type="code"
        )
        service.log_action(
            session=db_session,
            admin_user_id="admin-1",
            action="create",
            resource_type="subscription"
        )
        service.log_action(
            session=db_session,
            admin_user_id="admin-1",
            action="create",
            resource_type="user"
        )

        logs = service.get_audit_logs(
            session=db_session,
            resource_type="subscription"
        )

        assert len(logs) == 1
        assert logs[0].resource_type == "subscription"

    def test_get_audit_logs_filter_by_action(self, db_session: Session):
        """Test filtering logs by action."""
        service = AdminAuditService()

        service.log_action(
            session=db_session,
            admin_user_id="admin-1",
            action="create_code",
            resource_type="code"
        )
        service.log_action(
            session=db_session,
            admin_user_id="admin-1",
            action="delete_code",
            resource_type="code"
        )
        service.log_action(
            session=db_session,
            admin_user_id="admin-1",
            action="create_code",
            resource_type="code"
        )

        logs = service.get_audit_logs(
            session=db_session,
            action="delete_code"
        )

        assert len(logs) == 1
        assert logs[0].action == "delete_code"

    def test_get_audit_logs_filter_by_resource_id(self, db_session: Session):
        """Test filtering logs by resource ID."""
        service = AdminAuditService()

        service.log_action(
            session=db_session,
            admin_user_id="admin-1",
            action="create",
            resource_type="code",
            resource_id="code-123"
        )
        service.log_action(
            session=db_session,
            admin_user_id="admin-1",
            action="create",
            resource_type="code",
            resource_id="code-456"
        )
        service.log_action(
            session=db_session,
            admin_user_id="admin-1",
            action="update",
            resource_type="code",
            resource_id="code-123"
        )

        logs = service.get_audit_logs(
            session=db_session,
            resource_id="code-123"
        )

        assert len(logs) == 2
        for log in logs:
            assert log.resource_id == "code-123"

    def test_get_audit_logs_pagination_limit(self, db_session: Session):
        """Test pagination with limit parameter."""
        service = AdminAuditService()

        # Create 10 logs
        for i in range(10):
            service.log_action(
                session=db_session,
                admin_user_id=f"admin-{i}",
                action="create_code",
                resource_type="code"
            )

        logs = service.get_audit_logs(
            session=db_session,
            limit=5
        )

        assert len(logs) == 5

    def test_get_audit_logs_pagination_offset(self, db_session: Session):
        """Test pagination with offset parameter."""
        import time
        service = AdminAuditService()

        # Create logs with different admin_user_ids and slight delays for consistent ordering
        for i in range(10):
            service.log_action(
                session=db_session,
                admin_user_id=f"admin-{i:02d}",
                action="create_code",
                resource_type="code"
            )
            # Small delay to ensure different timestamps
            time.sleep(0.01)

        # Get first 5
        first_page = service.get_audit_logs(
            session=db_session,
            limit=5,
            offset=0
        )

        # Get next 5
        second_page = service.get_audit_logs(
            session=db_session,
            limit=5,
            offset=5
        )

        # Verify correct counts
        assert len(first_page) == 5
        assert len(second_page) == 5

        # Verify total is 10 unique logs
        all_ids = {log.id for log in first_page + second_page}
        assert len(all_ids) == 10

    def test_get_audit_logs_default_limit(self, db_session: Session):
        """Test that default limit of 50 is applied."""
        service = AdminAuditService()

        # Create more than default limit logs
        for i in range(60):
            service.log_action(
                session=db_session,
                admin_user_id=f"admin-{i}",
                action="create_code",
                resource_type="code"
            )

        logs = service.get_audit_logs(session=db_session)

        # Default limit is 50
        assert len(logs) == 50

    def test_get_audit_logs_order_by_created_at_desc(self, db_session: Session):
        """Test that logs are ordered by created_at descending."""
        import time
        service = AdminAuditService()

        # Create multiple logs with slight delay to ensure different timestamps
        log_ids = []
        for i in range(3):
            log = service.log_action(
                session=db_session,
                admin_user_id=f"admin-{i}",
                action="create_code",
                resource_type="code"
            )
            log_ids.append(log.id)
            # Add small delay to ensure different timestamps
            if i < 2:
                time.sleep(0.01)

        logs = service.get_audit_logs(session=db_session)

        # Verify we got 3 logs
        assert len(logs) == 3
        # Most recent should be first (admin-2 was created last)
        assert logs[0].admin_user_id == "admin-2"
        assert logs[2].admin_user_id == "admin-0"

    def test_get_audit_logs_combined_filters(self, db_session: Session):
        """Test combining multiple filters."""
        service = AdminAuditService()

        # Create logs with different combinations
        service.log_action(
            session=db_session,
            admin_user_id="admin-1",
            action="create_code",
            resource_type="code",
            resource_id="code-1"
        )
        service.log_action(
            session=db_session,
            admin_user_id="admin-1",
            action="delete_code",
            resource_type="code",
            resource_id="code-2"
        )
        service.log_action(
            session=db_session,
            admin_user_id="admin-2",
            action="create_code",
            resource_type="code",
            resource_id="code-3"
        )
        service.log_action(
            session=db_session,
            admin_user_id="admin-1",
            action="create_code",
            resource_type="subscription",
            resource_id="sub-1"
        )

        logs = service.get_audit_logs(
            session=db_session,
            admin_user_id="admin-1",
            action="create_code",
            resource_type="code"
        )

        assert len(logs) == 1
        assert logs[0].admin_user_id == "admin-1"
        assert logs[0].action == "create_code"
        assert logs[0].resource_type == "code"
        assert logs[0].resource_id == "code-1"

    def test_get_audit_logs_empty_result(self, db_session: Session):
        """Test that empty result when no logs match filters."""
        service = AdminAuditService()

        service.log_action(
            session=db_session,
            admin_user_id="admin-1",
            action="create_code",
            resource_type="code"
        )

        logs = service.get_audit_logs(
            session=db_session,
            admin_user_id="nonexistent-admin"
        )

        assert len(logs) == 0

    def test_get_audit_logs_shorthand_action_matches_prefixed_actions(self, db_session: Session):
        service = AdminAuditService()

        service.log_action(
            session=db_session,
            admin_user_id="admin-1",
            action="create_code",
            resource_type="code",
        )
        service.log_action(
            session=db_session,
            admin_user_id="admin-1",
            action="create",
            resource_type="code",
        )
        service.log_action(
            session=db_session,
            admin_user_id="admin-1",
            action="approve_skill",
            resource_type="skill",
        )

        create_logs = service.get_audit_logs(session=db_session, action="create")
        approve_logs = service.get_audit_logs(session=db_session, action="approve")

        assert {log.action for log in create_logs} == {"create", "create_code"}
        assert [log.action for log in approve_logs] == ["approve_skill"]


@pytest.mark.unit
class TestAdminAuditServiceSingleton:
    """Tests for the singleton instance."""

    def test_singleton_exists(self):
        """Test that singleton instance exists."""
        from services.admin_audit_service import admin_audit_service

        assert admin_audit_service is not None
        assert isinstance(admin_audit_service, AdminAuditService)

    def test_singleton_is_same_instance(self):
        """Test that imported singleton is the same instance."""
        from services.admin_audit_service import admin_audit_service as instance1
        from services.admin_audit_service import admin_audit_service as instance2

        assert instance1 is instance2
