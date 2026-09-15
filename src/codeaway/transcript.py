from __future__ import annotations

import threading
import time
from collections import deque
from dataclasses import dataclass
from typing import Any, Callable, Literal
from uuid import uuid4


MessageRole = Literal["user", "assistant"]
MessageState = Literal["streaming", "complete", "unknown"]
EventKind = Literal[
    "message_added",
    "text_appended",
    "message_replaced",
    "message_completed",
]
ResultMode = Literal["pending", "snapshot", "delta", "unchanged"]
SAMPLE_INTERVAL_SECONDS = 1.0
ACTIVITY_LEASE_SECONDS = 4.0
MAX_DELTA_REVISIONS = 128


@dataclass(frozen=True)
class ObservedMessage:
    source_id: str | None
    role: MessageRole
    text: str
    state: MessageState = "unknown"


@dataclass(frozen=True)
class TranscriptObservation:
    task_identity: str
    messages: tuple[ObservedMessage, ...]
    captured_at: str


@dataclass(frozen=True)
class TranscriptMessage:
    id: str
    source_id: str | None
    role: MessageRole
    text: str
    state: MessageState


@dataclass(frozen=True)
class TranscriptEvent:
    kind: EventKind
    message_id: str
    text: str | None = None
    message: TranscriptMessage | None = None
    state: MessageState | None = None


@dataclass(frozen=True)
class TranscriptResult:
    mode: ResultMode
    stream_id: str | None
    revision: int
    messages: tuple[TranscriptMessage, ...] = ()
    events: tuple[TranscriptEvent, ...] = ()
    captured_at: str | None = None
    stale: bool = False
    error: str | None = None


class TranscriptStore:
    def __init__(self, max_revisions: int = 128) -> None:
        self._lock = threading.RLock()
        self._history: deque[tuple[int, tuple[TranscriptEvent, ...]]] = deque(
            maxlen=max_revisions
        )
        self._stream_id: str | None = None
        self._task_identity: str | None = None
        self._revision = 0
        self._message_counter = 0
        self._messages: list[TranscriptMessage] = []
        self._captured_at: str | None = None
        self._stale = False
        self._error: str | None = None

    def _new_message(self, observed: ObservedMessage) -> TranscriptMessage:
        self._message_counter += 1
        return TranscriptMessage(
            id=f"message-{self._message_counter}",
            source_id=observed.source_id,
            role=observed.role,
            text=observed.text,
            state=observed.state,
        )

    def observe(self, observation: TranscriptObservation) -> None:
        with self._lock:
            if observation.task_identity != self._task_identity:
                self._task_identity = observation.task_identity
                self._stream_id = f"stream-{uuid4().hex}"
                self._revision = 1
                self._history.clear()
                self._messages = [
                    self._new_message(message) for message in observation.messages
                ]
                self._captured_at = observation.captured_at
                self._stale = False
                self._error = None
                return

            events: list[TranscriptEvent] = []
            matched_indexes: set[int] = set()
            for observed in observation.messages:
                index = self._match(observed, matched_indexes)
                if index is None:
                    message = self._new_message(observed)
                    self._messages.append(message)
                    matched_indexes.add(len(self._messages) - 1)
                    events.append(
                        TranscriptEvent(
                            "message_added", message.id, message=message
                        )
                    )
                    continue
                matched_indexes.add(index)
                current = self._messages[index]
                updated = TranscriptMessage(
                    current.id,
                    observed.source_id or current.source_id,
                    observed.role,
                    observed.text,
                    observed.state,
                )
                if observed.text != current.text and observed.text.startswith(current.text):
                    suffix = observed.text[len(current.text) :]
                    events.append(TranscriptEvent("text_appended", current.id, text=suffix))
                elif observed.text != current.text:
                    events.append(
                        TranscriptEvent(
                            "message_replaced",
                            current.id,
                            text=observed.text,
                            message=updated,
                            state=observed.state,
                        )
                    )
                if (
                    current.state == "streaming"
                    and observed.state != "streaming"
                ) or (
                    current.state != "complete" and observed.state == "complete"
                ):
                    events.append(
                        TranscriptEvent(
                            "message_completed", current.id, state=observed.state
                        )
                    )
                if updated != current:
                    self._messages[index] = updated

            active_streaming_id = next(
                (
                    self._messages[index].id
                    for index in sorted(matched_indexes, reverse=True)
                    if self._messages[index].state == "streaming"
                ),
                None,
            )
            completed_ids = {
                event.message_id
                for event in events
                if event.kind == "message_completed"
            }
            for index, current in enumerate(self._messages):
                if (
                    current.state != "streaming"
                    or current.id == active_streaming_id
                ):
                    continue
                self._messages[index] = TranscriptMessage(
                    current.id,
                    current.source_id,
                    current.role,
                    current.text,
                    "unknown",
                )
                if current.id not in completed_ids:
                    events.append(
                        TranscriptEvent(
                            "message_completed", current.id, state="unknown"
                        )
                    )
            self._captured_at = observation.captured_at
            recovered = self._stale
            self._stale = False
            self._error = None
            if events or recovered:
                self._revision += 1
                self._history.append((self._revision, tuple(events)))

    def _match(
        self,
        observed: ObservedMessage,
        matched_indexes: set[int],
    ) -> int | None:
        if observed.source_id is not None:
            exact = [
                index
                for index, current in enumerate(self._messages)
                if index not in matched_indexes
                and current.role == observed.role
                and current.source_id == observed.source_id
            ]
            if len(exact) == 1:
                return exact[0]
        compatible = [
            index
            for index, current in enumerate(self._messages)
            if index not in matched_indexes
            and current.role == observed.role
            and (
                current.text == observed.text
                or current.text.startswith(observed.text)
                or observed.text.startswith(current.text)
            )
        ]
        return compatible[0] if len(compatible) == 1 else None

    def mark_stale(self, error: str) -> None:
        with self._lock:
            if self._stale and self._error == error:
                return
            self._stale = True
            self._error = error
            if self._stream_id is not None:
                self._revision += 1
                self._history.append((self._revision, ()))

    def poll(self, stream_id: str | None, after: int | None) -> TranscriptResult:
        with self._lock:
            if self._stream_id is None:
                return TranscriptResult(
                    "pending",
                    None,
                    0,
                    stale=self._stale,
                    error=self._error,
                )
            if stream_id != self._stream_id or after is None:
                return TranscriptResult(
                    "snapshot",
                    self._stream_id,
                    self._revision,
                    messages=tuple(self._messages),
                    captured_at=self._captured_at,
                    stale=self._stale,
                    error=self._error,
                )
            if after == self._revision:
                return TranscriptResult(
                    "unchanged",
                    self._stream_id,
                    self._revision,
                    captured_at=self._captured_at,
                    stale=self._stale,
                    error=self._error,
                )
            if (
                after < 0
                or after > self._revision
                or not self._history
                or self._history[0][0] > after + 1
            ):
                return TranscriptResult(
                    "snapshot",
                    self._stream_id,
                    self._revision,
                    messages=tuple(self._messages),
                    captured_at=self._captured_at,
                    stale=self._stale,
                    error=self._error,
                )
            events = tuple(
                event
                for revision, batch in self._history
                if revision > after
                for event in batch
            )
            return TranscriptResult(
                "delta",
                self._stream_id,
                self._revision,
                events=events,
                captured_at=self._captured_at,
                stale=self._stale,
                error=self._error,
            )


