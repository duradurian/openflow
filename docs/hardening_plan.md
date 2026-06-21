# Codex Plan Mode Brief: Migrate Local Transcription Architecture to Main-Brokered Python Worker

## Objective

Evaluate and plan a migration from the current Electron + FastAPI/Uvicorn local backend architecture to a safer local-first architecture where Electron main becomes the explicit policy, lifecycle, and transport broker for local transcription.

Target architecture:

```text
Outer OS / app sandbox
└── Electron app
    ├── sandboxed renderer: UI and microphone capture only
    ├── preload/contextBridge: narrow validated dictation API
    ├── Electron main process: broker, permissions, IPC validation, lifecycle
    └── Faster-Whisper worker: supervised Python sidecar process
```

The goal is not to rewrite the transcription engine.

The goal is to replace the current renderer-owned WebSocket transport and weak backend lifecycle with a main-process-supervised, bounded, protocol-driven Python worker while preserving the reusable backend transcription core.

Codex should operate in plan mode first: inspect the repository, confirm or correct assumptions, identify required code changes, identify packaging/runtime blockers, produce a threat model, and then produce a staged implementation plan before modifying code.

---

## Executive Summary

The existing architecture is migratable, but not production-ready.

Migration feasibility is approximately 7/10 because the core transcription logic appears relatively well separated from the FastAPI WebSocket adapter. The major migration should be a transport, lifecycle, and supervision refactor rather than a full rewrite.

Current production readiness is closer to 4/10 because packaging, Python/runtime distribution, model strategy, process supervision, cancellation, backpressure, local security boundaries, model verification, and update strategy are incomplete.

The current architecture has a reasonable foundation:

* Electron renderers appear to be sandboxed.
* `nodeIntegration` is disabled.
* `contextIsolation` is enabled.
* There is an existing preload/contextBridge layer.
* The Python transcription core is mostly isolated in reusable modules.
* The WebSocket route appears to be a relatively thin transport adapter.
* Normal app flow streams microphone PCM only; it does not appear to pass arbitrary file paths to the backend.

However, the current app has several production blockers:

* The hidden recorder renderer directly owns the backend WebSocket.
* Local FastAPI/Uvicorn exposes a loopback WebSocket surface.
* Local authentication is disabled by default.
* Another local process can connect to the loopback backend if it can satisfy the current weak/default trust model.
* Electron main process discards or underuses backend stdout/stderr.
* Backend startup is not tied to a structured readiness handshake.
* There is no reliable process-tree shutdown.
* There is no restart policy or failure classification.
* There is no explicit backpressure model.
* There is no real cancellation model.
* Stale transcript events after cancel are not explicitly suppressed.
* The renderer uses high-frequency binary streaming.
* There is no explicit binary frame-size limit before backend processing.
* Electron packaging is missing or incomplete.
* Python, dependencies, CUDA/CPU runtime variants, and Whisper models are not packaged.
* The app appears to assume a source checkout with a sibling `backend/` directory.
* The current audio capture uses `ScriptProcessorNode`, which should eventually be replaced with AudioWorklet.
* Optional local LLM refinement may leak transcript text if pointed at a remote or untrusted endpoint.
* Model download and model cache trust boundaries require stronger definition.

The proposed architecture is a strong direction because it removes the direct renderer-to-backend path and makes Electron main the explicit policy, lifecycle, and transport boundary.

This migration should be treated as:

```text
workerization + supervision + IPC hardening + bounded protocol + packaging/runtime planning
```

not merely:

```text
replace FastAPI with stdio
```

---

## Current Architecture

Current local dictation flow:

```text
User hotkey
  ↓
Electron main
  ↓
hidden recorder renderer
  ↓
getUserMedia microphone capture
  ↓
renderer downsamples to PCM16 mono 16 kHz
  ↓
renderer opens WebSocket directly to FastAPI backend
  ↓
FastAPI /v1/transcribe
  ↓
websocket.py transport adapter
  ↓
TranscriptionSession
  ↓
VAD / buffering / partials / finalization
  ↓
faster-whisper transcriber
  ↓
JSON transcript events back to renderer
  ↓
renderer sends completed transcript to Electron main
  ↓
optional LLM refinement
  ↓
clipboard + synthetic paste
```

The problematic current trust path is:

```text
sandboxed renderer
└── direct WebSocket
    └── local FastAPI backend
        └── transcription session / model inference
```

The renderer is sandboxed, but it still owns too much protocol and backend interaction.

---

