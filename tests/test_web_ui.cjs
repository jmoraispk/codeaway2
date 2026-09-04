const test = require("node:test");
const assert = require("node:assert/strict");
const {
  calibrationRequest,
  createSetupModel,
  normalizeRectangle,
  phoneUrlForStatus,
  setupDiagramLabels,
} = require("../src/codeaway/web/app.js");

test("setup diagram names every required capture", () => {
  assert.deepEqual(setupDiagramLabels, ["Sidebar", "Conversation", "Composer"]);
});

test("drag coordinates normalize against the displayed screenshot", () => {
  assert.deepEqual(
    normalizeRectangle({ x: 20, y: 10 }, { x: 120, y: 60 }, 200, 100),
    { x: 0.1, y: 0.1, width: 0.5, height: 0.5 },
  );
});

test("drag coordinates clamp and normalize from either direction", () => {
  assert.deepEqual(
    normalizeRectangle({ x: 240, y: 120 }, { x: -40, y: -20 }, 200, 100),
    { x: 0, y: 0, width: 1, height: 1 },
  );
});

test("setup save keeps every API tuple after the user redraws one area", () => {
  const model = createSetupModel(
    {
      sidebar: [0, 0, 0.2, 1],
      conversation: [0.2, 0, 0.8, 0.75],
      composer: [0.3, 0.75, 0.6, 0.2],
    },
    { bind_ip: "100.64.0.7", port: 8765 },
  );

  assert.equal(model.phoneUrl, "http://100.64.0.7:8765/");
  model.replace("composer", { x: 0.31, y: 0.76, width: 0.58, height: 0.19 });

  assert.deepEqual(JSON.parse(calibrationRequest(model.surfaces).body), {
    surfaces: {
      sidebar: [0, 0, 0.2, 1],
      conversation: [0.2, 0, 0.8, 0.75],
      composer: [0.31, 0.76, 0.58, 0.19],
    },
  });
});

test("setup derives the phone URL from an IPv4 status address", () => {
  assert.equal(
    phoneUrlForStatus({ bind_ip: "100.64.0.7", port: 8765 }),
    "http://100.64.0.7:8765/",
  );
});

test("setup brackets an IPv6 status address in the phone URL", () => {
  assert.equal(
    phoneUrlForStatus({ bind_ip: "fd7a:115c:a1e0::7", port: 8765 }),
    "http://[fd7a:115c:a1e0::7]:8765/",
  );
});
