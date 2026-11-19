# crash-is-not-a-catch

This is a small Python tool for people who plant bugs in their own code and need an honest answer to one question: did a test catch it? A planted bug counts as caught only when an assertion fails.

## What you get

`verdict` runs each test selector in its own fresh process and prints one of six plain verdicts, then a count line. `audit` reads the driver scripts under a folder and reports every selector that names a test which does not exist. An audit parses files and imports nothing. It needs Python 3.9 or newer and nothing to install.

## Quick start

```
sh examples/demo.sh
```

```
--- 1. baseline ---
test_cart.Weak.test_total_formats    passed: not a catch
test_cart.Strong.test_total_formats  passed: not a catch
passed 2 of 2
exit 0
--- 2. planted bug, unittest exit codes ---
test_cart.Weak.test_total_formats 1
test_cart.Strong.test_total_formats 1
test_cart.Strong.test_total_format 1
--- 3. planted bug, verdict ---
test_cart.Weak.test_total_formats    crashed (TypeError): not a catch
test_cart.Strong.test_total_formats  caught
test_cart.Strong.test_total_format   no such test: not a catch
caught 1 of 3
exit 1
--- 4. audit ---
BROKEN mutate_cart.py test_cart.Strong.test_total_format searched test_cart.py; Strong is there and test_total_format is not
checked 3 selectors in 1 drivers: 2 real, 1 broken, 0 unsure
exit 1
```

## The six verdicts

A run that hit the time limit reads `timed out`, whatever else happened in it. A child that ended without a report reads `crashed (exit N)`. Every other run gets the first row below that fits.

| Verdict | What happened | Why it is not a catch |
| --- | --- | --- |
| `no such test: not a catch` | The selector names nothing. There is no module of that name, or the module loads and the class or method is missing, or the name is not a test. | Nothing ran, so nothing checked the planted bug. |
| `caught` | At least one assertion failed, including a failed subTest. Other tests under the same selector may have crashed. | This is a catch. |
| `crashed (NAME): not a catch` | No assertion failed, and something else was raised. NAME is the class of the first such exception. A child that ends without a report reads `crashed (exit N): not a catch`. A test module that raises while it is imported counts here too, so a planted bug that breaks an import reads `crashed (ImportError): not a catch`. | The test blew up before an assertion could fail. |
| `passed: not a catch` | At least one test ran to the end, and nothing failed or crashed. An expected failure and an unexpected success count as passed. | The tests still agree with the code. |
| `nothing ran: not a catch` | The selector loaded, and no test ran, or every test was skipped. | There is no result to score. |
| `timed out: not a catch` | The child was killed at the timeout. | A killed run has no assertion result. |

## Use it in your own driver

Shell form, four steps: baseline, plant, verdict, restore.

```
python3 catch.py verdict --baseline --cwd examples test_cart.Strong.test_total_formats
python3 -c 'from pathlib import Path; p = Path("examples/cart.py"); t = p.read_text(encoding="utf-8"); p.write_text(t.replace("return sum(items)", "return None", 1), encoding="utf-8")'
python3 catch.py verdict --cwd examples test_cart.Strong.test_total_formats
python3 -c 'from pathlib import Path; p = Path("examples/cart.py"); t = p.read_text(encoding="utf-8"); p.write_text(t.replace("return None", "return sum(items)", 1), encoding="utf-8")'
```

`verdict` prints one line for each selector, then a count line. It exits 0 when every selector was caught and 1 otherwise. With `--baseline` the count line counts `passed`, and the exit code is 0 when every selector passed and 1 otherwise, which is the check to make before you plant anything. `--cwd DIR` is the folder the tests are run from, and `--timeout SEC` is the time limit for each selector, 60 seconds unless you set it. SEC is a number above zero and may have a decimal part, such as `1.5`. A usage error exits 2 with the reason on standard error. What the test itself prints is not shown, so run it with `python3 -m unittest` to read its own output.

