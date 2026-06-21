"use strict";

const { EventEmitter } = require("events");
const { WorkerSupervisor, ProtocolError } = require("./worker_supervisor");

class LocalWorkerTransport extends EventEmitter {
  constructor(options = {}) {
    super();
    this.supervisor = options.supervisor || new WorkerSupervisor(options);
    this.session = null;
    this.generation = 0;
    this.creditBytes = 0;
    this._bindSupervisor();
  }

  _bindSupervisor() {
    this.supervisor.on("event", (event) => {
      if (event.type === "accepted") this.creditBytes = Math.max(0, Number(event.creditBytes) || 0);
      if (event.type === "model_state") this.emit("model", event);
      if (event.sessionId && (!this.session || event.sessionId !== this.session.id || event.generation !== this.generation)) return;
      this.emit("event", event);
      this.emit(event.type, event);
      // The terminal event still belongs to the just-finished session, but a
      // subsequent start must not be blocked by stale local state.
      if (event.type === "stopped" || event.type === "canceled" || (event.type === "status" && event.status === "stopped")) {
        this.session = null;
        this.creditBytes = 0;
      }
    });
    this.supervisor.on("backpressure", (state) => this.emit("pressure", { ...state, creditBytes: this.creditBytes }));
    this.supervisor.on("fatal", (error) => this.emit("error", error));
    this.supervisor.on("exit", (detail) => { this.session = null; this.emit("exit", detail); });
  }

  startWorker() { return this.supervisor.start(); }
  getState() { return { worker: this.supervisor.state, model: this.supervisor.modelState, session: this.session && { ...this.session }, creditBytes: this.creditBytes }; }

  start({ sessionId, sampleRate = 16000, channels = 1, format = "pcm_s16le", language = null, mode = "fast" }) {
    if (this.session) throw new Error("A dictation session is already active");
    if (!sessionId || typeof sessionId !== "string") throw new TypeError("sessionId is required");
    this.generation += 1;
    this.session = { id: sessionId, state: "starting" };
    this.creditBytes = 0;
    this.supervisor.send({ type: "start", sessionId, generation: this.generation, sequence: 0, sampleRate, channels, format, language, mode });
    return { sessionId, generation: this.generation };
  }

  sendAudio(audio) {
    if (!this.session) throw new Error("No active dictation session");
    const bytes = Buffer.isBuffer(audio) ? audio : Buffer.from(audio);
    if (bytes.length === 0 || bytes.length > this.supervisor.options.maxAudioBytes || bytes.length % 2) {
      throw new ProtocolError("Invalid PCM audio frame", "INVALID_AUDIO_FRAME");
    }
    if (this.creditBytes && bytes.length > this.creditBytes) {
      this.emit("pressure", { creditBytes: this.creditBytes });
      return false;
    }
    this.supervisor.send({ type: "audio", sessionId: this.session.id, generation: this.generation, sequence: (this.session.sequence = (this.session.sequence || 0) + 1), audioBase64: bytes.toString("base64") });
    if (this.creditBytes) this.creditBytes -= bytes.length;
    return true;
  }

  stop() { return this._finish("stop", "stopping"); }
  cancel() { return this._finish("cancel", "canceling"); }
  _finish(type, state) {
    if (!this.session) return false;
    this.session.state = state;
    this.supervisor.send({ type, sessionId: this.session.id, generation: this.generation, sequence: (this.session.sequence || 0) + 1 });
    return true;
  }
  shutdown() { this.session = null; return this.supervisor.stop(); }
}

module.exports = { LocalWorkerTransport };
