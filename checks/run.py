"""Run one required protected task suite without treating an empty run as success."""

from pathlib import Path
import re
import sys
import unittest


def main():
    if len(sys.argv) != 2 or not re.fullmatch(r"CB[0-9]+", sys.argv[1]):
        raise SystemExit("Usage: checks/run.py CB<number>")
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    module = f"checks.test_{sys.argv[1].lower()}"
    loader = unittest.TestLoader()
    suite = loader.loadTestsFromName(module)
    if loader.errors or suite.countTestCases() == 0:
        for error in loader.errors:
            print(error, file=sys.stderr)
        raise SystemExit(f"No runnable tests for {module}")
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    executed = result.testsRun - len(result.skipped) - len(result.expectedFailures)
    return int(executed <= 0 or not result.wasSuccessful() or bool(result.expectedFailures))


if __name__ == "__main__":
    raise SystemExit(main())
