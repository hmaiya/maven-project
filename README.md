# Realtime conversation analysis

**The assignment:** read a recorded Mavenagi Realtime conversation (a
`.jsonline` file with one `RealtimeServerEvent` per line), rebuild it as an
ordered list of **user-message / bot-message / function-call /
function-response** actions, link each function call to its response, output
the result as JSON Lines, and use it to diagnose what went wrong in the
conversation.

**The short answer:** the knowledge search never returned anything useful.
The first search found nothing; the second returned two car-magazine articles.
The bot answered both times anyway, from the model's own memory. Its
instructions say *"You are a RAG based AI, and should rely on knowledge
function calls to provide context for your responses"*, so both answers break
that rule. There are also secondary problems: the bot was interrupted once,
one response was cancelled before it produced anything, and the recording
ends mid-reply.

```bash
uv sync
uv run unify conversation     # rebuild + diagnose; writes out/<file>.actions.jsonl and .summary.json
uv run unify run              # load the same actions into SQLite (out/warehouse.db)
uv run unify query "select position, type, issues from conversation_actions"
uv run pytest                 # 86 tests
```

---

## How the three commands fit together

```
data/raw/harvard-bridge-example-ORIG.jsonline      352 events, last line truncated
        │
        ▼  realtime.parse_file()     fold events into items and responses
        ▼  realtime.build_actions()  classify, order by previous_item_id, link call_id
        ▼  realtime.diagnose()       retrieval quality + grounding checks
        │
        ├──► unify conversation ──► terminal timeline
        │                           out/<file>.actions.jsonl    one action per line   (the deliverable)
        │                           out/<file>.summary.json     one report per conversation
        │
        └──► unify run ───────────► out/warehouse.db
                                      conversation_actions      same actions, as a SQL table
                                      rejects                   anything that could not be parsed
                                          │
                                          ▼
                                    unify query "<sql>"         ask questions of those tables
```

All three commands share the same parsing code, so they always agree.
`conversation` is the direct answer to the assignment. `run` and `query` make
the same result queryable, which covers the brief's bonus point: "output
that's easy to read or analyze downstream".

---

## 1. `unify conversation`: the rebuilt conversation

It produces three things.

### a) A timeline in the terminal

One row per action, in conversation order:

| # | type | content | issues |
|---|---|---|---|
| 1 | user-message | How long is the Harvard Bridge in Boston? | |
| 2 | function-call | `get_knowledge_for_topic({"queryString": "length of the Harvard Bridge in Boston"})` | |
| 3 | function-response | `No relevant document excerpts found` | `no_relevant_documents` |
| 4 | bot-message | The Harvard Bridge … is about 2,164.8 feet long … According | `interrupted`, `answer_not_grounded_in_retrieved_documents` |
| 5 | user-message | Um, that's interesting. | |
| 6 | user-message | Can you tell me more about that? | |
| 7 | function-call | `get_knowledge_for_topic({"queryString": "Harvard Bridge length smoots"})` | |
| 8 | function-response | 2 document(s): *Modified Masterpiece \| Grassroots Motorsports*; *How to turn a pile of tubes into your own creation \| Grassroots Motorsports* | `documents_unrelated_to_query` |
| 9 | bot-message | The Harvard Bridge's length is famously measured in "smoots" … 364.4 smoots … | `response_never_completed`, `answer_not_grounded_in_retrieved_documents` |

Below the table it prints the conversation-level problems:
- responses cancelled before producing any output,
- responses that never finished,
- recording errors and malformed events.

### b) `out/<file>.actions.jsonl`: the deliverable

One JSON object per line, one line per action. Empty fields are left out.
Here are real rows from this recording, lightly shortened:

```json
{"position": 2, "type": "function-call", "itemId": "item_BMFSPcXwvrDilbmsYhoxY",
 "previousItemId": "item_BMFSP67c3nfIV0WscPBuP", "callId": "call_mnOmiursWZ8WB7c0",
 "name": "get_knowledge_for_topic", "arguments": {"queryString": "length of the Harvard Bridge in Boston"},
 "responseId": "resp_BMFSPPMHiW1j2hagMk5aE", "status": "completed", "responseStatus": "completed",
 "responseItemId": "item_BMFSQ8qvDc1h7P4AxtNSY", "eventRange": [9, 25]}

{"position": 8, "type": "function-response", "itemId": "item_BMFSVwNFtYwoaf8wnxPul",
 "callId": "call_9qhJRSBN2aJCQwm0", "name": "get_knowledge_for_topic", "callItemId": "item_BMFSUfV4NnAwnBihniLut",
 "documents": [{"title": "Modified Masterpiece | Articles | Grassroots Motorsports", "url": "https://grassrootsmotorsports.com/..."}],
 "output": "LlmPromptBuilder(prompt=---- Doc excerpt ---- ...",
 "issues": ["documents_unrelated_to_query"],
 "queryTermCoverage": 0.0, "queryTermsMissing": ["bridge", "harvard", "smoots"], "retrievalOk": false}
```

**Fields the assignment requires, and where each comes from:**

| Required field | Our field | Where the value comes from |
|---|---|---|
| `type` | `type` | `item.type` + `item.role`: `message`/`user` → user-message, `message`/`assistant` → bot-message, `function_call` → function-call, `function_call_output` → function-response |
| text | `text` | **user-message:** `input_audio_transcription.completed`. **bot-message:** `response.audio_transcript.done`; if the stream never finished, the joined `…delta` events |
| arguments | `arguments` | `response.function_call_arguments.done` (or the joined deltas), parsed from a JSON string into an object |
| output | `output` | `conversation.item.created` for the `function_call_output` item |
| `itemId` | `itemId` | `item.id` |
| `callId` | `callId` | `item.call_id` (function-call and function-response only) |
| `previousItemId` | `previousItemId` | `previous_item_id` on `conversation.item.created`. This is the only event type that sets it |

**Extra metadata ("any other metadata you find relevant"):**

| Field | Meaning |
|---|---|
| `position` | Order in the conversation (1 = first) |
| `name` | Tool name. It also appears on the function-response, copied from its call |
| `responseItemId` / `callItemId` | **The call↔response link, in both directions.** Matched on `call_id` |
| `responseId`, `responseStatus` | Which model response produced this item and how that response ended: `completed`, `cancelled`, or `in_progress` if it never ended |
| `status` | The item's own status: `completed`, `incomplete` (cut short) or `in_progress` |
| `audioStartMs`, `audioEndMs` | For user messages: where the speech sits in the user's audio stream. **The recording has no wall-clock timestamps**, so this is the only timing available |
| `eventRange` | First and last line numbers in the `.jsonline` file that contributed to this action. Use it to find the raw events behind any row |
| `firstEventId` | The `event_id` of the first of those events |
| `documents` | For function-responses: the ID, title and URL of each document the knowledge search returned |
| `queryTermCoverage`, `queryTermsMissing`, `retrievalOk` | The relevance check (see the issue flags below) |
| `issues` | Everything wrong with this action. See the table below |

### c) `out/<file>.summary.json`: one report for the whole conversation

```json
{
  "sessionId": "sess_BMFSI1MJC41ma4PWr734k",
  "model": "gpt-4o-realtime-preview-2024-10-01",
  "tools": ["get_knowledge_for_topic"],
  "events": 352,
  "actions":   {"user-message": 3, "function-call": 2, "function-response": 2, "bot-message": 2},
  "responses": {"completed": 2, "cancelled": 2, "in_progress": 1},
  "responsesCancelledWithoutOutput": ["resp_BMFSUEHGBrRJslfeoqhdq"],
  "responsesNeverCompleted": ["resp_BMFSV51lSIxxBuA4ak7NZ"],
  "issues": {"no_relevant_documents": 1, "interrupted": 1, "answer_not_grounded_in_retrieved_documents": 2,
             "documents_unrelated_to_query": 1, "response_never_completed": 1},
  "recordingErrors": ["harvard-bridge-example-ORIG.jsonline: bad JSON at line 353: Unterminated string ..."],
  "malformedEvents": ["event 28 conversation.item.input_audio_transcription.delta: missing 'item_id'", "..."],
  "threadIssues": [], "serverErrors": [], "unhandledEventTypes": {}
}
```

