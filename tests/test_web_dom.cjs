const test = require("node:test");
const assert = require("node:assert/strict");

const {
  initializePhoneWorkspace,
  initializeSetup,
} = require("../src/codeaway/web/app.js");

function setConnected(element, isConnected) {
  if (!element || typeof element !== "object") return;
  element.isConnected = isConnected;
  for (const child of element.children || []) setConnected(child, isConnected);
}

class FakeClassList {
  constructor() {
    this.values = new Set();
  }

  toggle(name, force) {
    const enabled = force === undefined ? !this.values.has(name) : Boolean(force);
    if (enabled) this.values.add(name);
    else this.values.delete(name);
    return enabled;
  }

  contains(name) {
    return this.values.has(name);
  }
}

class FakeElement {
  constructor(id = "") {
    this.id = id;
    this.attributes = {};
    this.children = [];
    this.classList = new FakeClassList();
    this.complete = true;
    this.disabled = false;
    this.hidden = false;
    this.listeners = new Map();
    this.naturalWidth = 800;
    this.style = {};
    this.textContent = "";
    this.value = "";
    this._box = { left: 0, top: 0, width: 800, height: 600 };
    this._src = "";
  }

  addEventListener(type, listener, options = {}) {
    const listeners = this.listeners.get(type) || [];
    listeners.push({ listener, once: Boolean(options.once) });
    this.listeners.set(type, listeners);
  }

  removeEventListener(type, listener) {
    this.listeners.set(
      type,
      (this.listeners.get(type) || []).filter((entry) => entry.listener !== listener),
    );
  }

  async emit(type, values = {}) {
    const event = { type, preventDefault() {}, ...values };
    const listeners = [...(this.listeners.get(type) || [])];
    for (const entry of listeners) {
      await entry.listener(event);
      if (entry.once) this.removeEventListener(type, entry.listener);
    }
  }

  append(...children) {
    for (const child of children) {
      if (child && typeof child === "object") {
        child.parentElement = this;
        setConnected(child, Boolean(this.isConnected));
      }
    }
    this.children.push(...children);
  }

  replaceChildren(...children) {
    for (const child of children) {
      if (child && typeof child === "object") {
        child.parentElement = this;
        setConnected(child, Boolean(this.isConnected));
      }
    }
    this.children = [...children];
    this.textContent = "";
  }

  setAttribute(name, value) {
    this.attributes[name] = String(value);
  }

  getBoundingClientRect() {
    return this._box;
  }

  setPointerCapture(pointerId) {
    this.pointerCapture = pointerId;
  }

  async decode() {}

  set src(value) {
    this._src = value;
    queueMicrotask(() => this.emit("load"));
  }

  get src() {
    return this._src;
  }

  focus() {
    this.focusedWhileConnected = Boolean(this.isConnected);
    if (this.ownerDocument) this.ownerDocument.activeElement = this;
  }
}

class FakeDocument extends FakeElement {
  constructor(ids, { phone = false } = {}) {
    super("document");
    this.elements = Object.fromEntries(ids.map((id) => [id, new FakeElement(id)]));
    for (const element of Object.values(this.elements)) element.ownerDocument = this;
    this.phone = phone;
    this.visibilityState = "visible";
    this.checkedRegion = new FakeElement("checked-region");
    this.checkedRegion.value = "sidebar";
  }

  querySelector(selector) {
    if (selector === "#phone-workspace") {
      return this.phone ? this.elements["phone-workspace"] : null;
    }
    if (selector === 'input[name="region"]:checked') return this.checkedRegion;
    if (selector.startsWith("#")) return this.elements[selector.slice(1)] || null;
    return null;
  }

  createElement() {
    const element = new FakeElement();
    element.ownerDocument = this;
    return element;
  }

  createElementNS() {
    const element = new FakeElement();
    element.ownerDocument = this;
    return element;
  }
}

class FakeWindow {
  constructor() {
    this.intervals = new Map();
    this.nextInterval = 1;
  }

  setInterval(callback) {
    const id = this.nextInterval++;
    this.intervals.set(id, callback);
    return id;
  }

  clearInterval(id) {
    this.intervals.delete(id);
  }
}

function response(value, status = 200) {
  return {
    ok: status >= 200 && status < 300,
    status,
    headers: { get: () => "application/json; charset=utf-8" },
    async json() { return value; },
  };
}

