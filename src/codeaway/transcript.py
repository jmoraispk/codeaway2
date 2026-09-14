from __future__ import annotations

import threading
from collections import deque
from dataclasses import dataclass
from typing import Literal
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
                if current.state != "complete" and observed.state == "complete":
                    events.append(
                        TranscriptEvent(
                            "message_completed", current.id, state="complete"
                        )
                    )
                if updated != current:
                    self._messages[index] = updated
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
