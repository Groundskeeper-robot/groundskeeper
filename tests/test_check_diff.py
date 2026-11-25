"""scripts/check_diff.py: before review, a branch that adds a test skip or edits tests/test_guards.py is caught, and
nothing else is. Synthetic diffs; one test runs the script on a throwaway git repository."""
import importlib.util
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location("check_diff", ROOT / "scripts" / "check_diff.py")
check_diff = importlib.util.module_from_spec(spec)
spec.loader.exec_module(check_diff)

SKIP = "pytest." + "skip"            # split, so this file adds no skip of its own
SKIPIF = "@pytest.mark." + "skipif"

DIFF = f"""diff --git a/tests/test_x.py b/tests/test_x.py
--- a/tests/test_x.py
+++ b/tests/test_x.py
@@ -10,0 +11,2 @@ def helper():
+{SKIPIF}(True, reason="flaky")
+def test_y():
@@ -40 +42 @@
+    {SKIP}("later")
diff --git a/app/old.py b/app/old.py
deleted file mode 100644
--- a/app/old.py
+++ /dev/null
@@ -1 +0,0 @@
-x = 1
"""


def test_added_lines_are_numbered_as_in_the_new_file():
    added = check_diff.added_lines(DIFF)
    assert added["tests/test_x.py"] == [(11, f'{SKIPIF}(True, reason="flaky")'), (12, "def test_y():"),
                                        (42, f'    {SKIP}("later")')]
    assert "app/old.py" not in added


def test_an_added_line_that_starts_with_two_pluses_is_content_not_a_header():
    diff = "diff --git a/web/a.js b/web/a.js\n--- a/web/a.js\n+++ b/web/a.js\n@@ -1,0 +2 @@\n+++ i;\n"
    assert check_diff.added_lines(diff) == {"web/a.js": [(2, "++ i;")]}


def test_an_added_skip_in_a_test_is_a_problem():
    out = check_diff.problems(check_diff.added_lines(DIFF))
    assert [p.split(": ")[0] for p in out] == ["tests/test_x.py:11", "tests/test_x.py:42"]
    assert "@pytest.mark.node" in out[0]


@pytest.mark.parametrize("text", [f"{SKIPIF}(sys.platform == 'win32', reason='x')", "pytest.mark." + "xfail",
                                  "pytest." + "importorskip('numpy')", "needs = pytest.mark." + "skip",
                                  "from pytest import raises, " + "skip as later"])
def test_every_kind_of_skip_is_caught(text):
    assert check_diff.problems({"tests/test_x.py": [(1, text)]})


def test_a_skip_added_to_conftest_is_caught_too():
    assert check_diff.problems({"tests/conftest.py": [(9, f"    {SKIP}('later')")]})


def test_the_node_marker_and_skips_outside_tests_are_not_problems():
    changed = {"tests/test_x.py": [(1, "@pytest.mark.node"), (2, "def test_it_skips_a_stale_sample():"),
                                   (3, "from pytest import raises")],
               "app/why.py": [(3, f"# {SKIP}(...) is how tests leave things out")],
               "tests/flow.cjs": [(1, f"// {SKIP}(")]}
    assert check_diff.problems(changed) == []


def test_any_change_to_the_guard_tests_is_a_problem_unless_a_maintainer_says_so():
    cut_only = {"tests/test_guards.py": []}           # even a change that only removes lines
    assert check_diff.problems(cut_only)[0].startswith("tests/test_guards.py: changed")
    assert check_diff.problems(cut_only, guard_change_ok=True) == []


def git(cwd, *args):
    env = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "user@example.com",
           "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "user@example.com"}
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, env=env)


def test_the_script_compares_the_working_tree_with_where_the_branch_left_main(tmp_path):
    (tmp_path / "scripts").mkdir()
    shutil.copy(ROOT / "scripts" / "check_diff.py", tmp_path / "scripts")
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_a.py").write_text("def test_a():\n    pass\n", encoding="utf-8")
    git(tmp_path, "init", "-q", "-b", "main")
    git(tmp_path, "add", "-A")
    git(tmp_path, "commit", "-q", "-m", "base")
    git(tmp_path, "checkout", "-q", "-b", "work")
    run = lambda: subprocess.run([sys.executable, "scripts/check_diff.py"], cwd=tmp_path, capture_output=True,
                                 text=True, env={**os.environ, "GK_BASE": "main"})
    clean = run()
    assert clean.returncode == 0 and "0 problem(s)" in clean.stdout
    (tmp_path / "tests" / "test_b.py").write_text(f"import pytest\n{SKIPIF}(True, reason='x')\n", encoding="utf-8")
    new_file = run()                                  # a new file, not yet added to git, counts
    assert new_file.returncode == 1 and "tests/test_b.py:2: an added skip" in new_file.stdout
    (tmp_path / "tests" / "test_b.py").unlink()
    (tmp_path / "tests" / "test_a.py").write_text(f"def test_a():\n    {SKIP}('x')\n", encoding="utf-8")
    unstaged = run()                                  # so does an edit not yet committed
    assert unstaged.returncode == 1 and "tests/test_a.py:2: an added skip" in unstaged.stdout


def test_with_no_main_to_compare_with_the_script_says_so_and_passes(tmp_path):
    (tmp_path / "scripts").mkdir()
    shutil.copy(ROOT / "scripts" / "check_diff.py", tmp_path / "scripts")
    git(tmp_path, "init", "-q", "-b", "work")
    git(tmp_path, "add", "-A")
    git(tmp_path, "commit", "-q", "-m", "only")
    env = {k: v for k, v in os.environ.items() if k != "GK_BASE"}
    r = subprocess.run([sys.executable, "scripts/check_diff.py"], cwd=tmp_path, capture_output=True, text=True,
                       env=env)
    assert r.returncode == 0 and "skipped" in r.stdout
