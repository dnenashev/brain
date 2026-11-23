import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from deploy.app import load_config


class ClosedDeployment(unittest.TestCase):
    def test_configuration_rejects_public_bind_and_bad_port(self):
        with TemporaryDirectory() as name:
            root = Path(name)
            path = root / "stand.json"
            credentials = root / "credentials.json"
            credentials.write_text(json.dumps([
                {"credential_sha256": "a" * 64, "actor_id": "reader-1", "project_id": "demo", "role": "reader"}
            ]))
            credentials.chmod(0o600)
            fields = {"bind": "127.0.0.1", "http_port": 0, "review_port": 0,
                      "data_dir": "data", "credential_file": "credentials.json"}
            path.write_text(json.dumps(fields))
            config = load_config(path)
            self.assertEqual(config["bind"], "127.0.0.1")
            self.assertEqual(Path(config["data_dir"]).resolve(), (root / "data").resolve())
            for change in ({"bind": "0.0.0.0"}, {"bind": "::"}, {"review_port": 65536}, {"http_port": True}):
                path.write_text(json.dumps(dict(fields, **change)))
                with self.assertRaises(ValueError):
                    load_config(path)
