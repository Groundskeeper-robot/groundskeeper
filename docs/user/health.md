# Health
<!-- order: 60 -->

Under **Robot → Health**: the robot now and over the last 24 hours, 7 days or 30 days. Hover (or touch) a chart
to read the value at that time.

- **Positioning:** fix now, share of time fixed, satellites, DOP, correction age.
- **Link:** the local connection and HaLow over time.
- **Network:** each link's strength now, and the robot's Wi-Fi.
  - **HaLow** in dBm: amber below −77, red at −82 (where the robot switches to 4G).
  - **4G:** the values the robot reports, as it reports them; "not reported" when they are all zero.
  - **Wi-Fi:** the network it is on and its signal (0–100 %), from the latest scan by the panel or the phone app.
  - **Scan** asks the robot for nearby networks (a few seconds; up to a minute on a weak link). Not during a plan.
  - **Join** a network from the list with its password, or **Forget** a saved one. Open networks can't be joined
    from here yet. Both need
    map writing on (Settings → Safety) and the robot on its charger with no plan. The panel then asks the robot
    which network it is on, or scans again, and says whether it worked. A forgotten network needs its password to
    join again. Changing Wi-Fi can drop the panel's local connection for a moment; the cloud carries on.

The top bar's connections pill shows the same as **signal bars**: HaLow (4 bars from −60 dBm, none at the 4G
level), Wi-Fi when the robot is on it (from the latest scan), and GPS (RTK fixed 4, or 3 with a high heading DOP;
float 2; single 1; no fix 0).
- **Power:** charge, voltage, cell temperatures.
- **Deck and CPU.**
- **Firmware history** and **error codes**, with a dictionary of the robot's status codes.