Read it top to bottom:
1. What session and model this was.
2. How many actions and responses there were, and how each response ended.
3. Which problems occurred, and how often.
4. Whether the recording itself was damaged.

Empty `threadIssues` means the `previous_item_id` chain was complete and
unbranched. Empty `unhandledEventTypes` means every event type in the file was
understood.

### Issue flags

| Flag | On | Meaning | Derived from |
|---|---|---|---|
| `no_relevant_documents` | function-response | The knowledge search returned nothing | Output contains `No relevant document excerpts found`, or no documents could be parsed |
| `documents_unrelated_to_query` | function-response | Documents came back, but they aren't about the query | Less than 50% of the query's distinctive words appear in the returned text |
| `answer_not_grounded_in_retrieved_documents` | bot-message | The bot answered although every search in this turn failed | No `retrievalOk` function-response since the last user message |
| `interrupted` | bot-message | The user talked over the bot and the server cancelled the reply | `response.done` with `status: cancelled` |
| `response_never_completed` | bot-message | The recording ends before this reply finished | `response.created` with no matching `response.done` |
| `no_function_response` / `no_matching_function_call` | call / response | A call with no response, or a response with no call | No partner with the same `call_id` |
| `no_transcript`, `no_text`, `arguments_not_json`, `empty_output` | various | A missing or unparseable field | The field itself |

---

## 2. `unify run`: the same result as database tables

```
┏━━━━━━━━━━━━━━━━━━━━━━┳━━━━━━┓
┃ table                ┃ rows ┃
┡━━━━━━━━━━━━━━━━━━━━━━╇━━━━━━┩
│ conversation_actions │    9 │
│ rejects              │    4 │
└──────────────────────┴──────┘
```

- **`conversation_actions`** has 9 rows, one per action, with the same fields as
  `actions.jsonl`. It also has `source_system` (the file name) and `session_id`,
  so several recordings can share one table. Nested fields (`arguments`,
  `documents`, `issues`) are stored as JSON text, which SQLite's `json_*`
  functions can read.
- **`rejects`** has 4 rows. Nothing that couldn't be parsed is silently
  dropped; it lands here with the reason:
  - **1 truncated line.** Line 353 is cut off mid-string, inside an
    audio chunk. The recording stopped while the bot was still talking.
  - **3 malformed events.** Lines 28, 143 and 163 are
    `input_audio_transcription.delta` events with nothing in them: no
    `item_id`, no `delta`. They carry no data; the `.completed` events that
    follow them have the full transcript.

Every run drops and rebuilds both tables, so running it again is always
safe.

## 3. `unify query "<sql>"`: questions against those tables

It runs any SQL against `out/warehouse.db` and prints a table. These four
queries answer the assignment's questions directly. All four have been run
against this recording.

**The conversation in order:**
```sql
select position, type, substr(coalesce(text, arguments, output), 1, 60) as content
from conversation_actions order by position
```

**Each function call joined to its response** (the correlation the brief asks for):
```sql
select c.position as call_pos, json_extract(c.arguments, '$.queryString') as query,
       r.position as response_pos, json_array_length(r.documents) as docs,
       r.queryTermCoverage, json_extract(r.issues, '$[0]') as issue
from conversation_actions c
join conversation_actions r on r.callId = c.callId and r.type = 'function-response'
where c.type = 'function-call'
```
| call_pos | query | response_pos | docs | queryTermCoverage | issue |
|---|---|---|---|---|---|
| 2 | length of the Harvard Bridge in Boston | 3 | – | – | no_relevant_documents |
| 7 | Harvard Bridge length smoots | 8 | 2 | 0.0 | documents_unrelated_to_query |

