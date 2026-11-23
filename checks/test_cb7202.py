from unittest import TestCase
from unittest.mock import Mock, patch

from brain.sandbox.runner import run_code


class NativeSupervisorWiring(TestCase):
    @patch("brain.sandbox.runner.sys.platform", "darwin")
    @patch("brain.sandbox._native.run")
    def test_runner_hands_session_bound_dispatch_and_limits_to_native(self, native):
        sdk = Mock()
        sdk.call.return_value = "record"
        native.return_value = {"status": "ok", "result": 42, "stdout": "", "stderr": ""}
        result = run_code("result = 42", sdk=sdk, session_id="bound", timeout_seconds=1.5,
                          memory_bytes=50000000, output_bytes=1100)
        self.assertEqual(result, native.return_value)
        args, kwargs = native.call_args
        self.assertEqual(args, ("result = 42",))
        self.assertEqual((kwargs["timeout_seconds"], kwargs["memory_bytes"], kwargs["output_bytes"]),
                         (1.5, 50000000, 1100))
        broker = kwargs["dispatch"]
        self.assertEqual(broker({"operation": "read", "parameters": {"doc_id": "fixture"}}),
                         {"ok": True, "result": "record"})
        sdk.call.assert_called_once_with("bound", "read", doc_id="fixture")