## Proposed Target Architecture

Target local dictation flow:

```text
User hotkey
  ↓
Electron main
  ↓
preload/contextBridge
  ↓
sandboxed recorder renderer
  ↓
getUserMedia microphone capture
  ↓
AudioWorklet eventually, existing PCM encoder initially
  ↓
bounded audio chunks over narrow preload IPC
  ↓
Electron main validates IPC and session state
  ↓
Electron main sends framed messages to Python worker over stdio
  ↓
Python worker reuses TranscriptionSession / VAD / transcriber
  ↓
worker emits worker_ready / model_state / status / partial / final / error events
  ↓
Electron main validates, sanitizes, and routes events
  ↓
renderer/UI receive status/transcript events through preload
  ↓
main performs optional LLM refinement and paste automation
```

Improved trust path:

```text
sandboxed renderer
└── narrow preload IPC
    └── Electron main broker
        └── supervised Python worker
            └── faster-whisper
```

Electron main is responsible for:

* IPC validation
* sender/window validation
* permission checks
* session ownership
* worker lifecycle
* startup readiness
* model readiness state
* backpressure
* cancellation
* stale event suppression
* crash handling
* stderr capture
* transcript event routing
* local/remote mode selection
* clipboard/paste automation
* optional LLM refinement policy

The Python worker must be treated as semi-trusted and high-risk because it loads native ML/audio libraries, model files, Python dependencies, and potentially GPU/CUDA libraries.

Moving from loopback WebSocket to stdio removes the local network surface, but it does not automatically sandbox the worker.

---

## Main Migration Principle

Do not rewrite the transcription engine.

Keep and reuse as much of the backend core as possible:

* `backend/app/session.py`
* `backend/app/transcriber.py`
* `backend/app/vad.py`
* `backend/app/audio.py`
* `backend/app/merge.py`
* `backend/app/model_store.py`
* `backend/app/cuda_runtime.py`
* protocol schema concepts from `backend/app/schemas.py`

Replace or add:

* local worker entry point
* transport-neutral protocol schemas
* framed stdio protocol
* Electron main worker supervisor
* preload dictation API
* recorder renderer transport layer
* lifecycle state machine
* backpressure
* cancellation
* stale event suppression
* error routing
* packaging/runtime plan

Keep FastAPI as an optional remote/server adapter unless product requirements explicitly remove remote/Docker backend support.

Default local dictation should not require FastAPI, Uvicorn, an open local port, or a renderer-owned WebSocket.

---

## Required Threat Model

Before implementing the migration, Codex must produce a concise threat model.

Threats to consider:

```text
Malicious renderer script
Buggy renderer flooding audio
Renderer attempting arbitrary IPC
Renderer attempting arbitrary backend URL selection
Local malware connecting to old loopback backend
Compromised model file
Compromised Python dependency
Worker crash during dictation
Worker emitting malformed events
Worker stdout polluted by dependency logs
Transcript leakage through logs
Transcript leakage through optional LLM refinement
Clipboard/paste abuse
Remote backend token leakage
Remote transcription accidentally enabled
Unbounded memory growth from audio queues
Stale inference results after cancel
Packaged app resolving worker paths incorrectly
CUDA/native DLL load failure
```

For each threat, Codex should identify:

* current exposure
* proposed control
* files likely involved
* whether the control is required before migration, during migration, or before production release

Minimum controls expected from the migration:

* renderer has no direct local backend socket in default mode
* renderer has no raw IPC
* main validates IPC sender and payloads
* main enforces session state
* worker protocol is bounded and versioned
* audio queues are bounded
* cancel suppresses stale results
* worker stdout is protocol-only
* logs do not include raw audio, partials, finals, secrets, or tokens by default
* worker is spawned without shell
* worker receives a minimal environment
* no arbitrary model URLs or filesystem paths cross from renderer to worker
* default local mode opens no local network port

---

## Phase 0: Repository and Packaging Reconnaissance

Inspect the repository and confirm or correct all assumptions before changing code.

### Architecture reconnaissance

Confirm:

