"""Independent SDK version pinning and capability checks."""

import unittest

from brain.sdk.api import VersionedSDK


class SDKVersions(unittest.TestCase):
    def setUp(self):
        self.sdk = VersionedSDK()

    def test_existing_session_keeps_previous_behavior(self):
        self.sdk.register("v1", {"read": lambda key: f"v1:{key}"})
        self.assertEqual(self.sdk.open_session("old", "reader"), "v1")
        self.sdk.register("v2", {"read": lambda key: f"v2:{key}", "check": lambda: "checked"})
        self.assertEqual(self.sdk.open_session("new", "reader"), "v2")
        self.assertEqual(self.sdk.describe("old"), {"revision": "v1", "operations": ["read"]})
        self.assertEqual(self.sdk.describe("new"), {"revision": "v2", "operations": ["check", "read"]})
        self.assertEqual(self.sdk.call("old", "read", key="rule"), "v1:rule")
        self.assertEqual(self.sdk.call("new", "read", key="rule"), "v2:rule")
        self.assertEqual(self.sdk.open_session("old", "reader"), "v1")

    def test_describe_and_call_enforce_the_same_role(self):
        self.sdk.register("v1", {"read": lambda: "read", "draft": lambda: "draft", "task": lambda: "task"})
        for role in ("reader", "writer", "operator"):
            self.sdk.open_session(role, role)
        self.assertEqual(self.sdk.describe("reader")["operations"], ["read"])
        self.assertEqual(self.sdk.describe("writer")["operations"], ["draft", "read"])
        with self.assertRaises(PermissionError):
            self.sdk.call("reader", "draft")
        with self.assertRaises(PermissionError):
            self.sdk.call("writer", "task")
        self.assertEqual(self.sdk.call("operator", "task"), "task")
