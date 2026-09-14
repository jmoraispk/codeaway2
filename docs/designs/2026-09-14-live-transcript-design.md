# Live Semantic Transcript Design

Date: 2026-09-14

## Context

CodeAway currently treats the calibrated conversation screenshot as both the
reading surface and the interaction surface. The phone polls `/api/status`, and
the status handler captures and hashes the conversation image to detect visual
changes. This repeatedly captures pixels even when the user only wants to read
text, consumes more network data when the image changes, and produces a less
usable reading experience than selectable text.

The Codex desktop application exposes its mounted conversation through Windows
UI Automation. The accessible document includes user and assistant role markers
and the actual message text, including a response while it is being generated.
Codex virtualizes long conversations, so the accessible document may not contain
the entire historical thread at every instant.

CodeAway will add a semantic transcript channel for reading and retain the
screenshot only as an on-demand visual control surface.

## Goals

- Show a scrollable, selectable transcript on the phone.
- Reflect partial assistant output approximately once per second.
- Transfer only transcript changes after the initial snapshot.
- Perform no screenshot capture or image transfer during status or transcript
  polling.
- Preserve the existing screenshot-based click and scroll controls behind an
  explicit toggle.
- Keep Windows mechanics, Codex parsing, synchronization, and HTTP/UI concerns
  in separate components.
- Define a backend-neutral transcript contract that other agent applications can
  implement later.

## Non-goals

- Recovering conversation history that Codex has never exposed during the
  current CodeAway session.
- Persisting transcript content to disk.
- Reconstructing Markdown or rich Codex cards exactly from accessibility text.
- Extracting text from images or tool screenshots with OCR.
- Adding WebSockets, Server-Sent Events, authentication, or a new network path.
- Implementing semantic transcript readers for Cursor or Claude in this change.

## Considered approaches

### Extract on every phone request

Each `/api/transcript` request could read UI Automation directly. This is small,
but concurrent clients would repeat an expensive desktop read and a slow provider
would occupy HTTP request threads.

### Shared sampler with incremental polling

A single service samples the selected Codex conversation, reconciles it with the
previous sample, and publishes a cached revision. Phones poll that cache with a
cursor. This provides one extraction cost regardless of client count and keeps
HTTP responses fast.

### Server push

Server-Sent Events or WebSockets could push every transcript change. This saves
empty polling requests but adds connection recovery and mobile backgrounding
behavior that the current application does not need.

## Decision

Use a shared, serialized sampler and ordinary incremental HTTP polling. The
browser polls once per second. The sampler never overlaps its own reads and
serves its last good result while a subsequent read is in progress.

The sampler is demand-driven. A transcript request activates it immediately and
renews a short activity lease. When no visible client has polled for several
seconds, sampling pauses. The browser already stops polling while its page is
hidden and resumes when visible.

## Architecture

### Desktop semantic access

`desktop.py` will expose a platform-neutral semantic-document operation in
addition to the existing interaction-oriented accessibility tree. It must:

- include hidden, unbounded, and offscreen accessible nodes;
- preserve parent/depth order and stable runtime identities when available; and
- obtain the text represented by a selected accessible subtree.

The Windows implementation uses UI Automation and `TextPattern`. The public
desktop contract contains no Codex selectors. The existing accessibility tree
used for navigator actions remains unchanged so that newly included hidden nodes
cannot alter click targeting.

### Agent transcript reader

`AgentBackend` gains an optional transcript-reading capability. `CodexAgent`
implements it by locating the Codex conversation container, finding the hidden
`You said:` and `ChatGPT said:` role markers, selecting the smallest associated
message groups, and reading their subtree text.

The reader returns an ordered semantic observation containing:

- an identity for the selected task;
- observed messages with role, text, and the best available stable identity;
- whether the final assistant message is streaming, complete, or unknown; and
- the capture time.

Selectors and normalization rules live in `agents.py`; Windows API calls do not.
An agent backend that cannot provide a transcript reports the capability as
unavailable while retaining all screenshot and input behavior.

### Transcript service

A focused `transcript.py` module owns:

- sampler activation and serialized one-second reads;
- the last good observation for the active task;
- reconciliation of changing observations into stable session message IDs;
- monotonic stream revisions;
- a bounded history of delta events; and
- stale and unavailable state.

It has no knowledge of Codex class names or Windows UI Automation. The service is
owned by the application and shut down with the HTTP server.

### HTTP server

`server.py` exposes the transcript endpoint and sampler lifecycle. It no longer
captures the conversation from `/api/status`. Screenshot routes remain
on-demand and independent from transcript revisions.

### Browser

The browser maintains a local transcript model, applies ordered events, renders
selectable messages, and polls only while visible. It owns the on-demand screen
toggle and never assigns a screenshot URL while that toggle is closed.

## Transcript model

Each transcript stream represents one selected agent task. A task change creates
a new opaque `stream_id` and starts revision numbering for that stream. Message
IDs are stable only within the running CodeAway process.

```json
{
  "id": "message-7",
  "role": "assistant",
  "text": "The response so far...",
  "state": "streaming"
}
```

`role` is `user` or `assistant`. `state` is `streaming`, `complete`, or
`unknown`. Text is plain Unicode with meaningful line breaks preserved. The
phone renders it as text rather than HTML.

