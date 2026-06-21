const actuatorGrid = document.getElementById("actuator-grid");
const actuatorTemplate = document.getElementById("actuator-card-template");
const eventLog = document.getElementById("event-log");
const healthPill = document.getElementById("health-pill");
const wsStatus = document.getElementById("ws-status");
const connectionState = document.getElementById("connection-state");
const playbackState = document.getElementById("playback-state");
const emulationState = document.getElementById("emulation-state");
const updatedAt = document.getElementById("updated-at");
const refreshButton = document.getElementById("refresh-button");
const legSelector = document.getElementById("leg-selector");
const focusedLegTitle = document.getElementById("focused-leg-title");
const previewUpdatedAt = document.getElementById("preview-updated-at");
const previewSyncState = document.getElementById("preview-sync-state");
const previewLegLabel = document.getElementById("preview-leg-label");
const previewJointMapping = document.getElementById("preview-joint-mapping");
const hipPosition = document.getElementById("hip-position");
const hipJointName = document.getElementById("hip-joint-name");
const hipAngle = document.getElementById("hip-angle");
const hipTarget = document.getElementById("hip-target");
const kneePosition = document.getElementById("knee-position");
const kneeJointName = document.getElementById("knee-joint-name");
const kneeAngle = document.getElementById("knee-angle");
const kneeTarget = document.getElementById("knee-target");
const fixedJointName = document.getElementById("fixed-joint-name");
const fixedJointAngle = document.getElementById("fixed-joint-angle");
const currentHipLink = document.getElementById("current-hip-link");
const currentKneeLink = document.getElementById("current-knee-link");
const targetHipLink = document.getElementById("target-hip-link");
const targetKneeLink = document.getElementById("target-knee-link");
const currentKneeJoint = document.getElementById("current-knee-joint");
const currentFootJoint = document.getElementById("current-foot-joint");
const targetKneeJoint = document.getElementById("target-knee-joint");
const targetFootJoint = document.getElementById("target-foot-joint");

const actuatorCards = new Map();
const legPreviews = new Map();
const POSITION_RANGE = { min: 0, max: 4095, fallback: 2048 };
const COMMAND_RANGE = { min: 0, max: 1800, fallback: 900 };
const LEG_LENGTHS = { hip: 84, knee: 90 };
const LEG_ORIGIN = { x: 160, y: 64 };
let selectedLegId = "front_right";

function formatTimestamp(value) {
  if (!value) {
    return "-";
  }

  const date = new Date(value);
  if (Number.isNaN(date.getTime())) {
    return value;
  }

  return date.toLocaleString();
}

function setPillState(element, text, variant) {
  element.textContent = text;
  element.classList.remove("is-ok", "is-pending", "is-error");
  element.classList.add(variant);
}

function formatDegrees(radians) {
  return `${(radians * 180 / Math.PI).toFixed(1)} deg`;
}

function pushEvent(type, message) {
  const item = document.createElement("li");
  item.innerHTML = `<strong>${type}</strong> ${message}`;
  eventLog.prepend(item);
  while (eventLog.children.length > 14) {
    eventLog.removeChild(eventLog.lastChild);
  }
}

function renderSystem(system) {
  connectionState.textContent = system.connection_state;
  playbackState.textContent = system.playback_status;
  emulationState.textContent = system.emulate_devices ? "Emulated" : "Real Serial";
  updatedAt.textContent = formatTimestamp(system.updated_at);

  if (system.connection_state === "connected") {
    setPillState(healthPill, "Hardware connected", "is-ok");
  } else if (system.connection_state === "connecting") {
    setPillState(healthPill, "Connecting to hardware", "is-pending");
  } else {
    setPillState(healthPill, `State: ${system.connection_state}`, "is-error");
  }
}

function ensureCard(actuator) {
  if (actuatorCards.has(actuator.actuator_id)) {
    return actuatorCards.get(actuator.actuator_id);
  }

  const fragment = actuatorTemplate.content.cloneNode(true);
  const card = fragment.querySelector(".actuator-card");
  const title = fragment.querySelector(".actuator-title");
  const label = fragment.querySelector(".actuator-label");
  const port = fragment.querySelector(".actuator-port");
  const feedback = fragment.querySelector(".card-feedback");
  const form = fragment.querySelector(".target-form");
  const modeField = form.elements.namedItem("mode");
  const valueField = form.elements.namedItem("value");
  const rangeHint = fragment.querySelector(".range-hint");

  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    const mode = modeField.value;
    const value = Number(valueField.value);
    feedback.textContent = "Sending target...";

    try {
      const response = await fetch(`/api/actuators/${actuator.actuator_id}/target`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ mode, value }),
      });

      if (!response.ok) {
        throw new Error(`HTTP ${response.status}`);
      }

      feedback.textContent = `Sent ${mode}=${value}`;
      pushEvent("command", `${actuator.label}: ${mode}=${value}`);
    } catch (error) {
      feedback.textContent = `Send failed: ${error.message}`;
    }
  });

  title.textContent = `Actuator ${actuator.actuator_id}`;
  label.textContent = actuator.label;
  port.textContent = `${actuator.port_role} / ${actuator.local_index}`;

  actuatorGrid.appendChild(fragment);

  const createdCard = actuatorGrid.lastElementChild;
  const references = {
    element: createdCard,
    position: createdCard.querySelector(".telemetry-position"),
    voltage: createdCard.querySelector(".telemetry-voltage"),
    command: createdCard.querySelector(".telemetry-command"),
    updated: createdCard.querySelector(".telemetry-updated"),
    form,
    modeField,
    valueField,
    rangeHint,
    feedback,
  };
  actuatorCards.set(actuator.actuator_id, references);
  return references;
}

