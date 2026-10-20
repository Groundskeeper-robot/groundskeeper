"""The panel's version. Bump it with each release that changes behaviour, move CHANGELOG.md's "Unreleased" entry
under the new number in the same commit, and tag the commit `v<version>`. The build (git commit) comes from the
image: scripts/deploy.sh passes it as PANEL_BUILD."""
import os

__version__ = "0.16.0"
BUILD = os.environ.get("PANEL_BUILD") or None


def version_info() -> dict:
    return {"version": __version__, "build": BUILD}
