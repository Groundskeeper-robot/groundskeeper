# Schedule
<!-- order: 40 -->

The robot keeps no schedules of its own; the panel does.

- A **window** is days, a start and end time, a plan, and a head (mower, snow, or any).
- Inside a window the checks run every minute. When they all pass the panel raises **one** confirmation (valid
  30 minutes) and notifies you with a link and a camera snapshot. Confirm or dismiss on Live or here.
- **Auto-start** skips your confirmation but not the checks.
- Each window's outcome for the day is kept: pending, started, dismissed, expired, skipped, failed. A skipped window
  says why and what would fix it, and sends a notification.
- The "Ready to start" check is unfolded here so you can see every gate.

Starting needs control switched on ([Safety](#help/safety)).
