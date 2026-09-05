"use strict";

const setupDiagramLabels = ["Sidebar", "Conversation", "Composer"];
const regionNames = ["sidebar", "conversation", "composer"];
const minimumRegionSize = 0.01;

function clamp(value, minimum, maximum) {
  return Math.min(maximum, Math.max(minimum, value));
}

function pointToFraction(clientX, clientY, box) {
  return {
    x: clamp((clientX - box.left) / box.width, 0, 1),
    y: clamp((clientY - box.top) / box.height, 0, 1),
  };
}

function swipeToSteps(deltaY) {
  if (Math.abs(deltaY) <= 8) return 0;
  const steps = clamp(Math.round(deltaY / 24), -12, 12);
  return steps || Math.sign(deltaY);
}

function toggleProject(expanded, project) {
  return { ...expanded, [project]: !expanded[project] };
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

function createPhoneController({ postAction, requestImage, onComposerClear = () => {} }) {
  const state = {
    composerText: "",
    conversationError: "",
    imageRevision: null,
    imageRequestToken: 0,
    requestedImageRevision: null,
  };
  const controller = {
    get composerText() {
      return state.composerText;
    },
    get conversationError() {
      return state.conversationError;
    },
    get imageRevision() {
      return state.imageRevision;
    },
    setComposerText(text) {
      state.composerText = text;
    },
    canHandleGesture(image) {
      return state.imageRevision !== null && image.naturalWidth > 0;
    },
    async refreshConversation(revision, successfulAction = false) {
      if (
        state.requestedImageRevision !== null
        && (
          revision < state.requestedImageRevision
          || (!successfulAction && revision === state.requestedImageRevision)
        )
      ) return false;
      state.requestedImageRevision = revision;
      state.imageRevision = null;
      const requestToken = ++state.imageRequestToken;
      try {
        await requestImage(revision);
        if (requestToken !== state.imageRequestToken) return false;
        state.imageRevision = revision;
        state.conversationError = "";
        return true;
      } catch (_) {
        if (requestToken !== state.imageRequestToken) return false;
        state.conversationError = "The conversation image could not be refreshed.";
        return false;
      }
    },
    async performAction(value) {
      const result = await postAction(value);
      await controller.refreshConversation(result.revision, true);
      return result;
    },
    async send() {
      const result = await postAction({ kind: "send", text: state.composerText });
      state.composerText = "";
      onComposerClear();
      await controller.refreshConversation(result.revision, true);
      return result;
    },
  };
  return controller;
}

function initializePhoneWorkspace({
  documentRef = document,
  windowRef = window,
  fetchFn = fetch,
} = {}) {
  const elements = {
    composer: documentRef.querySelector("#composer"),
    composerInput: documentRef.querySelector("#composer-input"),
    composerMessage: documentRef.querySelector("#composer-message"),
    composerSend: documentRef.querySelector("#composer-send"),
    conversationImage: documentRef.querySelector("#conversation-image"),
    conversationMessage: documentRef.querySelector("#conversation-message"),
    navigatorProjects: documentRef.querySelector("#navigator-projects"),
    statusMessage: documentRef.querySelector("#status-message"),
  };
  const state = {
    actionBusy: false,
    expanded: {},
    gesture: null,
    navigator: null,
    pollTimer: null,
    refreshing: null,
    revision: null,
  };

  function showMessage(element, message, error = false) {
    element.textContent = message;
    element.classList.toggle("error", error);
  }

  async function request(path, options = {}) {
    const response = await fetchFn(path, options);
    if (!response.ok) {
      let message = `Request failed (${response.status}).`;
      try { message = (await response.json()).error.message; } catch (_) { /* use status */ }
      throw new Error(message);
    }
    return response.json();
  }

  function actionRequest(value) {
    return {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(value),
    };
  }

  function svgIcon(className, path) {
    const svg = documentRef.createElementNS("http://www.w3.org/2000/svg", "svg");
    svg.setAttribute("class", className);
    svg.setAttribute("viewBox", "0 0 24 24");
    svg.setAttribute("width", "16");
    svg.setAttribute("height", "16");
    svg.setAttribute("aria-hidden", "true");
    const shape = documentRef.createElementNS("http://www.w3.org/2000/svg", "path");
    shape.setAttribute("d", path);
    shape.setAttribute("fill", "none");
    shape.setAttribute("stroke", "currentColor");
    shape.setAttribute("stroke-width", "2");
    shape.setAttribute("stroke-linecap", "round");
    shape.setAttribute("stroke-linejoin", "round");
    svg.append(shape);
    return svg;
  }

  function renderStatus(status) {
    if (status.ready) {
      const target = status.target?.title || "agent";
      const agent = status.target?.agent_id || "agent";
      showMessage(elements.statusMessage, `Connected: ${agent} — ${target} (ready).`);
      return;
    }
    showMessage(elements.statusMessage, "Setup is required before controls are available.", true);
  }

  function renderNavigator(snapshot) {
    elements.navigatorProjects.replaceChildren();
    if (!snapshot.available) {
      elements.navigatorProjects.textContent = snapshot.error || "The navigator is unavailable.";
      return;
    }
    for (const project of snapshot.projects) {
      if (!(project.name in state.expanded)) state.expanded[project.name] = project.expanded;
      const expanded = state.expanded[project.name];
      const projectItem = documentRef.createElement("section");
      projectItem.className = "project";

      const toggle = documentRef.createElement("button");
      toggle.className = "project-toggle";
      toggle.type = "button";
      toggle.setAttribute("aria-expanded", String(expanded));
      const chevron = svgIcon("project-chevron", "m8 9 4 4 4-4");
      chevron.classList.toggle("expanded", expanded);
      const name = documentRef.createElement("span");
      name.className = "project-name";
      name.textContent = project.name;
      const projectState = documentRef.createElement("span");
      projectState.className = "project-state";
      projectState.textContent = project.state;
      toggle.append(chevron, name, projectState);
      toggle.addEventListener("click", () => {
        state.expanded = toggleProject(state.expanded, project.name);
        renderNavigator(state.navigator);
      });
      projectItem.append(toggle);

      const tasks = documentRef.createElement("div");
      tasks.className = "task-list";
      tasks.hidden = !expanded;
      for (const task of project.tasks) {
        const taskButton = documentRef.createElement("button");
        taskButton.className = "task";
        taskButton.type = "button";
        taskButton.classList.toggle("selected", task.selected);
        if (task.worktree) {
          taskButton.append(svgIcon("worktree-marker", "M7 4v9a3 3 0 0 0 3 3h1m-1-12 4 4m-4-4-4 4m8 8 4 4m-4-4-4 4"));
        }
        const title = documentRef.createElement("span");
        title.textContent = task.title;
        const taskState = documentRef.createElement("span");
        taskState.className = "task-state";
        taskState.textContent = task.state;
        taskButton.append(title, taskState);
        taskButton.addEventListener("click", async () => {
          if (state.actionBusy) return;
          try {
            await performAction({ kind: "navigate", target: "task", project: project.name, title: task.title });
            showMessage(elements.conversationMessage, "");
          } catch (error) {
            showMessage(elements.conversationMessage, error.message, true);
          }
        });
        tasks.append(taskButton);
      }
      projectItem.append(tasks);
      elements.navigatorProjects.append(projectItem);
    }
  }

  function requestConversationImage(revision) {
    return new Promise((resolve, reject) => {
      const image = elements.conversationImage;
      const cleanup = () => {
        image.removeEventListener("load", loaded);
        image.removeEventListener("error", failed);
      };
      const loaded = () => {
        cleanup();
        resolve();
      };
      const failed = () => {
        cleanup();
        reject(new Error("The conversation image could not be refreshed."));
      };
      image.addEventListener("load", loaded, { once: true });
      image.addEventListener("error", failed, { once: true });
      image.src = `/api/screenshot/conversation?revision=${encodeURIComponent(revision)}`;
    });
  }

  const phone = createPhoneController({
    postAction: (value) => request("/api/action", actionRequest(value)),
    requestImage: requestConversationImage,
    onComposerClear: () => { elements.composerInput.value = ""; },
  });

  function showConversationRefreshStatus() {
    showMessage(
      elements.conversationMessage,
      phone.conversationError,
      Boolean(phone.conversationError),
    );
  }

  async function performAction(value) {
    if (state.actionBusy) return;
    state.actionBusy = true;
    try {
      const result = await phone.performAction(value);
      state.revision = result.revision;
      showConversationRefreshStatus();
      return result;
    } finally {
      state.actionBusy = false;
    }
  }

  async function refreshWorkspace() {
    if (state.refreshing) return state.refreshing;
    state.refreshing = (async () => {
      const [statusResult, navigatorResult] = await Promise.allSettled([
        request("/api/status"),
        request("/api/navigator"),
      ]);
      if (statusResult.status === "fulfilled") {
        const status = statusResult.value;
        if (state.revision === null || status.revision >= state.revision) {
          state.revision = status.revision;
          renderStatus(status);
          await phone.refreshConversation(status.revision);
          showConversationRefreshStatus();
        }
      } else {
        showMessage(elements.statusMessage, statusResult.reason.message, true);
      }
      if (navigatorResult.status === "fulfilled") {
        state.navigator = navigatorResult.value;
        renderNavigator(state.navigator);
      } else {
        elements.navigatorProjects.textContent = navigatorResult.reason.message;
      }
    })();
    try {
      await state.refreshing;
    } finally {
      state.refreshing = null;
    }
  }

  function startPolling() {
    if (state.pollTimer !== null) return state.refreshing || Promise.resolve();
    const refresh = refreshWorkspace();
    state.pollTimer = windowRef.setInterval(refreshWorkspace, 2000);
    return refresh;
  }

  function stopPolling() {
    if (state.pollTimer === null) return;
    windowRef.clearInterval(state.pollTimer);
    state.pollTimer = null;
  }

  elements.conversationImage.addEventListener("pointerdown", (event) => {
    if (state.actionBusy || !phone.canHandleGesture(elements.conversationImage)) return;
    event.preventDefault();
    state.gesture = { id: event.pointerId, x: event.clientX, y: event.clientY };
    elements.conversationImage.setPointerCapture(event.pointerId);
  });
  elements.conversationImage.addEventListener("pointerup", async (event) => {
    const gesture = state.gesture;
    if (!gesture || gesture.id !== event.pointerId || state.actionBusy) return;
    state.gesture = null;
    const deltaY = event.clientY - gesture.y;
    const distance = Math.hypot(event.clientX - gesture.x, deltaY);
    let action = null;
    if (distance < 8) {
      const point = pointToFraction(event.clientX, event.clientY, elements.conversationImage.getBoundingClientRect());
      action = { kind: "click", surface: "conversation", ...point };
    } else if (Math.abs(deltaY) >= 8) {
      const amount = swipeToSteps(deltaY);
      if (amount) action = { kind: "scroll", amount };
    }
    if (!action) return;
    try {
      await performAction(action);
      showConversationRefreshStatus();
    } catch (error) {
      showMessage(elements.conversationMessage, error.message, true);
    }
  });
  elements.conversationImage.addEventListener("pointercancel", () => {
    state.gesture = null;
  });
  elements.composer.addEventListener("submit", async (event) => {
    event.preventDefault();
    const text = elements.composerInput.value;
    if (!text.trim() || state.actionBusy) return;
    elements.composerSend.disabled = true;
    elements.composerInput.disabled = true;
    phone.setComposerText(text);
    state.actionBusy = true;
    try {
      const result = await phone.send();
      state.revision = result.revision;
      showMessage(elements.composerMessage, "");
      showConversationRefreshStatus();
    } catch (error) {
      showMessage(elements.composerMessage, error.message, true);
    } finally {
      state.actionBusy = false;
      elements.composerSend.disabled = false;
      elements.composerInput.disabled = false;
    }
  });
  documentRef.addEventListener("visibilitychange", () => {
    if (documentRef.visibilityState === "visible") return startPolling();
    stopPolling();
    return undefined;
  });

  const ready = documentRef.visibilityState === "visible"
    ? startPolling()
    : Promise.resolve();
  return { ready, refreshWorkspace, startPolling, stopPolling };
}

function initializeSetup({ documentRef = document, fetchFn = fetch } = {}) {
  const elements = {
    complete: documentRef.querySelector("#setup-complete"),
    dragHelp: documentRef.querySelector("#drag-help"),
    loadWindow: documentRef.querySelector("#load-window"),
    message: documentRef.querySelector("#setup-message"),
    overlays: documentRef.querySelector("#region-overlays"),
    refreshWindows: documentRef.querySelector("#refresh-windows"),
    regionChoice: documentRef.querySelector("#region-choice"),
    saveCalibration: documentRef.querySelector("#save-calibration"),
    screenshot: documentRef.querySelector("#window-screenshot"),
    stage: documentRef.querySelector("#screenshot-stage"),
    windowSelect: documentRef.querySelector("#window-select"),
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
    return documentRef.querySelector('input[name="region"]:checked').value;
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
      const overlay = documentRef.createElement("div");
      overlay.className = `region-overlay ${name}${name === active ? " selected" : ""}`;
      overlay.style.left = `${surface.x * 100}%`;
      overlay.style.top = `${surface.y * 100}%`;
      overlay.style.width = `${surface.width * 100}%`;
      overlay.style.height = `${surface.height * 100}%`;
      const label = documentRef.createElement("span");
      label.textContent = setupDiagramLabels[regionNames.indexOf(name)];
      overlay.append(label);
      elements.overlays.append(overlay);
    }
  }

  async function request(path, options = {}) {
    const response = await fetchFn(path, options);
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
      elements.loadWindow.disabled = !elements.windowSelect.value;
    }
  }

  async function loadWindows() {
    elements.refreshWindows.disabled = true;
    elements.loadWindow.disabled = true;
    try {
      const result = await request("/api/windows");
      state.windows = result.windows;
      elements.windowSelect.replaceChildren();
      if (!state.windows.length) {
        state.selectedWindow = null;
        state.surfaces = null;
        elements.windowSelect.disabled = true;
        elements.regionChoice.disabled = true;
        elements.saveCalibration.disabled = true;
        elements.stage.hidden = true;
        elements.dragHelp.hidden = true;
        elements.refreshWindows.hidden = false;
        showMessage("No compatible agent windows are open. Open one, then select Refresh windows.", true);
        return;
      }

      elements.refreshWindows.hidden = true;
      const current = state.windows.find((candidate) => candidate.current);
      if (!current) {
        const placeholder = documentRef.createElement("option");
        placeholder.value = "";
        placeholder.textContent = "Choose a window…";
        placeholder.disabled = true;
        elements.windowSelect.append(placeholder);
      }
      for (const candidate of state.windows) {
        const option = documentRef.createElement("option");
        option.value = candidate.id;
        const pieces = candidate.process_path.split(/[\\\\/]/);
        option.textContent = `${candidate.agent_id} — ${candidate.title} (${pieces.at(-1)})`;
        elements.windowSelect.append(option);
      }
      elements.windowSelect.disabled = false;
      if (current) {
        elements.windowSelect.value = current.id;
        elements.loadWindow.disabled = false;
        state.selectedWindow = current;
        state.surfaces = createSetupModel(current.surfaces, state.status).surfaces;
        elements.regionChoice.disabled = false;
        elements.saveCalibration.disabled = false;
        await loadScreenshot(state.status.revision);
        showMessage("Current calibration loaded. Select another window and choose Load window only to change targets.");
      } else {
        elements.windowSelect.value = "";
        showMessage("Choose a window, then select Load window.");
      }
    } catch (error) {
      showMessage(error.message, true);
    } finally {
      elements.refreshWindows.disabled = false;
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
  elements.refreshWindows.addEventListener("click", loadWindows);
  elements.windowSelect.addEventListener("change", () => {
    elements.loadWindow.disabled = !state.windows.some(
      (candidate) => candidate.id === elements.windowSelect.value,
    );
  });
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
      const link = documentRef.createElement("a");
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

  return { ready: initialize(), loadWindows, selectWindow };
}

if (typeof module !== "undefined" && module.exports) {
  module.exports = {
    calibrationRequest,
    createPhoneController,
    createSetupModel,
    initializePhoneWorkspace,
    initializeSetup,
    normalizeRectangle,
    phoneUrlForStatus,
    pointToFraction,
    setupDiagramLabels,
    swipeToSteps,
    toggleProject,
  };
}

if (typeof document !== "undefined") {
  document.addEventListener("DOMContentLoaded", () => {
    if (document.querySelector("#phone-workspace")) {
      initializePhoneWorkspace();
      return;
    }
    initializeSetup();
  });
}
