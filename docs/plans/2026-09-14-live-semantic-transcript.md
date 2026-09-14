# Live Semantic Transcript Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a selectable, live-updating Codex transcript that transfers incremental text every second while keeping conversation screenshots strictly on demand.

**Architecture:** A backend-neutral transcript store reconciles semantic observations into snapshots and delta events. Windows supplies hidden/offscreen UI Automation nodes and subtree text, Codex interprets them as messages, a demand-driven sampler updates shared cached state, and the phone applies deltas independently from its existing status/navigator polling. The screenshot remains an interactive fallback inside a collapsed disclosure and is never captured by status or transcript reads.

**Tech Stack:** Python 3.11, `uiautomation`, `ThreadingHTTPServer`, dataclasses, browser JavaScript, HTML/CSS, pytest, Node's built-in test runner

**Spec:** `docs/designs/2026-09-14-live-transcript-design.md`

## Global Constraints

- Support Python 3.11 and keep the existing `uiautomation` and Pillow dependencies; add no runtime dependency.
- Poll transcript state every 1,000 milliseconds while the phone page is visible.
- Never overlap semantic desktop samples.
- Never call desktop pixel capture from status or transcript polling.
- Fetch the conversation screenshot only after Screen controls is opened, manually refreshed, or an action succeeds while Screen controls is open.
- Keep transcript text and reconciliation state in process memory only.
- Preserve plain Unicode text and meaningful line breaks; never render extracted text as HTML.
- Keep Codex selectors in `agents.py`, Windows APIs in `desktop.py`, synchronization in `transcript.py`, transport in `server.py`, and presentation in `web/`.
- Retain the existing bind address, Host/Origin validation, LAN/Tailscale behavior, composer, navigator, click, and scroll controls.
- Use test-driven development and commit after every task.

---

## File structure

- Create `src/codeaway/transcript.py`: transcript domain types, reconciliation, bounded event history, and the demand-driven sampler.
- Modify `src/codeaway/desktop.py`: generic semantic document/subtree-text interface and Windows UI Automation implementation.
- Modify `src/codeaway/agents.py`: optional agent transcript capability and Codex-specific semantic parser.
- Modify `src/codeaway/server.py`: transcript API, sampler integration, and removal of automatic conversation capture.
- Modify `src/codeaway/cli.py`: sampler shutdown with the HTTP server.
- Modify `src/codeaway/web/app.js`: transcript cursor/controller, rendering, polling, and on-demand image lifecycle.
- Modify `src/codeaway/web/index.html`: transcript surface, New text control, and collapsed Screen controls.
- Modify `src/codeaway/web/style.css`: message, stale, follow-latest, disclosure, and screen-control styles.
- Create `tests/test_transcript.py`: reconciliation, history, stale state, and sampler tests.
- Modify `tests/test_desktop.py`: semantic document conversion and Windows-native UI Automation tests.
- Modify `tests/test_agents.py`: Codex semantic transcript parsing fixtures.
- Modify `tests/test_server.py`: transcript endpoint and zero-capture contract tests.
- Modify `tests/test_cli.py`: sampler/application shutdown tests.
- Modify `tests/test_web_ui.cjs`: pure transcript controller and image lifecycle tests.
- Modify `tests/test_web_dom.cjs`: transcript DOM, timers, auto-scroll, and Screen controls tests.
- Modify `README.md`: live transcript behavior and Windows smoke-test instructions.

---

### Task 1: Transcript domain and incremental reconciliation

**Files:**
- Create: `src/codeaway/transcript.py`
- Create: `tests/test_transcript.py`

**Interfaces:**
- Produces: `ObservedMessage`, `TranscriptObservation`, `TranscriptMessage`, `TranscriptEvent`, `TranscriptResult`, and `TranscriptStore`.
- Produces: `TranscriptStore.observe(observation: TranscriptObservation) -> None`.
- Produces: `TranscriptStore.mark_stale(error: str) -> None` and `TranscriptStore.poll(stream_id: str | None, after: int | None) -> TranscriptResult`.
- Consumes: no desktop, agent, server, or browser type.

- [ ] **Step 1: Write failing tests for initial snapshots and suffix-only streaming**