function renderLegSelector() {
  const existingValue = legSelector.value || selectedLegId;
  legSelector.innerHTML = "";
  for (const preview of legPreviews.values()) {
    const option = document.createElement("option");
    option.value = preview.leg_id;
    option.textContent = preview.label;
    legSelector.appendChild(option);
  }

  if (legPreviews.has(existingValue)) {
    selectedLegId = existingValue;
  } else if (legPreviews.size > 0) {
    selectedLegId = legPreviews.keys().next().value;
  }
  legSelector.value = selectedLegId;
}

function calculateLegPoints(hipAngleRad, kneeAngleRad, mirrorX) {
  const mirror = mirrorX ? -1 : 1;
  const hipAbsolute = Math.PI / 2 + hipAngleRad;
  const kneeAbsolute = hipAbsolute + kneeAngleRad;

  const hipEnd = {
    x: LEG_ORIGIN.x + mirror * LEG_LENGTHS.hip * Math.cos(hipAbsolute),
    y: LEG_ORIGIN.y + LEG_LENGTHS.hip * Math.sin(hipAbsolute),
  };
  const foot = {
    x: hipEnd.x + mirror * LEG_LENGTHS.knee * Math.cos(kneeAbsolute),
    y: hipEnd.y + LEG_LENGTHS.knee * Math.sin(kneeAbsolute),
  };

  return { hipEnd, foot };
}

function setLinePosition(line, start, end) {
  line.setAttribute("x1", start.x.toFixed(1));
  line.setAttribute("y1", start.y.toFixed(1));
  line.setAttribute("x2", end.x.toFixed(1));
  line.setAttribute("y2", end.y.toFixed(1));
}

function setCirclePosition(circle, point) {
  circle.setAttribute("cx", point.x.toFixed(1));
  circle.setAttribute("cy", point.y.toFixed(1));
}

function renderFocusedLeg() {
  const preview = legPreviews.get(selectedLegId);
  if (!preview) {
    previewSyncState.textContent = "No preview data";
    return;
  }

  focusedLegTitle.textContent = preview.label;
  previewLegLabel.textContent = preview.leg_id.replaceAll("_", " ");
  hipPosition.textContent = preview.hip.position;
  hipJointName.textContent = preview.hip.joint_name;
  hipAngle.textContent = formatDegrees(preview.hip.angle_rad);
  hipTarget.textContent = `Target ${preview.hip.target_position} / ${formatDegrees(preview.hip.target_angle_rad)}`;
  kneePosition.textContent = preview.knee.position;
  kneeJointName.textContent = preview.knee.joint_name;
  kneeAngle.textContent = formatDegrees(preview.knee.angle_rad);
  kneeTarget.textContent = `Target ${preview.knee.target_position} / ${formatDegrees(preview.knee.target_angle_rad)}`;
  fixedJointName.textContent = preview.fixed_joint_name;
  fixedJointAngle.textContent = formatDegrees(preview.fixed_joint_angle_rad);
  previewSyncState.textContent = "Live telemetry";
  previewJointMapping.textContent = `${preview.fixed_joint_name} is fixed. ${preview.hip.joint_name} follows hip and ${preview.knee.joint_name} follows knee.`;
  setPillState(previewUpdatedAt, `Updated ${formatTimestamp(preview.updated_at)}`, "is-ok");

  const currentPoints = calculateLegPoints(preview.hip.angle_rad, preview.knee.angle_rad, preview.mirror_x);
  const targetPoints = calculateLegPoints(
    preview.hip.target_angle_rad,
    preview.knee.target_angle_rad,
    preview.mirror_x,
  );

  setLinePosition(currentHipLink, LEG_ORIGIN, currentPoints.hipEnd);
  setLinePosition(currentKneeLink, currentPoints.hipEnd, currentPoints.foot);
  setLinePosition(targetHipLink, LEG_ORIGIN, targetPoints.hipEnd);
  setLinePosition(targetKneeLink, targetPoints.hipEnd, targetPoints.foot);

  setCirclePosition(currentKneeJoint, currentPoints.hipEnd);
  setCirclePosition(currentFootJoint, currentPoints.foot);
  setCirclePosition(targetKneeJoint, targetPoints.hipEnd);
  setCirclePosition(targetFootJoint, targetPoints.foot);
}

