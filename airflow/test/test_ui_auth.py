"""Check stable UI credentials and the SimpleAuthManager password file contract."""

import json
from pathlib import Path
import stat
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).parent.parent))
from ui_auth import configure_ui


class UiAuthTests(unittest.TestCase):
    def test_stable_credentials_and_explicit_updates(self) -> None:
        """Preserve configured passwords, JSON-escape special characters and restrict access."""
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'ui-passwords.json'
            for _ in range(2):
                configure_ui(path, 'admin', 'quoted"password\\é')
                self.assertEqual(json.loads(path.read_text()), {'admin': 'quoted"password\\é'})
                self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
            configure_ui(path, 'other', 'changed')
            self.assertEqual(json.loads(path.read_text()), {'other': 'changed'})
            self.assertEqual(list(path.parent.iterdir()), [path])

    def test_invalid_settings_leave_current_credentials_intact(self) -> None:
        """Reject usernames that would alter the configured role list and empty passwords."""
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'ui-passwords.json'
            configure_ui(path, 'admin', 'unchanged')
            for username, password in [('admin:viewer', 'value'), ('admin,other', 'value'), ('admin', '')]:
                with self.assertRaises(ValueError):
                    configure_ui(path, username, password)
            self.assertEqual(json.loads(path.read_text()), {'admin': 'unchanged'})


if __name__ == '__main__':
    unittest.main()