Python form:

```
import catch
print(catch.verdict("test_cart.Strong.test_total_formats", cwd="examples").caught)
```

Each verdict runs the test in a fresh child process that compiles your code from source, its bytecode cache folder is new and empty, nothing is written to it, a Python process the test starts gets the same folder, and so a planted change is seen even when it lands in the same second and keeps the file the same size.

Ctrl-C, a terminate or a hang-up stops the run, the test process is stopped, the temporary files are removed, `stopped` is printed on standard error, and the exit code is 130, 143 or 129.

`catch.verdict(selector, cwd=".", timeout=60.0)` raises `ValueError` when `cwd` is not a folder or the timeout is not a positive number. Zero, a negative number, infinity and nan are all refused, on the command line too. Otherwise it returns a `Verdict` with `selector`, `kind`, and `detail`. `kind` is one of `caught`, `crashed`, `passed`, `no such test`, `nothing ran`, `timed out`. `detail` holds the exception name, or the exit text, for a crash, and is empty otherwise. `.caught` is true only for kind `caught`. `str` on the result is the verdict text above.

## Audit your selectors

```
python3 catch.py audit .
```

A driver is a `.py` file under the root whose name matches a driver glob (the default is `*mutat*.py`; `--drivers` replaces it and can be repeated), outside any folder named `.git`, `node_modules`, `.venv`, `venv`, or `__pycache__` (`--skip` adds a folder name), that does not itself define a unittest test class. Every file is read with `ast`. Nothing is imported or run.

A selector, for the audit, is a string literal whose whole text is `Class.test_method` or `some.module.Class.test_method`: the class starts with a capital letter and the method starts with `test_`. A test file is named `test_*.py` or `*_test.py`.

A selector with a module path is looked up in that module's file only, beside the driver first and then from the root. With no module path, it is looked up in the test files the driver names (a string that is a test file name, or a module prefix ending in a dot such as `"test_cart."`, including the constant part of an f-string); when the driver names none, in the test files in the driver's folder; when that folder has none, in every test file under the root. A selector is real when a searched file defines that class and method, in the class body or on a base class defined in the same file.

Real selectors are not listed. `BROKEN` means the selector was not found. Either no searched file has the class and the method, or a searched file could not be read as Python, or there was nothing to search: the module's file, or a test file the driver names, was not found beside the driver or from the root, or the root holds no test file. The line names the files that were searched, or reads `searched nothing`. `UNSURE` means the class is there, the method is not, and the class has a base the file does not define, other than `TestCase`, `unittest.TestCase`, `IsolatedAsyncioTestCase`, or `unittest.IsolatedAsyncioTestCase`. `UNREADABLE` means the driver could not be read or parsed. A test name that is assigned in the class body, not defined with def, reads UNSURE, and a selector may name a class nested in another class.

The last line is `checked S selectors in D drivers: R real, B broken, U unsure`, with `, X unreadable` added only when X is above zero. Exit 1 when anything is broken or unreadable, otherwise 0. Exit 2, with one line on standard error that starts `checked nothing:`, when no root was given, when the root is not a folder, when no driver matched (the line names the globs), or when the drivers hold no selector (the line says how many drivers were read). A flag with no value, an unknown flag or a second root also exits 2, and that line starts `audit:`. An audit that read nothing never exits 0.

## Why a crash is not a catch

`examples/cart.py` has `total(items)`, which returns the sum, and `fmt(n)`, which returns the dollar text. `Weak.test_total_formats` checks `fmt(total([1, 2]))` in one step. `Strong.test_total_formats` checks that the total is 3, then checks the formatted text. Both pass on the clean code.

