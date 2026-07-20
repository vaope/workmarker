// update_manager.js — testable state machine around electron-updater.
// The renderer receives plain data only; it never gets direct updater access.

function errorMessage(error) {
  if (error && typeof error.message === 'string') return error.message;
  return String(error || 'unknown update error');
}

function releaseNotesValue(notes) {
  let raw = '';
  if (typeof notes === 'string') raw = notes;
  else if (Array.isArray(notes)) {
    raw = notes
      .map((entry) => (typeof entry === 'string' ? entry : entry && entry.note))
      .filter(Boolean)
      .join('\n\n');
  }
  if (!/<\/?(?:p|br|ul|ol|li|div|h[1-6])\b/i.test(raw)) return raw;
  return raw
    .replace(/\r/g, '')
    .replace(/<br\s*\/?>/gi, '\n')
    .replace(/<li\b[^>]*>/gi, '- ')
    .replace(/<\/li>\s*/gi, '\n')
    .replace(/<\/p>\s*/gi, '\n\n')
    .replace(/<\/(?:div|ul|ol|h[1-6])>\s*/gi, '\n')
    .replace(/<[^>]*>/g, '')
    .replace(/&nbsp;/gi, ' ')
    .replace(/&amp;/gi, '&')
    .replace(/&lt;/gi, '<')
    .replace(/&gt;/gi, '>')
    .replace(/&quot;/gi, '"')
    .replace(/&#(?:39|x27);/gi, "'")
    .replace(/[ \t]+\n/g, '\n')
    .replace(/\n{3,}/g, '\n\n')
    .trim();
}

function createUpdateManager({
  autoUpdater,
  currentVersion,
  isPackaged,
  emit = () => {},
  beforeInstall = () => {},
  logger = console,
}) {
  autoUpdater.autoDownload = false;
  autoUpdater.autoInstallOnAppQuit = false;
  autoUpdater.logger = logger;

  let state = isPackaged
    ? { status: 'idle', currentVersion }
    : { status: 'development_mode', currentVersion };

  const setState = (patch) => {
    state = { currentVersion, ...patch };
    emit({ ...state });
    return getState();
  };

  const getState = () => ({ ...state });

  autoUpdater.on('checking-for-update', () => {
    setState({ status: 'checking' });
  });

  autoUpdater.on('update-available', (info = {}) => {
    setState({
      status: 'available',
      version: info.version || '',
      releaseDate: info.releaseDate || '',
      releaseNotes: releaseNotesValue(info.releaseNotes),
    });
  });

  autoUpdater.on('update-not-available', () => {
    setState({ status: 'not_available' });
  });

  autoUpdater.on('download-progress', (progress = {}) => {
    const number = Number(progress.percent || 0);
    setState({
      ...state,
      status: 'downloading',
      progress: {
        percent: Math.round(Math.max(0, Math.min(100, number)) * 100) / 100,
        transferred: Number(progress.transferred || 0),
        total: Number(progress.total || 0),
        bytesPerSecond: Number(progress.bytesPerSecond || 0),
      },
    });
  });

  autoUpdater.on('update-downloaded', (info = {}) => {
    setState({
      status: 'ready',
      version: info.version || state.version || '',
    });
  });

  autoUpdater.on('error', (error) => {
    logger.error('auto update failed', error);
    setState({ status: 'error', message: errorMessage(error) });
  });

  async function checkForUpdates() {
    if (!isPackaged) {
      return { ok: false, kind: 'development_mode', state: getState() };
    }
    if (state.status === 'checking' || state.status === 'downloading') {
      return { ok: false, kind: 'update_busy', state: getState() };
    }
    setState({ status: 'checking' });
    try {
      await autoUpdater.checkForUpdates();
      return { ok: true, state: getState() };
    } catch (error) {
      if (state.status !== 'error') setState({ status: 'error', message: errorMessage(error) });
      return { ok: false, kind: 'update_error', state: getState() };
    }
  }

  async function downloadUpdate() {
    if (!isPackaged) {
      return { ok: false, kind: 'development_mode', state: getState() };
    }
    if (state.status !== 'available') {
      return { ok: false, kind: 'update_not_available', state: getState() };
    }
    setState({ ...state, status: 'downloading' });
    try {
      await autoUpdater.downloadUpdate();
      return { ok: true, state: getState() };
    } catch (error) {
      if (state.status !== 'error') setState({ status: 'error', message: errorMessage(error) });
      return { ok: false, kind: 'update_error', state: getState() };
    }
  }

  function installUpdate() {
    if (state.status !== 'ready') {
      return { ok: false, kind: 'update_not_ready', state: getState() };
    }
    beforeInstall();
    autoUpdater.quitAndInstall(false, true);
    return { ok: true };
  }

  return { getState, checkForUpdates, downloadUpdate, installUpdate };
}

module.exports = { createUpdateManager, releaseNotesValue };
