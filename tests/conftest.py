"""Shared pytest setup.

`@pytest.mark.node` marks a test that runs a page script in Node.js. Without node it is skipped on a contributor's
machine (node is optional there, as for check.py's syntax check) and fails in CI, where the front-end tests must run.
Use the marker rather than a `skipif` of your own, so no test can be left out of CI quietly."""
import os
import shutil

import pytest


def pytest_configure(config):
    config.addinivalue_line("markers", "node: runs a page script in Node.js; skipped without node, fails in CI")


def pytest_runtest_setup(item):
    if item.get_closest_marker("node") is None or shutil.which("node"):
        return
    if os.environ.get("CI"):
        pytest.fail("node is missing in CI: the front-end tests must run there", pytrace=False)
    pytest.skip("node is not installed")
