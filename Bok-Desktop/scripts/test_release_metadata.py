from __future__ import annotations

import hashlib
import shutil
import tempfile
import unittest
from pathlib import Path

from release_metadata import release_version, write_checksums


WORKSPACE = Path(__file__).resolve().parents[2]


class ReleaseMetadataTests(unittest.TestCase):
    def test_versions_match_and_drift_is_rejected(self):
        version = release_version(WORKSPACE)
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            for relative in (
                "Bok/bok_core/version.py", "Bok-Desktop/src-tauri/tauri.conf.json",
                "Bok-Desktop/src-tauri/Cargo.toml", "Bok-Desktop/Cargo.lock",
            ):
                target = root / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(WORKSPACE / relative, target)
            self.assertEqual(release_version(root), version)
            (root / "Bok/bok_core/version.py").write_text('VERSION = "0.0.0"\n')
            with self.assertRaisesRegex(ValueError, "must match"):
                release_version(root)

    def test_checksums_are_relative_and_repeatable(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            with self.assertRaisesRegex(ValueError, "No release"):
                write_checksums(root)
            (root / "Bok.zip").write_bytes(b"synthetic archive")
            write_checksums(root)
            expected = f'{hashlib.sha256(b"synthetic archive").hexdigest()}  Bok.zip\n'
            self.assertEqual((root / "SHA256SUMS.txt").read_text(), expected)
            write_checksums(root)
            self.assertEqual((root / "SHA256SUMS.txt").read_text(), expected)


if __name__ == "__main__":
    unittest.main(verbosity=2)