1. Exact current renderer WebSocket ownership.
2. Exact preload APIs currently exposed.
3. Whether raw IPC is exposed.
4. Electron main backend lifecycle code.
5. Existing trusted sender validation.
6. Existing URL policy and remote backend mode.
7. FastAPI WebSocket route and protocol.
8. How `TranscriptionSession` is constructed and used.
9. Whether `TranscriptionSession` can be reused without FastAPI objects.
10. How `WhisperTranscriber` is initialized and shared.
11. How model readiness is currently exposed through `/health`.
12. How cancellation or stop is currently represented.
13. How partial/final events are generated.
14. Whether transcript text appears in logs.
15. Whether backend stdout/stderr are captured, discarded, or logged.
16. How optional LLM refinement is triggered and configured.
17. How clipboard/paste automation is performed.
18. Whether local/remote backend mode is currently a supported product concept.

### Packaging reconnaissance

Packaging must be inspected early because worker design depends on runtime and path decisions.

Confirm:

1. Current Electron packaging state in `desktop/package.json`.
2. Whether there is an installer or only development scripts.
3. Whether Python is expected to exist on the user machine.
4. Whether Python should be bundled.
5. Whether the worker should run from source, from a bundled Python environment, or from a frozen executable.
6. Whether CPU and GPU builds are expected.
7. How CUDA DLLs are currently located.
8. How model files are located.
9. Whether models are bundled, downloaded, or user-provided.
10. Whether model downloads are pinned and verified.
11. Whether app updates are signed.
12. Whether model updates are signed or checksum-verified.
13. Whether packaged app paths are compatible with `app.asar`.
14. Whether the current app assumes a sibling `backend/` source folder.

Deliverable:

```text
A concise repository findings report confirming:
- files to modify
- files to preserve
- assumptions verified
- assumptions disproven
- hidden blockers
- packaging/runtime blockers
- security-sensitive paths
```

Do not modify code in this phase.

---

## Phase 1: Decide Local/Remote Product Mode and Migration Boundary

Before refactoring the recorder, decide whether remote/Docker backend mode remains a supported product feature.

### Option A: Local-only product

Default and only supported transcription mode:

```text
renderer
  ↓
preload
  ↓
Electron main
  ↓
Python worker
```

FastAPI can be retained for development, testing, or future server mode, but it is not part of the user-facing product path.

### Option B: Local plus explicit remote mode

Final architecture:

```text
renderer
  ↓
preload dictation API
  ↓
Electron main DictationTransport
     ├── LocalWorkerTransport
     └── RemoteWebSocketTransport
```

The renderer must not directly own the remote WebSocket either.

Remote mode requirements:

* explicit user opt-in
* clear UI indication when remote transcription is active
* authentication required
* token not exposed to renderers
* strict URL validation
* `wss://` required outside development
* no silent fallback from local to remote
* no broad CSP allowances unless required
* remote endpoint health and model readiness reported through main
* remote transcript privacy warning
* secure token storage where practical

Acceptance criteria:

* Default local mode uses Python worker, not FastAPI.
* Renderer uses the same preload dictation API regardless of transport.
* Renderer does not choose arbitrary backend URLs.
* FastAPI remains useful only as an optional server adapter, not required for local dictation.

---

## Phase 2: Define Transport-Neutral Dictation Semantics

Before designing byte-level framing, define the transport-neutral state machine.

### Required session states

```text
idle
worker_starting
worker_ready
model_loading
model_ready
starting_session
listening
stopping
finalizing
canceling
stopped
failed
```

### Required message semantics

`stop` and `cancel` must be distinct:

```text
stop:
  stop accepting new audio
  process queued audio as allowed
  finalize active speech
  emit final events if available
  end session cleanly

cancel:
  stop accepting new audio
  clear queued audio
  suppress partial/final transcript delivery
  terminate or reset active session state
  ignore stale worker events
```

Every session must have:

```text
session_id
generation_id
created_at
state
sequence counters
```

Every worker event that belongs to a session must include:

```text
session_id
generation_id
```

Electron main must ignore events for inactive, canceled, completed, or superseded generations.

Acceptance criteria:

* session lifecycle is explicit
* stop and cancel behavior are distinct
* stale events after cancellation are ignored
* model readiness is separate from dictation readiness
* one active local dictation session is supported initially
* multiple sessions are not required unless existing product behavior needs them

---

## Phase 3: Define the Worker Protocol

Design a versioned, bounded, framed protocol for communication between Electron main and the Python worker.

Preferred transport:

```text
Electron main ⇄ Python worker over stdin/stdout
```

Required stream separation:

```text
stdout = protocol only
stderr = logs only
```

Do not allow dependency logs, Python `print()`, progress bars, warnings, or diagnostic messages to pollute stdout.

### Recommended framing

Prefer robust binary framing over Base64-in-JSON for audio.

Recommended frame structure:

```text
frame_header:
  magic
  protocol_version
  frame_type: json | audio
  header_length
  payload_length
  session_id or session reference
  sequence number

payload:
  JSON control message
  or raw PCM audio bytes
```

A simpler length-prefixed protocol is acceptable if it still supports:

* protocol versioning
* JSON control messages
* raw binary audio frames
* maximum frame size
* maximum JSON size
* maximum audio chunk size
* graceful handling of malformed frames

Base64 audio inside JSON may be acceptable for a first proof-of-concept only if:

* chunks are small
* message size limits are strict
* the plan explicitly treats it as temporary
* the design does not lock the app into Base64 long-term

### Minimum main-to-worker messages

```json
{
  "type": "start",
  "protocol_version": 1,
  "session_id": "...",
  "generation_id": 1,
  "sample_rate": 16000,
  "channels": 1,
  "format": "pcm_s16le",
  "language": "en",
  "mode": "fast"
}
```

```json
{
  "type": "audio",
  "protocol_version": 1,
  "session_id": "...",
  "generation_id": 1,
  "seq": 1,
  "byte_length": 3200
}
```

Audio payload should be raw PCM bytes in an audio frame.

```json
{
  "type": "stop",
  "protocol_version": 1,
  "session_id": "...",
  "generation_id": 1,
  "flush": true
}
```

```json
{
  "type": "cancel",
  "protocol_version": 1,
  "session_id": "...",
  "generation_id": 1
}
```

```json
{
  "type": "shutdown",
  "protocol_version": 1
}
```

### Minimum worker-to-main messages

```json
{
  "type": "worker_ready",
  "protocol_version": 1
}
```

```json
{
  "type": "model_state",
  "protocol_version": 1,
  "state": "loading"
}
```

```json
{
  "type": "model_state",
  "protocol_version": 1,
  "state": "ready"
}
```

```json
{
  "type": "model_state",
  "protocol_version": 1,
  "state": "error",
  "code": "...",
  "message": "..."
}
```

```json
{
  "type": "status",
  "protocol_version": 1,
  "session_id": "...",
  "generation_id": 1,
  "status": "listening"
}
```

```json
{
  "type": "partial",
  "protocol_version": 1,
  "session_id": "...",
  "generation_id": 1,
  "segment_id": "...",
  "text": "...",
  "start": 0.0,
  "end": 1.0
}
```

```json
{
  "type": "final",
  "protocol_version": 1,
  "session_id": "...",
  "generation_id": 1,
  "segment_id": "...",
  "text": "...",
  "start": 0.0,
  "end": 1.0
}
```

```json
{
  "type": "error",
  "protocol_version": 1,
  "session_id": "...",
  "generation_id": 1,
  "code": "...",
  "message": "..."
}
```

### Protocol limits

Define explicit constants:

```text
MAX_JSON_FRAME_BYTES
MAX_AUDIO_FRAME_BYTES
MAX_AUDIO_QUEUE_BYTES
MAX_AUDIO_QUEUE_FRAMES
MAX_SESSION_SECONDS
MAX_UTTERANCE_SECONDS
MAX_STDERR_CAPTURE_BYTES
MAX_WORKER_STARTUP_SECONDS
MAX_MODEL_LOAD_SECONDS or long-loading status behavior
```

Acceptance criteria:

* protocol has explicit max JSON size
* protocol has explicit max audio chunk size
* protocol has explicit max queued bytes
* protocol defines session lifecycle states
* protocol distinguishes stop from cancel
* protocol handles stale events after cancellation
* protocol handles worker startup/model loading separately
* protocol has forward-compatible versioning
* protocol rejects malformed frames safely
* stdout pollution is detected and treated as protocol failure

---

## Phase 4: Add Python Worker Entry Point

Create a new local worker entry point, likely one of:

```text
backend/app/worker.py
backend/scripts/run_worker.py
```

The worker should:

* initialize app settings
* initialize/load `WhisperTranscriber`
* emit `worker_ready`
* emit `model_state` events
* read framed messages from stdin
* write framed messages to stdout
* send all logs to stderr
* redirect or configure Python logging so stdout remains protocol-only
* reuse `TranscriptionSession` for audio/VAD/transcription logic
* support one active local dictation session initially
* enforce message and audio limits
* reject missing start-before-audio
* reject unsupported protocol versions
* reject malformed messages gracefully
* support graceful shutdown
* support stop-and-finalize
* support cancel
* include session/generation IDs in all session events
* avoid accepting arbitrary file paths from the renderer/main unless a future explicit feature requires it