```python
from codeaway.transcript import ObservedMessage, TranscriptObservation, TranscriptStore


def observation(text: str, *, task: str = "task-1", state: str = "streaming"):
    return TranscriptObservation(
        task_identity=task,
        messages=(ObservedMessage("native-1", "assistant", text, state),),
        captured_at="2026-09-14T23:00:00+00:00",
    )


def test_store_returns_snapshot_then_suffix_delta():
    store = TranscriptStore(max_revisions=8)
    store.observe(observation("Hello"))
    first = store.poll(None, None)

    store.observe(observation("Hello world"))
    changed = store.poll(first.stream_id, first.revision)

    assert first.mode == "snapshot"
    assert first.messages[0].text == "Hello"
    assert changed.mode == "delta"
    assert [(event.kind, event.text) for event in changed.events] == [
        ("text_appended", " world")
    ]
```

- [ ] **Step 2: Run the focused tests and confirm the missing module failure**

Run: `uv run pytest tests/test_transcript.py -v`

Expected: FAIL during collection with `ModuleNotFoundError: No module named 'codeaway.transcript'`.

- [ ] **Step 3: Add immutable transcript types and the minimal store**

```python
@dataclass(frozen=True)
class ObservedMessage:
    source_id: str | None
    role: Literal["user", "assistant"]
    text: str
    state: Literal["streaming", "complete", "unknown"] = "unknown"


@dataclass(frozen=True)
class TranscriptObservation:
    task_identity: str
    messages: tuple[ObservedMessage, ...]
    captured_at: str


@dataclass(frozen=True)
class TranscriptMessage:
    id: str
    source_id: str | None
    role: Literal["user", "assistant"]
    text: str
    state: Literal["streaming", "complete", "unknown"]


@dataclass(frozen=True)
class TranscriptEvent:
    kind: Literal[
        "message_added", "text_appended", "message_replaced", "message_completed"
    ]
    message_id: str
    text: str | None = None
    message: TranscriptMessage | None = None


@dataclass(frozen=True)
class TranscriptResult:
    mode: Literal["pending", "snapshot", "delta", "unchanged"]
    stream_id: str | None
    revision: int
    messages: tuple[TranscriptMessage, ...] = ()
    events: tuple[TranscriptEvent, ...] = ()
    captured_at: str | None = None
    stale: bool = False
    error: str | None = None
```

Implement `TranscriptStore` with a lock, an opaque stream ID, monotonic revision,
message counter, and `deque(maxlen=max_revisions)` of revision/event batches.

- [ ] **Step 4: Add failing tests for replacement, completion, virtualization, reset, and bounded history**

```python
def test_store_replaces_non_prefix_text_and_completes_message():
    store = TranscriptStore(max_revisions=8)
    store.observe(observation("draft"))
    cursor = store.poll(None, None)
    store.observe(observation("final", state="complete"))

    result = store.poll(cursor.stream_id, cursor.revision)

    assert [event.kind for event in result.events] == [
        "message_replaced",
        "message_completed",
    ]
    assert result.events[0].text == "final"


def test_store_does_not_delete_a_message_missing_from_virtualized_observation():
    store = TranscriptStore(max_revisions=8)
    store.observe(TranscriptObservation(
        "task-1",
        (
            ObservedMessage("user-1", "user", "Question", "complete"),
            ObservedMessage("assistant-1", "assistant", "Answer", "complete"),
        ),
        "2026-09-14T23:00:00+00:00",
    ))
    store.observe(TranscriptObservation(
        "task-1",
        (ObservedMessage("assistant-1", "assistant", "Answer", "complete"),),
        "2026-09-14T23:00:01+00:00",
    ))

    assert [message.text for message in store.poll(None, None).messages] == [
        "Question",
        "Answer",
    ]


def test_task_change_returns_a_new_stream_snapshot():
    store = TranscriptStore(max_revisions=8)
    store.observe(observation("First task"))
    first = store.poll(None, None)
    store.observe(observation("Second task", task="task-2"))

    second = store.poll(first.stream_id, first.revision)

    assert second.mode == "snapshot"
    assert second.stream_id != first.stream_id
    assert [message.text for message in second.messages] == ["Second task"]
```

For bounded history, observe ten suffixes with `max_revisions=3`, then assert a
cursor older than the retained batches receives `mode == "snapshot"`.

- [ ] **Step 5: Implement reconciliation and bounded delta fallback**

Match by `source_id` first. For observations without a stable source identity,
walk retained messages in order and accept a match only when roles agree and
either text is identical or one text is a prefix of the other. Allocate a new
session message ID for unmatched observations. Never delete retained messages
merely because a subsequent observation omits them.

