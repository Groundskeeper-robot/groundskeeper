"""`@pytest.mark.node` (tests/conftest.py): a front-end test is skipped without node on a contributor's machine, and
fails in CI rather than being left out quietly."""
import pytest

from tests import conftest


class FakeItem:
    def __init__(self, marked):
        self.marked = marked

    def get_closest_marker(self, name):
        return object() if self.marked and name == "node" else None


@pytest.fixture
def no_node(monkeypatch):
    monkeypatch.setattr(conftest.shutil, "which", lambda name: None)


def test_a_node_test_runs_when_node_is_installed(monkeypatch):
    monkeypatch.setattr(conftest.shutil, "which", lambda name: "/usr/bin/node")
    monkeypatch.setenv("CI", "true")
    conftest.pytest_runtest_setup(FakeItem(marked=True))        # neither skips nor fails


def test_a_node_test_is_skipped_without_node_off_ci(no_node, monkeypatch):
    monkeypatch.delenv("CI", raising=False)
    with pytest.raises(pytest.skip.Exception):
        conftest.pytest_runtest_setup(FakeItem(marked=True))


def test_a_node_test_fails_without_node_in_ci(no_node, monkeypatch):
    monkeypatch.setenv("CI", "true")
    with pytest.raises(pytest.fail.Exception, match="node is missing in CI"):
        conftest.pytest_runtest_setup(FakeItem(marked=True))


def test_an_unmarked_test_never_needs_node(no_node, monkeypatch):
    monkeypatch.setenv("CI", "true")
    conftest.pytest_runtest_setup(FakeItem(marked=False))
