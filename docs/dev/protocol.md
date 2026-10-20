# Yarbo MQTT protocol reference

What the robot exposes over MQTT, locally and through Yarbo's cloud, as observed from a Yarbo with the mower head
in autumn 2026. Write commands are in [`map-writes.md`](map-writes.md); state and error codes in
[`robot-states.md`](robot-states.md).

Tools in `tools/`: `yarbo_local.py` (local broker, read-only, standard library only) and `yarbo_cloud.py` (a cloud
diagnostic on Yarbo's SDK).

## Brokers

| Thing | Value |
|---|---|
| Robot (rover) | `<robot-ip>`, EMQX on **1883 plain, anonymous**. Open TCP ports: 22, 1883, 8083 (EMQX WebSocket), 8084 (TLS), 8088, 8089, 8090, 8883 |
| Data Center (base station) | No open ports. The rover talks to it over HaLow. |
| Serial in topics | `{SN}`, the robot's serial, e.g. `YB0000DEMO0001` (uppercase works; lowercase is also granted) |

Find the robot on the network by its DHCP client name (it contains "yarbo"), not by a MAC vendor prefix.

**Cloud routing** (from the SDK source, `yarbo_robot_sdk/client.py` and `mqtt_client.py`): per serial, the SDK asks
a migration endpoint whether the robot has moved to the newer dedicated EMQX broker. New broker: `user=email,
pass=JWT`. Legacy broker: `user=JWT, pass=""`. Hosts come from the unauthenticated
`GET https://data-api.yarbo.ai/sdk/config`. A `Not authorized` from the new broker usually means a stale session
(the SDK can swallow a failed token refresh), not a problem with the account.

**Official command catalogue:** the SDK's `devices/yarbo_Y.json` → `control_topics`. It covers plan run control
(`start_plan`, `pause`, `resume`, `stop`, `cmd_recharge`), blade, lights, sound, child lock, follow mode, person
detection, obstacle handling, wireless charging and the read requests. **It has no map, plan or schedule writes and
no manual drive.** Those are learned by watching the official app on `snowbot/{SN}/app/#`.

## Message format

- **Requests:** `snowbot/{SN}/app/{cmd}`, payload `zlib.compress(json.dumps({...}))`.
- **Replies:** `snowbot/{SN}/device/data_feedback`, zlib JSON `{topic: <cmd>, state: 0, msg, data}`. `state` 0 is
  success; negative values are errors with a message.
- **Replies are matched by topic name only.** There is no request id. Two requests for the same command in flight
  cannot be told apart, so a client must serialise them and check any id the reply carries.
- **Heartbeat:** `snowbot/{SN}/device/heart_beat`, **plain** JSON `{"working_state":0|1}`, about 0.6 Hz, always on.
- **`DeviceMSG`** streams at about 1–2 Hz only while the phone app is connected. Otherwise poll `get_device_msg`.
- **Run commands get no reply** on either broker. Confirm them by the robot's state changing.
- Other device streams seen: `device/recharge_feedback`, `device/cloud_points_feedback`, `device/combined_odom_path`
  (the trail while the app records a boundary by driving).
- The official app sends action commands (lights, buzzer, plans) after a `get_controller` handshake. Read requests
  and plan/map saves do not need it.

## Access control (after the September 2026 "cleaning up MQTT" firmware)

| Operation | Result |
|---|---|
| CONNECT, no credentials | **accepted** |
| SUBSCRIBE `#`, `$SYS/#` | **refused** (128) |
| SUBSCRIBE `snowbot/{SN}/device/#`, `snowbot/{SN}/app/#`, `snowbot/{SN}/#` | **granted** |
| PUBLISH read requests (`get_device_msg`, `get_map`, `read_gps_ref`, `read_all_plan`, …) | **answered** |
| `get_controller` and action commands, locally | Not yet known; owner reports say this is what the update closed |
| **Cloud broker** (account session): SUBSCRIBE `snowbot/{SN}/#`, `/app/#`, `/device/#` | **granted for the account's own robot**; `snowbot/#` and `#` refused. The phone app's commands are visible here. |

Re-check after any firmware update: `python tools/yarbo_local.py acl '#' 'snowbot/<SN>/app/#'`.

**The phone app talks to the robot through the cloud broker, even on the home Wi-Fi.**

## Read commands

| Command | Returns |
|---|---|
| `get_device_msg` | Everything: battery (capacity, health, six cell temperatures, voltage, current); RTK (lat/lon, status, satellites, heading and DOP, raw GNGGA for rover and base); `CombinedOdom` x/y/phi and confidence; IMU pitch/roll/yaw; wheel speeds, distances, currents; blade motor rpm/current/temperature per blade; rain sensor; HaLow link strength; RTCM stats per source; `StateMSG` (working_state, error_code, stuck, obstacle, pedestrian, schedule_id, on_going_*); firmware versions; camera states; CPU/memory |
| `get_map` | `data` = base64(zlib(JSON)) with `areas`, `nogozones`, `novisionzones`, `pathways`, `sidewalks`, `allchargingData`, `deadends`, `elec_fence`, plus per-area `snowPiles`/`leaf_piles`/`humps`/`trimming_edges`. Each feature has `range: [{x, y, phi}]` in **local metres** from its own `ref: {latitude, longitude}` (x west, y north) |
| `read_gps_ref` | The map origin (lat/lon/height, `rtkFixType`) |
| `read_all_plan` | `{"data": [{id, name, areaIds, enable_self_order}, ...]}` |
| `read_all_clean_area` | Every area's full record (what an in-place area save starts from) |
| `read_mower_area_params {id}` | One area's mow settings; id 0 in the answer = never saved (robot defaults) |
| `read_mower_pathway_params {id}` | One pathway's mow settings |
| `read_deadend_params {id}` | One dead end's settings; id 0 = never saved, which does not say which dead end it is |
| `read_novisionzone {id}` | `{area_id: -1, by_pass_level, enable, id, name, range, ref}` |
| `read_low_cfd_config` | Per-area low-confidence (dead-reckoning) limits, each area's size in m², `enable_advanced_fusion` |
| `read_tow_params` | `{enable, move_speed}` |
| `read_global_params {id}` | Global settings (read by the app on opening) |
| `read_schedules` | `{"data": [record, ...]}`; `[]` when the robot has no schedules |
| `read_no_charge_period` | `[]` when none |
| `read_recharge_point` | The active charging point: `chargingPoint`/`startPoint` `{x, y, phi}`, `straightPhi`, flags |
| `get_plan_feedback` | The running plan's progress, or "no plan working" |
| `get_all_map_backup` | `data` is a JSON string: a list of `{id, name, timestamp (s), is_auto_backup}` |
| `get_map_buckup_from_id {id}` | One backup's map (**sic, the robot's spelling**) |
| `get_wifi_list` | `{"wifi_list": [...]}`: about 20 networks, strongest first |
| `get_connect_wifi_name` | The connected network; no answer when none is connected |
| `read_clean_area` | `state: -2` "An error occurred while reading the area data"; use `read_all_clean_area` |

