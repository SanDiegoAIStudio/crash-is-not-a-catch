#!/usr/bin/env python3
"""Audit rules: a selector counts only when a searched file defines that test."""
import contextlib
import io
import tempfile
import textwrap
import unittest
from pathlib import Path

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

    def audit(self, *extra):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = catch.main(["audit", str(self.root), *extra])
        return code, out.getvalue(), err.getvalue()


SUITE = """
import unittest
class Real(unittest.TestCase):
    def test_one(self):
        pass
    def test_two(self):
        pass
"""


class AuditRules(Temp):
    def test_a_missing_selector_is_broken_and_a_real_one_is_quiet(self):
        """source: a selector that names no test is reported, and a real selector is not listed."""
        self.write("test_thing.py", SUITE)
        self.write("run_mutation.py", 'S = ["Real.test_one", "Real.test_never"]\n')
        code, out, err = self.audit()
        self.assertEqual(err, "")
        self.assertEqual(code, 1)
        self.assertIn("BROKEN run_mutation.py Real.test_never", out)
        self.assertNotIn("Real.test_one", out)
        self.assertTrue(out.endswith("checked 2 selectors in 1 drivers: 1 real, 1 broken, 0 unsure\n"))

    def test_a_clean_driver_exits_clean(self):
        """source: the audit exits clean when every selector names a real test."""
        self.write("test_thing.py", SUITE)
        self.write("run_mutation.py", 'S = ["Real.test_one", "Real.test_two"]\n')
        code, out, err = self.audit()
        self.assertEqual((code, err), (0, ""))
        self.assertEqual(out, "checked 2 selectors in 1 drivers: 2 real, 0 broken, 0 unsure\n")

    def test_the_default_glob_matches_driver_names(self):
        """source: a driver is a python file whose name matches the driver glob."""
        self.write("test_thing.py", SUITE)
        self.write("sample_mutation.py", 'S = ["Real.test_never"]\n')
        self.write("plant.py", 'S = ["Real.test_never"]\n')
        code, out, _err = self.audit()
        self.assertEqual(code, 1)
        self.assertIn("sample_mutation.py", out)
        self.assertNotIn("plant.py", out)

    def test_drivers_flag_replaces_the_default_glob(self):
        """source: repeating the drivers option replaces the default glob."""
        self.write("test_thing.py", SUITE)
        self.write("sample_mutation.py", 'S = ["Real.test_never"]\n')
        self.write("plant.py", 'S = ["Real.test_one"]\n')
        code, out, _err = self.audit("--drivers", "plant.py")
        self.assertEqual(code, 0)
        self.assertNotIn("sample_mutation.py", out)
        self.assertIn("checked 1 selectors in 1 drivers: 1 real, 0 broken, 0 unsure", out)

    def test_skipped_folders_are_not_drivers(self):
        """source: drivers inside a skipped folder name are left unread."""
        self.write("test_thing.py", SUITE)
        self.write("run_mutation.py", 'S = ["Real.test_one"]\n')
        for name in (".git", "node_modules", ".venv", "venv", "__pycache__"):
            self.write("%s/bad_mutation.py" % name, 'S = ["Real.test_never"]\n')
        code, out, _err = self.audit()
        self.assertEqual(code, 0)
        self.assertNotIn("BROKEN", out)
        self.assertNotIn("bad_mutation.py", out)

    def test_skip_adds_a_folder_name(self):
        """source: an added skip name drops every folder with that name."""
        self.write("test_thing.py", SUITE)
        self.write("run_mutation.py", 'S = ["Real.test_one"]\n')
        self.write("hold/bad_mutation.py", 'S = ["Real.test_never"]\n')
        code, out, _err = self.audit("--skip", "hold")
        self.assertEqual(code, 0)
        self.assertNotIn("BROKEN", out)

    def test_a_named_file_is_searched_before_the_folder(self):
        """source: a module prefix in a driver, including the constant part of an f-string, names the file to search."""
        self.write("pkg/test_decoy.py", """
            import unittest
            class Decoy(unittest.TestCase):
                def test_other(self):
                    pass
        """)
        self.write("test_far.py", """
            import unittest
            class Only(unittest.TestCase):
                def test_one(self):
                    pass
        """)
        self.write("pkg/mutate_demo.py", """
            prefix = f"test_far.{name}"
            S = ["Only.test_one"]
        """)
        code, out, _err = self.audit()
        self.assertEqual(code, 0, out)
        self.assertNotIn("BROKEN", out)

    def test_a_named_file_that_is_missing_is_not_replaced_by_the_folder(self):
        """source: when a driver names a test file, only that file is searched."""
        self.write("pkg/test_decoy.py", SUITE)
        self.write("pkg/mutate_demo.py", """
            S = ["Real.test_one"]
            target = "test_absent.py"
        """)
        code, out, _err = self.audit()
        self.assertEqual(code, 1)
        self.assertIn("searched nothing", out)
        self.assertIn("test_absent.py", out)

    def test_the_driver_folder_is_searched_when_it_names_no_file(self):
        """source: with no named test file, the driver's own folder is searched and the rest of the tree is not."""
        self.write("pkg/test_local.py", """
            import unittest
            class Item(unittest.TestCase):
                def test_other(self):
                    pass
        """)
        self.write("nested/test_other.py", """
            import unittest
            class Item(unittest.TestCase):
                def test_missing(self):
                    pass
        """)
        self.write("pkg/mutate_demo.py", 'S = ["Item.test_missing"]\n')
        code, out, _err = self.audit()
        self.assertEqual(code, 1)
        self.assertIn("BROKEN", out)
        self.assertIn("pkg/test_local.py", out)
        self.assertNotIn("nested/test_other.py", out)

    def test_the_whole_tree_is_searched_when_the_folder_has_no_tests(self):
        """source: when the driver's folder has no test file, every test file under the root is searched."""
        self.write("nested/test_deep.py", """
            import unittest
            class Deep(unittest.TestCase):
                def test_one(self):
                    pass
        """)
        self.write("pkg/mutate_demo.py", 'S = ["Deep.test_one"]\n')
        code, out, _err = self.audit()
        self.assertEqual(code, 0, out)
        self.assertNotIn("BROKEN", out)

    def test_a_module_path_uses_only_that_file(self):
        """source: a selector with a module path is looked up in that module's file only."""
        self.write("pkg/test_decoy.py", """
            import unittest
            class Decoy(unittest.TestCase):
                def test_ok(self):
                    pass
        """)
        self.write("other/test_mod.py", """
            import unittest
            class Klass(unittest.TestCase):
                def test_ok(self):
                    pass
        """)
        self.write("pkg/mutate_demo.py", 'S = ["other.test_mod.Klass.test_ok"]\n')
        code, out, _err = self.audit()
        self.assertEqual(code, 0, out)

    def test_a_missing_module_file_is_broken(self):
        """source: a module path with no file is broken."""
        self.write("pkg/mutate_demo.py", 'S = ["missing_mod.Klass.test_one"]\n')
        self.write("pkg/test_decoy.py", SUITE)
        code, out, _err = self.audit()
        self.assertEqual(code, 1)
        self.assertIn("no file for module missing_mod", out)

    def test_a_method_on_a_base_in_the_same_file_is_real(self):
        """source: a method inherited from a base class defined in the same file is real."""
        self.write("test_suite.py", """
            import unittest
            class Grand(unittest.TestCase):
                def test_deep(self):
                    pass
            class Mid(Grand):
                pass
            class Child(Mid):
                pass
        """)
        self.write("mutate_demo.py", 'S = ["Child.test_deep"]\n')
        code, out, _err = self.audit()
        self.assertEqual(code, 0, out)
        self.assertNotIn("BROKEN", out)
        self.assertNotIn("UNSURE", out)

    def test_an_outside_base_is_unsure_and_still_exits_clean(self):
        """source: a missing method on a class with a base from another file is unsure, not broken."""
        self.write("test_suite.py", """
            import unittest
            class Child(Mixin, unittest.TestCase):
                def test_own(self):
                    pass
        """)
        self.write("mutate_demo.py", 'S = ["Child.test_extra", "Child.test_own"]\n')
        code, out, err = self.audit()
        self.assertEqual(err, "")
        self.assertEqual(code, 0)
        self.assertIn("UNSURE mutate_demo.py Child.test_extra", out)
        self.assertIn("extends Mixin", out)
        self.assertNotIn("Child.test_own", out)
        self.assertIn("0 broken, 1 unsure", out)

    def test_a_unittest_base_alone_is_broken_when_the_method_is_absent(self):
        """source: TestCase and IsolatedAsyncioTestCase do not make a missing method unsure."""
        self.write("test_suite.py", """
            import unittest
            class Plain(unittest.TestCase):
                def test_one(self):
                    pass
            class AsyncKind(unittest.IsolatedAsyncioTestCase):
                def test_one(self):
                    pass
        """)
        self.write("mutate_demo.py", 'S = ["Plain.test_missing", "AsyncKind.test_missing"]\n')
        code, out, _err = self.audit()
        self.assertEqual(code, 1)
        self.assertNotIn("UNSURE", out)
        self.assertEqual(out.count("BROKEN"), 2)

    def test_a_driver_that_cannot_be_parsed_is_unreadable(self):
        """source: a driver that cannot be parsed is unreadable and fails the audit."""
        self.write("bad_mutation.py", "def (\n")
        code, out, err = self.audit()
        self.assertEqual(err, "")
        self.assertEqual(code, 1)
        self.assertIn("UNREADABLE bad_mutation.py could not parse the file", out)
        self.assertTrue(out.strip().endswith("1 unreadable"))

    def test_the_summary_counts_an_unreadable_driver_beside_real_selectors(self):
        """source: the audit's last line adds the unreadable count only when it is above zero."""
        self.write("test_thing.py", SUITE)
        self.write("run_mutation.py", 'S = ["Real.test_one"]\n')
        self.write("bad_mutation.py", "def (\n")
        code, out, _err = self.audit()
        self.assertEqual(code, 1)
        self.assertTrue(out.endswith(
            "checked 1 selectors in 1 drivers: 1 real, 0 broken, 0 unsure, 1 unreadable\n"
        ))

    def test_a_file_that_will_not_decode_is_unreadable(self):
        """source: a driver that cannot be read is unreadable."""
        path = self.root / "bad_mutation.py"
        path.write_bytes(b"\xff\xfe not text")
        code, out, _err = self.audit()
        self.assertEqual(code, 1)
        self.assertIn("could not read the file", out)

    def test_checked_nothing_when_the_root_is_not_a_folder(self):
        """source: an audit that is not pointed at a folder reads nothing and does not exit clean."""
        target = self.write("notes.txt", "hello\n")
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = catch.main(["audit", str(target)])
        self.assertEqual(code, 2)
        self.assertEqual(out.getvalue(), "")
        self.assertTrue(err.getvalue().startswith("checked nothing:"))
        self.assertNotIn("Traceback", err.getvalue())

    def test_checked_nothing_when_no_driver_matches(self):
        """source: an audit whose glob matches no driver reads nothing and names the globs."""
        self.write("test_thing.py", SUITE)
        code, out, err = self.audit()
        self.assertEqual(code, 2)
        self.assertEqual(out, "")
        self.assertTrue(err.startswith("checked nothing:"))
        self.assertIn("*mutat*.py", err)
        self.assertEqual(len(err.splitlines()), 1)

    def test_checked_nothing_when_drivers_hold_no_selector(self):
        """source: drivers that hold no selector are a read with nothing to check, and the message says how many were read."""
        self.write("one_mutation.py", "MUTATIONS = []\n")
        self.write("two_mutation.py", "# nothing to audit\n")
        code, out, err = self.audit()
        self.assertEqual(code, 2)
        self.assertEqual(out, "")
        self.assertIn("read 2 drivers", err)
        self.assertTrue(err.startswith("checked nothing:"))

    def test_a_unittest_class_file_is_not_a_driver(self):
        """source: a file that defines a unittest class is not a driver, even when its name matches."""
        self.write("test_mutation.py", """
            import unittest
            class T(unittest.TestCase):
                def test_one(self):
                    pass
            S = ["Missing.test_nope"]
        """)
        self.write("hold/note_mutation.py", 'words = "unittest.TestCase"\nS = ["Missing.test_nope"]\n')
        self.write("hold/test_thing.py", SUITE)
        code, out, _err = self.audit()
        self.assertEqual(code, 1)
        self.assertIn("hold/note_mutation.py", out)
        self.assertNotIn("test_mutation.py", out)

    def test_a_same_file_subclass_of_a_test_class_is_not_a_driver(self):
        """source: a class that inherits a unittest class through another class in the same file still defines a test class."""
        self.write("suite_mutation.py", """
            import unittest
            class Base(unittest.TestCase):
                def test_a(self):
                    pass
            class Child(Base):
                def test_b(self):
                    pass
            S = ["Missing.test_nope"]
        """)
        code, out, err = self.audit()
        self.assertEqual(code, 2)
        self.assertIn("no driver matched", err)
        self.assertEqual(out, "")

    def test_a_test_file_may_be_named_with_the_test_suffix(self):
        """source: a file named with the test suffix is a test file."""
        self.write("cart_test.py", SUITE)
        self.write("mutate_demo.py", 'S = ["Real.test_one"]\n')
        code, out, _err = self.audit()
        self.assertEqual(code, 0, out)

    def test_an_f_string_constant_is_not_a_selector(self):
        """source: the constant part of an f-string is never a selector for the audit."""
        self.write("test_cart.py", """
            import unittest
            class Strong(unittest.TestCase):
                def test_total_formats(self):
                    pass
        """)
        self.write("mutate_cart.py", """
            suffix = "_formats"
            whole = "test_cart.Strong.test_total_formats"
            also = f"Strong.test_total{suffix}"
        """)
        code, out, err = self.audit()
        self.assertEqual((code, err), (0, ""))
        self.assertEqual(out, "checked 1 selectors in 1 drivers: 1 real, 0 broken, 0 unsure\n")

    def test_a_parent_directory_is_not_a_named_test_file(self):
        """source: a test file name that climbs out of the root is not a named test file."""
        self.write("outside/test_out.py", """
            import unittest
            class Hidden(unittest.TestCase):
                def test_one(self):
                    pass
        """)
        self.write("proj/mutate_x.py", """
            target = "../outside/test_out.py"
            S = ["Hidden.test_one"]
        """)
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = catch.main(["audit", str(self.root / "proj")])
        self.assertEqual(err.getvalue(), "")
        self.assertEqual(code, 1)
        self.assertIn("BROKEN", out.getvalue())

    def test_an_absolute_path_is_not_a_named_test_file(self):
        """source: an absolute path is not a named test file."""
        self.write("outside/test_out.py", """
            import unittest
            class Hidden(unittest.TestCase):
                def test_one(self):
                    pass
        """)
        target = (self.root / "outside" / "test_out.py").resolve().as_posix()
        self.assertNotIn(" ", target)
        self.write("proj/mutate_x.py", 'target = "%s"\nS = ["Hidden.test_one"]\n' % target)
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = catch.main(["audit", str(self.root / "proj")])
        self.assertEqual(err.getvalue(), "")
        self.assertEqual(code, 1)
        self.assertIn("BROKEN", out.getvalue())

    def test_a_repeated_selector_is_checked_once(self):
        """source: repeating a selector in one driver does not check it again."""
        self.write("test_thing.py", SUITE)
        self.write("mutate_demo.py", """
            one = "Real.test_one"
            two = "Real.test_one"
        """)
        code, out, err = self.audit()
        self.assertEqual((code, err), (0, ""))
        self.assertEqual(out, "checked 1 selectors in 1 drivers: 1 real, 0 broken, 0 unsure\n")

    def test_issue_lines_are_sorted_by_kind_then_file_then_selector(self):
        """source: audit issue lines are sorted by kind, then file, then selector."""
        self.write("test_suite.py", """
            import unittest
            class Child(Mixin, unittest.TestCase):
                def test_own(self):
                    pass
        """)
        self.write("mutate_a.py", 'S = ["Child.test_extra"]\n')
        self.write("mutate_b.py", 'S = ["Missing.test_nope"]\n')
        code, out, err = self.audit()
        self.assertEqual(err, "")
        self.assertEqual(code, 1)
        lines = [line for line in out.splitlines() if line.startswith("BROKEN") or line.startswith("UNSURE")]
        self.assertEqual([line.split()[0] for line in lines], ["BROKEN", "UNSURE"])

    def test_a_module_path_is_looked_up_beside_the_driver_first(self):
        """source: a selector with a module path is looked up beside the driver before the root."""
        self.write("test_m.py", """
            import unittest
            class Case(unittest.TestCase):
                def test_other(self):
                    pass
        """)
        self.write("sub/test_m.py", """
            import unittest
            class Case(unittest.TestCase):
                def test_a(self):
                    pass
        """)
        self.write("sub/mutate_x.py", 'S = ["test_m.Case.test_a"]\n')
        code, out, err = self.audit()
        self.assertEqual(err, "")
        self.assertEqual(code, 0)
        self.assertEqual(out, "checked 1 selectors in 1 drivers: 1 real, 0 broken, 0 unsure\n")

    def test_audit_usage_errors_do_not_print_a_traceback(self):
        """source: a bad audit argument is one plain line and no traceback."""
        cases = [
            ["audit"],
            ["audit", str(self.root), "--drivers"],
            ["audit", str(self.root), "--skip"],
            ["audit", str(self.root), "--nope"],
            ["audit", str(self.root), "extra"],
        ]
        for args in cases:
            with self.subTest(args=args):
                out, err = io.StringIO(), io.StringIO()
                with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                    code = catch.main(args)
                self.assertEqual(code, 2)
                self.assertEqual(out.getvalue(), "")
                self.assertEqual(len(err.getvalue().splitlines()), 1)
                self.assertNotIn("Traceback", err.getvalue())

    def test_an_assigned_name_in_the_class_body_reads_unsure(self):
        """source: a test name assigned in the class body, and not defined with def, reads unsure."""
        self.write("test_box.py", """
            import unittest
            class Base(unittest.TestCase):
                def test_a(self):
                    pass
                test_from_base = test_a
            class Box(Base):
                test_alias = test_a
                test_marked: object = test_a
                test_note: object
            class Plain(unittest.TestCase):
                def test_a(self):
                    pass
        """)
        self.write("mutate_box.py", """
            S = [
                "test_box.Box.test_alias",
                "test_box.Box.test_marked",
                "test_box.Box.test_from_base",
                "test_box.Box.test_note",
                "test_box.Plain.test_missing",
            ]
        """)
        code, out, err = self.audit()
        self.assertEqual(err, "")
        self.assertEqual(code, 1)
        for selector in (
            "test_box.Box.test_alias",
            "test_box.Box.test_marked",
            "test_box.Box.test_from_base",
        ):
            self.assertIn("UNSURE mutate_box.py " + selector, out)
        self.assertIn(
            "searched test_box.py; Box is there and test_alias is assigned in the class body",
            out,
        )
        self.assertIn(
            "searched test_box.py; Box is there and test_marked is assigned in the class body",
            out,
        )
        self.assertIn(
            "searched test_box.py; Box is there and test_from_base is assigned in the class body",
            out,
        )
        self.assertIn(
            "BROKEN mutate_box.py test_box.Box.test_note searched test_box.py; Box is there and test_note is not",
            out,
        )
        self.assertIn(
            "BROKEN mutate_box.py test_box.Plain.test_missing searched test_box.py; Plain is there and test_missing is not",
            out,
        )
        self.assertNotIn("test_note is assigned", out)
        self.assertNotIn("test_missing is assigned", out)

    def test_a_nested_class_with_its_method_is_real(self):
        """source: a selector may name a class nested in another class when that enclosing class is in the file."""
        self.write("test_x.py", """
            import unittest
            class Outer:
                class Inner(unittest.TestCase):
                    def test_c(self):
                        pass
        """)
        self.write("mutate_x.py", 'S = ["test_x.Outer.Inner.test_c"]\n')
        code, out, err = self.audit()
        self.assertEqual(err, "")
        self.assertEqual(code, 0)
        self.assertEqual(out, "checked 1 selectors in 1 drivers: 1 real, 0 broken, 0 unsure\n")
        self.assertNotIn("test_x.Outer.Inner.test_c", out)

    def test_a_nested_class_with_a_missing_method_is_broken(self):
        """source: a nested class whose method is missing stays broken."""
        self.write("test_x.py", """
            import unittest
            class Outer:
                class Inner(unittest.TestCase):
                    def test_c(self):
                        pass
        """)
        self.write("mutate_x.py", 'S = ["test_x.Outer.Inner.test_gone"]\n')
        code, out, err = self.audit()
        self.assertEqual(err, "")
        self.assertEqual(code, 1)
        self.assertIn("BROKEN mutate_x.py test_x.Outer.Inner.test_gone", out)
        self.assertIn("searched test_x.py; Inner is there and test_gone is not", out)
        self.assertNotIn("no file for module", out)

    def test_a_peeled_part_that_is_not_a_class_is_broken(self):
        """source: a peeled part that is not a class in the found file is broken."""
        self.write("test_x.py", """
            import unittest
            class Outer:
                class Inner(unittest.TestCase):
                    def test_c(self):
                        pass
        """)
        self.write("mutate_x.py", 'S = ["test_x.Nope.Inner.test_c"]\n')
        code, out, err = self.audit()
        self.assertEqual(err, "")
        self.assertEqual(code, 1)
        self.assertIn(
            "BROKEN mutate_x.py test_x.Nope.Inner.test_c searched test_x.py; Nope is not a class there",
            out,
        )

    def test_a_selector_with_no_module_part_is_unchanged(self):
        """source: a selector with no module part is not treated as a nested class path."""
        self.write("test_x.py", """
            import unittest
            class Outer:
                class Inner(unittest.TestCase):
                    def test_c(self):
                        pass
        """)
        self.write("mutate_bare.py", 'S = ["Outer.Inner.test_c"]\n')
        code, out, err = self.audit()
        self.assertEqual(err, "")
        self.assertEqual(code, 1)
        self.assertEqual(
            out,
            "BROKEN mutate_bare.py Outer.Inner.test_c searched nothing; no file for module Outer beside the driver or from the root\n"
            "checked 1 selectors in 1 drivers: 0 real, 1 broken, 0 unsure\n",
        )

    def test_a_searched_file_that_cannot_be_parsed_names_the_file(self):
        """source: a searched test file that cannot be parsed is broken because it could not be read as Python."""
        self.write("test_x.py", "def broken(:\n")
        self.write("mutate_x.py", 'S = ["T.test_x"]\n')
        code, out, err = self.audit()
        self.assertEqual(err, "")
        self.assertEqual(code, 1)
        self.assertEqual(
            out,
            "BROKEN mutate_x.py T.test_x searched test_x.py; test_x.py could not be read as Python\n"
            "checked 1 selectors in 1 drivers: 0 real, 1 broken, 0 unsure\n",
        )

    def test_a_lower_case_module_part_is_not_an_enclosing_class(self):
        """source: a module path whose file is missing stays missing when its last part does not start with a capital letter."""
        self.write("pkg.py", "VALUE = 1\n")
        self.write("mutate_pkg.py", 'S = ["pkg.helpers.T.test_x"]\n')
        code, out, err = self.audit()
        self.assertEqual(err, "")
        self.assertEqual(code, 1)
        self.assertIn(
            "BROKEN mutate_pkg.py pkg.helpers.T.test_x searched nothing; no file for module pkg.helpers beside the driver or from the root",
            out,
        )
        self.assertNotIn("is not a class there", out)


if __name__ == "__main__":
    unittest.main()