**Every problem, with where it happened:**
```sql
select j.value as issue, count(*) as n, group_concat(a.position) as positions
from conversation_actions a, json_each(a.issues) j group by 1 order by 2 desc
```
| issue | n | positions |
|---|---|---|
| answer_not_grounded_in_retrieved_documents | 2 | 4,9 |
| response_never_completed | 1 | 9 |
| no_relevant_documents | 1 | 3 |
| interrupted | 1 | 4 |
| documents_unrelated_to_query | 1 | 8 |

**What couldn't be parsed:** `select reason from rejects`

---

## What happened in this conversation, and why

Line numbers refer to `harvard-bridge-example-ORIG.jsonline`.

| Lines | What the events show | Action(s) | Why it matters |
|---|---|---|---|
| 1–2 | `session.created` / `session.updated`. One tool, `get_knowledge_for_topic`. Instructions: *"You should always call a function if you can. You are a RAG based AI, and should rely on knowledge function calls…"*. Turn detection is `server_vad`, with a 200 ms silence threshold | – | This sets the expected behaviour: **answers should come from the knowledge search** |
| 3–6 | The user speaks (0–2304 ms), and the audio is committed as user item `…scPBuP` with **no text yet** | 1 | The transcript is produced asynchronously by Whisper |
| 7–25 | Response 1: the model calls `get_knowledge_for_topic` and its arguments stream in 12 deltas | 2 | Correct behaviour: it searched first |
| 26 | Function output: `No relevant document excerpts found` | 3 | **Problem #1: the knowledge base has nothing on the topic** |
| 29 | The user's transcript finally arrives: *"How long is the Harvard Bridge in Boston?"* | 1 | This lands *after* the function call, which is why user text is attached by `item_id`, not by position |
| 27–132 | Response 2: the bot answers anyway, *"about 2,164.8 feet long…"* | 4 | **Problem #2: an ungrounded answer.** The search gave it nothing, so this came from the model's own memory, against its instructions |
| 127 → 132 | The user starts speaking (127) while the bot is talking. The server cancels response 2 (`response.done`, `cancelled`) and the item ends `incomplete` at *"…According"* | 4, 5 | Barge-in. `server_vad` cancels the reply on any speech. Our text is what was *generated*; what the user actually *heard* could be shorter |
| 136–138 | Response 3 is created and cancelled immediately, with no output, because the user spoke again | – (summary: `responsesCancelledWithoutOutput`) | A 200 ms silence threshold is aggressive: a brief pause ("Um, that's interesting.") triggers a response that is then thrown away |
| 142–162 | Response 4: a second search, *"Harvard Bridge length smoots"* | 7 | A reasonable follow-up query |
| 165 | Function output: two **Grassroots Motorsports** car articles. "harvard", "bridge" and "smoots" appear 0 times | 8 | **Problem #3: the search returns unrelated documents.** Either this agent is connected to the wrong knowledge base, or the similarity search has no minimum-relevance cut-off |
| 166–352 | Response 5: the bot answers *"364.4 smoots, plus or minus an ear…"*, again from its own memory | 9 | **Problem #2 again.** Nothing in the returned documents supports this answer |
| 353 | A truncated `response.audio.delta`. The file ends there: no `response.done` for response 5 | 9, rejects | **Problem #4: the recording is incomplete.** The logger stopped or crashed mid-stream |

**Diagnosis:**
- **Main problem: the retrieval (RAG) path is broken for this agent.** The
  knowledge base either doesn't cover the topic, or returns unrelated content
  with no relevance cut-off.
- **Why nobody noticed:** the model covers the gap with plausible
  answers from its own memory. They happen to be roughly correct here, which
  hides the failure. On a customer's own policies or pricing, the same
  behaviour would produce confident, made-up answers.
