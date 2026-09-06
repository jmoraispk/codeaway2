const assert = require("node:assert/strict");
const { spawn } = require("node:child_process");
const { existsSync } = require("node:fs");
const { mkdtemp, readFile, rm } = require("node:fs/promises");
const { createServer } = require("node:http");
const { tmpdir } = require("node:os");
const path = require("node:path");
const test = require("node:test");

function edgePath() {
  const candidates = [
    process.env["PROGRAMFILES(X86)"] && path.join(process.env["PROGRAMFILES(X86)"], "Microsoft", "Edge", "Application", "msedge.exe"),
    process.env.PROGRAMFILES && path.join(process.env.PROGRAMFILES, "Microsoft", "Edge", "Application", "msedge.exe"),
  ].filter(Boolean);
  return candidates.find(existsSync);
}

async function waitForFile(filename) {
  for (let attempt = 0; attempt < 100; attempt += 1) {
    try {
      return await readFile(filename, "utf8");
    } catch {
      await new Promise((resolve) => setTimeout(resolve, 50));
    }
  }
  throw new Error(`Timed out waiting for ${filename}`);
}

function createCdpClient(url) {
  const socket = new WebSocket(url);
  const pending = new Map();
  let nextId = 1;
  socket.addEventListener("message", ({ data }) => {
    const message = JSON.parse(data);
    const request = pending.get(message.id);
    if (!request) return;
    pending.delete(message.id);
    if (message.error) request.reject(new Error(message.error.message));
    else request.resolve(message.result);
  });
  return {
    async open() {
      await new Promise((resolve, reject) => {
        socket.addEventListener("open", resolve, { once: true });
        socket.addEventListener("error", reject, { once: true });
      });
    },
    call(method, params = {}) {
      const id = nextId++;
      return new Promise((resolve, reject) => {
        pending.set(id, { resolve, reject });
        socket.send(JSON.stringify({ id, method, params }));
      });
    },
    close() { socket.close(); },
  };
}

async function serveWeb() {
  const webRoot = path.join(__dirname, "..", "src", "codeaway", "web");
  const assets = new Map([
    ["/", "index.html"],
    ["/setup", "setup.html"],
    ["/app.js", "app.js"],
    ["/style.css", "style.css"],
  ]);
  const server = createServer(async (request, response) => {
    const asset = assets.get(request.url);
    if (!asset) {
      response.writeHead(404).end();
      return;
    }
    response.writeHead(200).end(await readFile(path.join(webRoot, asset)));
  });
  await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
  const { port } = server.address();
  return {
    url: `http://127.0.0.1:${port}/`,
    close: () => new Promise((resolve, reject) => server.close((error) => error ? reject(error) : resolve())),
  };
}

async function evaluateInDarkEdge(url, expression) {
  const executable = edgePath();
  assert.ok(executable, "Microsoft Edge must be installed for browser UI tests");
  const profile = await mkdtemp(path.join(tmpdir(), "codeaway-edge-"));
  const process = spawn(executable, [
    "--headless=new",
    "--remote-debugging-port=0",
    `--user-data-dir=${profile}`,
    "--no-first-run",
    "--no-default-browser-check",
  ], { stdio: "ignore", windowsHide: true });
  let client;
  try {
    const [port] = (await waitForFile(path.join(profile, "DevToolsActivePort"))).trim().split(/\r?\n/);
    const target = await (await fetch(`http://127.0.0.1:${port}/json/new?about:blank`, { method: "PUT" })).json();
    client = createCdpClient(target.webSocketDebuggerUrl);
    await client.open();
    await client.call("Emulation.setEmulatedMedia", {
      features: [{ name: "prefers-color-scheme", value: "dark" }],
    });
    await client.call("Page.enable");
    await client.call("Page.navigate", { url });
    for (let attempt = 0; attempt < 100; attempt += 1) {
      const result = await client.call("Runtime.evaluate", {
        expression: `document.readyState === "complete" && (${expression})`,
        returnByValue: true,
      });
      if (result.result.value) return result.result.value;
      await new Promise((resolve) => setTimeout(resolve, 25));
    }
    throw new Error("Timed out waiting for page evaluation");
  } finally {
    await client?.call("Browser.close").catch(() => {});
    client?.close();
    for (let attempt = 0; attempt < 100; attempt += 1) {
      try {
        await rm(profile, { recursive: true, force: true, maxRetries: 1, retryDelay: 50 });
        break;
      } catch (error) {
        if (attempt === 99) throw error;
        await new Promise((resolve) => setTimeout(resolve, 50));
      }
    }
  }
}

test("phone workspace follows the operating system dark color preference", async () => {
  const web = await serveWeb();
  let colors;
  try {
    colors = await evaluateInDarkEdge(web.url, `JSON.stringify({
      page: getComputedStyle(document.documentElement).backgroundColor,
      panel: getComputedStyle(document.querySelector(".navigator")).backgroundColor,
      text: getComputedStyle(document.documentElement).color,
    })`);
  } finally {
    await web.close();
  }

  assert.deepEqual(JSON.parse(colors), {
    page: "rgb(23, 25, 29)",
    panel: "rgb(30, 33, 39)",
    text: "rgb(239, 241, 245)",
  });
});

test("setup workspace follows the operating system dark color preference", async () => {
  const web = await serveWeb();
  let colors;
  try {
    colors = await evaluateInDarkEdge(`${web.url}setup`, `JSON.stringify({
      page: getComputedStyle(document.documentElement).backgroundColor,
      panel: getComputedStyle(document.querySelector(".setup-editor")).backgroundColor,
      text: getComputedStyle(document.documentElement).color,
    })`);
  } finally {
    await web.close();
  }

  assert.deepEqual(JSON.parse(colors), {
    page: "rgb(23, 25, 29)",
    panel: "rgb(30, 33, 39)",
    text: "rgb(239, 241, 245)",
  });
});
