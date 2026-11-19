#!/usr/bin/env python3
"""A planted bug counts as caught only when an assertion fails."""
from __future__ import annotations
import ast, importlib, json, os, re, shutil, signal, subprocess, sys, tempfile, unittest
from fnmatch import fnmatchcase
from pathlib import Path
from typing import NamedTuple
_SKIP_DIRS = (".git", "node_modules", ".venv", "venv", "__pycache__")
_DEFAULT_GLOBS = ("*mutat*.py",)
_UNITTEST_BASES = {"TestCase", "unittest.TestCase", "IsolatedAsyncioTestCase", "unittest.IsolatedAsyncioTestCase"}
_SELECTOR = re.compile(r"^(?:(?P<mod>(?:[A-Za-z_][A-Za-z0-9_]*\.)*)(?P<cls>[A-Z][A-Za-z0-9_]*))\.(?P<meth>test_[A-Za-z0-9_]*)$")
_PREFIX = re.compile(r"^(?:[A-Za-z_][A-Za-z0-9_]*\.)+$")
class Verdict(NamedTuple):
    selector: str
    kind: str
    detail: str
    @property
    def caught(self):
        return self.kind == "caught"
    def __str__(self):
        if self.kind == "caught": return "caught"
        if self.kind == "crashed": return "crashed (%s): not a catch" % (self.detail,)
        return "%s: not a catch" % (self.kind,)
def _report(loaded, failures=0, errors=(), passed=0, skipped=0, expected=0, unexpected=0):
    return {
        "loaded": loaded, "failures": failures, "errors": list(errors),
        "passed": passed, "skipped": skipped, "expected": expected, "unexpected": unexpected,
    }
class _Recorder(unittest.TestResult):
    def __init__(self):
        super().__init__()
        self.error_names, self.passes = [], 0
    def _note(self, err):
        self.error_names.append(getattr(err[0], "__name__", "Exception"))
    def addError(self, test, err):
        if issubclass(err[0], AssertionError): self.addFailure(test, err); return
        self._note(err)
        super().addError(test, err)
    def addSubTest(self, test, subtest, err):
        if err is not None and not issubclass(err[0], test.failureException):
            self._note(err)
        super().addSubTest(test, subtest, err)
    def addSuccess(self, test):
        self.passes += 1
def _walk(suite):
    for item in suite:
        if isinstance(item, unittest.TestSuite): yield from _walk(item)
        else: yield item
def _only_failed(suite):
    tests = list(_walk(suite))
    return bool(tests) and all(
        type(item).__module__ == "unittest.loader" and type(item).__name__ == "_FailedTest"
        for item in tests
    )
def _broke_on_import(selector):
    # Stop at the first error that is not "this prefix is not a module", so a broken module runs once.
    parts = selector.split(".")
    for length in range(len(parts), 0, -1):
        name = ".".join(parts[:length])
        try:
            importlib.import_module(name)
        except ModuleNotFoundError as exc:
            missing = exc.name or ""
            if missing == name or name.startswith(missing + "."):
                continue
            return type(exc).__name__
        except Exception as exc:
            return type(exc).__name__
        return None
    return None
def _execute(selector):
    crashed = _broke_on_import(selector)
    if crashed:
        return _report(True, errors=(crashed,))
    loader = unittest.TestLoader()
    try:
        suite = loader.loadTestsFromName(selector)
    except Exception:
        return _report(False)
    if _only_failed(suite):
        return _report(False)
    rec = _Recorder()
    suite.run(rec)
    return _report(True, len(rec.failures), rec.error_names, rec.passes, len(rec.skipped),
                   len(rec.expectedFailures), len(rec.unexpectedSuccesses))
def _classify(report, returncode, timed_out):
    # A killed child never finishes its report. Timeout wins over "exit N".
    if timed_out: return "timed out", ""
    if report is None: return "crashed", "exit %s" % (returncode,)
    if not report.get("loaded"): return "no such test", ""
    if report.get("failures"): return "caught", ""
    if report.get("errors"): return "crashed", report["errors"][0]
    done = report.get("passed", 0) + report.get("expected", 0) + report.get("unexpected", 0)
    if done: return "passed", ""
    return "nothing ran", ""
def _read_report(path):
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8") or "null")
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict) or "loaded" not in data:
        return None
    return data
