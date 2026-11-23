from hashlib import sha256
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from zipfile import ZipFile

from release.bundle import materialize, verify_bundle


class ReleasePackage(unittest.TestCase):
    def test_archive_and_source_digest_are_required_before_extraction(self):
        with TemporaryDirectory() as name:
            root = Path(name)
            archive = root / "release.zip"
            manifest = root / "manifest.json"
            files = {"brain/server.py": b"# stand", "deploy/app.py": b"# start",
                     "brain/storage/migrations/001.sql": b"-- fixture"}
            with ZipFile(archive, "w") as handle:
                for path, content in files.items():
                    handle.writestr(path, content)
            record = {"revision": "a" * 40, "archive_sha256": sha256(archive.read_bytes()).hexdigest(),
                      "files": {path: sha256(content).hexdigest() for path, content in files.items()}}
            manifest.write_text(json.dumps(record))
            self.assertEqual(verify_bundle(archive, manifest)["revision"], record["revision"])
            target = root / "stand"
            materialize(archive, manifest, target)
            self.assertEqual((target / "brain/server.py").read_bytes(), files["brain/server.py"])
            record["files"]["brain/server.py"] = "0" * 64
            manifest.write_text(json.dumps(record))
            invalid = root / "invalid"
            with self.assertRaises(ValueError):
                materialize(archive, manifest, invalid)
            self.assertFalse(invalid.exists())
