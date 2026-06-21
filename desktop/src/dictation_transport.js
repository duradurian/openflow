"use strict";

// Minimal contract for main-process-owned transports.  Concrete transports
// expose: startWorker(), start(options), sendAudio(Buffer), stop(), cancel(),
// getState(), shutdown(), plus EventEmitter events event/model/pressure/error.
const { LocalWorkerTransport } = require("./local_worker_transport");

function createDictationTransport(options = {}) {
  if (options.kind && options.kind !== "worker") throw new Error(`Unsupported dictation transport: ${options.kind}`);
  return new LocalWorkerTransport(options);
}

module.exports = { createDictationTransport, LocalWorkerTransport };