def _worker(report_path, selector, cache_dir):
    prior = os.environ.pop("CATCH_CALLER_PYCACHE", "")
    if prior:
        os.environ["PYTHONPYCACHEPREFIX"] = prior
    sys.pycache_prefix = cache_dir
    os.environ["PYTHONPYCACHEPREFIX"] = cache_dir
    sys.path.insert(0, os.getcwd())
    try:
        report = _execute(selector)
        Path(report_path).write_text(json.dumps(report), encoding="utf-8")
    except Exception:
        return 1
    return 0
def _timeout_value(timeout):
    try:
        number = float(timeout)
    except (TypeError, ValueError):
        number = 0
    if number != number or number <= 0 or number == float("inf"):
        raise ValueError("timeout must be a positive number")
    return number
def _run_child(selector, cwd, timeout, report_path, cache_dir):
    env = os.environ.copy()
    old = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = cwd if not old else cwd + os.pathsep + old
    prior = env.pop("PYTHONPYCACHEPREFIX", "")
    if prior:
        env["CATCH_CALLER_PYCACHE"] = prior
    else:
        env.pop("CATCH_CALLER_PYCACHE", None)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    cmd = [sys.executable, str(Path(__file__).resolve()), "--worker", report_path, selector, cache_dir]
    timed_out, returncode = False, 1
    try:
        completed = subprocess.run(
            cmd, cwd=cwd, env=env, stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            timeout=timeout, check=False,
        )
        returncode = completed.returncode
    except subprocess.TimeoutExpired:
        timed_out = True
    except OSError:
        returncode = 127
    report = None if timed_out else _read_report(report_path)
    return _classify(report, returncode, timed_out)
def verdict(selector, cwd=".", timeout=60.0):
    """Run one selector in a fresh child. Return a Verdict."""
    cwd_path = Path(cwd)
    if not cwd_path.is_dir():
        raise ValueError("not a folder: %s" % (cwd,))
    timeout = _timeout_value(timeout)
    fd, report_path = tempfile.mkstemp(prefix="catch-", suffix=".json")
    os.close(fd)
    cache_dir = tempfile.mkdtemp(prefix="catch-pyc-", dir=os.path.dirname(report_path))
    try:
        if "" in selector.split("."): return Verdict(selector, "no such test", "")
        kind, detail = _run_child(selector, str(cwd_path.resolve()), timeout, report_path, cache_dir)
        return Verdict(selector, kind, detail)
    finally:
        try:
            os.unlink(report_path)
        except OSError:
            pass
        shutil.rmtree(cache_dir, ignore_errors=True)
def _code_for_caught(caught_n, total):
    return 0 if caught_n == total else 1
def _code_for_passed(passed_n, total):
    return 0 if passed_n == total else 1
def _emit(rows, baseline):
    width = max(len(row.selector) for row in rows)
    for row in rows:
        sys.stdout.write("%s  %s\n" % (row.selector.ljust(width), row))
    total = len(rows)
    if baseline:
        n = sum(row.kind == "passed" for row in rows)
        label, code = "passed", _code_for_passed(n, total)
    else:
        n = sum(row.kind == "caught" for row in rows)
        label, code = "caught", _code_for_caught(n, total)
    sys.stdout.write("%s %s of %s\n" % (label, n, total))
    return code
def _parse_verdict(args):
    baseline, cwd, timeout, selectors, index = False, ".", "60", [], 0
    while index < len(args):
        arg = args[index]
        if arg == "--baseline": baseline = True
        elif arg in ("--cwd", "--timeout"):
            if index + 1 >= len(args):
                return "verdict needs a folder after --cwd" if arg == "--cwd" else "timeout must be a positive number"
            index += 1
            if arg == "--cwd": cwd = args[index]
            else: timeout = args[index]
        elif arg.startswith("-"): return "unknown argument: %s" % arg
        else: selectors.append(arg)
        index += 1
    if not selectors: return "verdict needs a selector"
    if not Path(cwd).is_dir(): return "not a folder: %s" % cwd
    try: number = _timeout_value(timeout)
    except ValueError: return "timeout must be a positive number"
    return baseline, cwd, number, selectors
def _cmd_verdict(args):
    parsed = _parse_verdict(args)
    if isinstance(parsed, str):
        sys.stderr.write(parsed + "\n")
        return 2
    baseline, cwd, timeout, selectors = parsed
    return _emit([verdict(selector, cwd=cwd, timeout=timeout) for selector in selectors], baseline)
