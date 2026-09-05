const test = require("node:test");
const assert = require("node:assert/strict");

const {
  initializePhoneWorkspace,
  initializeSetup,
} = require("../src/codeaway/web/app.js");

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
    this.children.push(...children);
  }

  replaceChildren(...children) {
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
}

class FakeDocument extends FakeElement {
  constructor(ids, { phone = false } = {}) {
    super("document");
    this.elements = Object.fromEntries(ids.map((id) => [id, new FakeElement(id)]));
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
    return new FakeElement();
  }

  createElementNS() {
    return new FakeElement();
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
  return new FakeDocument([
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
  assert.equal(documentRef.elements["window-select"].value, "");
  assert.equal(documentRef.elements["load-window"].disabled, true);
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
