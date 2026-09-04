"use strict";

const setupDiagramLabels = ["Sidebar", "Conversation", "Composer"];
const regionNames = ["sidebar", "conversation", "composer"];
const minimumRegionSize = 0.01;

function clamp(value, minimum, maximum) {
  return Math.min(maximum, Math.max(minimum, value));
}

function normalizeRectangle(start, end, width, height) {
  const startX = clamp(start.x / width, 0, 1);
  const startY = clamp(start.y / height, 0, 1);
  const endX = clamp(end.x / width, 0, 1);
  const endY = clamp(end.y / height, 0, 1);
  return {
    x: Math.min(startX, endX),
    y: Math.min(startY, endY),
    width: Math.abs(endX - startX),
    height: Math.abs(endY - startY),
  };
}

function surfaceObject([x, y, width, height]) {
  return { x, y, width, height };
}

function surfacesFromApi(surfaces) {
  return Object.fromEntries(
    regionNames.map((name) => [name, surfaceObject(surfaces[name])]),
  );
}

function surfacesForApi(surfaces) {
  return Object.fromEntries(
    regionNames.map((name) => {
      const { x, y, width, height } = surfaces[name];
      return [name, [x, y, width, height]];
    }),
  );
}

function jsonRequest(method, value) {
  return {
    method,
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(value),
  };
}

function calibrationRequest(surfaces) {
  return jsonRequest("PUT", { surfaces: surfacesForApi(surfaces) });
}

function phoneUrlForStatus({ bind_ip, port }) {
  const host = bind_ip.includes(":") && !bind_ip.startsWith("[")
    ? `[${bind_ip}]`
    : bind_ip;
  return `http://${host}:${port}/`;
}

function createSetupModel(surfaces, status) {
  const model = {
    phoneUrl: phoneUrlForStatus(status),
    surfaces: surfacesFromApi(surfaces),
    replace(name, rectangle) {
      model.surfaces[name] = { ...rectangle };
    },
  };
  return model;
}

if (typeof module !== "undefined" && module.exports) {
  module.exports = {
    calibrationRequest,
    createSetupModel,
    normalizeRectangle,
    phoneUrlForStatus,
    setupDiagramLabels,
  };
}

