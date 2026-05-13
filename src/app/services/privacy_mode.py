"""
Privacy Mode State.
Reads and writes an expiration timestamp to /dev/shm/privacy_mode.
The file contains a single float (Unix timestamp). If it exists and
the timestamp is in the future, privacy mode is active.

Designed so the Telegram bot can activate/deactivate by
writing or deleting the same file.
"""

import logging
import time

from ..settings import settings

logger = logging.getLogger(__name__)


class PrivacyMode:
    def is_active(self) -> bool:
        state_file = settings.CONFIG.services.privacy_mode_state_file
        try:
            expiry = float(state_file.read_text().strip())
            if time.time() < expiry:
                return True
            # Expired — clean up
            state_file.unlink(missing_ok=True)
            return False
        except FileNotFoundError:
            return False
        except Exception as e:
            logger.warning(f"Privacy mode state unreadable: {e}")
            return False

    def activate(self, duration_seconds: int) -> float:
        """Activates privacy mode for the given duration. Returns expiry timestamp."""
        expiry = time.time() + duration_seconds
        settings.CONFIG.services.privacy_mode_state_file.write_text(str(expiry))
        logger.info(f"🔒 Privacy mode activated for {duration_seconds}s.")
        return expiry

    def deactivate(self):
        settings.CONFIG.services.privacy_mode_state_file.unlink(missing_ok=True)
        logger.info("🔓 Privacy mode deactivated.")
