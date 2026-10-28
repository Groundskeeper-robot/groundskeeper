# Schedule
<!-- order: 40 -->

The panel does the scheduling. The robot keeps schedules of its own only if you save one in the Yarbo app, and
those start mows **without** any of the run rules (dry lawn, yard clear, someone home, daylight). The panel never
makes them; when the robot has one, this page shows it under **On the robot itself** and Events notes it. Remove it
in the app to let the panel decide.

- A **window** is days, a start and end time, a plan, and a head (mower, snow, or any).
- Inside a window the checks run every minute. When they all pass the panel raises **one** confirmation (valid
  30 minutes) and notifies you with a link and a camera snapshot. Confirm or dismiss on Live or here.
- **Auto-start** skips your confirmation but not the checks.
- Each window's outcome for the day is kept: pending, started, dismissed, expired, skipped, failed. A skipped window
  says why and what would fix it, and sends a notification.
- The "Ready to start" check is unfolded here so you can see every gate.

Starting needs control switched on ([Safety](#help/safety)).
