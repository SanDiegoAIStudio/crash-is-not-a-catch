#!/bin/sh
# Four labelled parts: clean baseline, unittest exit codes, verdict, audit.

HERE=$(CDPATH= cd -- "$(dirname "$0")" && pwd)
ROOT=$(CDPATH= cd -- "$HERE/.." && pwd)
PY=python3
S1=test_cart.Weak.test_total_formats
S2=test_cart.Strong.test_total_formats
S3=test_cart.Strong.test_total_format

TMP=$(mktemp -d "${TMPDIR:-/tmp}/catch-demo.XXXXXX")
trap 'rm -rf "$TMP"' EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
/bin/cp "$HERE/cart.py" "$HERE/test_cart.py" "$HERE/mutate_cart.py" "$TMP/"

fail() {
  printf '%s\n' "demo: $1" >&2
  exit 1
}

printf '%s\n' "--- 1. baseline ---"
"$PY" "$ROOT/catch.py" verdict --baseline --cwd "$TMP" "$S1" "$S2" > "$TMP/p1.out"
printf '%s\n' "$?" > "$TMP/p1.code"
cat "$TMP/p1.out"
printf '%s\n' "exit $(cat "$TMP/p1.code")"

"$PY" -c '
import sys
from pathlib import Path
path = Path(sys.argv[1])
text = path.read_text(encoding="utf-8")
old = "return sum(items)"
new = "return None"
if old not in text:
    sys.stderr.write("demo: could not plant the bug\n")
    sys.exit(1)
path.write_text(text.replace(old, new, 1), encoding="utf-8")
' "$TMP/cart.py" || fail "could not plant the bug"

printf '%s\n' "--- 2. planted bug, unittest exit codes ---"
: > "$TMP/p2.out"
for sel in "$S1" "$S2" "$S3"; do
  (cd "$TMP" && "$PY" -m unittest "$sel") >/dev/null 2>&1
  printf '%s %s\n' "$sel" "$?" | tee -a "$TMP/p2.out"
done

printf '%s\n' "--- 3. planted bug, verdict ---"
"$PY" "$ROOT/catch.py" verdict --cwd "$TMP" "$S1" "$S2" "$S3" > "$TMP/p3.out"
printf '%s\n' "$?" > "$TMP/p3.code"
cat "$TMP/p3.out"
printf '%s\n' "exit $(cat "$TMP/p3.code")"

printf '%s\n' "--- 4. audit ---"
"$PY" "$ROOT/catch.py" audit "$TMP" > "$TMP/p4.out"
printf '%s\n' "$?" > "$TMP/p4.code"
cat "$TMP/p4.out"
printf '%s\n' "exit $(cat "$TMP/p4.code")"

expected1='test_cart.Weak.test_total_formats    passed: not a catch
test_cart.Strong.test_total_formats  passed: not a catch
passed 2 of 2'
expected3='test_cart.Weak.test_total_formats    crashed (TypeError): not a catch
test_cart.Strong.test_total_formats  caught
test_cart.Strong.test_total_format   no such test: not a catch
caught 1 of 3'
expected4='BROKEN mutate_cart.py test_cart.Strong.test_total_format searched test_cart.py; Strong is there and test_total_format is not
checked 3 selectors in 1 drivers: 2 real, 1 broken, 0 unsure'

[ "$(cat "$TMP/p1.out")" = "$expected1" ] || fail "part 1 was not a clean baseline"
[ "$(cat "$TMP/p1.code")" = "0" ] || fail "part 1 did not exit 0"

while IFS= read -r line; do
  code=${line##* }
  [ "$code" != "0" ] || fail "part 2 had a zero exit code"
done < "$TMP/p2.out"
[ "$(wc -l < "$TMP/p2.out" | tr -d ' ')" = "3" ] || fail "part 2 did not report three exit codes"

[ "$(cat "$TMP/p3.out")" = "$expected3" ] || fail "part 3 was not the planted-bug verdict"
[ "$(cat "$TMP/p3.code")" = "1" ] || fail "part 3 did not exit 1"

[ "$(cat "$TMP/p4.out")" = "$expected4" ] || fail "part 4 did not report the misspelled selector"
[ "$(cat "$TMP/p4.code")" = "1" ] || fail "part 4 did not exit 1"
