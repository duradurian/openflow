# Tailored implementation plan: main-brokered transcription worker

## Decision

Proceed with the migration. The current FastAPI WebSocket endpoint is a thin adapter and `TranscriptionSession` is transport-independent enough to reuse. The migration is therefore a bounded transport, lifecycle, and packaging change, not a transcription-engine rewrite.

Do not begin the recorder cutover until the two architecture gates below are decided.

## Verified repository facts

* `desktop/src/recorder.js` creates the WebSocket, sends the backend API token in its `start` message, and streams PCM directly.
* `desktop/src/main.js` starts the backend from a source checkout using either `.venv\\Scripts\\python.exe` or PowerShell, discards stdio, waits one second, then polls health. It only calls `child.kill()` on exit.
* `backend/app/websocket.py` is a thin transport adapter. `backend/app/session.py`, audio/VAD, merging, and `WhisperTranscriber` can be retained.
* Renderers are sandboxed with context isolation and no Node integration. However, every registered local renderer can call the currently exposed preload APIs, including `config:get`, which returns the backend token.
* The backend has duration and rolling-buffer limits, but no explicit inbound JSON or frame-size limit. It has stop-and-finalize, but no cancel operation.
* `desktop/package.json` has no package/build configuration. Python dependencies are unpinned and the current application depends on a sibling source `backend/` directory.

## Architecture gates (P0)

### 1. Product boundary

Make one explicit product decision before implementation:

* **Recommended:** local worker is the default product path; retain FastAPI only as a development/server adapter.
* If remote transcription remains supported, it must be an explicitly enabled `RemoteWebSocketTransport` owned by Electron main. There is no silent local-to-remote fallback.

Remote credentials belong only in main-process storage and must not be returned by renderer configuration APIs.

### 2. Runtime and distribution

Choose the launchable production worker form before designing its paths:

* Development: direct Python interpreter plus `backend` source.
* Production CPU: a frozen worker executable or bundled Python environment extracted outside `app.asar`.
* GPU: a separately tested Windows SKU with defined CUDA/CTranslate2 compatibility.

Start with CPU packaging. GPU support, model downloads, and updates remain separate release gates. A stdio sidecar removes the network surface; it does not sandbox Python/native code. Windows containment must either be designed (for example a Job Object plus restricted process strategy) or documented as residual risk.

## Canonical local protocol contract (P0)

Define this contract before writing either implementation. Share the schemas conceptually between Node and Python, with independent runtime validation.

### Lifecycle

Main owns one active session and a monotonically increasing `generation`:

```text
worker: starting -> ready -> model_loading -> model_ready | unavailable | crashed
session: idle -> starting -> recording -> stopping -> idle
                              -> canceling -> idle
```

`stop` drains accepted audio and returns the final transcript. `cancel` clears queued audio and emits no transcript for that generation. Results received after cancel, stop completion, or a newer generation are discarded by main.

### Framing and ordering

Use a versioned, length-prefixed duplex stream:

* Control/event records: UTF-8 JSON with a four-byte big-endian byte length.
* Audio records: header (`protocolVersion`, `type`, `sessionId`, `generation`, `sequence`, payload length) followed by raw PCM16 bytes.
* A single writer owns each stdout/stdin stream. Any invalid length, malformed record, unsupported version, unexpected sequence, or stdout pollution is protocol corruption: capture bounded diagnostics, terminate the worker, and surface a structured error. Do not attempt stream resynchronization.
* Every command/event includes `sessionId`, `generation`, and a monotonic `sequence` where ordering matters.

Required input commands: `hello`, `start`, `audio`, `stop`, `cancel`, `shutdown`.

Required output events: `worker_ready`, `model_state`, `accepted`/credit update, `status`, `partial`, `final`, `stopped`, `canceled`, `error`, `shutdown_ack`.

### Limits and flow control

Specify constants in both processes and test boundary values:

* maximum control message bytes and audio frame bytes;
* maximum queued frames and bytes in Electron main and the Python worker;
* maximum session and utterance duration;
* bounded stderr retention and startup/model-load/inference escalation timeouts.

Electron does not dequeue audio merely because renderer IPC arrived. It sends to worker only while both its byte budget and Node stdin write buffer permit it; `drain` resumes writes. The worker issues accepted-byte credits after enqueueing. The renderer uses non-blocking `sendAudio` and receives a state/pressure event; it must not create one unresolved IPC invocation per audio callback.

Cancellation is cooperative: inference already running through `asyncio.to_thread` cannot be reliably interrupted. The worker must keep reading commands while serialized session work executes, main suppresses stale generation events, and a configured hung-inference timeout triggers worker termination/restart only after the session is closed.

## Implementation sequence

### Milestone 0 — Baseline and decisions

1. Capture current recorder protocol behaviour in backend tests and document persisted desktop configuration fields.
2. Record the local-only/remote decision and CPU packaging decision in an ADR.
3. Add a config migration: local-worker mode becomes default; retain legacy backend URL/token settings only for the explicitly retained remote/server feature. Redact token values from all renderer-facing configuration responses.
4. Add a feature flag `dictationTransport: legacy | worker`; default remains `legacy` until the worker passes integration tests. Do not implement automatic fallback.

Exit criteria: canonical state table, wire format, limits, recovery policy, and rollback owner are reviewed.

### Milestone 1 — Transport-neutral core and worker