def _dotted(node):
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        head = _dotted(node.value)
        return (head + "." + node.attr) if head else node.attr
    return ""
def _class_map(tree):
    found = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef):
            found.setdefault(node.name, node)
    return found
def _labels(node):
    return [_dotted(base) for base in node.bases]
def _simple(label):
    return label.split(".")[-1] if label else ""
def _defines_unittest_class(tree):
    classes = _class_map(tree)
    def is_unit(node, seen):
        if node.name in seen: return False
        seen.add(node.name)
        for label in _labels(node):
            simple = _simple(label)
            if simple in classes and is_unit(classes[simple], seen): return True
            if label in _UNITTEST_BASES: return True
        return False
    return any(is_unit(node, set()) for node in classes.values())
def _assigned(node, classes, seen):
    if node.name in seen: return set()
    seen.add(node.name)
    found = set()
    for item in node.body:
        targets = item.targets if isinstance(item, ast.Assign) else ()
        if isinstance(item, ast.AnnAssign) and item.value is not None: targets = (item.target,)
        found.update(t.id for t in targets if isinstance(t, ast.Name) and t.id.startswith("test_"))
    for label in _labels(node):
        simple = _simple(label)
        if simple in classes and simple not in seen:
            found.update(_assigned(classes[simple], classes, seen))
    return found
def _methods(node, classes, seen):
    if node.name in seen:
        return set()
    seen.add(node.name)
    found = {
        item.name for item in node.body
        if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)) and item.name.startswith("test_")
    }
    for label in _labels(node):
        simple = _simple(label)
        if simple in classes and simple not in seen:
            found.update(_methods(classes[simple], classes, seen))
    return found
def _external_bases(node, classes, seen):
    if node.name in seen:
        return []
    seen.add(node.name)
    found = []
    for label in _labels(node):
        simple = _simple(label)
        if simple and simple in classes:
            found.extend(_external_bases(classes[simple], classes, seen))
        elif label not in _UNITTEST_BASES:
            found.append(label or "an unnamed base")
    return found
def _is_test_filename(name):
    return (name.startswith("test_") and name.endswith(".py")) or name.endswith("_test.py")
def _named_rel(text):
    if _PREFIX.match(text):
        parts = text[:-1].split(".")
        filename = parts[-1] + ".py"
        if not _is_test_filename(filename): return None
        return Path(*(parts[:-1] + [filename]))
    if not text or any(mark in text for mark in ("\n", " ", "\t")): return None
    path = Path(text.replace("\\", "/"))
    if path.is_absolute() or ".." in path.parts or not _is_test_filename(path.name): return None
    return path
def _collect(tree):
    selectors, named, skip, seen = [], [], set(), set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.JoinedStr):
            continue
        for part in node.values:
            if isinstance(part, ast.Constant) and isinstance(part.value, str):
                skip.add(id(part))
                rel = _named_rel(part.value)
                if rel is not None and rel.as_posix() not in seen:
                    seen.add(rel.as_posix())
                    named.append(rel)
    for node in ast.walk(tree):
        if not isinstance(node, ast.Constant) or not isinstance(node.value, str) or id(node) in skip:
            continue
        if _SELECTOR.match(node.value):
            selectors.append(node.value)
        rel = _named_rel(node.value)
        if rel is not None and rel.as_posix() not in seen:
            seen.add(rel.as_posix())
            named.append(rel)
    return sorted(set(selectors)), named
def _split(selector):
    match = _SELECTOR.match(selector)
    module = match.group("mod")[:-1] if match.group("mod").endswith(".") else match.group("mod")
    return module, match.group("cls"), match.group("meth")
def _locate(driver, root, rel):
    beside = driver.parent / rel
    if beside.is_file():
        return beside
    at_root = root / rel
    return at_root if at_root.is_file() else None
def _walk_files(root, skip, pred):
    found = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [name for name in dirnames if name not in skip]
        found.extend(Path(dirpath) / name for name in filenames if pred(name))
    return sorted(found)
def _tests_in(folder):
    if not folder.is_dir():
        return []
    return sorted(path for path in folder.iterdir() if path.is_file() and _is_test_filename(path.name))
