import time
from unittest.mock import MagicMock, patch

import pytest

from app.services.privacy_mode import PrivacyMode


def _mock_settings(state_file):
    m = MagicMock()
    m.CONFIG.services.privacy_mode_state_file = state_file
    return m


@pytest.fixture
def state_file(tmp_path):
    return tmp_path / "privacy_mode"


@pytest.fixture
def mock_settings(state_file):
    return _mock_settings(state_file)


class TestPrivacyModeIsActive:
    def test_inactive_when_file_missing(self, state_file, mock_settings):
        with patch("app.services.privacy_mode.settings", mock_settings):
            assert not PrivacyMode().is_active()

    def test_active_with_future_expiry(self, state_file, mock_settings):
        state_file.write_text(str(time.time() + 3600))
        with patch("app.services.privacy_mode.settings", mock_settings):
            assert PrivacyMode().is_active()

    def test_inactive_when_expiry_passed(self, state_file, mock_settings):
        state_file.write_text(str(time.time() - 1))
        with patch("app.services.privacy_mode.settings", mock_settings):
            assert not PrivacyMode().is_active()

    def test_cleans_up_expired_file(self, state_file, mock_settings):
        state_file.write_text(str(time.time() - 1))
        with patch("app.services.privacy_mode.settings", mock_settings):
            PrivacyMode().is_active()
        assert not state_file.exists()

    def test_inactive_on_corrupt_file(self, state_file, mock_settings):
        state_file.write_text("not-a-float")
        with patch("app.services.privacy_mode.settings", mock_settings):
            assert not PrivacyMode().is_active()


class TestPrivacyModeActivate:
    def test_creates_file(self, state_file, mock_settings):
        with patch("app.services.privacy_mode.settings", mock_settings):
            PrivacyMode().activate(300)
        assert state_file.exists()

    def test_expiry_within_expected_range(self, state_file, mock_settings):
        before = time.time()
        with patch("app.services.privacy_mode.settings", mock_settings):
            expiry = PrivacyMode().activate(300)
        after = time.time()
        assert before + 300 <= expiry <= after + 300

    def test_returns_active_after_activate(self, state_file, mock_settings):
        with patch("app.services.privacy_mode.settings", mock_settings):
            pm = PrivacyMode()
            pm.activate(300)
            assert pm.is_active()


class TestPrivacyModeDeactivate:
    def test_removes_file(self, state_file, mock_settings):
        state_file.write_text(str(time.time() + 3600))
        with patch("app.services.privacy_mode.settings", mock_settings):
            PrivacyMode().deactivate()
        assert not state_file.exists()

    def test_no_error_when_already_inactive(self, state_file, mock_settings):
        with patch("app.services.privacy_mode.settings", mock_settings):
            PrivacyMode().deactivate()  # file never existed — must not raise