function renderLegPreview(preview) {
  legPreviews.set(preview.leg_id, preview);
  renderLegSelector();
  renderFocusedLeg();
}

function applyModeConstraints(card, actuator) {
  const mode = card.modeField.value;
  const range = mode === "command" ? COMMAND_RANGE : POSITION_RANGE;
  card.valueField.min = String(range.min);
  card.valueField.max = String(range.max);
  card.valueField.value = String(mode === "command" ? actuator.target_command : actuator.target_position);
  card.rangeHint.textContent = mode === "command"
    ? "Command range: 0-1800 (900 is neutral)"
    : "Position range: 0-4095";
}

function renderActuator(actuator) {
  const card = ensureCard(actuator);
  card.position.textContent = actuator.telemetry.position;
  card.voltage.textContent = actuator.telemetry.voltage;
  card.command.textContent = actuator.telemetry.command;
  card.updated.textContent = formatTimestamp(actuator.updated_at);

  applyModeConstraints(card, actuator);
  card.modeField.onchange = () => {
    applyModeConstraints(card, actuator);
  };
}

async function loadSnapshot() {
  const [healthResponse, actuatorsResponse, previewsResponse] = await Promise.all([
    fetch("/api/health"),
    fetch("/api/actuators"),
    fetch("/api/preview/legs"),
  ]);

  if (!healthResponse.ok || !actuatorsResponse.ok || !previewsResponse.ok) {
    throw new Error("Failed to load snapshot");
  }

  const health = await healthResponse.json();
  const actuators = await actuatorsResponse.json();
  const previews = await previewsResponse.json();
  renderSystem(health.system);
  actuators.items.forEach(renderActuator);
  previews.items.forEach(renderLegPreview);
  pushEvent("snapshot", `Loaded ${actuators.items.length} actuators`);
}

function handleEvent(event) {
  const data = JSON.parse(event.data);
  if (data.type === "snapshot") {
    renderSystem(data.payload.system);
    data.payload.actuators.forEach(renderActuator);
    data.payload.legs.forEach(renderLegPreview);
    pushEvent("ws", "Received initial snapshot");
    return;
  }

  if (data.type === "server_status") {
    renderSystem(data.payload);
    pushEvent("status", `Connection ${data.payload.connection_state}`);
    return;
  }

  if (data.type === "csv_playback_status") {
    playbackState.textContent = data.payload.status;
    pushEvent("csv", `Playback ${data.payload.status}`);
    return;
  }

  if (data.type === "leg_preview") {
    renderLegPreview(data.payload.leg);
    pushEvent("preview", `${data.payload.leg.label} preview updated`);
    return;
  }

  if (data.payload && data.payload.actuator) {
    renderActuator(data.payload.actuator);
    pushEvent(data.type, `${data.payload.actuator.label} updated`);
    return;
  }

  pushEvent(data.type, "Received event");
}

function connectWebSocket() {
  const protocol = window.location.protocol === "https:" ? "wss" : "ws";
  const socket = new WebSocket(`${protocol}://${window.location.host}/api/ws`);

  setPillState(wsStatus, "WebSocket connecting", "is-pending");

  socket.addEventListener("open", () => {
    setPillState(wsStatus, "WebSocket live", "is-ok");
    pushEvent("ws", "Connected");
  });

  socket.addEventListener("message", handleEvent);

  socket.addEventListener("close", () => {
    setPillState(wsStatus, "WebSocket disconnected", "is-error");
    pushEvent("ws", "Disconnected, retrying soon");
    window.setTimeout(connectWebSocket, 2000);
  });

  socket.addEventListener("error", () => {
    setPillState(wsStatus, "WebSocket error", "is-error");
  });
}

refreshButton.addEventListener("click", async () => {
  try {
    await loadSnapshot();
  } catch (error) {
    pushEvent("snapshot", `Refresh failed: ${error.message}`);
  }
});

legSelector.addEventListener("change", () => {
  selectedLegId = legSelector.value;
  renderFocusedLeg();
});

window.addEventListener("DOMContentLoaded", async () => {
  try {
    await loadSnapshot();
  } catch (error) {
    setPillState(healthPill, "Initial load failed", "is-error");
    pushEvent("snapshot", `Initial load failed: ${error.message}`);
  }
  connectWebSocket();
});
