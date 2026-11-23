import unittest

from brain.sandbox.runner import dispatch_sdk_request


class ScopedSDKBroker(unittest.TestCase):
    def setUp(self):
        self.calls = []

        class SDK:
            def call(inner, session_id, operation, **parameters):
                self.calls.append((session_id, operation, parameters))
                if operation == "task":
                    raise PermissionError("private detail from the SDK")
                return {"body": "fixture record"}

        self.sdk = SDK()

    def test_valid_tools_use_bound_session_and_leave_parameters_intact(self):
        request = {"operation": "read", "parameters": {"doc_id": "fixture", "revision": 2}}
        reply = dispatch_sdk_request(self.sdk, "bound", request)
        self.assertEqual(reply, {"ok": True, "result": {"body": "fixture record"}})
        self.assertEqual(self.calls, [("bound", "read", {"doc_id": "fixture", "revision": 2})])

    def test_broker_rejects_session_override_and_non_sdk_operations(self):
        for request in (
            {"operation": "read", "parameters": {}, "session_id": "someone-else"},
            {"operation": "get_secret", "parameters": {}},
            {"operation": "read", "parameters": []},
        ):
            reply = dispatch_sdk_request(self.sdk, "bound", request)
            self.assertIs(reply["ok"], False)
        self.assertEqual(self.calls, [])

    def test_denial_is_returned_without_sdk_exception_details(self):
        reply = dispatch_sdk_request(self.sdk, "bound", {"operation": "task", "parameters": {}})
        self.assertIs(reply["ok"], False)
        self.assertNotIn("private detail", str(reply))
