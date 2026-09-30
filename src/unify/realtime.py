"""Rebuild conversation items from a recorded Realtime API event stream.

A recording is JSON Lines, one RealtimeServerEvent per line. A single
conversation item (a user utterance, a bot reply, a tool call, a tool result)
is spread across many events: created once, streamed as deltas, finalised by
a `.done` event, and sometimes cancelled mid-stream. This module folds those
events into one `Item` per item_id and one `Response` per response_id.

Step 1 (this file so far) is parsing only: no classification or ordering.
Event reference: https://platform.openai.com/docs/api-reference/realtime-server-events
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .readers import read_jsonl

Event = dict[str, Any]


@dataclass
class Item:
    item_id: str
    item_type: str | None = None          # message | function_call | function_call_output
    role: str | None = None               # user | assistant (messages only)
    status: str | None = None             # completed | incomplete | in_progress
    previous_item_id: str | None = None
    call_id: str | None = None
    name: str | None = None
    response_id: str | None = None
    # Text arrives three ways; `text` picks the most authoritative one.
    text_deltas: list[str] = field(default_factory=list)
    text_done: str | None = None           # *.done / transcription.completed
    content_text: str | None = None        # item.content snapshot
    arguments_deltas: list[str] = field(default_factory=list)
    arguments_done: str | None = None
    output: str | None = None
    audio_start_ms: int | None = None
    audio_end_ms: int | None = None
    transcription_error: str | None = None
    first_seq: int = 0
    last_seq: int = 0
    first_event_id: str | None = None

    @property
    def text(self) -> str | None:
        """`.done` beats the item.content snapshot, which beats joined deltas.

        output_item.done content can be shorter than audio_transcript.done for
        an interrupted reply, so it is not preferred. Deltas are the only
        source when the stream never finished.
        """
        for candidate in (self.text_done, self.content_text, "".join(self.text_deltas)):
            if candidate and candidate.strip():
                return candidate.strip()
        return None

    @property
    def arguments(self) -> str | None:
        return self.arguments_done or ("".join(self.arguments_deltas) or None)


@dataclass
class Response:
    response_id: str
    status: str | None = None              # in_progress | completed | cancelled | incomplete | failed
    status_details: Any = None
    output_item_ids: list[str] = field(default_factory=list)
    created_seq: int | None = None
    done_seq: int | None = None


@dataclass
class Conversation:
    session: dict[str, Any] = field(default_factory=dict)
    items: dict[str, Item] = field(default_factory=dict)          # insertion = first-seen order
    responses: dict[str, Response] = field(default_factory=dict)
    read_errors: list[str] = field(default_factory=list)
    server_errors: list[Event] = field(default_factory=list)
    malformed: list[str] = field(default_factory=list)         # events missing a required field
    unhandled: Counter[str] = field(default_factory=Counter)
    event_count: int = 0


# --- parsing ---------------------------------------------------------------


def parse_file(path: Path) -> Conversation:
    conv = Conversation()
    return parse_events(read_jsonl(path, on_error=conv.read_errors.append), conv)


def parse_events(events: Iterable[Event], conv: Conversation | None = None) -> Conversation:
    conv = conv or Conversation()
    for seq, event in enumerate(events, start=1):
        conv.event_count += 1
        handler = HANDLERS.get(event.get("type", ""))
        if handler is None:
            conv.unhandled[event.get("type", "<missing type>")] += 1
            continue
        try:
            handler(conv, event, seq)
        except (KeyError, TypeError) as exc:
            # Recorded, not fatal: this recording has transcription.delta
            # events with no item_id or delta at all.
            conv.malformed.append(f"event {seq} {event.get('type')}: missing {exc}")
    return conv


def _item(conv: Conversation, item_id: str, event: Event, seq: int) -> Item:
    item = conv.items.get(item_id)
    if item is None:
        item = conv.items[item_id] = Item(item_id, first_seq=seq, first_event_id=event.get("event_id"))
    item.last_seq = seq
    return item


def _response(conv: Conversation, response_id: str) -> Response:
    return conv.responses.setdefault(response_id, Response(response_id))


def _merge_payload(conv: Conversation, payload: dict[str, Any], event: Event, seq: int) -> Item | None:
    """Fold an `item` object into its Item. Later non-null values win, except
    previous_item_id, which is only ever set by conversation.item.created
    (output_item.* sends null there, which must not erase it)."""
    item_id = payload.get("id")
    if not item_id:
        return None
    item = _item(conv, item_id, event, seq)
    for attr, key in (("item_type", "type"), ("role", "role"), ("status", "status"),
                      ("call_id", "call_id"), ("name", "name"), ("output", "output")):
        if payload.get(key) is not None:
            setattr(item, attr, payload[key])
    if payload.get("arguments"):
        item.arguments_done = payload["arguments"]
    snapshot = "".join(c.get("text") or c.get("transcript") or "" for c in payload.get("content") or [])
    if snapshot.strip():
        item.content_text = snapshot
    return item


# --- handlers, one per event type ------------------------------------------


def on_session(conv: Conversation, event: Event, seq: int) -> None:
    conv.session.update(event.get("session") or {})


def on_item_created(conv: Conversation, event: Event, seq: int) -> None:
    item = _merge_payload(conv, event.get("item") or {}, event, seq)
    if item and event.get("previous_item_id"):
        item.previous_item_id = event["previous_item_id"]


def on_output_item(conv: Conversation, event: Event, seq: int) -> None:
    item = _merge_payload(conv, event.get("item") or {}, event, seq)
    if item and event.get("response_id"):
        item.response_id = event["response_id"]
        response = _response(conv, event["response_id"])
        if item.item_id not in response.output_item_ids:
            response.output_item_ids.append(item.item_id)


def on_text_delta(conv: Conversation, event: Event, seq: int) -> None:
    _item(conv, event["item_id"], event, seq).text_deltas.append(event.get("delta") or "")


def on_text_done(conv: Conversation, event: Event, seq: int) -> None:
    text = event.get("transcript") if "transcript" in event else event.get("text")
    _item(conv, event["item_id"], event, seq).text_done = text


def on_arguments_delta(conv: Conversation, event: Event, seq: int) -> None:
    item = _item(conv, event["item_id"], event, seq)
    item.arguments_deltas.append(event.get("delta") or "")
    item.call_id = item.call_id or event.get("call_id")


def on_arguments_done(conv: Conversation, event: Event, seq: int) -> None:
    item = _item(conv, event["item_id"], event, seq)
    item.arguments_done = event.get("arguments")
    item.call_id = item.call_id or event.get("call_id")


def on_user_transcription_delta(conv: Conversation, event: Event, seq: int) -> None:
    _item(conv, event["item_id"], event, seq).text_deltas.append(event.get("delta") or "")


def on_user_transcription_done(conv: Conversation, event: Event, seq: int) -> None:
    # Whisper runs asynchronously, so this often lands after the bot has
    # already started answering. Matching by item_id, not position, handles it.
    _item(conv, event["item_id"], event, seq).text_done = event.get("transcript")


def on_user_transcription_failed(conv: Conversation, event: Event, seq: int) -> None:
    error = event.get("error") or {}
    _item(conv, event["item_id"], event, seq).transcription_error = error.get("message") or str(error)


def on_speech_started(conv: Conversation, event: Event, seq: int) -> None:
    _item(conv, event["item_id"], event, seq).audio_start_ms = event.get("audio_start_ms")


def on_speech_stopped(conv: Conversation, event: Event, seq: int) -> None:
    _item(conv, event["item_id"], event, seq).audio_end_ms = event.get("audio_end_ms")


def on_response_created(conv: Conversation, event: Event, seq: int) -> None:
    body = event.get("response") or {}
    response = _response(conv, body["id"])
    response.status = body.get("status")
    response.created_seq = seq


def on_response_done(conv: Conversation, event: Event, seq: int) -> None:
    body = event.get("response") or {}
    response = _response(conv, body["id"])
    response.status = body.get("status")
    response.status_details = body.get("status_details")
    response.done_seq = seq
    # The final snapshot of every output item, including ones a cancel cut short.
    for payload in body.get("output") or []:
        item = _merge_payload(conv, payload, event, seq)
        if item:
            item.response_id = response.response_id
            if item.item_id not in response.output_item_ids:
                response.output_item_ids.append(item.item_id)


def on_error(conv: Conversation, event: Event, seq: int) -> None:
    conv.server_errors.append({"seq": seq, **(event.get("error") or {})})


def ignore(conv: Conversation, event: Event, seq: int) -> None:
    """Events with nothing needed for the item list (audio bytes, buffer
    bookkeeping, rate limits, content-part framing)."""


HANDLERS: dict[str, Callable[[Conversation, Event, int], None]] = {
    "session.created": on_session,
    "session.updated": on_session,
    "conversation.item.created": on_item_created,
    "response.output_item.added": on_output_item,
    "response.output_item.done": on_output_item,
    "response.audio_transcript.delta": on_text_delta,
    "response.audio_transcript.done": on_text_done,
    "response.text.delta": on_text_delta,
    "response.text.done": on_text_done,
    "response.function_call_arguments.delta": on_arguments_delta,
    "response.function_call_arguments.done": on_arguments_done,
    "conversation.item.input_audio_transcription.delta": on_user_transcription_delta,
    "conversation.item.input_audio_transcription.completed": on_user_transcription_done,
    "conversation.item.input_audio_transcription.failed": on_user_transcription_failed,
    "input_audio_buffer.speech_started": on_speech_started,
    "input_audio_buffer.speech_stopped": on_speech_stopped,
    "response.created": on_response_created,
    "response.done": on_response_done,
    "error": on_error,
    "response.audio.delta": ignore,
    "response.audio.done": ignore,
    "response.content_part.added": ignore,
    "response.content_part.done": ignore,
    "input_audio_buffer.committed": ignore,
    "rate_limits.updated": ignore,
}

# TODO: conversation.item.truncated (client truncated a bot reply to what was
# actually played) and conversation.item.deleted are not in this recording.
# Handle them before trusting bot text as "what the user heard".
# TODO: one Conversation per file. If a recording can hold several sessions,
# split on session.created first.