def _test_files_under(root, skip):
    return _walk_files(root, skip, _is_test_filename)
def _bare_files(driver, root, named, skip):
    if named:
        return [path for path in (_locate(driver, root, rel) for rel in named) if path is not None]
    folder_tests = _tests_in(driver.parent)
    if folder_tests:
        return folder_tests
    return _test_files_under(root, skip)
def _module_rel(module):
    parts = module.split(".")
    return Path(parts[-1] + ".py") if len(parts) == 1 else Path(*parts[:-1], parts[-1] + ".py")
def _is_enclosing(part):
    return part[:1].isupper()
def _resolve_module(driver, root, module):
    if module:
        path = _locate(driver, root, _module_rel(module))
        parts, dropped = module.split("."), []
        while path is None and len(parts) > 1 and _is_enclosing(parts[-1]):
            dropped.insert(0, parts.pop())
            path = _locate(driver, root, _module_rel(".".join(parts)))
        if path is None or not dropped:
            return path, ""
        try: names = set(_class_map(ast.parse(path.read_text(encoding="utf-8"))))
        except (OSError, UnicodeDecodeError, SyntaxError): return path, ""
        for part in dropped:
            if part not in names:
                return path, part
        return path, ""
    return None, ""
def _search_files(driver, root, selector, named, skip):
    module, _cls, _meth = _split(selector)
    if module:
        path, missing = _resolve_module(driver, root, module)
        if path is None:
            return [], "no file for module %s beside the driver or from the root" % module
        if missing:
            return [path], "missing-class:" + missing
        return [path], ""
    found = _bare_files(driver, root, named, skip)
    if named and not found:
        shown = ", ".join(item.as_posix() for item in named)
        return [], "named %s was not found beside the driver or from the root" % shown
    return found, ""
def _read_tree(path):
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None, "could not read the file"
    try:
        return ast.parse(text), ""
    except SyntaxError:
        return None, "could not parse the file"
def _rel(root, path):
    try:
        return path.resolve().relative_to(Path(root).resolve()).as_posix()
    except ValueError:
        return path.as_posix()
def _class_state(tree, cls, meth):
    classes = _class_map(tree)
    if cls not in classes:
        return "absent", []
    if meth in _methods(classes[cls], classes, set()):
        return "real", []
    if meth in _assigned(classes[cls], classes, set()):
        return "assigned", []
    bases = _external_bases(classes[cls], classes, set())
    return ("unsure", bases) if bases else ("missing", [])
def _judge(root, paths, cls, meth, note):
    if note.startswith("missing-class:"):
        searched = ", ".join(_rel(root, path) for path in paths)
        return "broken", "searched %s; %s is not a class there" % (searched, note.split(":", 1)[1])
    if not paths:
        return "broken", ("searched nothing; " + note) if note else "searched nothing"
    unsure_bases, class_seen, assigned, unread = [], False, False, []
    for path in paths:
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, SyntaxError):
            unread.append(path)
            continue
        state, bases = _class_state(tree, cls, meth)
        if state == "real": return "real", ""
        if state == "absent": continue
        class_seen = True
        if state == "assigned": assigned = True; continue
        for base in bases:
            if base not in unsure_bases: unsure_bases.append(base)
    searched = ", ".join(_rel(root, path) for path in paths)
    if unread: return "broken", "searched %s; %s could not be read as Python" % (searched, ", ".join(_rel(root, path) for path in unread))
    if assigned:
        return "unsure", "searched %s; %s is there and %s is assigned in the class body" % (searched, cls, meth)
    if unsure_bases:
        reason = "searched %s; %s is there, %s is not, and %s extends %s" % (
            searched, cls, meth, cls, " and ".join(unsure_bases))
        return "unsure", reason
    if class_seen:
        return "broken", "searched %s; %s is there and %s is not" % (searched, cls, meth)
    return "broken", "searched %s; %s.%s is not defined" % (searched, cls, meth)
