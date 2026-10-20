# Groundskeeper

**A self-hosted recorder, map and guarded control panel for Yarbo robots.** It runs in one container on your own
network. It records every mow, shows the yard on aerial imagery, explains why the robot can't run, and starts,
pauses or docks it only when every safety check passes.

> Groundskeeper is an independent community project. It is **not affiliated with, endorsed by or supported by
> Yarbo**. It controls a machine with spinning blades: read [Safety](#safety) before switching control on, and use
> it at your own risk.

![Live: the robot mid-mow on aerial imagery, its track coloured by RTK fix, the plan's progress and the lawn conditions](docs/screenshots/live.png)

| | |
|---|---|
| [![Runs](docs/screenshots/runs.png)](docs/screenshots/runs.png) Every mow, its coverage, gaps and trends | [![One run](docs/screenshots/run.png)](docs/screenshots/run.png) One run: coverage by area, a gap, replay |
| [![3D map](docs/screenshots/live-3d.png)](docs/screenshots/live-3d.png) The 3D map with the follow camera | [![Map Studio](docs/screenshots/studio.png)](docs/screenshots/studio.png) Map Studio: an area's shape and mow settings |
| [![Schedule](docs/screenshots/schedule.png)](docs/screenshots/schedule.png) Schedule windows and why a start would wait | [![Robot health](docs/screenshots/robot-health.png)](docs/screenshots/robot-health.png) Robot health: RTK, link, battery, deck |
| [![Settings](docs/screenshots/settings.png)](docs/screenshots/settings.png) Settings → Safety: control is off until you switch it on | [![Light theme](docs/screenshots/live-light.png)](docs/screenshots/live-light.png) The light theme |
| [![Phone](docs/screenshots/phone-live.png)](docs/screenshots/phone-live.png) On a phone | |

The screenshots come from the built-in demo (`DEMO=1`): a made-up robot mowing a made-up yard laid out on a public
park's lawn, so no real home appears. `python scripts/screenshots.py` makes them again.

## What it does

- **Live:** the robot on aerial imagery with its track coloured by RTK quality, the plan's progress, the battery,
  the link, the lawn conditions and why the robot can't run right now. It also has a 3D map with a follow camera.
- **Runs:** every mow is recorded, with coverage and gaps, planned versus driven, problem areas, signal maps and
  replay.
- **Map Studio:** inspect, measure, diff and export the robot's map. It can edit no-go zones, pathways, dead ends,
  no-vision zones, areas and plans, and every write is read back and verified.
- **Schedule:** the panel owns the schedule, with run rules (dry lawn, daylight, someone home, the yard clear on
  cameras), and it can ask you before it starts.
- **Guards:** if a person appears in a yard camera's coverage (Frigate) the robot pauses, and if rain starts it
  docks.
- **Robot:** health, maintenance timers and an event log of everything sent or refused.
- **Integrations, all optional:** Home Assistant (sensors, presence, notifications), Frigate, a Tempest weather
  station, and AI assistants over MCP.

## Quick start

```bash
mkdir groundskeeper && cd groundskeeper
curl -fsSLO https://raw.githubusercontent.com/jpittelkow/groundskeeper/main/docker-compose.yml
docker compose up -d
```

Open `http://<host>:8425` and set the admin password. The setup code it asks for is in the log:
`docker logs groundskeeper`. Then the setup guide walks you through the robot's address and serial, the Yarbo cloud
sign-in (needed only for control), the imagery, cameras, Home Assistant and safety settings. All data stays in
`./data`. It includes your yard's location, so back it up and keep it private.

**Updating:** run `docker compose pull && docker compose up -d`. To stay on one release line, pin the image tag in
`docker-compose.yml` (for example `:1.0`). Each release's changes are in the [changelog](CHANGELOG.md) and on the
[releases page](https://github.com/jpittelkow/groundskeeper/releases).

**Other options** are in [`.env.example`](.env.example). The panel runs as `PUID:PGID` (1000:1000 by default), so
set those to your own user if you want to edit `./data` by hand. An Unraid example is in
[`deploy/`](deploy/docker-compose.unraid.yml). It runs on amd64 and arm64, including a Raspberry Pi 4 or 5.

## Safety

- The panel asks for a password. You set it on the first visit, using a one-time code from the container's log.
  Programs use named API tokens instead.
- Control is **off** on a new install. Switching it on means typing ENABLE, and it is logged.
- Only five commands exist: start a plan, pause, resume, stop and dock. Manual driving (`cmd_vel`) is blocked for
  good.
- Every start passes hard interlocks that can't be overridden: the robot is online, it has no active error and
  enough battery, the phone app isn't driving it, and the yard is clear. Soft checks, such as a wet lawn, need a
  person to override them.
- Guards that resume a paused robot on their own are opt-in.
- The robot's local MQTT broker is used read-only. Control goes through the Yarbo cloud, the same route the
  official SDK uses.

## Documentation

- [User guide](docs/user/): the same pages as the in-app Help.
- [Developer docs](docs/dev/): design, the Yarbo protocol, map write formats, robot states, the UI system and the
  imagery catalog.
- [Changelog](CHANGELOG.md)
- [Contributing](CONTRIBUTING.md) and [AGENTS.md](AGENTS.md), the rules for AI coding agents.
- [Security](SECURITY.md): report problems privately.

## License

[MIT](LICENSE). Yarbo is a trademark of its owner, used here only to say which robot this works with.
