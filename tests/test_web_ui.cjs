const test = require("node:test");
const assert = require("node:assert/strict");
const {
  calibrationRequest,
  createPhoneController,
  createSetupModel,
  normalizeRectangle,
  phoneUrlForStatus,
  pointToFraction,
  setupDiagramLabels,
  swipeToSteps,
  toggleProject,
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

test("conversation tap maps to image fractions", () => {
  assert.deepEqual(pointToFraction(150, 100, { left: 50, top: 50, width: 200, height: 100 }), {
    x: 0.5,
    y: 0.5,
  });
});

test("swipe distance maps proportionally to bounded logical steps", () => {
  assert.equal(swipeToSteps(240), 10);
  assert.equal(swipeToSteps(-240), -10);
  assert.equal(swipeToSteps(2000), 12);
});

test("project expansion is local UI state", () => {
  const next = toggleProject({ SummonLab: true }, "SummonLab");
  assert.deepEqual(next, { SummonLab: false });
});

test("send clears the composer when its POST succeeds but the PNG refresh fails", async () => {
  const controller = createPhoneController({
    postAction: async () => ({ revision: 7 }),
    requestImage: async () => { throw new Error("PNG unavailable"); },
  });
  controller.setComposerText("Please continue");

  await controller.send();

  assert.equal(controller.composerText, "");
  assert.match(controller.conversationError, /could not be refreshed/);
});

test("unchanged polling does not re-request a failed image revision", async () => {
  const requests = [];
  const controller = createPhoneController({
    postAction: async () => ({ revision: 3 }),
    requestImage: async (revision) => {
      requests.push(revision);
      throw new Error("PNG unavailable");
    },
  });

  await controller.refreshConversation(3);
  await controller.refreshConversation(3);

  assert.deepEqual(requests, [3]);
});

test("a successful action may re-request its conversation image revision", async () => {
  const requests = [];
  const controller = createPhoneController({
    postAction: async () => ({ revision: 3 }),
    requestImage: async (revision) => {
      requests.push(revision);
      throw new Error("PNG unavailable");
    },
  });

  await controller.refreshConversation(3);
  await controller.performAction({ kind: "scroll", amount: -1 });

  assert.deepEqual(requests, [3, 3]);
});

test("gestures require a loaded revision and a natural image", async () => {
  const controller = createPhoneController({
    postAction: async () => ({ revision: 3 }),
    requestImage: async () => {},
  });

  assert.equal(controller.canHandleGesture({ naturalWidth: 100 }), false);
  await controller.refreshConversation(3);
  assert.equal(controller.canHandleGesture({ naturalWidth: 0 }), false);
  assert.equal(controller.canHandleGesture({ naturalWidth: 100 }), true);
});

test("a stale poll cannot override a newer action image request", async () => {
  const requests = [];
  let completeNewerImage;
  const controller = createPhoneController({
    postAction: async () => ({ revision: 4 }),
    requestImage: (revision) => {
      requests.push(revision);
      if (revision === 4) {
        return new Promise((resolve) => { completeNewerImage = resolve; });
      }
      return Promise.resolve();
    },
  });

  const action = controller.performAction({ kind: "scroll", amount: -1 });
  await Promise.resolve();
  await controller.refreshConversation(3);

  assert.deepEqual(requests, [4]);
  completeNewerImage();
  await action;
  assert.equal(controller.imageRevision, 4);
  assert.equal(controller.conversationError, "");
});