Compute-only requests (the robot calculates and answers; nothing on it changes):

| Command | Returns |
|---|---|
| `preview_snowbot_area_path {range, id, ref, algorithm_type, push_snow_dir, snowPiles, leaf_piles, slope_degrees}` | `{path: [{x, y, phi}], total_time (s)}` in the area's `ref` frame, using the area's saved settings. Works locally; about 9 s and ~1,100 points for a 1,750 m² area |
| `preview_plan_path {id, percent}` | The app sends it right before `start_plan`. Not yet known whether it only computes |
| `check_map_connectivity {ids}` | Documented by community projects as a read-only check after a map write; not yet used by the panel |

**Privacy:** `get_map`, `read_gps_ref` and `get_device_msg` contain the home's GPS position, and the device
snapshot also carries the LTE SIM ICCID. `get_wifi_list` names nearby networks. Keep raw dumps out of anything
committed or synced.

### Map backups

- `get_all_map_backup {}` lists up to 20 backups (10 manual + 10 automatic slots). Names seen: "Auto Saved",
  "Manually Saved N", or user names.
- `get_map_buckup_from_id {"id": N}` answers in about 0.25 s on reply topic `get_map_buckup_from_id`
  (`get_map_backup_from_id` is reported to be an alias). `data` is a **plain dict** (not base64): the same map as
  `get_map` but with **singular keys**: `area`, `nogozone`, `novisionzone`, `pathway`, `sidewalk`, `deadend`,
  `elec_fence`, `chargingPoint`, `chargingPoints`, plus `id`, `name`, `timestamp`, `is_auto_backup`, `ref`, `dc`.
