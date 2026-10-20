"""The version, and the Help view's pages: every docs/user page and the changelog are served, unknown or unsafe ids
are not, and the links inside the pages point at views, Settings sections and help pages that exist."""
import asyncio
import re

import pytest
from fastapi import HTTPException

from app import help as help_mod
from app import main
from app.version import __version__, version_info

WEB = main.WEB


def test_version_is_semver_and_in_the_changelog():
    assert re.fullmatch(r"\d+\.\d+\.\d+", __version__)
    assert f"## {__version__} " in help_mod.CHANGELOG.read_text(encoding="utf-8")
    assert version_info()["version"] == __version__


def test_index_lists_every_page_and_the_changelog_last():
    idx = asyncio.run(main.help_index())
    ids = [p["id"] for p in idx["pages"]]
    on_disk = sorted(p.stem for p in help_mod.PAGES.glob("*.md"))
    assert sorted(i for i in ids if i not in help_mod.EXTRA) == on_disk
    assert ids[-1] == "changelog" and idx["version"] == __version__
    assert ids[0] == "howto"                      # ordered by the <!-- order: N --> comment, not by name
    assert all(p["title"] and p["title"] != p["id"] for p in idx["pages"])


@pytest.mark.parametrize("page_id", sorted(p.stem for p in help_mod.PAGES.glob("*.md")) + list(help_mod.EXTRA))
def test_every_page_is_served_as_markdown(page_id):
    p = asyncio.run(main.help_page(page_id))
    assert p["markdown"].startswith("# ") and p["title"]


@pytest.mark.parametrize("bad", ["nope", "../README", "..%2FCLAUDE", "CHANGELOG", "Live", "a/b", ""])
def test_unknown_or_unsafe_ids_are_404(bad):
    with pytest.raises(HTTPException) as e:
        asyncio.run(main.help_page(bad))
    assert e.value.status_code == 404


def _links():
    for p in list(help_mod.PAGES.glob("*.md")) + [help_mod.CHANGELOG]:
        for href in re.findall(r"\]\(([^)\s]+)\)", p.read_text(encoding="utf-8")):
            yield p.name, href


def test_links_in_the_pages_go_somewhere():
    app_js = (WEB / "app.js").read_text(encoding="utf-8")
    views_src = re.search(r"const VIEWS = \{(.*?)\};", app_js, re.S).group(1)
    views = set(re.findall(r'data-view="([a-z]+)"', (WEB / "index.html").read_text(encoding="utf-8")))
    views |= set(re.findall(r"(\w+):", views_src))           # every view, also those shown as tabs (Robot)
    sections = set(re.findall(r'^\s*\["([a-z]+)", "', (WEB / "settings.js").read_text(encoding="utf-8"), re.M))
    pages = {p.stem for p in help_mod.PAGES.glob("*.md")} | set(help_mod.EXTRA)
    routes = {r.path for r in main.app.routes}
    bad = []
    for name, href in _links():
        if href.startswith("#help/"):
            ok = href[6:].split("#")[0] in pages
        elif href.startswith("#settings/"):
            ok = href[10:] in sections
        elif href.startswith("#"):
            ok = href[1:] in views
        elif href.startswith("/"):
            ok = href in routes
        else:
            ok = href.startswith("https://")
        if not ok:
            bad.append((name, href))
    assert not bad, bad
