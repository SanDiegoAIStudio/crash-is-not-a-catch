#!/usr/bin/env python3
"""The demo script is the worked example."""
import subprocess
import unittest
from pathlib import Path


class Demo(unittest.TestCase):
    def test_demo_prints_the_four_parts_and_exits_clean(self):
        """source: the demo shows a clean baseline, the unittest exit codes, the planted verdicts, and the missed selector."""
        root = Path(__file__).resolve().parents[1]
        proc = subprocess.run(
            ["/bin/sh", str(root / "examples" / "demo.sh")],
            cwd=str(root),
            capture_output=True,
            text=True,
            timeout=120,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        for label in (
            "--- 1. baseline ---",
            "--- 2. planted bug, unittest exit codes ---",
            "--- 3. planted bug, verdict ---",
            "--- 4. audit ---",
        ):
            self.assertIn(label, proc.stdout)
        self.assertIn("passed 2 of 2", proc.stdout)
        self.assertIn("crashed (TypeError): not a catch", proc.stdout)
        self.assertIn("caught 1 of 3", proc.stdout)
        self.assertIn("no such test: not a catch", proc.stdout)
        self.assertIn("Strong.test_total_format", proc.stdout)
        self.assertNotIn("Traceback", proc.stdout)
        self.assertNotIn("Traceback", proc.stderr)


if __name__ == "__main__":
    unittest.main()