Important design rule:

```text
Never let dependency logs, warnings, progress bars, or print statements pollute stdout.
stdout is protocol only.
stderr is diagnostic logs only.
```

The worker should not expose FastAPI-specific dependencies in the local path.

Acceptance criteria:

* worker starts without Uvicorn
* worker can load model and report readiness
* worker can accept start/audio/stop messages
* worker can accept cancel
* worker can emit status/partial/final/error messages
* worker exits cleanly on shutdown
* worker returns structured errors for invalid messages
* worker enforces max JSON and audio frame size
* worker does not log transcript text by default
* worker stdout is protocol-only under normal and error conditions

---

## Phase 5: Add Electron Main Worker Supervisor

Add a main-process supervisor module, for example:

```text
desktop/src/worker_supervisor.js
```

Responsibilities:

* spawn the Python worker
* use `child_process.spawn`, not `exec`
* use `shell: false`
* avoid shell scripts in production launch path
* resolve worker path correctly in development and packaged app
* pass minimal environment variables
* avoid passing unnecessary secrets
* set an appropriate working directory
* capture stderr
* bound stderr memory usage
* parse framed stdout protocol
* detect stdout protocol corruption
* enforce startup timeout
* track worker state
* track model state
* track active dictation session state
* maintain bounded audio queue
* apply backpressure to renderer
* support stop-and-finalize
* support cancel
* suppress stale events by session/generation
* classify worker exits
* restart only when safe and intentional
* kill process tree on app quit
* surface user-visible errors

The main process must not perform expensive audio processing. It should validate, queue, route, and supervise only.

Recommended launch shape:

```js
spawn(pythonExecutable, [workerScript], {
  shell: false,
  stdio: ["pipe", "pipe", "pipe"],
  windowsHide: true,
  env: minimalEnv,
  cwd: workerWorkingDirectory
})
```

Do not use:

```js
exec(...)
spawn(..., { shell: true })
```

Acceptance criteria:

* startup does not resolve after a fixed sleep
* startup resolves after actual worker readiness or fails with timeout
* model readiness is explicit
* worker crash is reported clearly
* stderr is captured and bounded
* app quit terminates worker reliably
* renderer cannot spawn or command arbitrary worker operations
* worker messages are validated before being routed
* stale session events are ignored
* main remains responsive during audio streaming
* packaged path handling is designed, even if not fully implemented in the first patch

---

## Phase 6: Define Backpressure, Queueing, and Failure Semantics

Implement hard operational limits and precise backpressure behavior.

### Required limits

* maximum audio chunk size
* maximum queued chunks
* maximum queued bytes
* maximum session duration
* maximum utterance buffer duration
* maximum JSON message size
* maximum stderr capture size
* startup timeout
* model load timeout or long-loading user-visible state
* inference timeout behavior where practical
* maximum consecutive worker errors before requiring manual restart

### `sendAudio` result semantics

Renderer-facing audio submission should return a structured result:

```text
accepted
rejected_no_session
rejected_worker_not_ready
rejected_model_not_ready
rejected_over_limit
rejected_backpressure
rejected_canceling
rejected_stopping
```

### Backpressure policy

Use a small bounded queue.

When overloaded, prefer predictable rejection or dropping newest audio over unbounded memory growth.

Recommended behavior:

```text
If queue is below limit:
  accept audio

If queue is over limit:
  reject newest audio
  notify renderer of pressure/degraded capture
  continue session if possible

If overload persists:
  surface warning
  optionally cancel session with structured error
```

Do not allow a buggy or malicious renderer to enqueue unlimited audio.

Acceptance criteria:

* oversized messages are rejected
* oversized audio frames are rejected
* queue limits are enforced in main and worker
* renderer receives clear backpressure/failure signal
* cancel clears queued audio
* stop and cancel behave differently
* stale results after cancel are ignored
* overload does not freeze the UI indefinitely

---

## Phase 7: Extend Preload With Narrow Dictation APIs

Extend the preload bridge with task-specific APIs only.

Do not expose raw IPC.

Avoid APIs like:

```js
send(channel, payload)
invoke(channel, payload)
worker.send(message)
```

Expose narrow APIs like:

```js
window.openflow.dictation.start(configSubset)
window.openflow.dictation.sendAudio(arrayBuffer)
window.openflow.dictation.stop()
window.openflow.dictation.cancel()
window.openflow.dictation.onStatus(callback)
window.openflow.dictation.onTranscript(callback)
window.openflow.dictation.onError(callback)
window.openflow.dictation.onModelState(callback)
window.openflow.dictation.getState()
```

