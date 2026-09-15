import threading

from codeaway.transcript import (
    ObservedMessage,
    TranscriptObservation,
    TranscriptService,
    TranscriptStore,
)


class FakeClock:
    def __init__(self):
        self.value = 0.0

    def __call__(self):
        return self.value


class FakeEvent:
    def __init__(self):
        self.set_count = 0
        self._set = False

    def set(self):
        self.set_count += 1
        self._set = True

    def clear(self):
        self._set = False

    def is_set(self):
        return self._set

    def wait(self, timeout=None):
        del timeout
        return self._set


class FakeThread:
    def __init__(self, *, target, name, daemon):
        self.target = target
        self.name = name
        self.daemon = daemon
        self.start_count = 0
        self.join_count = 0

    def start(self):
        self.start_count += 1

    def join(self, timeout=None):
        del timeout
        self.join_count += 1


class ServiceHarness:
    def __init__(self):
        self.clock = FakeClock()
        self.wake = FakeEvent()
        self.stop = FakeEvent()
        self.thread = None

    def thread_factory(self, **kwargs):
        self.thread = FakeThread(**kwargs)
        return self.thread

    def service(self, sample):
        return TranscriptService(
            sample,
            clock=self.clock,
            wake_event=self.wake,
            stop_event=self.stop,
            thread_factory=self.thread_factory,
        )


def observation(
    text: str,
    *,
    task: str = "task-1",
    state: str = "streaming",
) -> TranscriptObservation:
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


def test_store_publishes_when_a_streaming_message_becomes_unknown():
    store = TranscriptStore(max_revisions=8)
    store.observe(observation("Finished text", state="streaming"))
    cursor = store.poll(None, None)

    store.observe(observation("Finished text", state="unknown"))
    result = store.poll(cursor.stream_id, cursor.revision)

    assert result.mode == "delta"
    assert [(event.kind, event.state) for event in result.events] == [
        ("message_completed", "unknown")
    ]


def test_store_demotes_a_virtualized_streaming_message_when_a_new_one_appears():
    store = TranscriptStore(max_revisions=8)
    store.observe(observation("Older response", state="streaming"))
    cursor = store.poll(None, None)

    store.observe(
        TranscriptObservation(
            "task-1",
            (ObservedMessage("native-2", "assistant", "New response", "streaming"),),
            "2026-09-14T23:00:01+00:00",
        )
    )
    result = store.poll(cursor.stream_id, cursor.revision)

    snapshot = store.poll(None, None)
    assert [message.state for message in snapshot.messages] == ["unknown", "streaming"]
    assert [(event.kind, event.state) for event in result.events] == [
        ("message_added", None),
        ("message_completed", "unknown"),
    ]


def test_store_does_not_delete_messages_omitted_by_virtualization():
    store = TranscriptStore(max_revisions=8)
    store.observe(
        TranscriptObservation(
            "task-1",
            (
                ObservedMessage("user-1", "user", "Question", "complete"),
                ObservedMessage("assistant-1", "assistant", "Answer", "complete"),
            ),
            "2026-09-14T23:00:00+00:00",
        )
    )

    store.observe(
        TranscriptObservation(
            "task-1",
            (ObservedMessage("assistant-1", "assistant", "Answer", "complete"),),
            "2026-09-14T23:00:01+00:00",
        )
    )

    assert [message.text for message in store.poll(None, None).messages] == [
        "Question",
        "Answer",
    ]


def test_store_adds_a_new_message_after_retained_virtualized_history():
    store = TranscriptStore(max_revisions=8)
    store.observe(
        TranscriptObservation(
            "task-1",
            (ObservedMessage("user-1", "user", "Question", "complete"),),
            "2026-09-14T23:00:00+00:00",
        )
    )
    cursor = store.poll(None, None)

    store.observe(
        TranscriptObservation(
            "task-1",
            (ObservedMessage("assistant-1", "assistant", "Answer", "streaming"),),
            "2026-09-14T23:00:01+00:00",
        )
    )
    result = store.poll(cursor.stream_id, cursor.revision)

    assert [message.text for message in store.poll(None, None).messages] == [
        "Question",
        "Answer",
    ]
    assert result.events[0].kind == "message_added"
    assert result.events[0].message is not None
    assert result.events[0].message.text == "Answer"


