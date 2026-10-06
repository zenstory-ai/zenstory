"""Real quota fixture teardown must not shadow subsequent class overrides."""

from services.features import file_version_service as versions
from tests.test_services.test_agent_file_preconditions_postgres import stable_dependencies


def test_precondition_fixture_restores_method_without_singleton_shadow(monkeypatch):
    singleton = versions.FileVersionService()
    monkeypatch.setattr(versions, "_file_version_service", singleton)
    assert "check_user_version_quota" not in vars(singleton)

    with monkeypatch.context() as fixture_patch:
        stable_dependencies.__wrapped__(fixture_patch)
        assert singleton.check_user_version_quota(None, "file", "user") == (True, 0, 10)

    assert "check_user_version_quota" not in vars(singleton)
    with monkeypatch.context() as later_patch:
        later_patch.setattr(
            versions.FileVersionService,
            "check_user_version_quota",
            lambda *_args, **_kwargs: (False, 17, 17),
        )
        assert singleton.check_user_version_quota(None, "file", "user") == (False, 17, 17)
    assert "check_user_version_quota" not in vars(singleton)