Make `total` return `None`. The weak test blows up inside the formatter before it checks anything, and the error is a `TypeError`. The strong test fails on the line that checks the value. A third selector spells the method wrong and loads nothing. Plain `python3 -m unittest` exits non-zero for all three, so an exit code cannot tell a crash caused by the planted bug from a selector that names nothing. A planted bug that breaks an import is a crash as well, because the test exists and never reached an assertion. `verdict` separates them: `crashed (TypeError): not a catch`, `caught`, and `no such test: not a catch`.

## Try it on your own suite

1. Pick one function and break it by hand: return `None`, flip a comparison, or drop a line.
2. Run its tests and read the first error. A red count on its own hides whether that error was an assertion.
3. When the error is a `TypeError`, a `KeyError`, an `AttributeError`, or any exception other than an assertion, that test did not catch the bug. Add an assertion on the value before the code that uses it.
4. When a mutation tool scores kills, count only assertion failures, and keep that rule in the tool.

## Limits

It reads Python's built-in unittest results only, so tests that only another runner can collect read as no such test. A bare function given as a selector is called once by unittest's loader while it loads. An `assert` that fails inside the code under test counts as an assertion failure. An assertion that fails in `setUpClass` or `setUpModule` counts as a failed assertion. A module that raises `unittest.SkipTest` while it is imported reads crashed (SkipTest).

The audit sees only selectors written whole as string literals, with class names that start with a capital letter. A method inherited from a base class in another file reads UNSURE. A test attached to a class after the class statement, for example with setattr, reads BROKEN in the audit although `verdict` runs it. When one test file defines the same class name twice, the audit reads the first definition and Python runs the last, so the audit and `verdict` can disagree about that class. The audit does not parse a file that starts with a byte order mark: such a driver reads UNREADABLE, and a selector that points into such a test file reads BROKEN although `verdict` runs the test. On Python 3.9 a NUL byte in a driver, or in a test file the audit searches, stops the audit with a traceback. On Python 3.14 such a driver reads UNREADABLE, and a selector that points into such a test file reads BROKEN.

The audit knows unittest's base classes only by the four names listed under Audit your selectors, as they are written in the file. After `import unittest as ut`, a class that extends `ut.TestCase` is read as having a base from outside the file: a missing method on it reads UNSURE where it would read BROKEN, and a file that matches a driver glob and defines such a class is read as a driver.

It plants nothing itself. At the time limit or a stop, the test process is stopped, and a process that test started may keep running. A test that leaves a non-daemon thread running keeps its process alive, so when that thread outlasts the time limit the test reads timed out, even when an assertion had already failed.

It was tested on macOS with Python 3.9 and Python 3.14. It uses the hang-up signal, which Windows does not have.

## Tests

From this folder:

```
python3 -m unittest discover -s tests -v
python3 tests/mutate.py
sh examples/demo.sh
```

The unit tests cover both commands. The mutation run plants one bug at a time in a fresh copy of `catch.py`, runs the tests against that copy in a temporary folder, and scores the result with the untouched tool, so a planted bug counts only when an assertion failed. Its last line reads `caught N of N` when every planted bug was caught, and it takes several minutes. It exits 0 when every planted bug was caught and 1 when one was not. Each run of the tests inside it is held to 90 seconds, and a planted bug whose run goes past that reads `timed out: not a catch` and counts as not caught. It exits 2, with the reason on standard error, when the tests do not pass on the clean copy inside that limit or when a planted text is not in `catch.py` exactly once. The demo exits 0 when parts 1, 3 and 4 printed the lines and the exit code shown under Quick start, and part 2 printed three exit codes that are not zero.

The tests and the mutation run use the Python that started them. The demo, and the one test that runs it, use the `python3` on your path.

Some tests start the tool as a process of its own and kill it when it does not exit in time. The tests that stop the tool with a signal while a slow test runs also kill that test's process, by the process id it wrote down, when it is still running.

## License

MIT. The terms are in the LICENSE file.

## Built at San Diego AI Studio

This tool comes out of San Diego AI Studio, the builders' community in San Diego, where builders show the systems they run. More projects: https://sandiegoaistudio.com/projects