Preload should:

* validate basic argument shapes before IPC
* use fixed IPC channel names
* avoid exposing backend tokens
* avoid exposing worker internals
* avoid exposing arbitrary URLs in local mode
* provide unsubscribe functions for event listeners
* preserve existing trusted sender checks in Electron main

Electron main should still perform authoritative validation. Preload validation is helpful but not sufficient.

Acceptance criteria:

* renderer has no raw IPC
* renderer has no direct worker access
* renderer has no backend token access unless absolutely required for explicit remote mode
* all IPC handlers validate sender and schema
* event subscriptions can be cleaned up
* invalid renderer payloads fail safely
* dictation API is transport-neutral

---

## Phase 8: Refactor Recorder Renderer

Modify the hidden recorder renderer so it no longer opens a WebSocket in default local mode.

Current behavior to remove from local mode:

* `new WebSocket(...)`
* direct backend URL construction
* direct start JSON send to backend
* direct binary PCM sends to backend
* direct backend event parsing as transport owner
* direct backend token handling in renderer

New behavior:

* capture microphone
* encode/downsample audio
* batch audio into bounded chunks
* send chunks through `window.openflow.dictation.sendAudio`
* respect `sendAudio` backpressure/failure results
* receive sanitized status/partial/final/error/model events from preload
* keep existing final transcript UX behavior initially
* preserve current user-visible behavior as much as possible

Recommended incremental approach:

1. Keep current microphone capture and PCM encoding initially.
2. Replace only the transport path first.
3. Replace `ScriptProcessorNode` with AudioWorklet in a later phase.

Do not combine AudioWorklet migration with the first transport migration unless the codebase makes it trivial.

Acceptance criteria:

* local dictation works without renderer opening a backend WebSocket
* renderer cannot choose arbitrary backend URL in local mode
* renderer respects backpressure signals or failed `sendAudio` calls
* renderer handles worker/model errors gracefully
* transcript completion behavior matches current user experience
* microphone permission denial is handled cleanly
* renderer transport code is minimal and policy-free

---

## Phase 9: Security Hardening

Security requirements:

* default local dictation opens no local port
* renderer has no direct local backend socket in default mode
* renderer has no arbitrary IPC access
* main validates sender frame/window
* main validates all IPC payloads
* main enforces session state
* main suppresses stale session/generation events
* worker is spawned without shell
* worker receives minimal environment
* worker gets no unnecessary secrets
* worker does not receive arbitrary model URLs from renderer
* worker does not receive arbitrary filesystem paths from renderer
* worker filesystem access is limited by packaging/sandbox strategy where possible
* CSP should be tightened, especially broad `ws:` / `wss:` allowances if no longer needed
* local FastAPI auth problem should disappear from default local mode
* remote/server mode should require authentication
* optional LLM refinement must be explicit and privacy-aware
* logs must not contain audio, partial transcripts, final transcripts, tokens, or secrets by default

### Model security requirements

Model files and model downloads are part of the trust boundary.

Requirements:

* allowlist permitted model sources
* pin exact model revisions or commit hashes where possible
* verify checksums for bundled or downloaded models
* reject arbitrary model URLs by default
* reject unsafe model formats unless explicitly trusted
* prefer non-code-executing model formats
* store models in a documented application data directory
* surface model verification errors clearly

### Optional LLM refinement requirements

If optional LLM refinement remains supported:

* default to disabled
* show clear warning when enabling
* restrict default endpoints to localhost
* require explicit opt-in for non-local endpoints
* never send raw audio to the LLM
* avoid sending partials unless explicitly required
* do not expose LLM endpoint tokens to renderer
* clearly indicate when text may leave the machine

Acceptance criteria:

* default local dictation has no open local port
* no broad WebSocket CSP is needed for local dictation
* tokens are not exposed through generic config APIs
* worker protocol rejects malformed input
* worker logs do not leak sensitive data
* Electron main remains small and does not parse or process audio expensively
* model strategy rejects untrusted arbitrary inputs
* remote/LLM modes are explicit and visible to the user

---

## Phase 10: Packaging and Runtime Plan

Packaging is a production blocker and must be planned explicitly before declaring the migration production-ready.

Codex should inspect current packaging state and propose a concrete packaging strategy.

Questions to answer:

