const test = require('node:test');
const assert = require('node:assert/strict');
const { EventEmitter } = require('node:events');

const { createUpdateManager } = require('../update_manager');

class FakeUpdater extends EventEmitter {
  constructor() {
    super();
    this.autoDownload = true;
    this.autoInstallOnAppQuit = true;
    this.checkCalls = 0;
    this.downloadCalls = 0;
    this.installCalls = [];
  }

  async checkForUpdates() {
    this.checkCalls += 1;
    return { updateInfo: { version: '0.2.0' } };
  }

  async downloadUpdate() {
    this.downloadCalls += 1;
    return ['downloaded.exe'];
  }

  quitAndInstall(isSilent, forceRunAfter) {
    this.installCalls.push({ isSilent, forceRunAfter });
  }
}

function makeHarness({ isPackaged = true } = {}) {
  const updater = new FakeUpdater();
  const emitted = [];
  let installPrepared = false;
  const manager = createUpdateManager({
    autoUpdater: updater,
    currentVersion: '0.1.0',
    isPackaged,
    emit: (payload) => emitted.push(payload),
    beforeInstall: () => { installPrepared = true; },
    logger: { info() {}, error() {} },
  });
  return { updater, emitted, manager, installPrepared: () => installPrepared };
}

test('disables unattended downloads and reports development mode', async () => {
  const { updater, manager } = makeHarness({ isPackaged: false });

  assert.equal(updater.autoDownload, false);
  assert.equal(updater.autoInstallOnAppQuit, false);
  assert.deepEqual(manager.getState(), {
    status: 'development_mode',
    currentVersion: '0.1.0',
  });

  const result = await manager.checkForUpdates();
  assert.deepEqual(result, {
    ok: false,
    kind: 'development_mode',
    state: manager.getState(),
  });
  assert.equal(updater.checkCalls, 0);
});

test('moves through check, download progress, and ready states', async () => {
  const { updater, emitted, manager } = makeHarness();

  const check = await manager.checkForUpdates();
  assert.equal(check.ok, true);
  assert.equal(updater.checkCalls, 1);
  assert.equal(manager.getState().status, 'checking');

  updater.emit('update-available', {
    version: '0.2.0',
    releaseDate: '2026-07-20T00:00:00.000Z',
    releaseNotes: '<p>Safer updates</p>',
  });
  assert.deepEqual(manager.getState(), {
    status: 'available',
    currentVersion: '0.1.0',
    version: '0.2.0',
    releaseDate: '2026-07-20T00:00:00.000Z',
    releaseNotes: '<p>Safer updates</p>',
  });

  const download = await manager.downloadUpdate();
  assert.equal(download.ok, true);
  assert.equal(updater.downloadCalls, 1);

  updater.emit('download-progress', {
    percent: 42.456,
    transferred: 4_000_000,
    total: 10_000_000,
    bytesPerSecond: 1_000_000,
  });
  assert.deepEqual(manager.getState().progress, {
    percent: 42.46,
    transferred: 4_000_000,
    total: 10_000_000,
    bytesPerSecond: 1_000_000,
  });

  updater.emit('update-downloaded', { version: '0.2.0' });
  assert.equal(manager.getState().status, 'ready');
  assert.ok(emitted.some((event) => event.status === 'ready'));
});

test('installs only after a downloaded update is ready', () => {
  const harness = makeHarness();

  assert.deepEqual(harness.manager.installUpdate(), {
    ok: false,
    kind: 'update_not_ready',
    state: harness.manager.getState(),
  });

  harness.updater.emit('update-downloaded', { version: '0.2.0' });
  assert.deepEqual(harness.manager.installUpdate(), { ok: true });
  assert.equal(harness.installPrepared(), true);
  assert.deepEqual(harness.updater.installCalls, [
    { isSilent: false, forceRunAfter: true },
  ]);
});

test('turns updater errors into renderer-safe state', () => {
  const { updater, manager } = makeHarness();

  updater.emit('error', new Error('network unavailable'));

  assert.deepEqual(manager.getState(), {
    status: 'error',
    currentVersion: '0.1.0',
    message: 'network unavailable',
  });
});