Emit `text_appended` when new text starts with old text, `message_replaced` when
it does not, and `message_completed` for a transition into `complete`. Increment
the revision once per changed observation and retain the event batch.

- [ ] **Step 6: Add stale and unchanged-result tests**

```python
def test_stale_transition_is_visible_without_erasing_messages():
    store = TranscriptStore(max_revisions=8)
    store.observe(observation("Keep me"))
    cursor = store.poll(None, None)

    store.mark_stale("accessibility read failed")
    stale = store.poll(cursor.stream_id, cursor.revision)

    assert stale.mode == "delta"
    assert stale.events == ()
    assert stale.stale is True
    assert stale.error == "accessibility read failed"
    assert store.poll(None, None).messages[0].text == "Keep me"
```

Also assert that a current cursor returns `mode == "unchanged"` and that a
successful later observation clears stale state with a new revision.

- [ ] **Step 7: Run transcript tests**

Run: `uv run pytest tests/test_transcript.py -v`

Expected: all transcript tests PASS.

- [ ] **Step 8: Commit the transcript core**

```powershell
git add src/codeaway/transcript.py tests/test_transcript.py
git commit -m "feat(transcript): add incremental store"
```

---

### Task 2: Platform-neutral semantic documents and Windows UI Automation

**Files:**
- Modify: `src/codeaway/desktop.py`
- Modify: `tests/test_desktop.py`

**Interfaces:**
- Produces: `SemanticNode` and `SemanticDocument`.
- Produces: `DesktopBackend.semantic_document(window: DesktopWindow) -> SemanticDocument`.
- Produces: `DesktopBackend.semantic_text(window: DesktopWindow, node: SemanticNode) -> str`.
- Preserves: existing `accessibility_tree()` filtering and action behavior unchanged.

- [ ] **Step 1: Write failing adapter tests for hidden/offscreen semantic nodes**

Add a semantic-document fixture to the existing fake native boundary and test:

```python
def test_semantic_document_keeps_hidden_and_offscreen_nodes(native, desktop_window):
    native.semantic_controls = [
        NativeSemanticControl(
            id="message",
            role="GroupControl",
            name="",
            class_name="group flex min-w-0 flex-col",
            depth=4,
            offscreen=True,
            stable_id="runtime:1:2",
            region=None,
        ),
        NativeSemanticControl(
            id="role",
            role="TextControl",
            name="ChatGPT said:",
            class_name="sr-only select-none",
            depth=6,
            offscreen=True,
            stable_id=None,
            region=None,
        ),
    ]

    document = WindowsDesktop(native).semantic_document(desktop_window)

    assert [node.name for node in document.nodes] == ["", "ChatGPT said:"]
    assert document.nodes[0].offscreen is True
    assert document.nodes[1].region is None
```

- [ ] **Step 2: Run the adapter test and verify the missing-interface failure**

Run: `uv run pytest tests/test_desktop.py -k semantic_document -v`

Expected: FAIL because `SemanticDocument` and `semantic_document` are undefined.

- [ ] **Step 3: Add semantic dataclasses and adapter conversion**

```python
@dataclass(frozen=True)
class SemanticNode:
    id: str
    role: str
    name: str
    class_name: str
    depth: int
    offscreen: bool
    stable_id: str | None = None
    region: PixelRegion | None = None


@dataclass(frozen=True)
class SemanticDocument:
    nodes: tuple[SemanticNode, ...]
```

Maintain a semantic-node-to-native-control map separate from the existing action
tree map. Converting a semantic document must not overwrite controls used by
`accessibility_action()`.

- [ ] **Step 4: Write failing native tests for subtree `TextPattern` reads**

Use small fake UI Automation controls and patterns. Assert that the walk includes
an offscreen `sr-only` role marker and that reading the containing message group
returns `"ChatGPT said:\nLive answer"`.

```python
def test_native_semantic_text_reads_the_selected_subtree(native_uia):
    controls = native_uia.semantic_document(42)
    message = next(control for control in controls if control.id == "control-2")

    assert native_uia.semantic_text(42, message.id) == "ChatGPT said:\nLive answer"
```

- [ ] **Step 5: Implement the Windows semantic walk and subtree text**

