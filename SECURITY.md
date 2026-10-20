# Security

Groundskeeper can start a robot with spinning blades and stores the location of a home. Please report security
problems privately, not in a public issue.

**How:** use [Report a vulnerability](https://github.com/jpittelkow/groundskeeper/security/advisories/new) on the
repository's Security tab. You'll get an answer within a week. Please give us a reasonable time to ship a fix before
you publish details.

**In scope:**
- Getting past sign-in or reading data without it.
- Making the robot move, or changing the map or settings, without the panel's own checks.
- Leaking the yard's location, the robot's serial, or the Yarbo cloud session.
- Server-side request forgery through imagery discovery or integrations.

**Not in scope:** running with `PANEL_AUTH=off` on a network others can reach. That turns sign-in off on purpose.
Problems in Yarbo's own robot, app or cloud should go to Yarbo.

Supported versions: the latest release. Fixes are not backported.
