const test = require("node:test");
const assert = require("node:assert/strict");
const {
  calibrationRequest,
  createPhoneController,
  createTranscriptController,
  createSetupModel,
  normalizeRectangle,
  phoneUrlForStatus,
  pointToFraction,
  setupDiagramLabels,
  swipeToSteps,
  toggleProject,
} = require("../src/codeaway/web/app.js");

test("transcript controller applies a snapshot and suffix delta", async () => {
  const replies = [
    {
      mode: "snapshot",
      stream_id: "s1",
      revision: 1,
      messages: [
        { id: "m1", role: "assistant", text: "Hello", state: "streaming" },
      ],
      stale: false,
    },
    {
      mode: "delta",
      stream_id: "s1",
      revision: 2,
      events: [
        { kind: "text_appended", message_id: "m1", text: " world" },
      ],
      stale: false,
    },
  ];
  const requests = [];
  const changes = [];
  const controller = createTranscriptController({
    requestTranscript: async (cursor) => {
      requests.push(cursor);
      return replies.shift();
    },
    onChange: (state) => changes.push(state),
  });

  assert.equal(await controller.poll(), true);
  assert.equal(await controller.poll(), true);

  assert.equal(controller.messages[0].text, "Hello world");
  assert.deepEqual(requests, [{}, { stream: "s1", after: 1 }]);
  assert.deepEqual(changes.at(-1).messages, controller.messages);
});

test("transcript controller handles every event without exposing mutable state", async () => {
  const replies = [
    { mode: "snapshot", stream_id: "s1", revision: 1, messages: [], stale: false },
    {
      mode: "delta",
      stream_id: "s1",
      revision: 2,
      events: [
        {
          kind: "message_added",
          message_id: "m1",
          message: { id: "m1", role: "assistant", text: "draft", state: "streaming" },
        },
        { kind: "message_replaced", message_id: "m1", text: "final", state: "unknown" },
        { kind: "message_completed", message_id: "m1", state: "complete" },
      ],
      stale: false,
    },
  ];
  const controller = createTranscriptController({
    requestTranscript: async () => replies.shift(),
    onChange: () => {},
  });

  await controller.poll();
  await controller.poll();
  const exposed = controller.messages;
  exposed[0].text = "mutated";

  assert.deepEqual(controller.messages, [
    { id: "m1", role: "assistant", text: "final", state: "complete" },
  ]);
});

test("transcript pending and unchanged retain text while stale state can change", async () => {
  const replies = [
    {
      mode: "snapshot",
      stream_id: "s1",
      revision: 1,
      messages: [{ id: "m1", role: "user", text: "Keep", state: "complete" }],
      stale: false,
    },
    { mode: "pending", stream_id: "s1", revision: 1, stale: true, error: "Delayed" },
    { mode: "unchanged" },
  ];
  const controller = createTranscriptController({
    requestTranscript: async () => replies.shift(),
    onChange: () => {},
  });

  await controller.poll();
  assert.equal(await controller.poll(), true);
  assert.equal(await controller.poll(), false);

  assert.equal(controller.messages[0].text, "Keep");
  assert.equal(controller.stale, true);
  assert.equal(controller.error, "Delayed");
});

test("transcript rejects an unknown event without changing state", async () => {
  const replies = [
    {
      mode: "snapshot",
      stream_id: "s1",
      revision: 1,
      messages: [{ id: "m1", role: "assistant", text: "Keep", state: "unknown" }],
      stale: false,
    },
    {
      mode: "delta",
      stream_id: "s1",
      revision: 2,
      events: [{ kind: "mystery", message_id: "m1" }],
      stale: false,
    },
  ];
  const controller = createTranscriptController({
    requestTranscript: async () => replies.shift(),
    onChange: () => {},
  });
  await controller.poll();

  await assert.rejects(controller.poll(), /event/);

  assert.equal(controller.revision, 1);
  assert.equal(controller.messages[0].text, "Keep");
});

test("an older transcript request cannot replace a newer stream", async () => {
  let finishOld;
  let calls = 0;
  const controller = createTranscriptController({
    requestTranscript: () => {
      calls += 1;
      if (calls === 1) return new Promise((resolve) => { finishOld = resolve; });
      return Promise.resolve({
        mode: "snapshot", stream_id: "new", revision: 1,
        messages: [{ id: "new-1", role: "assistant", text: "New", state: "unknown" }],
        stale: false,
      });
    },
    onChange: () => {},
  });

  const oldPoll = controller.poll();
  await controller.poll();
  finishOld({
    mode: "snapshot", stream_id: "old", revision: 9,
    messages: [{ id: "old-1", role: "assistant", text: "Old", state: "unknown" }],
    stale: false,
  });
  assert.equal(await oldPoll, false);

  assert.equal(controller.streamId, "new");
  assert.equal(controller.messages[0].text, "New");
});

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

test("setup rejects an impossible IPv6 status address", () => {
  assert.throws(
    () => phoneUrlForStatus({ bind_ip: "fd7a:115c:a1e0::7", port: 8765 }),
    /IPv4/,
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

test("a delayed older action cannot override an already requested poll image", async () => {
  const requests = [];
  let completeAction;
  const controller = createPhoneController({
    postAction: () => new Promise((resolve) => { completeAction = resolve; }),
    requestImage: async (revision) => { requests.push(revision); },
  });

  await controller.refreshConversation(5);
  const action = controller.performAction({ kind: "scroll", amount: -1 });
  completeAction({ revision: 4 });
  await action;

  assert.deepEqual(requests, [5]);
  assert.equal(controller.imageRevision, 5);
});

test("send does not request a screen image while Screen controls is closed", async () => {
  const requests = [];
  const controller = createPhoneController({
    postAction: async () => ({ revision: 7 }),
    requestImage: async (revision) => { requests.push(revision); },
    shouldRefreshScreen: () => false,
  });
  controller.setComposerText("Please continue");

  await controller.send();

  assert.equal(controller.composerText, "");
  assert.deepEqual(requests, []);
});

test("a successful action refreshes the image only while Screen controls is open", async () => {
  const requests = [];
  let open = false;
  const controller = createPhoneController({
    postAction: async () => ({ revision: 8 }),
    requestImage: async (revision) => { requests.push(revision); },
    shouldRefreshScreen: () => open,
  });

  await controller.performAction({ kind: "scroll", amount: 1 });
  open = true;
  await controller.performAction({ kind: "scroll", amount: 1 });

  assert.deepEqual(requests, [8]);
});

test("closing Screen controls invalidates an in-flight image", async () => {
  let finishImage;
  const controller = createPhoneController({
    postAction: async () => ({ revision: 1 }),
    requestImage: () => new Promise((resolve) => { finishImage = resolve; }),
  });

  const refresh = controller.refreshConversation(3);
  controller.closeScreen();
  finishImage();

  assert.equal(await refresh, false);
  assert.equal(controller.imageRevision, null);
});