Walk every UI Automation descendant to depth 40 without the current offscreen or
positive-rectangle filters. Capture name, control type, class, depth,
`IsOffscreen`, optional rectangle, and runtime ID.

For subtree text, obtain the root document's Text pattern and call its underlying
COM `RangeFromChild(control.Element)`, then wrap the returned range and call
`GetText(-1)`. Do not use `uiautomation.TextPattern.RangeFromChild`, because the
installed wrapper references the wrong child element. Convert provider failures
to `AccessibilityUnavailable`.

- [ ] **Step 6: Run desktop tests**

Run: `uv run pytest tests/test_desktop.py -v`

Expected: all desktop tests PASS, including existing navigator/action tests.

- [ ] **Step 7: Commit semantic desktop access**

```powershell
git add src/codeaway/desktop.py tests/test_desktop.py
git commit -m "feat(desktop): expose semantic documents"
```

---

### Task 3: Codex semantic transcript parser

**Files:**
- Modify: `src/codeaway/agents.py`
- Modify: `tests/test_agents.py`

**Interfaces:**
- Consumes: `SemanticDocument`, `SemanticNode`, `ObservedMessage`, and `TranscriptObservation`.
- Produces: optional `AgentBackend.read_transcript(desktop, target) -> TranscriptObservation | None` capability.
- Produces: `CodexAgent.read_transcript(desktop, target) -> TranscriptObservation`.

- [ ] **Step 1: Write a failing Codex fixture test with role markers and live text**

Construct a depth-ordered semantic document containing a
`thread-scroll-container`, one `bg-user-message` group with `You said:`, and one
assistant group with `ChatGPT said:`. Configure the fake desktop's subtree text
by semantic node ID.

```python
def test_codex_reads_user_and_streaming_assistant_messages(codex_target):
    desktop = SemanticDesktop(codex_conversation_document(), {
        "user-group": "You said:\nCan you help?",
        "assistant-group": "ChatGPT said:\nWorking on it",
    })

    result = CodexAgent().read_transcript(desktop, codex_target)

    assert result.task_identity == "SummonLab\0private_3\0runtime:task-7"
    assert [(message.role, message.text, message.state) for message in result.messages] == [
        ("user", "Can you help?", "complete"),
        ("assistant", "Working on it", "streaming"),
    ]
```

- [ ] **Step 2: Run the focused parser test and verify it fails**

Run: `uv run pytest tests/test_agents.py -k transcript -v`

Expected: FAIL because `CodexAgent.read_transcript` is undefined.

- [ ] **Step 3: Add the optional backend method and minimal Codex parser**

Add this method to the protocol:

```python
def read_transcript(
    self, desktop: DesktopBackend, target: AgentTarget
) -> TranscriptObservation | None: ...
```

The server will use `getattr(backend, "read_transcript", None)` so existing and
future backends without the capability remain valid at runtime.

In `CodexAgent`, identify the selected task using the same project/task helpers
as the navigator. Build `task_identity` from project, host, and stable task ID.
Find the `thread-scroll-container`, then find hidden role markers beneath it.
For each marker, walk ancestors by depth and choose the smallest group that owns
exactly that marker. Read that group's semantic text and strip only the leading
role label and surrounding blank lines.

- [ ] **Step 4: Add failing parser tests for normalization and ambiguity**

Cover all of these exact outcomes:

- U+FFFC embedded-object markers are removed without joining neighboring words.
- Repeated accessible button labels inside a message are de-duplicated only when
  they are identical adjacent lines.
- A role marker with no unique owning group is ignored.
- A document with no conversation container raises `AccessibilityUnavailable`.
- A selected task with no stable task ID uses the conversation header plus
  window identity as its session task identity.
- An absent busy marker yields assistant state `unknown`, not `complete`.

- [ ] **Step 5: Implement normalization, conservative ownership, and state inference**

Use the selected task's busy marker or an active stop-control marker to label the
last assistant message `streaming`. Label a non-active final assistant message
`complete` only when Codex exposes a positive idle/completion signal; otherwise
use `unknown`. Use the message group's runtime ID as `source_id` when present.

- [ ] **Step 6: Run agent tests**

Run: `uv run pytest tests/test_agents.py -v`

Expected: all agent tests PASS.

- [ ] **Step 7: Commit the Codex reader**

```powershell
git add src/codeaway/agents.py tests/test_agents.py
git commit -m "feat(codex): parse semantic transcripts"
```