Runtime IDs are preferred when matching observations. If they are absent or
unstable, reconciliation uses role, order, unchanged prefixes, and neighboring
messages. Ambiguity must cause a safe replacement or fresh snapshot, never an
invented merge.

Messages already observed for the current stream remain in memory when Codex
virtualizes them out of the current accessible tree. A task change resets the
active stream. Returning to a previously visited task starts with the snapshot
currently exposed by Codex; cross-task transcript retention and disk persistence
are deferred.

## Delta protocol

The browser requests:

```text
GET /api/transcript?stream=<stream_id>&after=<revision>
```

Both query values are omitted for the initial request. Responses use
`Cache-Control: no-store`.

### Initial or reset snapshot

The server returns `200 OK` with the complete retained transcript when the
client has no cursor, names a different stream, or has fallen behind the bounded
event history.

```json
{
  "mode": "snapshot",
  "stream_id": "stream-a1",
  "revision": 12,
  "messages": [],
  "captured_at": "2026-09-14T23:00:00Z",
  "stale": false
}
```

### Incremental update

The server returns `200 OK` with events after the requested revision:

```json
{
  "mode": "delta",
  "stream_id": "stream-a1",
  "revision": 15,
  "events": [
    {"kind": "text_appended", "message_id": "message-7", "text": " more"},
    {"kind": "message_completed", "message_id": "message-7"}
  ],
  "captured_at": "2026-09-14T23:00:01Z",
  "stale": false
}
```

Supported events are:

- `message_added`, containing the full message;
- `text_appended`, containing only a new suffix;
- `message_replaced`, containing the full corrected message and state; and
- `message_completed`.

When the stream is unchanged, the server returns `204 No Content`. While the
first demand-driven sample is pending, it returns `200 OK` with `mode: "pending"`
and the browser retries normally.

## Phone behavior

The Conversation panel becomes a vertically scrollable transcript with visually
distinct user and assistant messages. It preserves line breaks and permits text
selection.

When the user is already near the bottom, appended text remains in view. Once the
user scrolls upward, incoming updates do not change their position. A compact
`New text` control appears and scrolls to the newest content when pressed.

The transcript poll interval is one second. Navigator and general status polling
may retain their existing slower interval. Requests are serialized so a slow
poll cannot overwrite a newer result. Switching the selected task clears the
rendered stream only when its replacement snapshot is ready, avoiding a blank
flash.

The composer remains after the complete Conversation section.

## On-demand screen controls

The existing conversation screenshot moves into a collapsed `Screen controls`
disclosure below the text transcript.

- Page load and transcript polling never request an image.
- Opening the disclosure requests one current conversation screenshot.
- A visible Refresh control requests another screenshot.
- Click, scroll, navigation, and send actions refresh the image only when the
  disclosure is open.
- Closing it stops image refreshes and invalidates any pending visual refresh.
- New assistant text does not refresh the image.

The image retains direct tapping and swipe-to-scroll. If semantic extraction is
unavailable, the disclosure remains usable as the fallback rather than opening
automatically.

## Failure handling

- A temporary accessibility failure preserves the last good transcript and
  reports `stale: true` with an `Updating delayed` indication.
- An ambiguous sample preserves known messages and waits for a coherent sample;
  it does not delete or combine uncertain content.
- Text that is no longer a prefix of a streaming message produces
  `message_replaced`.
- Loss of the selected window marks the transcript unavailable without blocking
  navigator, composer, or screenshot controls.
- A slow semantic read delays only the next transcript revision. No overlapping
  read is started.
- Client disconnects remain benign and do not print server tracebacks.

## Performance and privacy

Unchanged polls have no body. Normal streaming updates transfer only the added
Unicode suffix plus a small JSON envelope. The semantic sampler reads one desktop
document per interval regardless of the number of phones.

Transcript text and reconciliation state remain in process memory and are not
written to configuration or cache files. The feature uses the existing CodeAway
bind address, Host/Origin checks, and user-selected LAN or Tailscale path.

## Verification

Automated tests will cover:

- Windows semantic-document conversion, hidden/offscreen nodes, and subtree text;
- Codex fixtures with role markers, streaming messages, and virtualized history;
- stable message reconciliation, suffix appends, replacements, completion,
  ambiguity, stale reads, and task resets;
- snapshot, delta, pending, unchanged, stale, and unavailable API responses;
- sampler serialization, activity leases, and bounded delta fallback;
- transcript rendering and ordered event application;
- bottom-following versus a manually scrolled transcript;
- browser visibility polling and stale-request ordering;
- zero desktop capture calls during status and transcript polling; and
- screenshot requests only after opening or explicitly refreshing Screen
  controls.

The existing Python 3.11 and browser suites remain required. A Windows smoke test
will select a Codex task, observe a live response growing on the phone within the
one-second polling cadence, verify selectable text, close Screen controls and
confirm no screenshot requests, then open it and exercise tap, scroll, and manual
refresh.

## Acceptance criteria

1. A visible phone page shows the selected Codex transcript and updates a live
   assistant message through incremental one-second polls.
2. Unchanged polls return no response body and do not read or transfer pixels.
3. Scrolling upward remains stable while new text arrives and exposes a clear way
   to return to the newest message.
4. Conversation screenshots are fetched only while explicitly requested through
   Screen controls.
5. Temporary extraction failures retain the last good text and do not interrupt
   sending, navigation, or screenshot controls.
6. Backend-specific parsing does not leak into the HTTP server or platform
   desktop layer.