function setupDocument() {
  const documentRef = new FakeDocument([
    "setup-complete",
    "drag-help",
    "load-window",
    "refresh-windows",
    "setup-message",
    "region-overlays",
    "region-choice",
    "save-calibration",
    "window-screenshot",
    "screenshot-stage",
    "window-select",
  ]);
  documentRef.elements["screenshot-stage"].hidden = true;
  documentRef.elements["drag-help"].hidden = true;
  documentRef.elements["refresh-windows"].hidden = true;
  return documentRef;
}

function phoneDocument() {
  const documentRef = new FakeDocument([
    "phone-workspace",
    "composer",
    "composer-input",
    "composer-message",
    "composer-send",
    "conversation-image",
    "conversation-message",
    "navigator-projects",
    "status-message",
  ], { phone: true });
  documentRef.elements["navigator-projects"].isConnected = true;
  return documentRef;
}

const savedSurfaces = {
  sidebar: [0, 0, 0.25, 1],
  conversation: [0.25, 0, 0.75, 0.7],
  composer: [0.35, 0.7, 0.5, 0.25],
};

test("setup preserves current calibration without selecting another window", async () => {
  const documentRef = setupDocument();
  const calls = [];
  const windows = [
    {
      agent_id: "codex", current: true, id: "current", process_path: "C:/Codex.exe",
      surfaces: savedSurfaces, title: "Current Codex",
    },
    {
      agent_id: "codex", current: false, id: "other", process_path: "C:/Codex.exe",
      surfaces: {
        sidebar: [0, 0, 0.2, 1], conversation: [0.2, 0, 0.8, 0.8],
        composer: [0.3, 0.8, 0.6, 0.2],
      },
      title: "Other Codex",
    },
  ];
  const fetchFn = async (path, options = {}) => {
    calls.push({ path, options });
    if (path === "/api/status") {
      return response({ bind_ip: "127.0.0.1", port: 8765, revision: 7 });
    }
    if (path === "/api/windows") return response({ windows });
    if (path === "/api/select") return response({ revision: 8 });
    throw new Error(`unexpected request ${path}`);
  };

  const setup = initializeSetup({ documentRef, fetchFn });
  await setup.ready;

  assert.equal(documentRef.elements["window-select"].value, "current");
  assert.equal(documentRef.elements["window-screenshot"].src, "/api/screenshot/window?revision=7");
  assert.equal(documentRef.elements["region-overlays"].children[0].style.width, "25%");
  assert.deepEqual(calls.map((call) => call.path), ["/api/status", "/api/windows"]);

  documentRef.elements["window-select"].value = "other";
  await documentRef.elements["window-select"].emit("change");

  assert.equal(documentRef.elements["load-window"].disabled, false);
  assert.equal(calls.some((call) => call.path === "/api/select"), false);
});

test("setup preselects the first available window without loading it", async () => {
  const documentRef = setupDocument();
  const calls = [];
  const windows = [
    {
      agent_id: "codex", current: false, id: "first", process_path: "C:/Codex.exe",
      surfaces: savedSurfaces, title: "First Codex",
    },
    {
      agent_id: "codex", current: false, id: "second", process_path: "C:/Codex.exe",
      surfaces: savedSurfaces, title: "Second Codex",
    },
  ];
  const fetchFn = async (path, options = {}) => {
    calls.push({ path, options });
    if (path === "/api/status") {
      return response({ bind_ip: "127.0.0.1", port: 8765, revision: 7 });
    }
    if (path === "/api/windows") return response({ windows });
    if (path === "/api/select") return response({ revision: 8 });
    throw new Error(`unexpected request ${path}`);
  };

  const setup = initializeSetup({ documentRef, fetchFn });
  await setup.ready;

  assert.equal(documentRef.elements["window-select"].value, "first");
  assert.equal(documentRef.elements["load-window"].disabled, false);
  assert.equal(documentRef.elements["screenshot-stage"].hidden, true);
  assert.equal(documentRef.elements["window-screenshot"].src, "");
  assert.deepEqual(calls.map((call) => call.path), ["/api/status", "/api/windows"]);
});

