const test = require("node:test");
const assert = require("node:assert/strict");
const {
  normalizeRectangle,
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
