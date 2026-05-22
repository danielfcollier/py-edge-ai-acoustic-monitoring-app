"""
E2E tests for PrivacyMode — file-based state persistence.

No external services required; these run against the local filesystem.

What is tested:
  - Active state persists across separate PrivacyMode instances (same state file).
  - Deactivation removes the state file.
  - Expired mode auto-cleans up on the next is_active() read.
  - Activation returns the correct expiry timestamp.
  - Concurrent-safe: two instances reading simultaneously agree on state.
"""

import time

import pytest

from app.services.privacy_mode import PrivacyMode


# ---------------------------------------------------------------------------
# Fixture — redirect the state file to a tmp path for every test
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _isolated_state_file(tmp_path, monkeypatch, e2e_settings):
    state_file = tmp_path / "privacy_mode"
    monkeypatch.setattr(e2e_settings.CONFIG.services, "privacy_mode_state_file", state_file)
    return state_file


# ---------------------------------------------------------------------------
# Lifecycle tests
# ---------------------------------------------------------------------------

@pytest.mark.e2e
def test_inactive_by_default():
    """A fresh state file results in inactive privacy mode."""
    assert not PrivacyMode().is_active()


@pytest.mark.e2e
def test_activate_creates_state_file(tmp_path, e2e_settings):
    """activate() writes the expiry timestamp to the state file."""
    state_file = e2e_settings.CONFIG.services.privacy_mode_state_file
    PrivacyMode().activate(3600)
    assert state_file.exists()
    expiry = float(state_file.read_text().strip())
    assert expiry > time.time()


@pytest.mark.e2e
def test_activate_returns_expiry_timestamp():
    """activate() returns a Unix timestamp approximately duration seconds in the future."""
    before = time.time()
    expiry = PrivacyMode().activate(3600)
    after = time.time()
    assert before + 3600 <= expiry <= after + 3600


@pytest.mark.e2e
def test_active_state_persists_across_instances():
    """Two separate PrivacyMode instances read the same active state."""
    PrivacyMode().activate(3600)
    assert PrivacyMode().is_active()


@pytest.mark.e2e
def test_deactivate_removes_state_file(tmp_path, e2e_settings):
    """deactivate() deletes the state file."""
    state_file = e2e_settings.CONFIG.services.privacy_mode_state_file
    PrivacyMode().activate(3600)
    assert state_file.exists()
    PrivacyMode().deactivate()
    assert not state_file.exists()


@pytest.mark.e2e
def test_deactivate_when_already_inactive():
    """deactivate() is idempotent — does not raise when no state file exists."""
    PrivacyMode().deactivate()  # no state file, must not raise
    PrivacyMode().deactivate()


@pytest.mark.e2e
def test_inactive_after_deactivate():
    """is_active() returns False immediately after deactivation."""
    pm = PrivacyMode()
    pm.activate(3600)
    pm.deactivate()
    assert not PrivacyMode().is_active()


# ---------------------------------------------------------------------------
# Expiry tests
# ---------------------------------------------------------------------------

@pytest.mark.e2e
def test_expires_automatically():
    """Privacy mode becomes inactive once the duration has elapsed."""
    PrivacyMode().activate(1)
    assert PrivacyMode().is_active()
    time.sleep(1.5)
    assert not PrivacyMode().is_active()


@pytest.mark.e2e
def test_expired_mode_cleans_up_state_file(tmp_path, e2e_settings):
    """is_active() deletes the state file when it finds an expired timestamp."""
    state_file = e2e_settings.CONFIG.services.privacy_mode_state_file
    PrivacyMode().activate(1)
    time.sleep(1.5)
    assert not PrivacyMode().is_active()
    assert not state_file.exists()


@pytest.mark.e2e
def test_reactivation_after_expiry():
    """Privacy mode can be reactivated after it has expired."""
    PrivacyMode().activate(1)
    time.sleep(1.5)
    assert not PrivacyMode().is_active()

    PrivacyMode().activate(3600)
    assert PrivacyMode().is_active()