test("setup overlay classes do not collide with phone panel classes", async () => {
  const documentRef = setupDocument();
  const fetchFn = async (path) => {
    if (path === "/api/status") {
      return response({ bind_ip: "127.0.0.1", port: 8765, revision: 3 });
    }
    if (path === "/api/windows") {
      return response({
        windows: [{
          agent_id: "codex", current: true, id: "current", process_path: "C:/Codex.exe",
          surfaces: savedSurfaces, title: "Current Codex",
        }],
      });
    }
    throw new Error(`unexpected request ${path}`);
  };

  const setup = initializeSetup({ documentRef, fetchFn });
  await setup.ready;

  const classes = documentRef.elements["region-overlays"].children.map(
    (overlay) => overlay.className.split(" "),
  );
  assert.deepEqual(classes, [
    ["region-overlay", "region-overlay--sidebar", "selected"],
    ["region-overlay", "region-overlay--conversation"],
    ["region-overlay", "region-overlay--composer"],
  ]);
});

test("setup refresh button retries discovery without reloading the page", async () => {
  const documentRef = setupDocument();
  let discoveryCount = 0;
  const fetchFn = async (path) => {
    if (path === "/api/status") {
      return response({ bind_ip: "127.0.0.1", port: 8765, revision: 0 });
    }
    if (path === "/api/windows") {
      discoveryCount += 1;
      return response({
        windows: discoveryCount === 1 ? [] : [{
          agent_id: "codex", current: false, id: "found", process_path: "C:/Codex.exe",
          surfaces: savedSurfaces, title: "Found Codex",
        }],
      });
    }
    throw new Error(`unexpected request ${path}`);
  };

  const setup = initializeSetup({ documentRef, fetchFn });
  await setup.ready;
  assert.equal(documentRef.elements["refresh-windows"].hidden, false);

  await documentRef.elements["refresh-windows"].emit("click");

  assert.equal(discoveryCount, 2);
  assert.equal(documentRef.elements["refresh-windows"].hidden, true);
  assert.equal(documentRef.elements["window-select"].value, "found");
  assert.equal(documentRef.elements["load-window"].disabled, false);
  assert.equal(documentRef.elements["screenshot-stage"].hidden, true);
});

test("phone wiring refreshes autonomous revisions and manages visibility polling", async () => {
  const documentRef = phoneDocument();
  const windowRef = new FakeWindow();
  let statusCount = 0;
  const fetchFn = async (path) => {
    if (path === "/api/status") {
      const revision = statusCount++ === 0 ? 0 : 1;
      return response({
        ready: true,
        revision,
        target: { agent_id: "codex", title: "Agent Window" },
      });
    }
    if (path === "/api/navigator") {
      return response({ available: false, error: "Accessibility unavailable", projects: [] });
    }
    throw new Error(`unexpected request ${path}`);
  };

  const phone = initializePhoneWorkspace({ documentRef, windowRef, fetchFn });
  await phone.ready;

  assert.equal(documentRef.elements["conversation-image"].src, "/api/screenshot/conversation?revision=0");
  assert.match(documentRef.elements["status-message"].textContent, /codex.*ready/i);
  assert.equal(windowRef.intervals.size, 1);

  await [...windowRef.intervals.values()][0]();
  assert.equal(documentRef.elements["conversation-image"].src, "/api/screenshot/conversation?revision=1");

  documentRef.visibilityState = "hidden";
  await documentRef.emit("visibilitychange");
  assert.equal(windowRef.intervals.size, 0);

  documentRef.visibilityState = "visible";
  await documentRef.emit("visibilitychange");
  assert.equal(windowRef.intervals.size, 1);
});

