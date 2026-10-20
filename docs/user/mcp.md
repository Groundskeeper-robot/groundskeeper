# AI assistants (MCP)
<!-- order: 90 -->

An AI assistant that speaks MCP (Claude Desktop, Claude Code, others) can read the panel and help set it up: find
the most detailed imagery for your yard, set up cameras, weather and Home Assistant, run rules, the schedule and
maintenance, by conversation.

**Optional, but the recommended second step** after the robot is connected: everything else in Settings can then be
done by asking. Nothing depends on it, and you can set every item by hand instead.

## Connect

**1. Create a token.** [Settings → AI assistant](#settings/assistant) → **Create a token**. Copy it: it is shown
once; only a fingerprint is kept. That section also shows the snippets below with your token already filled in.

**2. Add the panel to your assistant.** The panel must be reachable from where the assistant runs (same network or
a VPN).

Claude Code (terminal), one command:

```
claude mcp add --transport http groundskeeper http://<panel address>/mcp --header "Authorization: Bearer <token>"
```

Claude Desktop connects through `mcp-remote`, a small bridge that runs on your computer; it needs
[Node.js](https://nodejs.org/). Settings → Developer → Edit config, add the `groundskeeper` entry inside
`mcpServers` (keep any servers already there), save and restart Claude:

```
{ "mcpServers": { "groundskeeper": {
    "command": "npx",
    "args": ["-y", "mcp-remote", "http://<panel address>/mcp", "--allow-http", "--header", "Authorization:${AUTH_HEADER}"],
    "env": { "AUTH_HEADER": "Bearer <token>" } } } }
```

`--allow-http` lets the bridge use a plain-http address on your network. The token goes in through `AUTH_HEADER`
because Windows mangles a header with a space in it.

**Not Settings → Connectors → Add custom connector.** It asks for an https address because Anthropic's cloud makes
the connection, and the cloud cannot reach your home network or send the panel's token. Putting a `"type": "http"`
entry straight into Claude Desktop's config file does not work either: that file only starts local programs.

Other clients: streamable HTTP at `http://<panel address>/mcp` with the header `Authorization: Bearer <token>`. A
client that only takes https addresses can use `mcp-remote` the same way.

**3. Ask it something.** Good first questions:

- *"Find the most detailed aerial map of my yard: check every source that covers it, look at the samples, and if
  a county or state service would be sharper, find it for me."*
- *"Walk me through setting up this panel: cameras, run rules, lawn conditions, schedule and alerts."*
- *"What's the robot doing, and would it start right now?"*

**Revoke** in the same place turns MCP off and switches assistant commands off.

## Finding the most detailed map with it

This is what an assistant is best at, because it can read numbers, look at pictures and search the web in one go:

- It lists every source that covers your yard with its sharpness in **cm per pixel** (smaller is sharper: 7–10 cm
  shows pavers and sprinkler heads, 15–20 cm clear beds and edges, 30 cm soft edges, 60 cm a blur; see
  [Map imagery](#help/imagery) for the full scale) and whether the sample found real imagery.
- It looks at the samples of the sharpest few, since a clear 15 cm photo beats a cloudy 7 cm one, and a spring
  (leaf-off) flight shows the ground under trees.
- If the best free one is coarse (30 cm or more), it searches your **region** (your state or country, never your
  address) for a county or state orthoimagery service, which is often at 7–15 cm, checks what the address serves
  and gives it to you to paste under Settings → Map imagery → Add a source. Trying it at your yard is your click.
- It can then switch the panel to the best source and help line the photo up with the robot's map.

## What it can do

- **Read** everything you can see: status, runs, lawn conditions, schedule, maintenance, events, settings, imagery,
  and these help pages.
- **Set up** the panel: choose imagery among the catalog's sources, store keys, align; find new imagery sources
  (it checks an address, then gives it to you to add in Settings); polling; the notify service; schedule windows;
  maintenance; yard cameras and run rules, but only to make them **stricter**. It is told to say what it will
  change and wait for your yes. The panel cannot check that you said yes, so nothing that would make the robot less
  safe, blind a check or send your yard somewhere new is offered to it at all.
- **Control** the robot (start a plan, pause, resume, stop, dock) only when you switch on **Assistant commands** in
  [Settings → Safety](#settings/safety), control is on, and every check passes. It can never override a check, and
  for its start or resume a check that cannot tell (camera, weather or Home Assistant down) counts as failed.

## What it cannot do

Switch control, map writing or assistant commands on; turn on guard auto-resume; loosen a run rule or remove a
yard camera; change the lawn rules, camera coverage, presence entity, or the robot, Frigate, Home Assistant or
weather station connections; set a webhook or any Home Assistant service other than a notify one; try or save a new
imagery source; make or change auto-start schedule windows; write the robot's map; touch robot backups; sign in to
the Yarbo cloud; drive the robot by hand.

## Privacy

What the assistant reads has coordinates, the robot's serial and your Yarbo account removed, so it cannot pass your
home location on to a web search. It finds imagery by region (your state or country), and checking an address
sends no location. Trying a new source at your yard is your step, in Settings. Imagery samples of the yard, if you
ask it to look at them, go to the assistant.