* Will the app remain Windows-only?
* Will Python be bundled?
* Will the worker run as a Python script, bundled environment, or frozen executable?
* Will dependencies be installed at build time or first run?
* Will there be CPU and GPU builds?
* How will CUDA runtime requirements be handled?
* How will CTranslate2/faster-whisper native dependencies be handled?
* Are models bundled, downloaded, or user-provided?
* If models are downloaded, how are they verified?
* Where are models stored?
* How are app updates signed?
* How are model updates handled?
* How are dependency vulnerabilities patched?
* How will `app.asar` affect worker and model paths?
* How will first-run model loading/downloading failures be surfaced?
* How will the app avoid source-checkout assumptions?

Minimum production packaging requirements:

* Electron packaging config
* signed installer/builds
* bundled or reliably provisioned Python runtime
* pinned Python dependencies
* reproducible worker environment
* documented CPU/GPU runtime strategy
* model path strategy
* model verification strategy
* first-run failure handling
* no dependence on sibling `backend/` source folder in packaged app
* worker path resolution works in packaged app
* logs and crash reports avoid transcript leakage

Acceptance criteria:

* a clean user machine can install and run the app
* app can start worker without developer checkout
* worker path resolution works in development and packaged modes
* model strategy is documented and tested
* failure to load/download model produces clear user-facing error
* app update and model update strategies are documented

---

## Phase 11: Preserve FastAPI as Optional Server Adapter, If Needed

If remote/server mode is retained, keep FastAPI as a separate adapter over the same transcription core.

Desired structure:

```text
Transcription core:
  session.py
  transcriber.py
  vad.py
  audio.py
  merge.py
  model_store.py

Adapters:
  worker.py       local stdio adapter
  websocket.py    optional FastAPI WebSocket adapter
```

FastAPI should not be required for default local dictation.

Remote mode requirements:

* explicit user opt-in
* authentication required
* strict origin/token policy
* no unauthenticated loopback assumptions for production
* renderer does not own WebSocket
* Electron main owns remote transport
* token is not broadly exposed to renderers
* secure URL validation
* clear UI indication when using remote transcription
* clear privacy indication that audio/text may leave the device
* separate dev-mode allowances from production behavior

Acceptance criteria:

* default local mode uses Python worker
* remote mode still works if configured and retained
* renderer transport code is unified through preload/main
* FastAPI remains useful as a server adapter, not a local production dependency
* local and remote transports produce compatible transcript events

---

## Phase 12: Replace ScriptProcessorNode With AudioWorklet

This should happen after the transport migration unless repository inspection shows it is trivial.

Goals:

* reduce deprecated API usage
* improve audio capture reliability
* reduce main-thread audio callback pressure
* produce predictable chunk sizes
* integrate cleanly with backpressure behavior

Acceptance criteria:

* AudioWorklet path matches previous audio format
* sample rate conversion remains correct
* chunk sizes are bounded and predictable
* microphone permission errors still surface cleanly
* fallback behavior is defined if AudioWorklet is unavailable
* transport migration is not blocked by this phase

---

## Phase 13: Testing Plan

Add or update tests for the worker protocol, Electron main supervisor, preload boundary, renderer behavior, and end-to-end dictation.

### Worker protocol tests

* valid start/audio/stop flow
* valid start/audio/cancel flow
* malformed JSON frame
* malformed binary frame
* unsupported protocol version
* oversized JSON message
* oversized audio chunk
* missing start before audio
* duplicate start
* cancel behavior
* stop-and-finalize behavior
* stale event generation behavior
* worker shutdown
* model load error
* inference error
* stdout pollution detection
* stderr logging does not break protocol

### Electron main tests

* worker spawn success
* worker startup timeout
* worker model loading state
* worker crash before ready
* worker crash during dictation
* stderr capture and truncation
* process-tree shutdown
* IPC sender validation
* payload validation
* backpressure behavior
* stale event suppression
* stop behavior
* cancel behavior
* packaged worker path resolution where practical

### Renderer/preload tests

* no raw IPC exposed
* dictation API works
* event unsubscription works
* invalid payloads fail safely
* microphone permission denied
* microphone device unavailable
* audio send failure/backpressure
* cancel clears UI state
* final transcript still routes to existing UX

### End-to-end tests

* local dictation happy path
* stop finalizes transcript
* cancel discards transcript
* worker crash during dictation
* model unavailable
* optional LLM refinement disabled by default
* optional remote backend mode, if retained
* packaged app path resolution
* first-run model unavailable/download failure
* app quit terminates worker

