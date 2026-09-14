from codeaway.transcript import (
    ObservedMessage,
    TranscriptObservation,
    TranscriptStore,
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