test("phone navigator renders accessible state and worktree icons without status words", async () => {
  const documentRef = phoneDocument();
  const windowRef = new FakeWindow();
  const fetchFn = async (path) => {
    if (path === "/api/status") {
      return response({
        ready: true,
        revision: 0,
        target: { agent_id: "codex", title: "Agent Window" },
      });
    }
    if (path === "/api/navigator") {
      return response({
        available: true,
        projects: [
          {
            name: "Alpha", connected: true, expanded: true, host: "private_3", state: "connected",
            tasks: [
              { task_id: "0", title: "Running", state: "busy", worktree: true, selected: true },
              { task_id: "1", title: "Finished", state: "done", worktree: false, selected: false },
              { task_id: "2", title: "Unclassified", state: "unknown", worktree: true, selected: false },
              { task_id: "3", title: "Waiting", state: "idle", worktree: false, selected: false },
            ],
          },
          { name: "Beta", connected: false, expanded: false, host: "remote-ssh", state: "busy", tasks: [] },
          { name: "Gamma", connected: false, expanded: false, host: null, state: "idle", tasks: [] },
          { name: "Delta", connected: false, expanded: false, host: "", state: "unknown", tasks: [] },
        ],
      });
    }
    throw new Error(`unexpected request ${path}`);
  };
  const assertIcon = (icon, className, label) => {
    assert.equal(icon.attributes.class, className);
    assert.equal(icon.attributes["aria-label"], label);
    assert.equal(icon.attributes.title, label);
    assert.equal(icon.textContent, "");
  };

  const phone = initializePhoneWorkspace({ documentRef, windowRef, fetchFn });
  await phone.ready;

  const projects = documentRef.elements["navigator-projects"].children;
  assert.equal(projects.length, 4);
  const alphaMeta = projects[0].children[0].children[1];
  assert.equal(alphaMeta.className, "project-meta");
  assert.equal(alphaMeta.children[0].className, "project-host");
  assert.equal(alphaMeta.children[0].textContent, "private_3");
  assertIcon(alphaMeta.children[1], "status-icon status-icon--connected", "Connected");
  const betaMeta = projects[1].children[0].children[1];
  assert.equal(betaMeta.children[0].textContent, "remote-ssh");
  assertIcon(betaMeta.children[1], "status-icon status-icon--busy", "Busy");
  const gammaMeta = projects[2].children[0].children[1];
  assert.equal(gammaMeta.children[0].textContent, "local");
  assertIcon(gammaMeta.children[1], "status-icon status-icon--connected", "Connected");
  const deltaMeta = projects[3].children[0].children[1];
  assert.equal(deltaMeta.children[0].textContent, "local");
  assertIcon(deltaMeta.children[1], "status-icon status-icon--connected", "Connected");

  const tasks = projects[0].children[1].children.map((row) => row.children[0]);
  assert.equal(tasks[0].classList.contains("selected"), true);
  assert.equal(tasks[0].children[0].textContent, "Running");
  const runningMeta = tasks[0].children[1];
  assert.equal(runningMeta.className, "task-meta");
  assertIcon(runningMeta.children[0], "worktree-marker", "Worktree");
  assertIcon(runningMeta.children[1], "status-icon status-icon--busy", "Busy");
  assert.equal(tasks[1].children[0].textContent, "Finished");
  assert.equal(tasks[1].children[1].className, "task-meta");
  assertIcon(tasks[1].children[1].children[0], "status-icon status-icon--ready", "Ready");
  assert.equal(tasks[2].children[1].children.length, 1);
  assertIcon(tasks[2].children[1].children[0], "worktree-marker", "Worktree");
  assert.equal(tasks[3].children[1].children.length, 0);
});

test("project action on the right creates a chat from its initial prompt", async () => {
  const documentRef = phoneDocument();
  const windowRef = new FakeWindow();
  const actions = [];
  const fetchFn = async (path, options = {}) => {
    if (path === "/api/status") {
      return response({
        ready: true,
        revision: 0,
        target: { agent_id: "codex", title: "Agent Window" },
      });
    }
    if (path === "/api/navigator") {
      return response({
        available: true,
        projects: [{
          name: "SummonLab",
          connected: true,
          expanded: true,
          host: "private_3",
          state: "connected",
          tasks: [],
        }],
      });
    }
    if (path === "/api/action") {
      actions.push(JSON.parse(options.body));
      return response({ revision: 1 });
    }
    throw new Error(`unexpected request ${path}`);
  };

  const phone = initializePhoneWorkspace({ documentRef, windowRef, fetchFn });
  await phone.ready;

  let project = documentRef.elements["navigator-projects"].children[0];
  const projectHeader = project.children[0];
  const createButton = projectHeader.children.find(
    (child) => child.className === "project-create",
  );
  assert.ok(createButton);
  assert.equal(createButton.attributes["aria-label"], "New chat in SummonLab (private_3)");
  assert.equal(projectHeader.children.at(-1), createButton);

  await createButton.emit("click");
  project = documentRef.elements["navigator-projects"].children[0];
  const form = project.children.find(
    (child) => child.className === "inline-editor create-chat-form",
  );
  assert.ok(form);
  assert.equal(form.children[0].focusedWhileConnected, true);
  form.children[0].value = "Investigate the regression";
  await form.emit("submit");

  assert.deepEqual(actions, [{
    kind: "create_chat",
    project: "SummonLab",
    host: "private_3",
    text: "Investigate the regression",
  }]);
});