def test_store_matches_unstable_source_by_role_and_prefix():
    store = TranscriptStore(max_revisions=8)
    store.observe(
        TranscriptObservation(
            "task-1",
            (ObservedMessage(None, "assistant", "Working", "streaming"),),
            "2026-09-14T23:00:00+00:00",
        )
    )
    cursor = store.poll(None, None)

    store.observe(
        TranscriptObservation(
            "task-1",
            (ObservedMessage(None, "assistant", "Working now", "streaming"),),
            "2026-09-14T23:00:01+00:00",
        )
    )

    result = store.poll(cursor.stream_id, cursor.revision)
    assert [(event.kind, event.text) for event in result.events] == [
        ("text_appended", " now")
    ]
    assert len(store.poll(None, None).messages) == 1


def test_task_change_returns_a_new_stream_snapshot():
    store = TranscriptStore(max_revisions=8)
    store.observe(observation("First task"))
    first = store.poll(None, None)

    store.observe(observation("Second task", task="task-2"))
    second = store.poll(first.stream_id, first.revision)

    assert second.mode == "snapshot"
    assert second.stream_id != first.stream_id
    assert [message.text for message in second.messages] == ["Second task"]


def test_cursor_older_than_bounded_history_receives_snapshot():
    store = TranscriptStore(max_revisions=3)
    store.observe(observation("0"))
    old = store.poll(None, None)
    for suffix in range(1, 6):
        store.observe(observation("0" + "x" * suffix))

    result = store.poll(old.stream_id, old.revision)

    assert result.mode == "snapshot"
    assert result.messages[0].text == "0xxxxx"


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


def test_successful_observation_clears_stale_state_with_new_revision():
    store = TranscriptStore(max_revisions=8)
    store.observe(observation("Keep me"))
    first = store.poll(None, None)
    store.mark_stale("accessibility read failed")
    stale = store.poll(first.stream_id, first.revision)

    store.observe(observation("Keep me"))
    recovered = store.poll(stale.stream_id, stale.revision)

    assert recovered.mode == "delta"
    assert recovered.stale is False
    assert recovered.error is None


def test_current_cursor_returns_unchanged():
    store = TranscriptStore(max_revisions=8)
    store.observe(observation("Stable"))
    first = store.poll(None, None)

    unchanged = store.poll(first.stream_id, first.revision)

    assert unchanged.mode == "unchanged"
    assert unchanged.messages == ()
    assert unchanged.events == ()


def test_service_poll_activates_sampler_and_initially_reports_pending():
    harness = ServiceHarness()
    service = harness.service(lambda: observation("Live"))

    result = service.poll(None, None)

    assert result.mode == "pending"
    assert harness.wake.set_count == 1
    assert service._lease_deadline == 4.0
    assert harness.thread.start_count == 1


def test_service_poll_renews_lease_and_it_expires_after_four_idle_seconds():
    harness = ServiceHarness()
    service = harness.service(lambda: observation("Live"))
    service.poll(None, None)
    harness.clock.value = 2.0

    service.poll(None, None)

    assert service._lease_deadline == 6.0
    harness.clock.value = 5.999
    assert service._lease_active() is True
    harness.clock.value = 6.0
    assert service._lease_active() is False


def test_service_sample_once_never_overlaps():
    harness = ServiceHarness()
    service = harness.service(lambda: observation("Live"))
    service._sampling_lock.acquire()
    try:
        assert service.sample_once() is False
    finally:
        service._sampling_lock.release()


def test_service_marks_stale_after_failure_and_recovers_on_good_sample():
    harness = ServiceHarness()
    samples = iter((observation("First"), RuntimeError("provider failed"), observation("First again")))

    def sample():
        value = next(samples)
        if isinstance(value, Exception):
            raise value
        return value

    service = harness.service(sample)
    assert service.sample_once() is True
    first = service.poll(None, None)
    assert service.sample_once() is True
    stale = service.poll(first.stream_id, first.revision)
    assert stale.stale is True
    assert stale.error == "Transcript updating is delayed."
    assert service.sample_once() is True
    recovered = service.poll(stale.stream_id, stale.revision)
    assert recovered.stale is False
    assert recovered.error is None


def test_service_close_is_idempotent():
    harness = ServiceHarness()
    service = harness.service(lambda: observation("Live"))

    service.close()
    service.close()

    assert harness.stop.set_count == 1
    assert harness.wake.set_count == 1
    assert harness.thread.join_count == 1


def test_service_worker_samples_immediately_after_poll_activation():
    sampled = threading.Event()

    def sample():
        sampled.set()
        return observation("Live")

    service = TranscriptService(sample)
    try:
        assert service.poll(None, None).mode == "pending"
        assert sampled.wait(1.0) is True
        assert service.poll(None, None).messages[0].text == "Live"
    finally:
        service.close()
