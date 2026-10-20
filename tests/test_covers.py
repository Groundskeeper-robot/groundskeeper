"""python -m app.imagery covers LAT LON: which bundled sources claim a place, offline, sharpest free first."""
from app.imagery import tools
from app.imagery.coverage import covers


def test_annapolis_lists_maryland_before_naip_and_keyed_last():
    hits = tools.sources_at(-76.4911, 38.9789)                     # Maryland State House
    ids = [c["id"] for c in hits]
    assert ids.index("md_imap_sixinch") < ids.index("usgs_naip")
    keyed = [bool(c.get("auth")) for c in hits]
    assert keyed == sorted(keyed)                                  # every free source before any keyed one
    free = [c["native_m"] for c in hits if not c.get("auth")]
    assert free == sorted(free)
    assert all(covers(c, -76.4911, 38.9789) for c in hits)


def test_the_open_ocean_gets_only_worldwide_sources(capsys):
    hits = tools.sources_at(-30.0, 0.0)
    assert hits and all(c["coverage"]["type"] == "World" for c in hits)
    assert tools.main(["covers", "0", "-30"]) == 0
    assert "FINDING.md" in capsys.readouterr().out


def test_lat_lon_out_of_range_is_refused(capsys):
    assert tools.main(["covers", "200", "0"]) == 2