test("task action on the right renames its chat", async () => {
  const documentRef = phoneDocument();
  const windowRef = new FakeWindow();
  const actions = [];
  const fetchFn = async (path, options = {}) => {
    if (path === "/api/status") {
      return response({
        ready: true,
        revision: 0,
        target: { agent_id: "codex", title: "Agent Window" },
      });
    }
    if (path === "/api/navigator") {
      return response({
        available: true,
        projects: [{
          name: "SummonLab",
          connected: true,
          expanded: true,
          host: "private_3",
          state: "connected",
          tasks: [{
            task_id: "0",
            title: "Old title",
            state: "unknown",
            worktree: false,
            selected: false,
          }],
        }],
      });
    }
    if (path === "/api/action") {
      actions.push(JSON.parse(options.body));
      return response({ revision: 1 });
    }
    throw new Error(`unexpected request ${path}`);
  };

  const phone = initializePhoneWorkspace({ documentRef, windowRef, fetchFn });
  await phone.ready;

  let project = documentRef.elements["navigator-projects"].children[0];
  let taskRow = project.children.at(-1).children[0];
  const renameButton = taskRow.children.find(
    (child) => child.className === "task-rename",
  );
  assert.ok(renameButton);
  assert.equal(renameButton.attributes["aria-label"], "Rename Old title");
  assert.equal(taskRow.children.at(-1), renameButton);

  await renameButton.emit("click");
  project = documentRef.elements["navigator-projects"].children[0];
  taskRow = project.children.at(-1).children[0];
  const form = taskRow.children.find(
    (child) => child.className === "inline-editor rename-chat-form",
  );
  assert.ok(form);
  assert.equal(form.children[0].value, "Old title");
  form.children[0].value = "Clear title";
  await form.emit("submit");

  assert.deepEqual(actions, [{
    kind: "rename_chat",
    project: "SummonLab",
    host: "private_3",
    task_id: "0",
    title: "Old title",
    new_title: "Clear title",
  }]);
});

test("navigator polling preserves a focused create draft and restores the project action on cancel", async () => {
  const documentRef = phoneDocument();
  const windowRef = new FakeWindow();
  const snapshot = {
    available: true,
    projects: [{
      name: "SummonLab", connected: true, expanded: true, host: "private_3", state: "connected", tasks: [],
    }],
  };
  const fetchFn = async (path) => {
    if (path === "/api/status") return response({
      ready: true, revision: 0, target: { agent_id: "codex", title: "Agent Window" },
    });
    if (path === "/api/navigator") return response(snapshot);
    throw new Error(`unexpected request ${path}`);
  };

  const phone = initializePhoneWorkspace({ documentRef, windowRef, fetchFn });
  await phone.ready;
  let project = documentRef.elements["navigator-projects"].children[0];
  await project.children[0].children.at(-1).emit("click");
  let form = documentRef.elements["navigator-projects"].children[0].children.find(
    (child) => child.className === "inline-editor create-chat-form",
  );
  form.children[0].value = "Keep this draft";
  await form.children[0].emit("input");
  form.children[0].focus();

  await [...windowRef.intervals.values()][0]();

  project = documentRef.elements["navigator-projects"].children[0];
  form = project.children.find((child) => child.className === "inline-editor create-chat-form");
  assert.equal(form.children[0].value, "Keep this draft");
  assert.equal(documentRef.activeElement, form.children[0]);

  await form.children.at(-1).emit("click");
  assert.equal(documentRef.activeElement, documentRef.elements["navigator-projects"].children[0].children[0].children.at(-1));
});

