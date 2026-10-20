# Setting up
<!-- order: 1 -->

[Settings → Set up](#settings/setup) walks through these steps in order. It opens by itself on a fresh install
(no robot address or no map yet), and each step is the same controls as its own Settings section, so you can also
skip the guide and work through the list below. A tick means the panel already has what the step needs; nothing
is stored for the guide itself.

Before any of this, the first visit sets the admin password. It needs the setup code from the panel's log
(`docker logs groundskeeper`); see [Signing in](#help/settings).

1. **Robot** ([Robot](#settings/robot)): the robot's address on your network. Test reads the serial from its
   heartbeat; then the map is read on its own.
2. **Yarbo cloud** (optional, [Robot](#settings/robot)): only for sending commands and writing the map. You type
   your own password; it goes to Yarbo's SDK and is never stored.
3. **AI assistant** (optional, [AI assistant](#settings/assistant)): a token so an assistant can do the rest with
   you by conversation. See [AI assistants](#help/mcp).
4. **Map imagery** ([Map imagery](#settings/imagery)): the aerial photo under the map, sharpest first, and the
   alignment. See [Map imagery](#help/imagery).
5. **Cameras** (optional, [Cameras](#settings/cameras)): with Frigate, switch on the cameras that see the yard and
   draw what each covers.
6. **Home Assistant** (optional, [Home Assistant](#settings/ha)): the address and a long-lived token, then the
   presence entity, picked from a list. If the panel runs in Docker, use HA's IP address: `.local` names often do
   not resolve there.
7. **Weather** ([Lawn conditions](#settings/lawn)): a Tempest on your network is heard with no setup; Home
   Assistant sensors are picked from lists matched to each input.
8. **Safety** ([Safety](#settings/safety), [Run rules](#settings/rules)): the recommended values are control off,
   yard must be clear on, and no resume after a guard pause. "Use the recommended" sets those three and sends
   nothing to the robot. See [Safety](#help/safety).
9. **Notifications** ([Notifications](#settings/alerts)): an HA notify service or a webhook, then send a test.