---

### Task 4: Demand-driven transcript sampler

**Files:**
- Modify: `src/codeaway/transcript.py`
- Modify: `tests/test_transcript.py`

**Interfaces:**
- Consumes: `sample: Callable[[], TranscriptObservation | None]`.
- Produces: `TranscriptService.poll(stream_id: str | None, after: int | None) -> TranscriptResult`.
- Produces: `TranscriptService.close() -> None`.
- Uses constants: `SAMPLE_INTERVAL_SECONDS = 1.0`, `ACTIVITY_LEASE_SECONDS = 4.0`, and `MAX_DELTA_REVISIONS = 128`.

- [ ] **Step 1: Write failing deterministic service tests**

Inject a monotonic clock, a fake wake event, and a synchronous thread harness.
Verify that:

```python
def test_poll_activates_sampler_and_initially_reports_pending():
    harness = ServiceHarness()
    service = harness.service(lambda: observation("Live"))

    result = service.poll(None, None)

    assert result.mode == "pending"
    assert harness.wake_count == 1
    assert harness.lease_deadline == 4.0


def test_sample_once_never_overlaps():
    harness = ServiceHarness()
    service = harness.service(lambda: observation("Live"))
    service._sampling_lock.acquire()
    try:
        assert service.sample_once() is False
    finally:
        service._sampling_lock.release()
```

Also test lease renewal, pause after four idle seconds, stale marking after an
exception, recovery after the next good observation, and idempotent `close()`.

- [ ] **Step 2: Run sampler tests and verify missing-service failures**

Run: `uv run pytest tests/test_transcript.py -k service -v`

Expected: FAIL because `TranscriptService` is undefined.

- [ ] **Step 3: Implement sampler lifecycle**

`poll()` renews the activity deadline, wakes the worker, and returns cached store
state immediately. The worker samples immediately after activation, then no more
than once per second while leased. `sample_once()` uses a non-blocking sampling
lock and returns `False` when another sample is running. Provider exceptions call
`store.mark_stale("Transcript updating is delayed.")` and keep the last text.

Use a daemon thread so interpreter shutdown is safe, but still implement
`close()` by setting the stop event, waking the worker, and joining it with a
bounded timeout.

- [ ] **Step 4: Run all transcript tests**

Run: `uv run pytest tests/test_transcript.py -v`

Expected: all transcript store and sampler tests PASS.

- [ ] **Step 5: Commit the sampler**

```powershell
git add src/codeaway/transcript.py tests/test_transcript.py
git commit -m "feat(transcript): add shared sampler"
```

---

### Task 5: Transcript API and pixel-free status polling

**Files:**
- Modify: `src/codeaway/server.py`
- Modify: `src/codeaway/cli.py`
- Modify: `tests/test_server.py`
- Modify: `tests/test_cli.py`

**Interfaces:**
- Consumes: `TranscriptService.poll()` and agent `read_transcript()`.
- Produces: `GET /api/transcript?stream=<opaque>&after=<non-negative integer>`.
- Produces: `Application.close() -> None`.
- Changes: `GET /api/status` becomes metadata-only and never captures pixels.

- [ ] **Step 1: Replace the old pixel-revision status test with a zero-capture test**

```python
def test_status_never_captures_conversation_pixels(app):
    response = app.dispatch("GET", "/api/status", {}, b"")

    assert response.status == 200
    assert app.fake_desktop.capture_calls == []
```

Delete the assertions that status advances its revision when screenshot pixels
change. Preserve action revision assertions in their action tests.

- [ ] **Step 2: Run the focused status test and verify it fails**

Run: `uv run pytest tests/test_server.py::test_status_never_captures_conversation_pixels -v`

Expected: FAIL because `_status()` still invokes `_observe_conversation_locked()`.

- [ ] **Step 3: Remove automatic screenshot observation and caching**

Delete `conversation_png`, `conversation_digest`, `conversation_token`,
`_observe_conversation_locked()`, and `_clear_conversation_cache_locked()`.
Make `_status()` resolve the current target and return `_status_locked()` without
calling `_capture_png()`. Make `/api/screenshot/conversation` capture a fresh PNG
only when that route is requested.

- [ ] **Step 4: Write failing transcript route tests with a fake service**