test("navigator polling preserves a focused rename draft and restores its action on cancel", async () => {
  const documentRef = phoneDocument();
  const windowRef = new FakeWindow();
  const snapshot = {
    available: true,
    projects: [{
      name: "SummonLab", connected: true, expanded: true, host: "private_3", state: "connected",
      tasks: [{ task_id: "0", title: "Draft title", state: "idle", worktree: false, selected: false }],
    }],
  };
  const fetchFn = async (path) => {
    if (path === "/api/status") return response({
      ready: true, revision: 0, target: { agent_id: "codex", title: "Agent Window" },
    });
    if (path === "/api/navigator") return response(snapshot);
    throw new Error(`unexpected request ${path}`);
  };

  const phone = initializePhoneWorkspace({ documentRef, windowRef, fetchFn });
  await phone.ready;
  let taskRow = documentRef.elements["navigator-projects"].children[0].children.at(-1).children[0];
  await taskRow.children.at(-1).emit("click");
  let form = documentRef.elements["navigator-projects"].children[0].children.at(-1).children[0].children.find(
    (child) => child.className === "inline-editor rename-chat-form",
  );
  form.children[0].value = "Working title";
  await form.children[0].emit("input");
  form.children[0].focus();

  await [...windowRef.intervals.values()][0]();

  taskRow = documentRef.elements["navigator-projects"].children[0].children.at(-1).children[0];
  form = taskRow.children.find((child) => child.className === "inline-editor rename-chat-form");
  assert.equal(form.children[0].value, "Working title");
  assert.equal(documentRef.activeElement, form.children[0]);

  await form.children.at(-1).emit("click");
  assert.equal(documentRef.activeElement, documentRef.elements["navigator-projects"].children[0].children.at(-1).children[0].children.at(-1));
});

test("rename submits the originally opened task after a polling snapshot changes its title", async () => {
  const documentRef = phoneDocument();
  const windowRef = new FakeWindow();
  const actions = [];
  let navigatorReads = 0;
  const fetchFn = async (path, options = {}) => {
    if (path === "/api/status") return response({
      ready: true, revision: 0, target: { agent_id: "codex", title: "Agent Window" },
    });
    if (path === "/api/navigator") {
      navigatorReads += 1;
      const title = navigatorReads === 1 ? "Original title" : "Refreshed title";
      return response({ available: true, projects: [{
        name: "Project", host: "host", connected: false, expanded: true, state: "idle",
        tasks: [{ task_id: "runtime:task", title, state: "idle", worktree: false, selected: false }],
      }] });
    }
    if (path === "/api/action") {
      actions.push(JSON.parse(options.body));
      return response({ revision: 1 });
    }
    throw new Error(`unexpected request ${path}`);
  };

  const phone = initializePhoneWorkspace({ documentRef, windowRef, fetchFn });
  await phone.ready;
  let taskRow = documentRef.elements["navigator-projects"].children[0].children.at(-1).children[0];
  await taskRow.children.at(-1).emit("click");
  await [...windowRef.intervals.values()][0]();
  taskRow = documentRef.elements["navigator-projects"].children[0].children.at(-1).children[0];
  const form = taskRow.children.find((child) => child.className === "inline-editor rename-chat-form");
  form.children[0].value = "Requested title";
  await form.emit("submit");

  assert.deepEqual(actions, [{
    kind: "rename_chat", project: "Project", host: "host", task_id: "runtime:task",
    title: "Original title", new_title: "Requested title",
  }]);
});

test("rename polling preserves an intentionally empty draft", async () => {
  const documentRef = phoneDocument();
  const windowRef = new FakeWindow();
  const snapshot = { available: true, projects: [{
    name: "Project", host: "host", connected: false, expanded: true, state: "idle",
    tasks: [{ task_id: "runtime:task", title: "Original title", state: "idle", worktree: false, selected: false }],
  }] };
  const fetchFn = async (path) => {
    if (path === "/api/status") return response({
      ready: true, revision: 0, target: { agent_id: "codex", title: "Agent Window" },
    });
    if (path === "/api/navigator") return response(snapshot);
    throw new Error(`unexpected request ${path}`);
  };

  const phone = initializePhoneWorkspace({ documentRef, windowRef, fetchFn });
  await phone.ready;
  let taskRow = documentRef.elements["navigator-projects"].children[0].children.at(-1).children[0];
  await taskRow.children.at(-1).emit("click");
  taskRow = documentRef.elements["navigator-projects"].children[0].children.at(-1).children[0];
  let form = taskRow.children.find((child) => child.className === "inline-editor rename-chat-form");
  form.children[0].value = "";
  await form.children[0].emit("input");

  await [...windowRef.intervals.values()][0]();

  taskRow = documentRef.elements["navigator-projects"].children[0].children.at(-1).children[0];
  form = taskRow.children.find((child) => child.className === "inline-editor rename-chat-form");
  assert.equal(form.children[0].value, "");
});