class TranscriptService:
    def __init__(
        self,
        sample: Callable[[], TranscriptObservation | None],
        *,
        store: TranscriptStore | None = None,
        clock: Callable[[], float] = time.monotonic,
        wake_event: Any | None = None,
        stop_event: Any | None = None,
        thread_factory: Callable[..., Any] = threading.Thread,
    ) -> None:
        self._sample = sample
        self._store = store or TranscriptStore(MAX_DELTA_REVISIONS)
        self._clock = clock
        self._wake_event = wake_event or threading.Event()
        self._stop_event = stop_event or threading.Event()
        self._state_lock = threading.Lock()
        self._sampling_lock = threading.Lock()
        self._lease_deadline = 0.0
        self._closed = False
        self._thread = thread_factory(
            target=self._run,
            name="codeaway-transcript",
            daemon=True,
        )
        self._thread.start()

    def poll(self, stream_id: str | None, after: int | None) -> TranscriptResult:
        with self._state_lock:
            if not self._closed:
                self._lease_deadline = self._clock() + ACTIVITY_LEASE_SECONDS
                self._wake_event.set()
        return self._store.poll(stream_id, after)

    def _lease_active(self) -> bool:
        with self._state_lock:
            return not self._closed and self._clock() < self._lease_deadline

    def sample_once(self) -> bool:
        if not self._sampling_lock.acquire(blocking=False):
            return False
        try:
            try:
                observation = self._sample()
                if observation is not None:
                    self._store.observe(observation)
            except Exception:
                self._store.mark_stale("Transcript updating is delayed.")
            return True
        finally:
            self._sampling_lock.release()

    def _run(self) -> None:
        next_sample_at = 0.0
        was_active = False
        while not self._stop_event.is_set():
            now = self._clock()
            with self._state_lock:
                lease_deadline = self._lease_deadline
            active = now < lease_deadline
            if not active:
                was_active = False
                self._wake_event.wait()
                self._wake_event.clear()
                continue
            if not was_active:
                next_sample_at = now
                was_active = True
            if now >= next_sample_at:
                self.sample_once()
                next_sample_at = self._clock() + SAMPLE_INTERVAL_SECONDS
                now = self._clock()
            wait_seconds = min(next_sample_at - now, lease_deadline - now)
            if wait_seconds <= 0:
                continue
            self._wake_event.wait(wait_seconds)
            self._wake_event.clear()

    def close(self) -> None:
        with self._state_lock:
            if self._closed:
                return
            self._closed = True
            self._stop_event.set()
            self._wake_event.set()
        self._thread.join(timeout=2.0)
