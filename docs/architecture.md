# Architecture

Openflow is a local dictation stack. The backend owns API validation, transcription sessions, VAD, faster-whisper inference, and transcript event generation. The desktop client owns the Windows hotkey, microphone capture, and text insertion.

## Components

- FastAPI app: exposes `/health`, `/v1/models`, and `/v1/transcribe`.
- WebSocket handler: validates control messages, accepts PCM audio frames, and returns JSON events.
- Session: manages rolling buffers, VAD state, partial/final triggers, and duplicate cleanup.
- Transcriber: owns faster-whisper model loading and inference.
- Scripts: provide local file transcription, WAV streaming, and model benchmarking.
- Electron main process: registers the global hotkey, starts the backend, manages the tray menu, optionally refines finalized text with llama.cpp or Ollama, and pastes text into the focused app.
- Electron recorder renderer: captures microphone audio, converts it to `pcm_s16le` mono 16 kHz, and streams it to the backend WebSocket.

## Repository Architecture

This component map covers the complete repository: application entry points, desktop processes, backend runtime, persistence, and optional integrations. Solid arrows represent runtime calls or data flow; dotted arrows are configuration or deployment relationships.

```mermaid
flowchart TB
  User["User / focused Windows application"]

  subgraph Desktop["desktop/ — Electron client"]
    Electron["src/main.js\nElectron main process"]
    Preload["src/preload.js\ncontextBridge IPC boundary"]
    Recorder["src/recorder.js\nhidden recorder renderer"]
    Views["settings.js · advanced_settings.js · status.js\nHTML renderer views"]
    DesktopConfig["userData/config.json\npersisted desktop settings"]
    Policies["url_policy.js · window_security.js\nproduct_identity.js"]
    LLM["text_processor.js\noptional text refinement"]
  end

  subgraph Backend["backend/ — FastAPI transcription service"]
    Launcher["scripts/run_server.py\nrun_backend.ps1 / .bat"]
    Main["app/main.py\nFastAPI composition + lifespan"]
    HTTP["GET /health · GET /v1/models"]
    WS["app/websocket.py\nWS /v1/transcribe"]
    Security["app/security.py\ntrust, origin, token policy"]
    Schemas["app/schemas.py\nprotocol/domain models"]
    Session["app/session.py\nstreaming utterance orchestration"]
    Audio["app/audio.py · app/vad.py\nPCM conversion + energy VAD"]
    Merge["app/merge.py · app/metrics.py\ndedupe + session measurements"]
    Transcriber["app/transcriber.py\nWhisper inference adapter"]
    ModelStore["app/model_store.py\nmodel source / cache resolution"]
    Settings["app/config.py\nPydantic settings from .env"]
    CUDA["app/cuda_runtime.py\nWindows CUDA DLL setup"]
  end

  subgraph Tools["backend/scripts — operational tools"]
    Install["install_model.py"]
    FileTx["transcribe_file.py"]
    Stream["stream_wav.py"]
    Bench["benchmark_models.py"]
  end

  OS["Windows / Electron APIs\nhotkey · tray · microphone · clipboard/paste"]
  Whisper["faster-whisper / CTranslate2\nWhisperModel"]
  Models["Local model directory\nor permitted Hugging Face download"]
  GPU["NVIDIA CUDA runtime / GPU"]
  Llama["llama.cpp server\n/v1/chat/completions"]
  Ollama["Ollama server\n/api/chat and model APIs"]
  Docker["docker-compose.yml + Dockerfile\nGPU backend deployment"]

  User --> Electron
  Electron <--> Preload
  Preload <--> Recorder
  Preload <--> Views
  Electron -. load/save .-> DesktopConfig
  Electron --> Policies
  Electron <--> OS
  Recorder -->|start JSON + PCM frames| WS
  WS -->|events| Recorder
  Electron -->|health check / optional auto-start| Launcher
  Electron -->|optional finalized text| LLM
  LLM --> Llama
  LLM --> Ollama

  Launcher --> Main
  Settings -. configure .-> Main
  Settings -. configure .-> Security
  Settings -. configure .-> Session
  Settings -. configure .-> Transcriber
  Main --> HTTP
  Main --> WS
  Main -->|background load + shared inference semaphore| Transcriber
  HTTP --> Security
  WS --> Security
  WS --> Schemas
  WS --> Session
  Session --> Audio
  Session --> Merge
  Session --> Schemas
  Session -->|partial/final inference| Transcriber
  Transcriber --> ModelStore
  Transcriber --> CUDA
  Transcriber --> Whisper
  ModelStore --> Models
  CUDA --> GPU

  Install --> ModelStore
  FileTx --> Audio
  FileTx --> Transcriber
  Stream -->|same WebSocket protocol| WS
  Bench --> Transcriber
  Docker -. environment + model volume .-> Main
  Docker -. NVIDIA runtime .-> GPU
```

The backend has no transcript database: audio buffers, VAD state, metrics, and generated events live only for the active WebSocket session. Model files and the Electron configuration file are the durable application state.

## Live Transcription Flow

```mermaid
sequenceDiagram
  participant Client
  participant Socket
  participant Session
  participant VAD
  participant Model
  Client->>Socket: start JSON
  Socket->>Session: create session
  Socket->>Client: ready
  loop audio frames
    Client->>Socket: binary PCM
    Socket->>Session: accept_pcm16
    Session->>VAD: process frame
    alt active speech and partial interval elapsed
      Session->>Model: transcribe rolling window
      Session->>Client: partial
    end
    alt silence threshold reached
      Session->>Model: transcribe speech buffer
      Session->>Client: final
    end
  end
  Client->>Socket: stop
  Socket->>Session: finalize active speech
  Socket->>Client: stopped
```

## Desktop Dictation Flow

```mermaid
sequenceDiagram
  participant User
  participant Main as Electron Main
  participant Recorder as Hidden Recorder
  participant Backend
  participant Target as Focused Textbox
  User->>Target: focus textbox
  User->>Main: press global hotkey
  Main->>Recorder: start dictation
  Recorder->>Backend: start JSON
  loop microphone frames
    Recorder->>Backend: binary PCM
  end
  User->>Main: press global hotkey
  Main->>Recorder: stop dictation
  Recorder->>Backend: stop JSON
  Backend->>Recorder: final transcript
  Recorder->>Main: transcript text
  Main->>Main: optional text refinement
  Main->>Target: paste transcript
```
