#!/usr/bin/env python3
"""Plant one exact bug at a time and score the copy with the clean tool.

Each swap is applied to a fresh copy of catch.py. The copy's tests run in a
temp folder. The untouched verdict decides whether an assertion caught the bug.
A swap that is not in the source exactly once is an error.
"""
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import catch  # noqa: E402

SOURCE = (ROOT / "catch.py").read_text(encoding="utf-8")
SUITE_FILES = ("test_verdict.py", "test_audit.py", "test_demo.py")
RUN_ALL = (
    "import unittest\n"
    "\n"
    "def load_tests(loader, standard_tests, pattern):\n"
    "    suite = unittest.TestSuite()\n"
    "    for name in ('tests.test_verdict', 'tests.test_audit', 'tests.test_demo'):\n"
    "        suite.addTests(loader.loadTestsFromName(name))\n"
    "    return suite\n"
)

MUTATIONS = [
    (
        "load-failure-is-not-a-crash",
        "    if _only_failed(suite):\n        return _report(False)\n",
        "    if False and _only_failed(suite):\n        return _report(False)\n",
    ),
    (
        "assertion-before-crash",
        '    if report.get("failures"): return "caught", ""\n'
        '    if report.get("errors"): return "crashed", report["errors"][0]\n',
        '    if report.get("errors"): return "crashed", report["errors"][0]\n'
        '    if report.get("failures"): return "caught", ""\n',
    ),
    (
        "crash-before-pass",
        '    if report.get("errors"): return "crashed", report["errors"][0]\n'
        '    done = report.get("passed", 0) + report.get("expected", 0) + report.get("unexpected", 0)\n'
        '    if done: return "passed", ""\n',
        '    done = report.get("passed", 0) + report.get("expected", 0) + report.get("unexpected", 0)\n'
        '    if done: return "passed", ""\n'
        '    if report.get("errors"): return "crashed", report["errors"][0]\n',
    ),
    (
        "skip-is-not-a-pass",
        '    done = report.get("passed", 0) + report.get("expected", 0) + report.get("unexpected", 0)\n',
        '    done = report.get("passed", 0) + report.get("expected", 0) + report.get("unexpected", 0) + report.get("skipped", 0)\n',
    ),
    (
        "timeout-before-missing-report",
        '    if timed_out: return "timed out", ""\n'
        '    if report is None: return "crashed", "exit %s" % (returncode,)\n',
        '    if report is None: return "crashed", "exit %s" % (returncode,)\n'
        '    if timed_out: return "timed out", ""\n',
    ),
    (
        "count-line",
        '    sys.stdout.write("%s %s of %s\\n" % (label, n, total))\n',
        '    sys.stdout.write("%s %s out of %s\\n" % (label, n, total))\n',
    ),
    (
        "exit-when-all-caught",
        "def _code_for_caught(caught_n, total):\n    return 0 if caught_n == total else 1\n",
        "def _code_for_caught(caught_n, total):\n    return 1 if caught_n == total else 1\n",
    ),
    (
        "exit-when-one-is-missed",
        "def _code_for_caught(caught_n, total):\n    return 0 if caught_n == total else 1\n",
        "def _code_for_caught(caught_n, total):\n    return 0 if caught_n == total else 0\n",
    ),
    (
        "baseline-counts-passes",
        '        n = sum(row.kind == "passed" for row in rows)\n',
        '        n = sum(row.kind == "caught" for row in rows)\n',
    ),
    (
        "baseline-exit",
        "def _code_for_passed(passed_n, total):\n    return 0 if passed_n == total else 1\n",
        "def _code_for_passed(passed_n, total):\n    return 0\n",
    ),
    (
        "driver-glob",
        '_DEFAULT_GLOBS = ("*mutat*.py",)\n',
        '_DEFAULT_GLOBS = ("*zzznomatch*.py",)\n',
    ),
    (
        "skip-venv",
        '"node_modules", ".venv", "venv", "__pycache__"',
        '"node_modules", ".venv", "not-a-venv-name", "__pycache__"',
    ),
    (
        "lookup-named-file",
        "    if named:\n"
        "        return [path for path in (_locate(driver, root, rel) for rel in named) if path is not None]\n",
        "    if False and named:\n"
        "        return [path for path in (_locate(driver, root, rel) for rel in named) if path is not None]\n",
    ),
    (
        "lookup-driver-folder",
        "    if folder_tests:\n        return folder_tests\n",
        "    if False and folder_tests:\n        return folder_tests\n",
    ),
    (
        "lookup-whole-tree",
        "    return _test_files_under(root, skip)\n",
        "    return []\n",
    ),
    (
        "lookup-module-file",
        "    if module:\n        path = _locate(driver, root, _module_rel(module))\n",
        "    if False and module:\n        path = _locate(driver, root, _module_rel(module))\n",
    ),
    (
        "same-file-inheritance",
        "            found.update(_methods(classes[simple], classes, seen))\n",
        "            found.update(set())\n",
    ),
    (
        "outside-base-is-unsure",
        '        return "unsure", reason\n',
        '        return "broken", reason\n',
    ),
    (
        "unreadable-syntax",
        '        return None, "could not parse the file"\n',
        '        return ast.parse(""), ""\n',
    ),
    (
        "checked-nothing-not-a-folder",
        '        sys.stderr.write("checked nothing: %s is not a folder\\n" % root)\n        return 2\n',
        '        sys.stderr.write("checked nothing: %s is not a folder\\n" % root)\n        return 0\n',
    ),
    (
        "checked-nothing-no-driver",
        '        sys.stderr.write("checked nothing: no driver matched %s\\n" % shown)\n        return 2\n',
        '        sys.stderr.write("checked nothing: no driver matched %s\\n" % shown)\n        return 0\n',
    ),
    (
        "checked-nothing-no-selector",
        '        sys.stderr.write("checked nothing: read %s drivers and found no selector\\n" % len(parsed))\n'
        "        return 2\n",
        '        sys.stderr.write("checked nothing: read %s drivers and found no selector\\n" % len(parsed))\n'
        "        return 0\n",
    ),
    (
        "audit-summary-line",
        '    line = "checked %s selectors in %s drivers: %s real, %s broken, %s unsure" % (\n',
        '    line = "checked %s selectors across %s drivers: %s real, %s broken, %s unsure" % (\n',
    ),
    (
        "unreadable-suffix",
        "    if unread_n:\n        line += \", %s unreadable\" % unread_n\n",
        "    if False and unread_n:\n        line += \", %s unreadable\" % unread_n\n",
    ),
    (
        "expected-failure-is-not-passed",
        '    done = report.get("passed", 0) + report.get("expected", 0) + report.get("unexpected", 0)\n',
        '    done = report.get("passed", 0) + report.get("unexpected", 0)\n',
    ),
    (
        "unexpected-success-is-not-passed",
        '    done = report.get("passed", 0) + report.get("expected", 0) + report.get("unexpected", 0)\n',
        '    done = report.get("passed", 0) + report.get("expected", 0)\n',
    ),
    (
        "missing-folder-is-accepted",
        "    if not cwd_path.is_dir():\n",
        "    if False:\n",
    ),
    (
        "f-string-constant-is-a-selector",
        "skip.add(id(part))",
        "pass",
    ),
    (
        "parent-path-is-a-named-test-file",
        '    if path.is_absolute() or ".." in path.parts or not _is_test_filename(path.name): return None\n',
        '    if path.is_absolute() or not _is_test_filename(path.name): return None\n',
    ),
    (
        "absolute-path-is-a-named-test-file",
        '    if path.is_absolute() or ".." in path.parts or not _is_test_filename(path.name): return None\n',
        '    if ".." in path.parts or not _is_test_filename(path.name): return None\n',
    ),
    (
        "duplicate-selector-counted-twice",
        "    return sorted(set(selectors)), named\n",
        "    return sorted(selectors), named\n",
    ),
    (
        "nan-timeout-is-accepted",
        '    if number != number or number <= 0 or number == float("inf"):\n',
        '    if number <= 0 or number == float("inf"):\n',
    ),
    (
        "audit-issues-keep-read-order",
        "    for kind, rel, selector, reason in sorted(issues):\n",
        "    for kind, rel, selector, reason in issues:\n",
    ),
    (
        "module-path-skips-the-driver-folder",
        "    if beside.is_file():\n",
        "    if False:\n",
    ),
    (
        "import-crash-reads-as-no-such-test",
        "    if crashed:\n        return _report(True, errors=(crashed,))\n",
        "    if False and crashed:\n        return _report(True, errors=(crashed,))\n",
    ),
    (
        "missing-inner-module-reads-as-no-such-test",
        '            if missing == name or name.startswith(missing + "."):\n',
        "            if True:\n",
    ),
    (
        "cache-folder-not-set",
        "    sys.pycache_prefix = cache_dir\n",
        "    pass\n",
    ),
    (
        "started-process-keeps-the-callers-cache",
        '    os.environ["PYTHONPYCACHEPREFIX"] = cache_dir\n',
        "    pass\n",
    ),
    (
        "interrupt-exit-code",
        '    _STOP["code"] = 130\n',
        '    _STOP["code"] = 1\n',
    ),
    (
        "terminate-handler-not-installed",
        "    for sig in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP):\n",
        "    for sig in (signal.SIGINT, signal.SIGHUP):\n",
    ),
    (
        "ignored-signal-taken-over",
        "        if previous == signal.SIG_IGN: continue\n",
        "        if False and previous == signal.SIG_IGN: continue\n",
    ),
    (
        "setup-assertion-is-a-crash",
        "        if issubclass(err[0], AssertionError): self.addFailure(test, err); return\n",
        "        if False and issubclass(err[0], AssertionError): self.addFailure(test, err); return\n",
    ),
    (
        "unparsed-file-reads-as-not-defined",
        '    if unread: return "broken", "searched %s; %s could not be read as Python" % (searched, ", ".join(_rel(root, path) for path in unread))\n',
        '    if unread: return "broken", "searched %s; %s.%s is not defined" % (searched, cls, meth)\n',
    ),
    (
        "lower-case-part-peeled",
        "        while path is None and len(parts) > 1 and _is_enclosing(parts[-1]):\n",
        "        while path is None and len(parts) > 1:\n",
    ),
    (
        "bytecode-writing-left-on",
        '    env["PYTHONDONTWRITEBYTECODE"] = "1"\n',
        "    pass\n",
    ),
    (
        "cache-folder-left-behind",
        "        shutil.rmtree(cache_dir, ignore_errors=True)\n",
        "        pass\n",
    ),
    (
        "child-shares-stdin",
        "            cmd, cwd=cwd, env=env, stdin=subprocess.DEVNULL,\n",
        "            cmd, cwd=cwd, env=env,\n",
    ),
    (
        "empty-part-reaches-the-import",
        '        if "" in selector.split("."): return Verdict(selector, "no such test", "")\n',
        '        if False and "" in selector.split("."): return Verdict(selector, "no such test", "")\n',
    ),
    (
        "assigned-name-reads-broken",
        '    if meth in _assigned(classes[cls], classes, set()):\n        return "assigned", []\n',
        '    if False and meth in _assigned(classes[cls], classes, set()):\n        return "assigned", []\n',
    ),
    (
        "nested-class-reads-broken",
        "    return part[:1].isupper()\n",
        "    return False\n",
    ),
]


