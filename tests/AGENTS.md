# tests/: pytest

Local rules for tests. The top-level `AGENTS.md` comes first.

- **Offline and synthetic.** No network, no robot, no real data. Coordinates are the made-up Annapolis yard or a
  public landmark; addresses `192.0.2.x`; serial `YB0000DEMO0001`. Never paste a real capture, even trimmed.
- **Reuse the fakes** before writing a new one: `fakehttp.py` (`FakeHTTP`, `no_network`) for imagery and HTTP;
  `test_mapwrite.py` `FakeRobot` for writes and read-back; `test_phase2.py` `FakeCloud` and `make_controller` for
  commands and interlocks; `test_runs.py` `FakeRec`, `FakeController`, `FakeNotifier` for runs and guards;
  `test_settings_api.py` `fake_request` for routes; `app/demo_robot.py` for a whole simulated robot.
- **Test the behaviour, not the implementation.** One test per behaviour, named for it
  (`test_a_paused_plan_keeps_the_run_open`).
- **A bug fix starts with a failing test** that reproduces it.
- **`test_guards.py` is not yours to change.** It encodes the rules in `AGENTS.md`. If it fails, the change is wrong;
  if the rule itself must change, say so in the issue first.
- Run one file with `python -m pytest -q tests/test_x.py`; everything with `python scripts/check.py`.
