function secureWebPreferences(options = {}) {
  return {
    ...options,
    contextIsolation: true,
    nodeIntegration: false,
    sandbox: false,
  };
}

function isTrustedFileSender(event) {
  const url = event?.senderFrame?.url || "";
  return url.startsWith("file://");
}

function assertTrustedFileSender(event) {
  if (!isTrustedFileSender(event)) {
    throw new Error("Untrusted renderer IPC sender");
  }
}

function installPermissionPolicy(session, getRecorderWindow) {
  session.setPermissionRequestHandler((webContents, permission, callback) => {
    const recorderWindow = getRecorderWindow();
    callback(permission === "media" && recorderWindow?.webContents === webContents);
  });
}

module.exports = {
  assertTrustedFileSender,
  installPermissionPolicy,
  isTrustedFileSender,
  secureWebPreferences,
};
