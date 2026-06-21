# Web UI

This folder now contains a zero-build browser dashboard:

- `index.html` for the dashboard shell
- `styles.css` for layout and visual styling
- `app.js` for REST and WebSocket integration

Current capabilities:

- Reads `GET /api/health`
- Reads `GET /api/actuators`
- Reads `GET /api/preview/legs`
- Subscribes to `WS /api/ws`
- Shows live actuator telemetry
- Shows a focused single-leg articulated preview driven by telemetry and target position
- Sends simple target commands with `POST /api/actuators/{id}/target`

This is intentionally the lightest possible front end so the monitoring flow
can be validated before introducing React, Vue, or a build pipeline.