- **Backups hold a second copy of every pathway and sidewalk** (same name and points, fresh ids, other start/end
  ids). De-duplicate before comparing a backup with the live map.
- **Feature ids are global across kinds and look sequential, and the robot reuses freed ids.**

### Wi-Fi list

`signal` is a **string, 0–100** (a quality percentage, not dBm); `security` like `WPA2`, `WPA3`, `WPA2/WPA3`;
`saved` true for networks the robot has joined before. Each request is a fresh scan of about 4 s. While mowing on a
weak link the answer can take 30–60 s, and a request sent after a timeout can be answered together with the old one
(two answers about 0.1 s apart), so a client should use long timeouts, drop repeats and not place late answers on a
map.

## Commands the phone app sends that are not writes

| Command | Payload | Notes |
|---|---|---|
| `set_working_state` | `{"state":1}` | Keep-awake, about every 10 s while the app is open |
| `cmd_vel` | `{"vel":0.0,"rev":0.0}` | **Manual drive** (linear/angular velocity). The app zeroes it on opening and streams it from its joystick. Not in the SDK. **Never send it.** |
| `cmd_roller` | `{"vel": 1000}` | A motor command sent in bursts around blade switches and on opening. Never send it |
| `smart_vision_control` | `{"state":0}` / `{"state":1}` | Live view off/on. Reply: "Smart vision control command sent: N". No stream URL, SDP or token on MQTT |
| `mower_head_sensor_switch` | `{"cmd": 0}` or `{"state": -99}` | A status query on opening; reply state 33, "status query feedback" |
| `wireless_charging_cmd` | `{"cmd": 0}` | Sent by the app on opening and closing |
| `cmd_recharge` | `{"cmd": 2}` | Return to dock |
| `cmd_motor_protect` | `{"state": 15}` | Seen once after a boundary recording |
| `correct_map` | `{}` | The app's map-drift correction. Refused with state −5 "yarbo rtk status is not 4" without a fixed RTK; never sent by the panel |

## Low-confidence config

`read_low_cfd_config` returns `area_max_low_cfd_dis` (50 m: how far the robot may dead-reckon inside an area),
`path_max_low_cfd_dis` (10 m on boundaries and pathways), `draw_path_max_low_cfd_dis` (5 m while recording),
`enable_advanced_fusion`, `use_global_low_cfd_dis`, and each area's size in m². The size the robot reports for an
area can be the drawn polygon shrunk by about 0.3 m all round.

## Validation facts

- An area recorded by driving can have 252 points (the largest seen), small self-crossing kinks (~0.3 m) and repeated
  points a few millimetres apart; the robot tolerates all of these.
- `slope_degrees` and `push_snow_dir` of 9999 mean "not set". Some `slope_degrees` hold uninitialised doubles (e.g.
  2.7e-312): send them back as read.

## RTK at the dock

A docked, idle robot often shows GGA quality 1 (no RTK fix), `rtcm_age = -1` and every RTCM counter at zero. In recorded runs
the fix and corrections arrive once it leaves the dock, so RTK is not a useful start gate (see `design.md` §10.3).