if (typeof document !== "undefined") {
  document.addEventListener("DOMContentLoaded", () => {
    const elements = {
      complete: document.querySelector("#setup-complete"),
      dragHelp: document.querySelector("#drag-help"),
      loadWindow: document.querySelector("#load-window"),
      message: document.querySelector("#setup-message"),
      overlays: document.querySelector("#region-overlays"),
      regionChoice: document.querySelector("#region-choice"),
      saveCalibration: document.querySelector("#save-calibration"),
      screenshot: document.querySelector("#window-screenshot"),
      stage: document.querySelector("#screenshot-stage"),
      windowSelect: document.querySelector("#window-select"),
    };
    const state = {
      drag: null,
      phoneUrl: null,
      selectedWindow: null,
      status: null,
      surfaces: null,
      windows: [],
    };

    function showMessage(message, error = false) {
      elements.message.textContent = message;
      elements.message.classList.toggle("error", error);
    }

    function selectedRegion() {
      return document.querySelector('input[name="region"]:checked').value;
    }

    function pointFor(event) {
      const box = elements.screenshot.getBoundingClientRect();
      return { x: event.clientX - box.left, y: event.clientY - box.top };
    }

    function currentRectangle() {
      if (!state.drag) return null;
      const box = elements.screenshot.getBoundingClientRect();
      return normalizeRectangle(state.drag.start, state.drag.end, box.width, box.height);
    }

    function renderRegions() {
      if (!state.surfaces) return;
      elements.overlays.replaceChildren();
      const active = selectedRegion();
      const drawing = currentRectangle();
      for (const name of regionNames) {
        const surface = name === active && drawing ? drawing : state.surfaces[name];
        if (!surface) continue;
        const overlay = document.createElement("div");
        overlay.className = `region-overlay ${name}${name === active ? " selected" : ""}`;
        overlay.style.left = `${surface.x * 100}%`;
        overlay.style.top = `${surface.y * 100}%`;
        overlay.style.width = `${surface.width * 100}%`;
        overlay.style.height = `${surface.height * 100}%`;
        const label = document.createElement("span");
        label.textContent = setupDiagramLabels[regionNames.indexOf(name)];
        overlay.append(label);
        elements.overlays.append(overlay);
      }
    }

    async function request(path, options = {}) {
      const response = await fetch(path, options);
      if (!response.ok) {
        let message = `Request failed (${response.status}).`;
        try { message = (await response.json()).error.message; } catch (_) { /* use status */ }
        throw new Error(message);
      }
      return response.headers.get("content-type")?.includes("application/json")
        ? response.json()
        : response;
    }

    async function loadScreenshot(revision) {
      elements.stage.hidden = false;
      elements.dragHelp.hidden = false;
      elements.screenshot.src = `/api/screenshot/window?revision=${encodeURIComponent(revision)}`;
      await elements.screenshot.decode();
      renderRegions();
    }

    async function selectWindow() {
      const windowId = elements.windowSelect.value;
      const selected = state.windows.find((candidate) => candidate.id === windowId);
      if (!selected) return;
      elements.loadWindow.disabled = true;
      elements.saveCalibration.disabled = true;
      showMessage("Loading the selected window…");
      try {
        const result = await request("/api/select", jsonRequest("POST", { window_id: windowId }));
        state.selectedWindow = selected;
        state.surfaces = createSetupModel(selected.surfaces, state.status).surfaces;
        await loadScreenshot(result.revision);
        elements.regionChoice.disabled = false;
        elements.saveCalibration.disabled = false;
        showMessage("Drag a rectangle for each area, then save all three together.");
      } catch (error) {
        showMessage(error.message, true);
      } finally {
        elements.loadWindow.disabled = false;
      }
    }

    async function loadWindows() {
      try {
        const result = await request("/api/windows");
        state.windows = result.windows;
        elements.windowSelect.replaceChildren();
        if (!state.windows.length) {
          showMessage("No compatible agent windows are open. Open one, then reload this page.", true);
          return;
        }
        for (const candidate of state.windows) {
          const option = document.createElement("option");
          option.value = candidate.id;
          const pieces = candidate.process_path.split(/[\\\\/]/);
          option.textContent = `${candidate.agent_id} — ${candidate.title} (${pieces.at(-1)})`;
          elements.windowSelect.append(option);
        }
        elements.windowSelect.disabled = false;
        elements.loadWindow.disabled = false;
        await selectWindow();
      } catch (error) {
        showMessage(error.message, true);
      }
    }

    async function initialize() {
      try {
        state.status = await request("/api/status");
        state.phoneUrl = phoneUrlForStatus(state.status);
        await loadWindows();
      } catch (error) {
        showMessage(error.message, true);
      }
    }

    elements.loadWindow.addEventListener("click", selectWindow);
    elements.regionChoice.addEventListener("change", renderRegions);
    elements.screenshot.addEventListener("load", renderRegions);
    elements.stage.addEventListener("pointerdown", (event) => {
      if (!state.surfaces || !elements.screenshot.complete) return;
      event.preventDefault();
      const point = pointFor(event);
      state.drag = { start: point, end: point };
      elements.stage.setPointerCapture(event.pointerId);
      renderRegions();
    });
    elements.stage.addEventListener("pointermove", (event) => {
      if (!state.drag) return;
      state.drag.end = pointFor(event);
      renderRegions();
    });
    elements.stage.addEventListener("pointerup", (event) => {
      if (!state.drag) return;
      state.drag.end = pointFor(event);
      const rectangle = currentRectangle();
      state.drag = null;
      if (rectangle.width < minimumRegionSize || rectangle.height < minimumRegionSize) {
        showMessage("That area is too small. Drag an area at least 1% of the screenshot wide and tall.", true);
      } else {
        state.surfaces[selectedRegion()] = rectangle;
        showMessage("Area updated. Save when all three rectangles look right.");
      }
      renderRegions();
    });
    elements.stage.addEventListener("pointercancel", () => {
      state.drag = null;
      renderRegions();
    });
    elements.saveCalibration.addEventListener("click", async () => {
      elements.saveCalibration.disabled = true;
      showMessage("Saving calibration…");
      try {
        await request("/api/calibration", calibrationRequest(state.surfaces));
        const phoneUrl = state.phoneUrl;
        elements.complete.replaceChildren(
          "Saved. On your phone, open ",
          phoneUrl,
          ". Then use the ",
        );
        const link = document.createElement("a");
        link.href = "/";
        link.textContent = "CodeAway controls";
        elements.complete.append(link, ".");
        elements.complete.hidden = false;
        showMessage("Calibration saved.");
      } catch (error) {
        showMessage(error.message, true);
      } finally {
        elements.saveCalibration.disabled = false;
      }
    });

    initialize();
  });
}