1. Extract protocol-neutral validation and event construction from `backend/app/websocket.py` into `backend/app/worker_protocol.py` (or equivalent); keep `StartMessage`/transcript schema concepts, but add generation and sequence fields.
2. Add `backend/app/worker.py` and `backend/scripts/run_worker.py`.
3. Load the model asynchronously: emit `worker_ready` when the command loop is safe, then model-state updates separately. Keep stdout protocol-only; route logging to stderr with redaction.
4. Implement independent command reading, bounded queueing, serialized session work, and a single event writer. Reuse `TranscriptionSession` and its shared semaphore.
5. Add explicit frame-size checks before PCM conversion/allocation. Add `cancel` without changing FastAPI behaviour.

Exit criteria: worker unit/integration tests cover valid flows, malformed/corrupt frames, oversized frames, model failure, cancel during inference, and clean shutdown.

### Milestone 2 — Electron supervisor

1. Add `desktop/src/worker_supervisor.js` and `desktop/src/local_worker_transport.js`.
2. Spawn a direct executable/interpreter with `shell: false`, a minimal allowlisted environment, explicit cwd, and piped stdio. Resolve separate development and packaged paths; no PowerShell production launch.
3. Parse protocol frames with strict caps; maintain worker/model/session state, credits, queues, stdout-corruption handling, bounded stderr ring buffer, startup timeout, exit classification, and user-visible errors.
4. Implement verified Windows process-tree cleanup. Do not treat `child.kill()` as sufficient; the selected launch strategy must have a tested tree-termination mechanism.
5. Add a dedicated `dictation_transport.js` interface so a future remote implementation uses the same main-owned contract.

Exit criteria: no fixed-delay readiness, worker crash and timeout handling work, app quit leaves no child process, and main stays responsive under queue pressure.

### Milestone 3 — Privileged IPC and recorder cutover

1. Add narrowly scoped preload methods: `dictation.start`, `sendAudio`, `stop`, `cancel`, `getState`, and unsubscribe-based status/transcript/error/model listeners.
2. In main, validate the sender window and frame/URL, payload types, byte lengths, session ownership, and state transition for every handler. Do not expose generic IPC or worker commands.
3. Refactor `recorder.js` to retain capture/PCM conversion but send bounded chunks through preload. It no longer constructs a backend URL, holds a backend token, parses worker protocol, or opens a WebSocket in worker mode.
4. Route worker final text through the existing main-process refinement/paste flow. Keep remote LLM disabled by default and require a clear non-local opt-in.
5. Tighten recorder CSP by removing `ws:`/`wss:` from local worker mode. Keep remote CSP allowances only if an explicit remote product mode needs them.

Exit criteria: worker-mode local dictation works with no listener on port 8000 and no token exposed to a renderer.

### Milestone 4 — Safe rollout and server compatibility

1. Ship worker mode opt-in with `legacy` rollback for one release or equivalent controlled beta.
2. Run soak and fault tests: worker crash, framing corruption, audio flood, cancel during inference, model unavailable, microphone loss, app quit, and stale events.
3. Make worker mode default only after the test matrix passes. Remove local FastAPI autostart from default mode.
4. If remote mode was approved, implement it as a main-owned transport with explicit UI state, `wss` outside development, main-only token storage, strict endpoint validation, and privacy disclosure. Keep FastAPI as its separate adapter.

Exit criteria: rollback is documented and manual, never silent; default local mode has no loopback backend port.

### Milestone 5 — Packaging and supply chain

1. Add Electron build configuration, signing plan, packaged-worker path tests, and a clean-machine CPU smoke test.
2. Pin Python and Node dependencies with reproducible lockfiles. Bundle/provision the worker without a source-checkout assumption.
3. Define model manifest: approved source, exact revision, SHA-256, size, storage directory, verification error UX, and update policy. Do not accept arbitrary model URLs or paths from a renderer.
4. Add GPU packaging only after CPU packaging is stable, with a documented driver/CUDA matrix and failure-to-CPU policy.

Exit criteria: installer starts the worker from a packaged build, verifies/locates its model, and reports first-run failure without transcript leakage.

### Milestone 6 — Independent audio modernization

Replace `ScriptProcessorNode` with `AudioWorklet` only after the transport is stable. Preserve PCM format and backpressure behaviour; this is not part of the first cutover.

## Security and privacy acceptance checklist

| Risk | Required control |
| --- | --- |
| Renderer access | narrow bridge, per-window/frame validation, stateful main handlers |
| Local network attack | no FastAPI listener in default local mode |
| Audio flood/memory growth | strict input caps, byte-bounded queues, credit-based flow control |
| Stale/canceled text | generation IDs and main-side suppression |
| Worker fault/hang | readiness handshake, timeouts, exit classification, tested tree cleanup |
| Protocol pollution | protocol-only stdout, capped parser, fail closed on corruption |
| Transcript/token disclosure | redacted logs/stderr/crash reports; credentials never returned to renderers |
| Model compromise | allowlisted revision + manifest checksum verification |
| LLM leakage | disabled by default; non-local endpoint is explicit and visible |

## Files expected to change

Preserve: `backend/app/session.py`, `audio.py`, `vad.py`, `merge.py`, `transcriber.py`, `model_store.py`; retain `websocket.py` only as the optional FastAPI adapter.

Add: `backend/app/worker.py`, `backend/app/worker_protocol.py`, `backend/scripts/run_worker.py`, `desktop/src/worker_supervisor.js`, `desktop/src/dictation_transport.js`, `desktop/src/local_worker_transport.js`, and tests for each boundary.

Modify: `desktop/src/main.js`, `preload.js`, `recorder.js`, `window_security.js`, CSP-bearing HTML files, backend schemas/config/logging, package/build configuration, and the existing backend protocol tests.
