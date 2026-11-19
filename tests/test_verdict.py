#!/usr/bin/env python3
"""Verdict rules: an assertion is a catch, and the other endings are not."""
import contextlib
import io
import os
import signal
import subprocess
import sys
import tempfile
import textwrap
import time
import unittest
from pathlib import Path
from unittest import mock

import catch


class Temp(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def write(self, rel, text):
        path = self.root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(textwrap.dedent(text).lstrip("\n"), encoding="utf-8")
        return path

    def one(self, selector, timeout=60.0):
        return catch.verdict(selector, cwd=str(self.root), timeout=timeout)

    def cli(self, args):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = catch.main(args)
        return code, out.getvalue(), err.getvalue()


class VerdictRules(Temp):
    def test_an_assertion_failure_is_caught(self):
        """source: a planted bug counts as caught only when an assertion fails."""
        self.write("mod.py", """
            import unittest
            class T(unittest.TestCase):
                def test_value(self):
                    self.assertEqual(1, 2)
        """)
        result = self.one("mod.T.test_value")
        self.assertEqual(result.kind, "caught")
        self.assertEqual(result.detail, "")
        self.assertTrue(result.caught)
        self.assertEqual(str(result), "caught")

    def test_a_raised_exception_is_not_a_catch(self):
        """source: an exception other than an assertion is a crash, not a catch."""
        self.write("mod.py", """
            import unittest
            class T(unittest.TestCase):
                def test_boom(self):
                    raise TypeError("bad")
        """)
        result = self.one("mod.T.test_boom")
        self.assertEqual(result.kind, "crashed")
        self.assertEqual(result.detail, "TypeError")
        self.assertFalse(result.caught)
        self.assertEqual(str(result), "crashed (TypeError): not a catch")

    def test_a_passing_run_is_not_a_catch(self):
        """source: a test that finishes cleanly did not catch a planted bug."""
        self.write("mod.py", """
            import unittest
            class T(unittest.TestCase):
                def test_ok(self):
                    print("SECRET-NOISE")
                    self.assertTrue(True)
        """)
        result = self.one("mod.T.test_ok")
        self.assertEqual(str(result), "passed: not a catch")
        self.assertFalse(result.caught)
        code, out, err = self.cli(["verdict", "--cwd", str(self.root), "mod.T.test_ok"])
        self.assertNotIn("SECRET-NOISE", out)
        self.assertNotIn("SECRET-NOISE", err)
        self.assertEqual(err, "")

    def test_a_missing_method_did_not_load(self):
        """source: a selector whose method is not there did not run, so it is not a catch."""
        self.write("mod.py", """
            import unittest
            class T(unittest.TestCase):
                def test_ok(self):
                    self.assertTrue(True)
        """)
        result = self.one("mod.T.test_missing")
        self.assertEqual(str(result), "no such test: not a catch")
        self.assertEqual(result.kind, "no such test")

    def test_a_missing_module_did_not_load(self):
        """source: an import that fails means the selector did not load."""
        result = self.one("no_such_module.T.test_ok")
        self.assertEqual(result.kind, "no such test")

    def test_a_missing_class_did_not_load(self):
        """source: a class that is not in the module did not load."""
        self.write("mod.py", """
            import unittest
            class T(unittest.TestCase):
                def test_ok(self):
                    self.assertTrue(True)
        """)
        self.assertEqual(self.one("mod.Missing.test_ok").kind, "no such test")

    def test_a_name_that_is_not_a_test_did_not_load(self):
        """source: a name that is not a test is a load failure, not a crash."""
        self.write("mod.py", "VALUE = 'hello'\n")
        self.assertEqual(self.one("mod.VALUE").kind, "no such test")

    def test_a_bare_function_is_called_once_while_loading(self):
        """source: a bare function given as a selector is called once by the loader while it loads."""
        self.write("mod.py", """
            def not_a_test():
                open("hits.txt", "a", encoding="utf-8").write("x")
                return None
        """)
        result = self.one("mod.not_a_test")
        self.assertEqual(result.kind, "no such test")
        self.assertEqual((self.root / "hits.txt").read_text(encoding="utf-8"), "x")

    def test_every_skipped_test_means_nothing_ran(self):
        """source: a loaded selector where every test was skipped did not run."""
        self.write("mod.py", """
            import unittest
            class T(unittest.TestCase):
                @unittest.skip("later")
                def test_one(self):
                    self.assertTrue(False)
                @unittest.skip("later")
                def test_two(self):
                    raise RuntimeError("nope")
        """)
        self.assertEqual(self.one("mod.T").kind, "nothing ran")

    def test_an_empty_class_runs_nothing(self):
        """source: a loaded selector with no tests is not a catch."""
        self.write("mod.py", """
            import unittest
            class Empty(unittest.TestCase):
                pass
        """)
        self.assertEqual(str(self.one("mod.Empty")), "nothing ran: not a catch")

    def test_one_pass_beside_a_skip_counts_as_passed(self):
        """source: one test that finishes is enough to call the selector passed."""
        self.write("mod.py", """
            import unittest
            class T(unittest.TestCase):
                def test_ok(self):
                    self.assertTrue(True)
                @unittest.skip("later")
                def test_later(self):
                    self.assertTrue(False)
        """)
        self.assertEqual(self.one("mod.T").kind, "passed")

    def test_a_timeout_is_not_a_catch(self):
        """source: a child killed at the timeout is not a catch, even if the test would have failed."""
        self.write("mod.py", """
            import time
            import unittest
            class T(unittest.TestCase):
                def test_slow(self):
                    time.sleep(30)
                    self.assertTrue(False)
        """)
        result = self.one("mod.T.test_slow", timeout=0.6)
        self.assertEqual(str(result), "timed out: not a catch")
        self.assertEqual(result.detail, "")
        self.assertFalse(result.caught)

    def test_one_assertion_beats_another_crash(self):
        """source: one clean assertion failure is a catch even when another test under the selector crashes."""
        self.write("mod.py", """
            import unittest
            class Mix(unittest.TestCase):
                def test_a_fail(self):
                    self.assertTrue(False)
                def test_b_boom(self):
                    raise RuntimeError("boom")
        """)
        self.assertEqual(self.one("mod.Mix").kind, "caught")

    def test_a_crash_beats_a_pass(self):
        """source: with no assertion failure, a raised exception wins over a test that passed."""
        self.write("mod.py", """
            import unittest
            class Mix(unittest.TestCase):
                def test_a_ok(self):
                    self.assertTrue(True)
                def test_b_boom(self):
                    raise RuntimeError("boom")
        """)
        result = self.one("mod.Mix")
        self.assertEqual(result.kind, "crashed")
        self.assertEqual(result.detail, "RuntimeError")

    def test_a_failed_subtest_is_caught(self):
        """source: a failed subTest is an assertion failure."""
        self.write("mod.py", """
            import unittest
            class T(unittest.TestCase):
                def test_parts(self):
                    with self.subTest(i=1):
                        self.assertEqual(1, 0)
        """)
        self.assertTrue(self.one("mod.T.test_parts").caught)

    def test_a_subtest_exception_is_a_crash(self):
        """source: an exception inside a subTest is a crash when no assertion failed."""
        self.write("mod.py", """
            import unittest
            class T(unittest.TestCase):
                def test_parts(self):
                    with self.subTest(i=1):
                        raise RuntimeError("boom")
        """)
        self.assertEqual(self.one("mod.T.test_parts").detail, "RuntimeError")

    def test_setup_exception_is_a_crash(self):
        """source: an exception from setUp is a crash, not a catch."""
        self.write("mod.py", """
            import unittest
            class T(unittest.TestCase):
                def setUp(self):
                    raise TypeError("setup")
                def test_ok(self):
                    self.assertTrue(True)
        """)
        self.assertEqual(self.one("mod.T").detail, "TypeError")

    def test_setupclass_exception_is_a_crash(self):
        """source: an exception from setUpClass is a crash, not a catch."""
        self.write("mod.py", """
            import unittest
            class T(unittest.TestCase):
                @classmethod
                def setUpClass(cls):
                    raise ValueError("class")
                def test_ok(self):
                    self.assertTrue(True)
        """)
        self.assertEqual(self.one("mod.T").detail, "ValueError")

    def test_setupmodule_exception_is_a_crash(self):
        """source: an exception from setUpModule is a crash, not a catch."""
        self.write("mod.py", """
            import unittest
            def setUpModule():
                raise KeyError("mod")
            class T(unittest.TestCase):
                def test_ok(self):
                    self.assertTrue(True)
        """)
        self.assertEqual(self.one("mod.T").detail, "KeyError")

    def test_setupclass_assertion_is_caught(self):
        """source: an assertion that fails in setUpClass is a catch."""
        self.write("mod.py", """
            import unittest
            class T(unittest.TestCase):
                @classmethod
                def setUpClass(cls):
                    assert False
                def test_ok(self):
                    self.assertTrue(True)
        """)
        self.assertEqual(str(self.one("mod.T")), "caught")

    def test_setupmodule_assertion_is_caught(self):
        """source: an assertion that fails in setUpModule is a catch."""
        self.write("mod.py", """
            import unittest
            def setUpModule():
                assert False
            class T(unittest.TestCase):
                def test_ok(self):
                    self.assertTrue(True)
        """)
        self.assertEqual(str(self.one("mod.T")), "caught")

    def test_setupclass_value_error_is_a_crash(self):
        """source: an exception other than an assertion from setUpClass is a crash."""
        self.write("mod.py", """
            import unittest
            class T(unittest.TestCase):
                @classmethod
                def setUpClass(cls):
                    raise ValueError("class")
                def test_ok(self):
                    self.assertTrue(True)
        """)
        self.assertEqual(str(self.one("mod.T")), "crashed (ValueError): not a catch")

    def test_expected_failure_and_unexpected_success_count_as_passed(self):
        """source: an expected failure and an unexpected success count as passed."""
        self.write("mod.py", """
            import unittest
            class T(unittest.TestCase):
                @unittest.expectedFailure
                def test_will_fail(self):
                    self.assertTrue(False)
                @unittest.expectedFailure
                def test_will_pass(self):
                    self.assertTrue(True)
        """)
        self.assertEqual(self.one("mod.T").kind, "passed")

    def test_an_expected_failure_alone_counts_as_passed(self):
        """source: an expected failure counts as passed."""
        self.write("mod.py", """
            import unittest
            class T(unittest.TestCase):
                @unittest.expectedFailure
                def test_will_fail(self):
                    self.assertTrue(False)
        """)
        self.assertEqual(str(self.one("mod.T.test_will_fail")), "passed: not a catch")

    def test_an_unexpected_success_alone_counts_as_passed(self):
        """source: an unexpected success counts as passed."""
        self.write("mod.py", """
            import unittest
            class T(unittest.TestCase):
                @unittest.expectedFailure
                def test_will_pass(self):
                    self.assertTrue(True)
        """)
        self.assertEqual(str(self.one("mod.T.test_will_pass")), "passed: not a catch")

    def test_a_missing_folder_is_refused(self):
        """source: the library refuses a folder that does not exist."""
        missing = self.root / "no-such-folder"
        with self.assertRaises(ValueError) as caught:
            catch.verdict("a.B.test_c", cwd=str(missing))
        self.assertTrue(str(caught.exception).startswith("not a folder:"))

    def test_a_timeout_of_nan_is_refused(self):
        """source: a timeout of nan is refused by the library and by the command line."""
        with self.assertRaises(ValueError) as caught:
            catch.verdict("a.B.test_c", timeout=float("nan"))
        self.assertEqual(str(caught.exception), "timeout must be a positive number")
        code, out, err = self.cli(["verdict", "--timeout", "nan", "a.B.test_c"])
        self.assertEqual(code, 2)
        self.assertEqual(out, "")
        self.assertEqual(len(err.splitlines()), 1)
        self.assertNotIn("Traceback", err)

    def test_an_assert_inside_the_code_under_test_is_caught(self):
        """source: an assert that fails inside the code under test counts as an assertion failure."""
        self.write("mod.py", """
            import unittest
            def work():
                assert False
            class T(unittest.TestCase):
                def test_work(self):
                    work()
        """)
        self.assertTrue(self.one("mod.T.test_work").caught)

    def test_an_exception_while_importing_did_not_load(self):
        """source: an exception raised while the module is imported is a crash, because nothing reached an assertion."""
        self.write("mod.py", "raise ValueError('nope')\n")
        result = self.one("mod")
        self.assertEqual(result.kind, "crashed")
        self.assertEqual(result.detail, "ValueError")
        self.assertEqual(str(result), "crashed (ValueError): not a catch")

    def test_a_syntax_error_did_not_load(self):
        """source: a module that cannot be parsed crashed while it was imported."""
        self.write("mod.py", "def (\n")
        result = self.one("mod.T")
        self.assertEqual(result.kind, "crashed")
        self.assertEqual(result.detail, "SyntaxError")
        self.assertEqual(str(result), "crashed (SyntaxError): not a catch")

    def test_the_working_directory_is_first_on_the_import_path(self):
        """source: the child imports from the requested folder before the folder that holds this tool."""
        self.write("catch.py", "MARKER = 'temp-copy'\n")
        self.write("check_mod.py", """
            import catch
            import unittest
            class T(unittest.TestCase):
                def test_from_here(self):
                    self.assertEqual(catch.MARKER, "temp-copy")
        """)
        self.assertEqual(self.one("check_mod.T.test_from_here").kind, "passed")

    def test_a_dotted_package_selector_runs(self):
        """source: a selector may name a module inside a package."""
        self.write("pkg/__init__.py", "")
        self.write("pkg/test_box.py", """
            import unittest
            class Box(unittest.TestCase):
                def test_ok(self):
                    self.assertEqual(1 + 1, 2)
        """)
        self.assertEqual(self.one("pkg.test_box.Box.test_ok").kind, "passed")

    def test_a_child_that_writes_no_report_is_an_exit_crash(self):
        """source: a child that ends without writing its report is a crash named by its exit code."""
        kind, detail = catch._classify(None, 3, False)
        self.assertEqual((kind, detail), ("crashed", "exit 3"))
        self.assertEqual(str(catch.Verdict("s", kind, detail)), "crashed (exit 3): not a catch")


class ImportRules(Temp):
    def test_a_missing_helper_name_crashes_on_import(self):
        """source: a helper that exists but lacks the imported name breaks the test module before any assertion."""
        self.write("helper.py", """
            def other():
                return 1
        """)
        self.write("test_mod.py", """
            from helper import total
            import unittest
            class Case(unittest.TestCase):
                def test_x(self):
                    self.assertTrue(True)
        """)
        self.assertEqual(str(self.one("test_mod.Case.test_x")), "crashed (ImportError): not a catch")

    def test_a_syntax_error_in_the_test_module_is_a_crash(self):
        """source: a syntax error in the test module is a crash, because the test exists and never reached an assertion."""
        self.write("test_mod.py", "def (\n")
        self.assertEqual(str(self.one("test_mod.Case.test_x")), "crashed (SyntaxError): not a catch")

    def test_a_runtime_error_while_importing_is_a_crash(self):
        """source: an exception raised while the test module is imported is a crash."""
        self.write("test_mod.py", """
            raise RuntimeError("broke")
        """)
        self.assertEqual(str(self.one("test_mod.Case.test_x")), "crashed (RuntimeError): not a catch")

    def test_a_missing_helper_module_is_an_import_crash(self):
        """source: a test module that imports a module which is not there crashes while it is imported."""
        self.write("test_mod.py", """
            import no_such_helper
        """)
        self.assertEqual(
            str(self.one("test_mod.Case.test_x")),
            "crashed (ModuleNotFoundError): not a catch",
        )

    def test_a_selector_whose_first_part_is_not_a_module_names_nothing(self):
        """source: a selector whose first part is not a module names nothing."""
        self.assertEqual(str(self.one("no_such_module.Case.test_x")), "no such test: not a catch")

    def test_a_missing_class_or_method_after_a_clean_import_names_nothing(self):
        """source: a module that imports cleanly, with a missing class or a missing method, names nothing."""
        self.write("mod.py", """
            import unittest
            class Case(unittest.TestCase):
                def test_ok(self):
                    self.assertTrue(True)
        """)
        self.assertEqual(str(self.one("mod.Missing.test_x")), "no such test: not a catch")
        self.assertEqual(str(self.one("mod.Case.test_missing")), "no such test: not a catch")

    def test_a_package_module_that_raises_on_import_is_a_crash(self):
        """source: a module inside a package that raises while it is imported is a crash."""
        self.write("pkg/__init__.py", "")
        self.write("pkg/test_mod.py", """
            raise RuntimeError("broke")
        """)
        self.assertEqual(
            str(self.one("pkg.test_mod.Case.test_x")),
            "crashed (RuntimeError): not a catch",
        )

    def test_a_missing_inner_module_names_nothing(self):
        """source: a package that exists with no such inner module names nothing."""
        self.write("pkg/__init__.py", "")
        self.assertEqual(str(self.one("pkg.test_mod.Case.test_x")), "no such test: not a catch")

    def test_a_crashed_import_is_not_caught(self):
        """source: a selector that crashes while importing is not caught."""
        self.write("mod.py", """
            raise RuntimeError("broke")
        """)
        code, out, err = self.cli(["verdict", "--cwd", str(self.root), "mod.Case.test_x"])
        self.assertEqual(err, "")
        self.assertNotIn("Traceback", out)
        self.assertIn("crashed (RuntimeError): not a catch", out)
        self.assertTrue(out.endswith("caught 0 of 1\n"))
        self.assertEqual(code, 1)

    def test_a_broken_module_is_imported_once(self):
        """source: a broken module is not imported again for each shorter prefix of the selector."""
        self.write("mod.py", """
            open("hits.txt", "a", encoding="utf-8").write("x\\n")
            raise RuntimeError("broke")
        """)
        result = self.one("mod.Case.test_x")
        self.assertEqual(str(result), "crashed (RuntimeError): not a catch")
        text = (self.root / "hits.txt").read_text(encoding="utf-8")
        self.assertEqual(text.splitlines(), ["x"])


class VerdictCommand(Temp):
    def test_every_caught_selector_exits_clean_and_counts(self):
        """source: the count line names how many selectors were caught, and the process exits clean only when all were."""
        self.write("mod.py", """
            import unittest
            class T(unittest.TestCase):
                def test_bad(self):
                    self.assertTrue(False)
        """)
        code, out, err = self.cli(["verdict", "--cwd", str(self.root), "--timeout", "5", "mod.T.test_bad"])
        self.assertEqual(err, "")
        self.assertTrue(out.endswith("caught 1 of 1\n"))
        self.assertEqual(code, 0)

    def test_a_missed_selector_exits_with_failure(self):
        """source: the process fails when any selector was not caught."""
        self.write("mod.py", """
            import unittest
            class Wide(unittest.TestCase):
                def test_ok(self):
                    self.assertTrue(True)
            class Thin(unittest.TestCase):
                def test_bad(self):
                    self.assertTrue(False)
        """)
        code, out, err = self.cli([
            "verdict", "--cwd", str(self.root),
            "mod.Wide.test_ok", "mod.Thin.test_bad",
        ])
        self.assertEqual(err, "")
        lines = out.splitlines()
        self.assertEqual(lines[0], "mod.Wide.test_ok   passed: not a catch")
        self.assertEqual(lines[1], "mod.Thin.test_bad  caught")
        self.assertEqual(lines[2], "caught 1 of 2")
        self.assertEqual(code, 1)

    def test_baseline_counts_passes_and_exits_clean_only_when_all_pass(self):
        """source: baseline asks whether every selector passes on clean code, because a crash there would score every planted bug as caught."""
        self.write("mod.py", """
            import unittest
            class T(unittest.TestCase):
                def test_ok(self):
                    self.assertTrue(True)
                def test_bad(self):
                    self.assertTrue(False)
        """)
        clean, out, err = self.cli(["verdict", "--baseline", "--cwd", str(self.root), "mod.T.test_ok"])
        self.assertEqual(err, "")
        self.assertTrue(out.endswith("passed 1 of 1\n"))
        self.assertEqual(clean, 0)
        dirty, out, _err = self.cli(["verdict", "--baseline", "--cwd", str(self.root), "mod.T.test_bad"])
        self.assertIn("passed 0 of 1", out)
        self.assertEqual(dirty, 1)

    def test_usage_errors_go_to_stderr_without_a_traceback(self):
        """source: a missing selector, a bad folder, or a timeout that is not a positive number is a usage error."""
        missing = self.root / "absent"
        cases = [
            ["verdict"],
            ["verdict", "--cwd"],
            ["verdict", "--cwd", str(missing), "mod.T"],
            ["verdict", "--timeout"],
            ["verdict", "--timeout", "0", "mod.T"],
            ["verdict", "--timeout", "-1", "mod.T"],
            ["verdict", "--timeout", "nope", "mod.T"],
            ["verdict", "--nope", "mod.T"],
            [],
            ["nope"],
        ]
        for args in cases:
            with self.subTest(args=args):
                code, out, err = self.cli(args)
                self.assertEqual(code, 2)
                self.assertEqual(out, "")
                self.assertEqual(len(err.splitlines()), 1)
                self.assertNotIn("Traceback", err)


_CLEAN = "def add(a, b):\n    return a > b\n"
_BUGGY = "def add(a, b):\n    return a < b\n"
_WHEN = 1700000000


class CacheRules(Temp):
    def _same_size(self):
        self.assertEqual(len(_CLEAN.encode("utf-8")), len(_BUGGY.encode("utf-8")))

    def _stamp(self):
        for name in ("calc.py", "test_calc.py"):
            os.utime(self.root / name, (_WHEN, _WHEN))

    def _shared(self):
        folder = self.root / "shared-cache"
        folder.mkdir()
        return folder

    def test_same_second_and_same_size_still_sees_the_plant(self):
        """source: a planted change of the same size in the same whole second is still caught."""
        self._same_size()
        shared = self._shared()
        self.write("test_calc.py", """
            import unittest
            import calc
            class T(unittest.TestCase):
                def test_cmp(self):
                    self.assertTrue(calc.add(2, 1))
        """)
        with mock.patch.dict(os.environ, {"PYTHONPYCACHEPREFIX": str(shared)}):
            os.environ.pop("PYTHONDONTWRITEBYTECODE", None)
            self.write("calc.py", _CLEAN)
            self._stamp()
            self.assertEqual(self.one("test_calc.T.test_cmp").kind, "passed")
            self.write("calc.py", _BUGGY)
            self._stamp()
            self.assertEqual(self.one("test_calc.T.test_cmp").kind, "caught")
            self.write("calc.py", _CLEAN)
            self._stamp()
            self.assertEqual(self.one("test_calc.T.test_cmp").kind, "passed")

    def test_a_cache_primed_from_outside_does_not_hide_the_plant(self):
        """source: bytecode left in a shared cache by an earlier run is not reused."""
        self._same_size()
        shared = self._shared()
        self.write("calc.py", _CLEAN)
        self.write("test_calc.py", """
            import unittest
            import calc
            class T(unittest.TestCase):
                def test_cmp(self):
                    self.assertTrue(calc.add(2, 1))
        """)
        self._stamp()
        with mock.patch.dict(os.environ, {"PYTHONPYCACHEPREFIX": str(shared)}):
            os.environ.pop("PYTHONDONTWRITEBYTECODE", None)
            primed = subprocess.run(
                [sys.executable, "-m", "unittest", "test_calc"],
                cwd=str(self.root),
                env=os.environ.copy(),
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
            )
            self.assertEqual(primed.returncode, 0, "the priming run did not pass")
            self.assertTrue(any(shared.rglob("*.pyc")))
            self.write("calc.py", _BUGGY)
            self._stamp()
            self.assertEqual(self.one("test_calc.T.test_cmp").kind, "caught")

    def test_the_child_sees_its_own_cache_and_no_bytecode_writing(self):
        """source: the child turns bytecode writing off and gets a cache folder that is removed when the verdict ends."""
        self._same_size()
        shared = self._shared()
        self.write("calc.py", _CLEAN)
        self.write("test_calc.py", """
            import sys
            import unittest
            from pathlib import Path
            import calc
            class T(unittest.TestCase):
                def test_cmp(self):
                    Path("seen.txt").write_text(
                        "%s\\n%s\\n" % (sys.dont_write_bytecode, sys.pycache_prefix),
                        encoding="utf-8",
                    )
                    self.assertTrue(calc.add(2, 1))
        """)
        with mock.patch.dict(os.environ, {"PYTHONPYCACHEPREFIX": str(shared)}):
            os.environ.pop("PYTHONDONTWRITEBYTECODE", None)
            result = self.one("test_calc.T.test_cmp")
        self.assertEqual(result.kind, "passed")
        lines = (self.root / "seen.txt").read_text(encoding="utf-8").splitlines()
        self.assertEqual(lines[0], "True")
        folder = Path(lines[1])
        self.assertNotEqual(folder.resolve(), shared.resolve())
        self.assertFalse(folder.exists())
        self.assertEqual([path for path in shared.rglob("*") if path.is_file()], [])

    def test_the_cache_folder_is_removed_when_the_run_times_out(self):
        """source: the cache folder is removed when the child is killed at the time limit."""
        made = []
        real_mkdtemp = tempfile.mkdtemp

        def record(*args, **kwargs):
            folder = real_mkdtemp(*args, **kwargs)
            made.append(folder)
            return folder

        self.write("mod.py", """
            import time
            import unittest
            class T(unittest.TestCase):
                def test_slow(self):
                    time.sleep(30)
        """)
        with mock.patch("catch.tempfile.mkdtemp", record):
            result = self.one("mod.T.test_slow", timeout=0.6)
        self.assertEqual(result.kind, "timed out")
        self.assertEqual(len(made), 1)
        self.assertFalse(Path(made[0]).exists())

    def test_a_process_the_test_starts_sees_the_plant(self):
        """source: a Python process the test starts sees a planted change of the same size in the same second."""
        self._same_size()
        shared = self._shared()
        self.write("calc.py", _CLEAN)
        self.write("test_calc.py", """
            import subprocess
            import sys
            import unittest
            from pathlib import Path
            class T(unittest.TestCase):
                def test_cmp(self):
                    proc = subprocess.run(
                        [sys.executable, "-c", "import calc; print(calc.add(2, 1))"],
                        stdout=subprocess.PIPE,
                        stderr=subprocess.PIPE,
                        text=True,
                        check=False,
                    )
                    Path("seen.txt").write_text(proc.stdout, encoding="utf-8")
        """)
        self._stamp()
        with mock.patch.dict(os.environ, {"PYTHONPYCACHEPREFIX": str(shared)}):
            os.environ.pop("PYTHONDONTWRITEBYTECODE", None)
            primed = subprocess.run(
                [sys.executable, "-m", "unittest", "test_calc.T.test_cmp"],
                cwd=str(self.root),
                env=os.environ.copy(),
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                text=True,
                check=False,
            )
            self.assertEqual(primed.returncode, 0, primed.stderr)
            self.assertTrue(any(shared.rglob("*.pyc")))
            self.write("calc.py", _BUGGY)
            self._stamp()
            result = self.one("test_calc.T.test_cmp")
        self.assertEqual(result.kind, "passed", result)
        self.assertEqual((self.root / "seen.txt").read_text(encoding="utf-8"), "False\n")

    def test_the_cache_folder_starts_empty(self):
        """source: the bytecode cache folder handed to the child is empty."""
        self.write("test_calc.py", """
            import os
            import sys
            import unittest
            from pathlib import Path
            class T(unittest.TestCase):
                def test_cmp(self):
                    Path("count.txt").write_text(
                        "%s\\n" % len(os.listdir(sys.pycache_prefix)),
                        encoding="utf-8",
                    )
        """)
        result = self.one("test_calc.T.test_cmp")
        self.assertEqual(result.kind, "passed", result)
        self.assertEqual((self.root / "count.txt").read_text(encoding="utf-8"), "0\n")


class InputRules(Temp):
    def test_the_child_reads_nothing_from_the_caller(self):
        """source: the child reads nothing from the caller's standard input."""
        self.write("test_in.py", """
            import sys
            import unittest
            class T(unittest.TestCase):
                def test_empty(self):
                    self.assertEqual(sys.stdin.read(), "")
        """)
        tool = Path(catch.__file__).resolve()
        proc = subprocess.Popen(
            [
                sys.executable, str(tool), "verdict", "--baseline", "--timeout", "5",
                "--cwd", str(self.root), "test_in.T.test_empty",
            ],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        try:
            code = proc.wait(timeout=12)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=5)
            raise
        finally:
            if proc.poll() is None:
                proc.kill()
                proc.wait(timeout=5)
            out = proc.stdout.read().decode("utf-8") if not proc.stdout.closed else ""
            err = proc.stderr.read().decode("utf-8") if not proc.stderr.closed else ""
            if proc.stdin and not proc.stdin.closed:
                proc.stdin.close()
            proc.stdout.close()
            proc.stderr.close()
        self.assertEqual(code, 0, err)
        self.assertTrue(
            any(line.endswith("passed: not a catch") for line in out.splitlines()),
            out,
        )
        self.assertNotIn("Traceback", err)


class EmptyPartRules(Temp):
    def test_an_empty_part_names_nothing(self):
        """source: a selector with an empty part names nothing and is not imported."""
        self.write("test_cart.py", "open('hit.txt', 'w', encoding='utf-8').write('x')\n")
        selectors = [
            ".Weak", ".", "", "test_cart..Strong.test_total_formats", "test_cart.Strong.",
        ]
        code, out, err = self.cli(["verdict", "--cwd", str(self.root), *selectors])
        self.assertEqual(err, "")
        self.assertNotIn("Traceback", out)
        self.assertNotIn("Traceback", err)
        for selector in selectors:
            self.assertIn(selector, out)
            self.assertIn("no such test: not a catch", out)
        self.assertEqual(out.count("no such test: not a catch"), len(selectors))
        self.assertFalse((self.root / "hit.txt").exists())
        result = catch.verdict("", cwd=str(self.root))
        self.assertEqual(result.kind, "no such test")
        self.assertIsInstance(result, catch.Verdict)
        self.assertEqual(code, 1)


def _default_stops():
    signal.signal(signal.SIGINT, signal.SIG_DFL)
    signal.signal(signal.SIGTERM, signal.SIG_DFL)
    signal.signal(signal.SIGHUP, signal.SIG_DFL)


def _sentinel(signum, frame):
    return None


class StopRules(Temp):
    def _outside(self, sig, code):
        scratch = self.root / "scratch"
        scratch.mkdir()
        self.write("mod.py", """
            import os
            import time
            import unittest
            from pathlib import Path
            class T(unittest.TestCase):
                def test_slow(self):
                    Path("pid.txt").write_text(str(os.getpid()), encoding="utf-8")
                    time.sleep(30)
        """)
        env = os.environ.copy()
        env["TMPDIR"] = str(scratch)
        proc = subprocess.Popen(
            [
                sys.executable, str(Path(catch.__file__).resolve()),
                "verdict", "--timeout", "20", "--cwd", str(self.root), "mod.T.test_slow",
            ],
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            preexec_fn=_default_stops,
        )
        worker = None
        try:
            pid_path = self.root / "pid.txt"
            deadline = time.time() + 20
            while time.time() < deadline:
                if pid_path.is_file():
                    text = pid_path.read_text(encoding="utf-8").strip()
                    if text.isdigit():
                        worker = int(text)
                        break
                if proc.poll() is not None:
                    break
                time.sleep(0.05)
            self.assertIsNotNone(worker, "the test body did not write its pid")
            proc.send_signal(sig)
            try:
                _out, err = proc.communicate(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()
                _out, err = proc.communicate()
                self.fail("the tool did not exit: %r" % (err,))
            self.assertEqual(proc.returncode, code, err)
            self.assertEqual(err.decode("utf-8"), "stopped\n")
            gone = time.time() + 5
            while time.time() < gone:
                try:
                    os.kill(worker, 0)
                except OSError:
                    break
                time.sleep(0.05)
            else:
                self.fail("the test process was still running")
            self.assertEqual(list(scratch.iterdir()), [])
        finally:
            if proc.poll() is None:
                proc.kill()
                proc.wait(timeout=5)
            if worker is not None:
                try:
                    os.kill(worker, 0)
                except OSError:
                    pass
                else:
                    os.kill(worker, signal.SIGKILL)

    def test_an_interrupt_while_the_verdict_command_runs_stops_clean(self):
        """source: an interrupt while a verdict runs stops the run, removes the temporary files, and restores the terminate handler."""
        scratch = self.root / "scratch"
        scratch.mkdir()
        previous_temp = tempfile.tempdir
        tempfile.tempdir = str(scratch)
        self.addCleanup(setattr, tempfile, "tempdir", previous_temp)
        previous_term = signal.signal(signal.SIGTERM, _sentinel)
        self.addCleanup(signal.signal, signal.SIGTERM, previous_term)
        with mock.patch("catch._run_child", side_effect=KeyboardInterrupt):
            code, _out, err = self.cli(["verdict", "--cwd", str(self.root), "mod.T.test_x"])
        self.assertEqual(code, 130)
        self.assertEqual(err, "stopped\n")
        self.assertNotIn("Traceback", err)
        self.assertEqual(list(scratch.iterdir()), [])
        self.assertIs(signal.getsignal(signal.SIGTERM), _sentinel)

    def test_a_terminate_stops_the_run(self):
        """source: a terminate stops the test process, removes the temporary files, and exits with the terminate status."""
        self._outside(signal.SIGTERM, 143)

    def test_a_hangup_stops_the_run(self):
        """source: a hang-up stops the test process, removes the temporary files, and exits with the hang-up status."""
        self._outside(signal.SIGHUP, 129)

    def test_an_ignored_hangup_stays_ignored(self):
        """source: a hang-up that is ignored when the tool starts stays ignored while the child runs and afterwards."""
        self.write("mod.py", """
            import signal
            import unittest
            from pathlib import Path
            class T(unittest.TestCase):
                def test_hup(self):
                    seen = signal.getsignal(signal.SIGHUP)
                    Path("disp.txt").write_text(
                        "ignore" if seen == signal.SIG_IGN else "other",
                        encoding="utf-8",
                    )
        """)
        previous = signal.signal(signal.SIGHUP, signal.SIG_IGN)
        self.addCleanup(signal.signal, signal.SIGHUP, previous)
        code, out, err = self.cli(
            ["verdict", "--baseline", "--cwd", str(self.root), "mod.T.test_hup"]
        )
        self.assertEqual(err, "", err)
        self.assertEqual(code, 0, out)
        self.assertEqual((self.root / "disp.txt").read_text(encoding="utf-8"), "ignore")
        self.assertEqual(signal.getsignal(signal.SIGHUP), signal.SIG_IGN)


if __name__ == "__main__":
    unittest.main()