```python
def test_transcript_route_forwards_cursor_and_serializes_delta(app):
    app.fake_transcript.result = TranscriptResult(
        mode="delta",
        stream_id="stream-a",
        revision=4,
        events=(TranscriptEvent("text_appended", "message-1", text=" new"),),
        captured_at="2026-09-14T23:00:01+00:00",
    )

    response = app.dispatch(
        "GET", "/api/transcript?stream=stream-a&after=3", {}, b""
    )

    assert response.status == 200
    assert app.fake_transcript.poll_calls == [("stream-a", 3)]
    assert payload(response)["events"][0] == {
        "kind": "text_appended",
        "message_id": "message-1",
        "text": " new",
    }
    assert response.headers["Cache-Control"] == "no-store"
```

Add tests for snapshot, pending, unchanged-to-204, omitted cursor, negative or
non-integer `after` returning `400 invalid_request`, repeated query keys returning
`400`, stale metadata, and a backend without `read_transcript` returning an
unavailable pending state without breaking controls.

- [ ] **Step 5: Implement transcript serialization and the sample callback**

Parse the query with `parse_qs(..., keep_blank_values=True)` and reject unknown or
repeated parameters. Format dataclasses with `asdict`, omitting fields whose value
is `None`. Return an empty `Response(204, "application/json; charset=utf-8", b"")`
for `mode == "unchanged"`.

The sampler callback resolves the current target, obtains its backend, looks up
`read_transcript`, reads without holding an HTTP request thread, and verifies the
selection token again before accepting the observation.

- [ ] **Step 6: Add application shutdown tests**

Inject a fake transcript service into `Application`, call `Application.close()`
twice, and assert the service closes once. In `test_cli.py`, assert `start()` calls
`application.close()` both after `_serve=False` and in the `serve_forever()`
`finally` path. Add an `application_factory` seam to `Runtime` if needed rather
than monkeypatching module globals.

- [ ] **Step 7: Run server and CLI tests**

Run: `uv run pytest tests/test_server.py tests/test_cli.py -v`

Expected: all server and CLI tests PASS.

- [ ] **Step 8: Commit HTTP integration**

```powershell
git add src/codeaway/server.py src/codeaway/cli.py tests/test_server.py tests/test_cli.py
git commit -m "feat(server): serve transcript deltas"
```

---

### Task 6: Browser transcript cursor and event application

**Files:**
- Modify: `src/codeaway/web/app.js`
- Modify: `tests/test_web_ui.cjs`

**Interfaces:**
- Produces: `createTranscriptController({ requestTranscript, onChange })`.
- Produces: controller getters `messages`, `stale`, `error`, `streamId`, and `revision`.
- Produces: `poll() -> Promise<boolean>` that returns whether rendered state changed.
- Consumes: snapshot, delta, pending, and 204/unchanged endpoint results.

- [ ] **Step 1: Write failing pure-controller tests for snapshots and deltas**

```javascript
test("transcript controller applies a snapshot and suffix delta", async () => {
  const replies = [
    { mode: "snapshot", stream_id: "s1", revision: 1,
      messages: [{ id: "m1", role: "assistant", text: "Hello", state: "streaming" }],
      stale: false },
    { mode: "delta", stream_id: "s1", revision: 2,
      events: [{ kind: "text_appended", message_id: "m1", text: " world" }],
      stale: false },
  ];
  const changes = [];
  const controller = createTranscriptController({
    requestTranscript: async () => replies.shift(),
    onChange: (state) => changes.push(state),
  });

  await controller.poll();
  await controller.poll();

  assert.equal(controller.messages[0].text, "Hello world");
  assert.deepEqual(changes.at(-1).messages, controller.messages);
});
```

- [ ] **Step 2: Run the focused browser test and verify the missing export failure**

Run: `node --test --test-name-pattern="transcript controller" tests/test_web_ui.cjs`

Expected: FAIL because `createTranscriptController` is not exported.

- [ ] **Step 3: Implement immutable snapshot and event application**

Clone message objects before exposing them. Apply `message_added`,
`text_appended`, `message_replaced`, and `message_completed` by ID. A new
`stream_id` replaces all local messages. A response for an older request token
must not overwrite a newer revision. Never use `innerHTML` in this controller or
its consumers.

- [ ] **Step 4: Add tests for cursor construction and failure recovery**

