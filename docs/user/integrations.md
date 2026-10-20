# Integrations
<!-- order: 88 -->

All optional. Each is set in Settings and has a Test button.

- **Tempest weather station** ([Lawn conditions](#settings/lawn)): read from the hub's local broadcast on UDP
  50222. No account or cloud. Feeds Dry / Damp / Wet. When the hub hears several stations, pick yours.
- **Weather from Home Assistant** ([Lawn conditions](#settings/lawn)): rain (a rate, a running total, an on/off
  rain sensor, or a weather entity's rainy or not), temperature (°C, °F or K) and humidity sensors can stand in for
  the Tempest or back it up; pick the source per input (Either takes the Tempest while it reports). Each input is a
  list of the HA entities that fit it (matched by HA's device class, likely ones first, with what each reads now);
  "Show all sensors" adds the rest, and "Type an id…" takes anything else. **Soil moisture** sensors (Zigbee2MQTT ones arrive
  in HA as entities) hold the lawn at Damp or Wet above the levels you set, however long ago it rained.
- **Frigate** ([Cameras](#settings/cameras)): tick the cameras that see the yard and draw what each covers. They
  feed the yard-clear check, the person and pet guard, and the follow-cam.
- **Home Assistant** ([Home Assistant](#settings/ha)): with an address and a long-lived token the panel reads
  presence and publishes `sensor.yarbo_panel_*` entities every minute. In Docker, use HA's IP address: `.local`
  names often do not resolve inside a container, and Test says so. Presence is picked from a list: an alarm panel
  (armed away or on vacation = nobody home), `zone.home` (people in it), or a person, tracker or group of them.
- **Notifications** ([Notifications](#settings/alerts)): an HA notify service, picked from the ones your Home
  Assistant has (the companion app's phones first), and/or a webhook. Alerts: robot
  error, stuck, head changed, cloud needs sign-in, maintenance due, schedule confirmations.
- **API:** every endpoint with its schema is at [/api/docs](/api/docs).