Acceptance criteria:

* unit tests for protocol
* integration tests for worker
* Electron tests for IPC boundary where practical
* manual QA checklist for dictation and packaging
* privacy checklist for logs and crash output
* at least one packaged smoke test before production release

---

## Suggested Implementation Order

Recommended order:

1. Inspect repository and confirm assumptions.
2. Inspect packaging/runtime state.
3. Produce threat model.
4. Decide whether remote mode remains supported.
5. Define session lifecycle and transport-neutral semantics.
6. Define worker protocol and message schemas.
7. Build Python worker entry point.
8. Build Electron main worker supervisor.
9. Implement backpressure and cancellation semantics.
10. Add narrow preload dictation APIs.
11. Refactor recorder renderer to use preload IPC instead of WebSocket.
12. Keep FastAPI remote mode working only if explicitly retained.
13. Tighten security and CSP.
14. Create packaging plan and then packaging implementation.
15. Replace `ScriptProcessorNode` with AudioWorklet.
16. Add tests and QA checklist.
17. Run packaged smoke tests.

Avoid doing all changes in one giant patch.

The safest implementation path is incremental:

```text
first milestone:
renderer → preload IPC → Electron main → Python worker stdio → transcription core
```

Then harden:

```text
limits → cancellation → backpressure → packaging → tests → AudioWorklet
```

---

## Non-Goals for Initial Migration

Do not initially:

* rewrite faster-whisper integration
* rewrite VAD
* replace the entire UI
* remove FastAPI before deciding remote mode requirements
* move microphone capture into Electron main
* add native audio capture unless required
* solve every packaging issue inside the first transport migration patch
* replace ScriptProcessorNode and transport in the same first patch unless trivial
* support multiple concurrent dictation sessions unless existing product behavior requires it
* allow arbitrary user-provided model URLs
* allow arbitrary renderer-provided filesystem paths
* implement remote fallback without explicit user approval
* send transcript text to remote LLMs by default

---

## Key Files to Inspect

Critical:

```text
desktop/src/recorder.js
desktop/src/main.js
desktop/src/preload.js
desktop/src/window_security.js
desktop/src/url_policy.js
backend/app/websocket.py
backend/app/session.py
backend/app/transcriber.py
backend/app/schemas.py
backend/app/config.py
```

Important:

```text
backend/app/vad.py
backend/app/audio.py
backend/app/merge.py
backend/app/model_store.py
backend/app/cuda_runtime.py
backend/scripts/run_server.py
backend/run_backend.ps1
desktop/package.json
backend/requirements.txt
backend/requirements-gpu-windows.txt
backend/Dockerfile
docker-compose.yml
```

Likely new files:

```text
backend/app/worker.py
backend/app/worker_protocol.py
backend/scripts/run_worker.py
desktop/src/worker_supervisor.js
desktop/src/dictation_transport.js
desktop/src/local_worker_transport.js
desktop/src/remote_ws_transport.js
```

Exact file names should be confirmed during repository reconnaissance.

---

## Desired End State

Default local dictation should use this architecture:

```text
sandboxed renderer
  ↓ narrow preload API
Electron main broker
  ↓ framed stdio protocol
supervised Python worker
  ↓
TranscriptionSession / faster-whisper
```

The renderer should not own the backend transport.

Electron main should be the policy, lifecycle, and routing boundary.

The Python worker should be bounded, supervised, and isolated as much as the packaging platform allows.

FastAPI should remain only if remote/server mode is an explicit supported feature.

Production readiness requires both the migration and a packaging/runtime strategy. The migration improves security and lifecycle, but packaging Python, dependencies, CUDA/CPU variants, model files, signing, updates, and first-run behavior remains a separate P0 workstream.

---

## Final Recommendation

Proceed with the migration.

The old architecture is highly migratable because the WebSocket layer appears relatively isolated and the transcription core appears reusable. The highest-value first milestone is:

```text
Local dictation works with:
renderer → preload IPC → Electron main → Python worker stdio → transcription core
```

while FastAPI remains available only for optional remote/server mode if that remains a deliberate product requirement.

This migration should be treated as:

```text
workerization + supervision + IPC hardening + bounded protocol + packaging/runtime planning
```

not merely:

```text
replace FastAPI with stdio
```

The result will be a significantly safer and more production-oriented local transcription architecture, but it will still need packaging, model distribution, update, worker sandboxing, and privacy-safe logging work before the app should be considered production-ready.