Assert the first request omits cursor values, the second sends `{stream: "s1",
after: 2}`, unchanged returns `false`, pending retains messages, stale state is
reported without clearing text, unknown event kinds reject the response without
changing state, and an old delayed response cannot override a new stream.

- [ ] **Step 5: Refactor the image controller to make refresh explicitly conditional**

Change `createPhoneController` to consume `shouldRefreshScreen: () => boolean`.
After send or another successful action, call `refreshConversation()` only when
that callback returns true. Add `closeScreen()` to increment the image request
token, set `imageRevision` to `null`, and prevent a late load from becoming
current.

Add pure tests proving a successful send clears the composer without requesting
an image when Screen controls is closed and does request one when open.

- [ ] **Step 6: Run browser controller tests**

Run: `node --test tests/test_web_ui.cjs`

Expected: all controller tests PASS.

- [ ] **Step 7: Commit browser transcript state**

```powershell
git add src/codeaway/web/app.js tests/test_web_ui.cjs
git commit -m "feat(web): apply transcript deltas"
```

---

### Task 7: Phone transcript UI and on-demand Screen controls

**Files:**
- Modify: `src/codeaway/web/index.html`
- Modify: `src/codeaway/web/style.css`
- Modify: `src/codeaway/web/app.js`
- Modify: `tests/test_web_dom.cjs`
- Modify: `tests/test_server.py`

**Interfaces:**
- Consumes: `createTranscriptController` and `createPhoneController` from Task 6.
- Produces DOM IDs: `transcript`, `transcript-messages`, `transcript-stale`, `transcript-new`, `screen-controls`, and `screen-refresh`.
- Preserves DOM order: status, navigator, conversation, composer.

- [ ] **Step 1: Write failing markup tests for the transcript and collapsed disclosure**

In `tests/test_server.py`, assert the served page contains the new IDs, that
`<details id="screen-controls">` does not have `open`, and that the conversation
image has no `src` attribute. Keep the existing status/navigator/conversation/
composer ordering assertion.

- [ ] **Step 2: Run the markup test and verify it fails**

Run: `uv run pytest tests/test_server.py -k "phone_page or workspace_page" -v`

Expected: FAIL because the existing page contains only the always-visible image.

- [ ] **Step 3: Replace the conversation markup**

Use this structure inside the existing Conversation section:

```html
<p id="conversation-message" class="message" role="status"></p>
<p id="transcript-stale" class="transcript-stale" role="status" hidden></p>
<div id="transcript" class="transcript" tabindex="0" aria-label="Agent transcript">
  <div id="transcript-messages" class="transcript-messages"></div>
</div>
<button id="transcript-new" class="transcript-new" type="button" hidden>New text ↓</button>
<details id="screen-controls" class="screen-controls">
  <summary>Screen controls</summary>
  <div class="screen-toolbar">
    <button id="screen-refresh" type="button">Refresh screen</button>
  </div>
  <img id="conversation-image" alt="Current agent conversation">
</details>
```

- [ ] **Step 4: Write failing DOM tests for one-second polling and safe rendering**

Extend the fake DOM with `scrollTop`, `scrollHeight`, `clientHeight`, and a
recorded interval delay. Return one transcript snapshot from the fake fetcher.

Assert that:

- a 1,000 ms interval is registered separately from the 2,000 ms workspace poll;
- messages are created with `textContent` and role classes;
- no `/api/screenshot/conversation` request occurs at initialization;
- a transcript fetch includes the latest stream and revision after its first
  response; and
- hiding the document clears both intervals, while showing it restarts both.

- [ ] **Step 5: Implement transcript requests, rendering, and polling**

Add a fetch helper that treats status 204 as `{mode: "unchanged"}` before trying
to parse JSON. Maintain one in-flight transcript poll and one request token.
Render each message as an `<article>` containing a visible role label and a
`<pre>`-style text element populated through `textContent`.

Determine follow-latest before rendering with:

```javascript
function isNearBottom(element) {
  return element.scrollHeight - element.scrollTop - element.clientHeight <= 64;
}
```

If true, set `scrollTop = scrollHeight` after rendering. Otherwise keep the old
position and show `transcript-new`; its click scrolls to the bottom and hides it.

- [ ] **Step 6: Add failing Screen controls lifecycle tests**

Assert that opening the disclosure requests exactly one image, Refresh requests
one more, closing calls `phone.closeScreen()`, and an action refreshes the image
only while `details.open === true`. Assert that transcript updates never request
an image.