def _stage(root, old, new):
    (root / "tests").mkdir()
    (root / "examples").mkdir()
    text = SOURCE if old is None else SOURCE.replace(old, new, 1)
    (root / "catch.py").write_text(text, encoding="utf-8")
    for name in SUITE_FILES:
        shutil.copyfile(str(ROOT / "tests" / name), str(root / "tests" / name))
    examples = ROOT / "examples"
    for path in sorted(examples.iterdir()):
        if path.is_file():
            shutil.copyfile(str(path), str(root / "examples" / path.name))
    (root / "run_all.py").write_text(RUN_ALL, encoding="utf-8")


def _preflight():
    for name, old, new in MUTATIONS:
        if old == new or SOURCE.count(old) != 1:
            sys.stderr.write("%s: planted text appears %s times\n" % (name, SOURCE.count(old)))
            return 2
    return 0


def _clean_copy():
    tmp = tempfile.TemporaryDirectory()
    try:
        root = Path(tmp.name)
        _stage(root, None, None)
        return catch.verdict("run_all", cwd=str(root), timeout=90)
    finally:
        tmp.cleanup()


def main():
    failed = _preflight()
    if failed:
        return failed
    clean = _clean_copy()
    if clean.kind != "passed":
        sys.stderr.write("the clean copy does not pass: %s\n" % (clean,))
        return 2
    width = max(len(name) for name, _old, _new in MUTATIONS)
    caught = 0
    for name, old, new in MUTATIONS:
        tmp = tempfile.TemporaryDirectory()
        try:
            root = Path(tmp.name)
            _stage(root, old, new)
            result = catch.verdict("run_all", cwd=str(root), timeout=90)
        finally:
            tmp.cleanup()
        if result.caught:
            caught += 1
        sys.stdout.write("%s  %s\n" % (name.ljust(width), result))
        sys.stdout.flush()
    total = len(MUTATIONS)
    sys.stdout.write("caught %s of %s\n" % (caught, total))
    return 0 if caught == total else 1


if __name__ == "__main__":
    sys.exit(main())