test("duplicate project hosts and task titles retain their exact remote action identity", async () => {
  const documentRef = phoneDocument();
  const windowRef = new FakeWindow();
  const actions = [];
  const fetchFn = async (path, options = {}) => {
    if (path === "/api/status") return response({
      ready: true, revision: 0, target: { agent_id: "codex", title: "Agent Window" },
    });
    if (path === "/api/navigator") return response({
      available: true,
      projects: [
        { name: "Duplicate", host: "host-a", connected: false, expanded: true, state: "idle", tasks: [{ task_id: "0", title: "Same", state: "idle", worktree: false, selected: false }] },
        { name: "Duplicate", host: "host-b", connected: false, expanded: true, state: "idle", tasks: [{ task_id: "1", title: "Same", state: "idle", worktree: false, selected: false }] },
      ],
    });
    if (path === "/api/action") {
      actions.push(JSON.parse(options.body));
      return response({ revision: 1 });
    }
    throw new Error(`unexpected request ${path}`);
  };

  const phone = initializePhoneWorkspace({ documentRef, windowRef, fetchFn });
  await phone.ready;
  const second = documentRef.elements["navigator-projects"].children[1];
  const header = second.children[0];
  assert.equal(header.children[0].attributes["aria-label"], "Toggle Duplicate (host-b)");
  assert.equal(header.children.at(-1).attributes["aria-label"], "New chat in Duplicate (host-b)");
  assert.equal(header.children.at(-1).className, "project-create");
  await header.children.at(-1).emit("click");
  let project = documentRef.elements["navigator-projects"].children[1];
  let createForm = project.children.find((child) => child.className === "inline-editor create-chat-form");
  createForm.children[0].value = "Start this host";
  await createForm.children[0].emit("input");
  await createForm.emit("submit");
  project = documentRef.elements["navigator-projects"].children[1];
  const taskRow = project.children.at(-1).children[0];
  await taskRow.children[0].emit("click");
  await taskRow.children.at(-1).emit("click");
  const form = documentRef.elements["navigator-projects"].children[1].children.at(-1).children[0].children.find(
    (child) => child.className === "inline-editor rename-chat-form",
  );
  form.children[0].value = "Renamed";
  await form.emit("submit");

  assert.deepEqual(actions, [
    { kind: "create_chat", project: "Duplicate", host: "host-b", text: "Start this host" },
    { kind: "navigate", target: "task", project: "Duplicate", host: "host-b", task_id: "1", title: "Same" },
    { kind: "rename_chat", project: "Duplicate", host: "host-b", task_id: "1", title: "Same", new_title: "Renamed" },
  ]);
});

test("phone pointer and composer listeners dispatch validated actions", async () => {
  const documentRef = phoneDocument();
  const windowRef = new FakeWindow();
  const actions = [];
  let actionRevision = 1;
  const fetchFn = async (path, options = {}) => {
    if (path === "/api/status") {
      return response({
        ready: true,
        revision: 0,
        target: { agent_id: "codex", title: "Agent Window" },
      });
    }
    if (path === "/api/navigator") {
      return response({ available: true, projects: [] });
    }
    if (path === "/api/action") {
      actions.push(JSON.parse(options.body));
      return response({ revision: actionRevision++ });
    }
    throw new Error(`unexpected request ${path}`);
  };

  const phone = initializePhoneWorkspace({ documentRef, windowRef, fetchFn });
  await phone.ready;
  const image = documentRef.elements["conversation-image"];

  await image.emit("pointerdown", { pointerId: 4, clientX: 400, clientY: 300 });
  await image.emit("pointerup", { pointerId: 4, clientX: 400, clientY: 300 });

  const input = documentRef.elements["composer-input"];
  input.value = "Continue safely";
  await documentRef.elements.composer.emit("submit");

  assert.deepEqual(actions, [
    { kind: "click", surface: "conversation", x: 0.5, y: 0.5 },
    { kind: "send", text: "Continue safely" },
  ]);
  assert.equal(input.value, "");
  assert.equal(documentRef.elements["composer-send"].disabled, false);
});