def _audit(root, globs, extra_skips):
    root_path = Path(root)
    if not root_path.is_dir():
        sys.stderr.write("checked nothing: %s is not a folder\n" % root)
        return 2
    skip, shown = set(_SKIP_DIRS), ", ".join(globs)
    skip.update(extra_skips)
    matched = _walk_files(
        root_path, skip,
        lambda name: name.endswith(".py") and any(fnmatchcase(name, pattern) for pattern in globs),
    )
    parsed, unreadable = [], []
    for path in matched:
        tree, problem = _read_tree(path)
        if problem: unreadable.append((path, problem))
        elif not _defines_unittest_class(tree): parsed.append((path, tree))
    if not parsed and not unreadable:
        sys.stderr.write("checked nothing: no driver matched %s\n" % shown)
        return 2
    issues, real_n, broken_n, unsure_n, drivers_n = [], 0, 0, 0, 0
    for path, tree in parsed:
        selectors, named = _collect(tree)
        if not selectors:
            continue
        drivers_n += 1
        rel = _rel(root_path, path)
        for selector in selectors:
            _module, cls, meth = _split(selector)
            files, note = _search_files(path, root_path, selector, named, skip)
            status, reason = _judge(root_path, files, cls, meth, note)
            if status == "real":
                real_n += 1
            else:
                if status == "unsure": unsure_n += 1
                else: broken_n += 1
                issues.append((status.upper(), rel, selector, reason))
    for path, problem in unreadable:
        issues.append(("UNREADABLE", _rel(root_path, path), "", problem))
    selectors_n, unread_n = real_n + broken_n + unsure_n, len(unreadable)
    if selectors_n == 0 and unread_n == 0:
        sys.stderr.write("checked nothing: read %s drivers and found no selector\n" % len(parsed))
        return 2
    for kind, rel, selector, reason in sorted(issues):
        sys.stdout.write("%s %s %s%s\n" % (kind, rel, (selector + " ") if selector else "", reason))
    line = "checked %s selectors in %s drivers: %s real, %s broken, %s unsure" % (
        selectors_n, drivers_n, real_n, broken_n, unsure_n
    )
    if unread_n:
        line += ", %s unreadable" % unread_n
    sys.stdout.write(line + "\n")
    return 1 if broken_n or unread_n else 0
_STOP = {"code": 130}
def _on_stop(signum, _frame):
    if signum == signal.SIGTERM: _STOP["code"] = 143
    elif signum == signal.SIGHUP: _STOP["code"] = 129
    raise KeyboardInterrupt
def _guarded(func, args):
    _STOP["code"] = 130
    saved = []
    for sig in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP):
        previous = signal.getsignal(sig)
        saved.append((sig, previous))
        if previous == signal.SIG_IGN: continue
        signal.signal(sig, _on_stop)
    try:
        try: return func(args)
        except KeyboardInterrupt:
            sys.stderr.write("stopped\n")
            return _STOP["code"]
    finally:
        for sig, previous in saved: signal.signal(sig, previous)
def _parse_audit(args):
    root, globs, skips, index = None, None, [], 0
    while index < len(args):
        arg = args[index]
        if arg in ("--drivers", "--skip"):
            if index + 1 >= len(args):
                return "audit: %s needs a %s" % (arg, "glob" if arg == "--drivers" else "folder name")
            index += 1
            if arg == "--drivers":
                globs = [] if globs is None else globs
                globs.append(args[index])
            else: skips.append(args[index])
        elif arg.startswith("-"): return "audit: unknown argument %s" % arg
        elif root is None: root = arg
        else: return "audit: unexpected argument %s" % arg
        index += 1
    if root is None: return "checked nothing: audit needs a folder"
    return root, list(_DEFAULT_GLOBS) if globs is None else globs, skips
def _cmd_audit(args):
    parsed = _parse_audit(args)
    if isinstance(parsed, str):
        sys.stderr.write(parsed + "\n")
        return 2
    return _audit(*parsed)
def main(argv=None):
    if argv is None: argv = sys.argv[1:]
    if argv and argv[0] == "--worker":
        if len(argv) != 4:
            sys.stderr.write("verdict worker needs a report path and a selector\n")
            return 2
        return _worker(argv[1], argv[2], argv[3])
    if not argv:
        sys.stderr.write("usage: python3 catch.py verdict ... or python3 catch.py audit ROOT\n")
        return 2
    command, rest = argv[0], argv[1:]
    if command == "verdict": return _guarded(_cmd_verdict, rest)
    if command == "audit": return _guarded(_cmd_audit, rest)
    sys.stderr.write("unknown command: %s\n" % command)
    return 2
if __name__ == "__main__":
    sys.exit(main())
