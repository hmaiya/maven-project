"""Parser behaviour on small hand-built event streams.

Each helper builds the minimum event shape the Realtime API sends, so a test
reads as the sequence of events it covers.
"""

from __future__ import annotations

import json
from pathlib import Path

from unify.realtime import analyze, build_actions, parse_events, parse_file, parse_tool_output, write_jsonl


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


# --- step 2: classify, thread, link ------------------------------------------


def msg(item_id: str, role: str) -> dict:
    return {"id": item_id, "type": "message", "role": role}


def test_each_item_type_maps_to_one_action_type() -> None:
    actions, _ = build_actions(parse_events([
        created(msg("u1", "user")),
        created({"id": "f1", "type": "function_call", "call_id": "c1", "name": "lookup"}, "u1"),
        created({"id": "o1", "type": "function_call_output", "call_id": "c1", "output": "ok"}, "f1"),
        created(msg("b1", "assistant"), "o1"),
    ]))
    assert [a["type"] for a in actions] == [
        "user-message", "function-call", "function-response", "bot-message"]


def test_previous_item_id_beats_arrival_order() -> None:
    actions, issues = build_actions(parse_events([
        created(msg("b1", "assistant"), "u1"),       # arrives first, but threads after u1
        created(msg("u1", "user")),
    ]))
    assert [a["itemId"] for a in actions] == ["u1", "b1"]
    assert issues == []


def test_calls_and_responses_link_both_ways_by_call_id() -> None:
    actions, _ = build_actions(parse_events([
        created({"id": "f1", "type": "function_call", "call_id": "c1", "name": "lookup",
                 "arguments": '{"q": "x"}'}),
        created({"id": "o1", "type": "function_call_output", "call_id": "c1", "output": "ok"}, "f1"),
    ]))
    call, answer = actions
    assert call["arguments"] == {"q": "x"}
    assert call["responseItemId"] == "o1" and answer["callItemId"] == "f1"
    assert answer["name"] == "lookup"
    assert call["issues"] == answer["issues"] == []


def test_orphans_are_flagged_on_both_sides() -> None:
    actions, _ = build_actions(parse_events([
        created({"id": "f1", "type": "function_call", "call_id": "c1", "arguments": "{}"}),
        created({"id": "o2", "type": "function_call_output", "call_id": "c2", "output": "ok"}, "f1"),
    ]))
    assert "no_function_response" in actions[0]["issues"]
    assert "no_matching_function_call" in actions[1]["issues"]


def test_interrupted_reply_is_flagged() -> None:
    actions, _ = build_actions(parse_events([
        response("created", "r1", "in_progress"),
        output_item("added", msg("b1", "assistant"), "r1"),
        response("done", "r1", "cancelled"),
    ]))
    assert actions[0]["issues"] == ["interrupted", "no_text"]


def test_forks_missing_parents_and_unknown_types_are_kept_and_reported() -> None:
    actions, issues = build_actions(parse_events([
        created(msg("u1", "user")),
        created(msg("b1", "assistant"), "u1"),
        created(msg("b2", "assistant"), "u1"),
        created({"id": "x1", "type": "mcp_call"}, "gone"),
    ]))
    assert len(actions) == 4
    assert any("forks" in i for i in issues)
    assert any("gone" in i for i in issues)
    assert actions[-1]["type"] == "unknown"


# --- step 3: diagnose and output ---------------------------------------------

DOCS = ("LlmPromptBuilder(prompt=---- Doc excerpt ----\nID: d1\nTitle: Harvard Bridge facts\nUrl: https://x/1\n"
        "The Harvard Bridge is 364.4 smoots long.\n, urls=[])")


def turn(output: str, query: str = "Harvard Bridge smoots") -> list[dict]:
    """user asks -> tool call -> tool output -> bot answers"""
    return [
        created({**msg("u1", "user"), "content": [{"type": "input_text", "text": "How long is it?"}]}),
        created({"id": "f1", "type": "function_call", "call_id": "c1", "name": "get_knowledge_for_topic",
                 "arguments": json.dumps({"queryString": query})}, "u1"),
        created({"id": "o1", "type": "function_call_output", "call_id": "c1", "output": output}, "f1"),
        created({**msg("b1", "assistant"), "content": [{"type": "text", "text": "364.4 smoots."}]}, "o1"),
    ]


def test_documents_are_parsed_from_tool_output() -> None:
    assert parse_tool_output(DOCS) == [{"id": "d1", "title": "Harvard Bridge facts", "url": "https://x/1"}]


def test_relevant_documents_raise_no_issues() -> None:
    actions, summary = analyze(parse_events(turn(DOCS)))
    assert actions[2]["retrievalOk"] and actions[2]["issues"] == []
    assert actions[3]["issues"] == []
    assert summary["issues"] == {}


def test_empty_retrieval_makes_the_answer_ungrounded() -> None:
    output = "LlmPromptBuilder(prompt=\nNo relevant document excerpts found\n, urls=[])"
    actions, _ = analyze(parse_events(turn(output)))
    assert actions[2]["issues"] == ["no_relevant_documents"]
    assert actions[3]["issues"] == ["answer_not_grounded_in_retrieved_documents"]


def test_off_topic_retrieval_is_flagged() -> None:
    actions, _ = analyze(parse_events(turn(DOCS, query="refund policy for enterprise plans")))
    assert "documents_unrelated_to_query" in actions[2]["issues"]
    assert actions[2]["queryTermCoverage"] == 0.0
    assert "answer_not_grounded_in_retrieved_documents" in actions[3]["issues"]


def test_output_file_has_required_fields_and_no_nulls(tmp_path: Path) -> None:
    actions, _ = analyze(parse_events(turn(DOCS)))
    path = tmp_path / "actions.jsonl"
    write_jsonl(actions, path)
    records = [json.loads(line) for line in path.read_text().splitlines()]
    assert [r["type"] for r in records] == [
        "user-message", "function-call", "function-response", "bot-message"]
    call = records[1]
    assert {"type", "itemId", "previousItemId", "callId", "arguments", "position"} <= call.keys()
    assert all(v is not None for r in records for v in r.values())
    assert list(records[0])[:2] == ["position", "type"]
