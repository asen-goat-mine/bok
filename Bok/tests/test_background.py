import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from bok_core.config import BokConfig
from bok_core.errors import BokError
from bok_core.service import BokService


class BackgroundContracts(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.config = BokConfig(vault_root=Path(temporary.name), provider="none")
        self.service = BokService(self.config)
        self.service.process_captures = Mock()
        self.service.process_person_learning = Mock()

    def test_pause_survives_service_restart_without_losing_queued_work(self):
        self.service.background.update({"paused": True})
        restarted = BokService(self.config)
        restarted.process_captures = Mock()
        restarted.process_person_learning = Mock()
        self.assertTrue(restarted.process_background()["paused"])
        restarted.process_captures.assert_not_called()
        restarted.process_person_learning.assert_not_called()
        restarted.background.update({"paused": False, "batch_limit": 2, "interval_seconds": 60})
        self.assertEqual(restarted.process_background()["interval_seconds"], 60)
        restarted.process_captures.assert_called_once_with(limit=2, force=False)
        restarted.process_person_learning.assert_called_once_with(limit=2)

    def test_pause_during_capture_prevents_starting_the_learning_queue(self):
        self.service.process_captures.side_effect = lambda **kwargs: self.service.background.update({"paused": True})
        self.assertTrue(self.service.process_background()["paused"])
        self.service.process_person_learning.assert_not_called()

    def test_invalid_controls_cannot_resume_or_remove_limits(self):
        self.service.background.update({"paused": True})
        for values in ({"paused": "false"}, {"batch_limit": True}, {"batch_limit": 0}, {"batch_limit": 21}, {"interval_seconds": 0}, {"interval_seconds": 601}, {"provider": "cloud"}):
            with self.subTest(values=values), self.assertRaises(BokError):
                self.service.background.update(values)
        self.assertTrue(self.service.background.status()["paused"])

    def test_damaged_settings_fail_to_paused_and_can_be_repaired(self):
        self.service.background.update({"paused": False})
        self.service.background.path.write_text("broken json", encoding="utf-8")
        self.assertTrue(self.service.process_background()["paused"])
        self.service.process_captures.assert_not_called()
        self.assertFalse(self.service.background.update({"paused": False})["paused"])

    def test_new_vaults_do_not_spawn_ollama_when_unavailable(self):
        self.assertFalse(self.config.auto_start_local_model)
        self.service.provider.config.provider = "auto"
        with patch.object(self.service.provider, "_discover_ollama_model", return_value=""), patch("bok_core.provider.subprocess.Popen") as spawn:
            self.assertFalse(self.service.provider.ensure_local_provider())
        spawn.assert_not_called()


if __name__ == "__main__":
    unittest.main()
