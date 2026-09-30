"""Parser behaviour on small hand-built event streams.

Each helper builds the minimum event shape the Realtime API sends, so a test
reads as the sequence of events it covers.
"""

from __future__ import annotations

from pathlib import Path

from unify.realtime import parse_events, parse_file


def created(item: dict, previous: str | None = None) -> dict:
    return {"type": "conversation.item.created", "item": item, "previous_item_id": previous}


def output_item(kind: str, item: dict, response_id: str = "resp_1") -> dict:
    return {"type": f"response.output_item.{kind}", "response_id": response_id, "item": item}


def response(kind: str, response_id: str, status: str, output: list | None = None) -> dict:
    body = {"id": response_id, "status": status, "output": output or []}
    return {"type": f"response.{kind}", "response": body}


def test_user_transcript_arriving_late_still_attaches_to_its_item() -> None:
    conv = parse_events([
        created({"id": "u1", "type": "message", "role": "user", "status": "completed",
                 "content": [{"type": "input_audio", "transcript": None}]}),
        created({"id": "f1", "type": "function_call", "call_id": "c1"}, previous="u1"),
        {"type": "conversation.item.input_audio_transcription.completed", "item_id": "u1",
         "transcript": "How long is the bridge?\n"},
    ])
    assert conv.items["u1"].text == "How long is the bridge?"
    assert list(conv.items) == ["u1", "f1"]


def test_function_arguments_are_reassembled_from_deltas() -> None:
    conv = parse_events([
        output_item("added", {"id": "f1", "type": "function_call", "call_id": "c1", "name": "lookup"}),
        {"type": "response.function_call_arguments.delta", "item_id": "f1", "delta": '{"q":'},
        {"type": "response.function_call_arguments.delta", "item_id": "f1", "delta": '"x"}'},
    ])
    assert conv.items["f1"].arguments == '{"q":"x"}'
    assert conv.items["f1"].name == "lookup"


def test_output_item_events_do_not_erase_previous_item_id() -> None:
    conv = parse_events([
        created({"id": "b1", "type": "message", "role": "assistant"}, previous="u1"),
        output_item("done", {"id": "b1", "type": "message", "role": "assistant", "status": "completed"}),
    ])
    assert conv.items["b1"].previous_item_id == "u1"
    assert conv.items["b1"].response_id == "resp_1"


def test_cancelled_reply_keeps_full_transcript_and_incomplete_status() -> None:
    conv = parse_events([
        response("created", "r1", "in_progress"),
        {"type": "response.audio_transcript.delta", "item_id": "b1", "delta": "The bridge is "},
        {"type": "response.audio_transcript.done", "item_id": "b1",
         "transcript": "The bridge is long. According"},
        response("done", "r1", "cancelled", output=[
            {"id": "b1", "type": "message", "role": "assistant", "status": "incomplete",
             "content": [{"type": "audio", "transcript": "The bridge is"}]},
        ]),
    ])
    assert conv.items["b1"].text == "The bridge is long. According"     # .done beats the shorter snapshot
    assert conv.items["b1"].status == "incomplete"
    assert conv.responses["r1"].status == "cancelled"
    assert conv.responses["r1"].output_item_ids == ["b1"]


def test_stream_that_never_finishes_falls_back_to_deltas() -> None:
    conv = parse_events([
        response("created", "r1", "in_progress"),
        output_item("added", {"id": "b1", "type": "message", "role": "assistant", "status": "in_progress"}),
        {"type": "response.audio_transcript.delta", "item_id": "b1", "delta": "Half a "},
        {"type": "response.audio_transcript.delta", "item_id": "b1", "delta": "sentence"},
    ])
    assert conv.items["b1"].text == "Half a sentence"
    assert conv.responses["r1"].done_seq is None


def test_malformed_and_unknown_events_are_recorded_not_fatal() -> None:
    conv = parse_events([
        {"type": "conversation.item.input_audio_transcription.delta"},
        {"type": "some.future.event"},
    ])
    assert conv.malformed and "item_id" in conv.malformed[0]
    assert conv.unhandled["some.future.event"] == 1


def test_truncated_last_line_is_reported(tmp_path: Path) -> None:
    path = tmp_path / "conv.jsonline"
    path.write_text('{"type": "session.created", "session": {"id": "s1"}}\n{"type": "resp')
    conv = parse_file(path)
    assert conv.session["id"] == "s1"
    assert conv.read_errors and "line 2" in conv.read_errors[0]