- **Fixes, in order:**
  1. Check which knowledge base this agent is connected to.
  2. Add a minimum relevance score to `get_knowledge_for_topic`.
  3. Tell the model what to do when the search comes back empty: say it
     doesn't know, or ask a clarifying question.
  4. Separately, tune `silence_duration_ms` and fix the logger so it closes
     the file cleanly.

---

## How the assignment's criteria are met

| The brief asks for | Where it's done |
|---|---|
| Read the `.jsonline` file | `readers.read_jsonl` (`.jsonline` is registered). A truncated last line is reported, not fatal |
| "Items are composed of one or more events" | `realtime.parse_events` has one handler per event type and folds every event into its item, keyed by `item_id` |
| Thread items using `id` and `previous_item_id` | `realtime.thread_order` follows the chain and uses arrival order only to break ties. It flags forks, missing parents and cycles |
| Correlate call and response using `call_id` | `realtime.build_actions` links them in both directions (`responseItemId` / `callItemId`) and flags calls or responses without a partner |
| Accurate event→message classification | `realtime.ACTION_TYPES`. Unknown item types are kept as `unknown` and flagged, not dropped |
| Output JSON or JSONL with type, text/arguments/output, itemId, callId, previousItemId | `out/<file>.actions.jsonl`, via `realtime.write_jsonl` |
| Easy to read or analyze downstream (bonus) | Terminal timeline, `summary.json`, and the SQL table behind `unify query` |
| Diagnose the suspected problem | `realtime.diagnose` plus the walkthrough above |
| Document where more work is needed | `# TODO:` comments in `realtime.py`, summarized below |

### Handing over: what's left to do

- **What the user heard vs. what was generated.** `conversation.item.truncate`
  and `.truncated` events (the client reporting where playback stopped) aren't
  in this recording. Handle them so interrupted bot text matches what the user
  heard.
- **Better relevance check.** The current check counts matching words. Replace
  it with an embedding-similarity or LLM-judged score, and set the threshold
  from labelled conversations.
- **Structured tool output.** Documents are parsed out of the text of a
  platform object (`LlmPromptBuilder(prompt=…)`). Ask the platform team for
  JSON so that format stops being something the parser depends on.
- **Multiple sessions per file.** Split on `session.created` if a recording can
  contain more than one conversation.
- **Incremental loading.** `unify run` rebuilds its tables each time; upsert on
  `(session_id, itemId)` to process a stream of recordings.

---

## Project layout

| Path | Responsibility |
|---|---|
| `src/unify/realtime.py` | **The solution:** event parsing, classification, threading, linking, diagnosis, output |
| `src/unify/cli.py` | The `unify` command: `conversation`, `run`, `query`, `tables`, `profile`, `fetch` |
| `src/unify/transform.py` | Feeds realtime recordings into `unify run` as the `conversation_actions` table |
| `src/unify/readers.py` | CSV/TSV/JSON/JSONL/`.jsonline` readers that survive bad lines |
| `src/unify/load.py` | SQLite loader (tables rebuilt on each run) |
| `src/unify/normalize.py`, `profile.py`, `client.py` | General helpers for normalizing, profiling and pulling data from other sources |
| `tests/test_realtime.py` | 18 tests on small hand-built event streams: late transcripts, streamed arguments, interruptions, unfinished streams, orphans, forks, retrieval flags, output format |

Other commands: `unify profile` summarizes every file in `data/raw/`, and
`unify fetch <endpoint>` pulls records from a REST API into `data/raw/` as JSONL.

## Packaging

```bash
make test       # pytest + ruff
make package    # wheel in dist/  ->  uv tool install dist/*.whl
make binary     # single-file executable at dist/unify (built for this OS/CPU)
docker build -t unify . && docker run --rm -v "$PWD/data:/work/data" -v "$PWD/out:/work/out" unify conversation
```