- [ ] **Step 7: Implement on-demand image wiring**

Listen to the details `toggle` event. On open, call
`phone.refreshConversation(state.revision ?? 0, true)`. On close, call
`phone.closeScreen()` and leave the image without a usable current revision.
Wire Refresh to the same explicit refresh path. Pass
`() => elements.screenControls.open` as `shouldRefreshScreen` to the phone
controller.

- [ ] **Step 8: Style the transcript and disclosure in light and dark modes**

Use existing color variables. Give user and assistant messages distinct subtle
surface treatments, `white-space: pre-wrap`, `overflow-wrap: anywhere`, and a
comfortable mobile line height. Keep the transcript independently scrollable
with a bounded height. Style `transcript-new` as a compact sticky control at the
lower edge without covering text. Preserve reduced-motion behavior.

- [ ] **Step 9: Run all browser and markup tests**

Run: `node --test tests/test_web_ui.cjs tests/test_web_dom.cjs tests/test_web_theme.cjs`

Run: `uv run pytest tests/test_server.py -v`

Expected: all browser and server tests PASS.

- [ ] **Step 10: Commit the phone experience**

```powershell
git add src/codeaway/web/index.html src/codeaway/web/style.css src/codeaway/web/app.js tests/test_web_dom.cjs tests/test_server.py
git commit -m "feat(web): render live transcripts"
```

---

### Task 8: Documentation, live smoke test, and full verification

**Files:**
- Modify: `README.md`
- Modify: `tests/test_package.py` only if the packaged-resource assertions need an explicit new static filename; no new static filename is expected.

**Interfaces:**
- Documents: text-first behavior, Screen controls data behavior, supported platform boundary, and exact Windows smoke test.
- Verifies: distributable package still includes all web assets.

- [ ] **Step 1: Update the README feature and usage sections**

State that Windows Codex offers a live selectable transcript, refreshed by
incremental one-second polling. Explain that CodeAway does not save transcript
text and that only the currently exposed/observed conversation is available.
Document that Screen controls is collapsed by default and that screenshots use
data only when opened or refreshed.

- [ ] **Step 2: Add the live Windows smoke test to the README**

Document these exact steps:

1. Start CodeAway with `uvx codeaway --ip <laptop-ip>` and keep Codex visible.
2. Open the printed URL on the phone and select a Codex task.
3. Send a prompt that produces a multi-paragraph response.
4. Confirm assistant text grows on the phone within successive one-second polls.
5. Select and copy text on the phone.
6. Scroll the transcript upward, confirm it does not jump, then press New text.
7. Confirm browser network activity contains no PNG request while Screen controls
   is closed.
8. Open Screen controls, refresh once, then test tap and swipe interaction.
9. Close Screen controls and confirm later transcript updates issue no PNG request.

- [ ] **Step 3: Run formatting and placeholder checks**

Run: `git diff --check`

Run: `rg -n "TBD|TODO|FIXME|placeholder" src tests README.md`

Expected: `git diff --check` exits 0. Every search hit, if any, belongs to an
intentional existing test fixture rather than unfinished transcript work.

- [ ] **Step 4: Run the complete Python suite**

Run: `uv run pytest -v`

Expected: all Python tests PASS.

- [ ] **Step 5: Run the complete browser suite**

Run: `node --test tests/test_web_ui.cjs tests/test_web_dom.cjs tests/test_web_theme.cjs`

Expected: all browser tests PASS.

- [ ] **Step 6: Build and inspect both distributions**

Run: `uv build`

Run: `uv run --with twine twine check dist/*`

Expected: wheel and source distribution build successfully and `twine check`
reports `PASSED` for both current artifacts.

- [ ] **Step 7: Perform the documented Windows smoke test**

Run CodeAway on the configured Tailscale address, follow all nine README steps,
and record any provider-specific mismatch before declaring completion. Do not
publish a package version as part of this task.

- [ ] **Step 8: Commit documentation and verification fixes**

```powershell
git add README.md tests/test_package.py
git commit -m "docs: explain live transcript controls"
```

- [ ] **Step 9: Review the complete branch and push**

Run: `git status --short`

Run: `git log --oneline --decorate -10`

Expected: no uncommitted files and one focused commit per task. Review the full
diff against `docs/designs/2026-09-14-live-transcript-design.md`, then push the
approved commits to `origin main`.
