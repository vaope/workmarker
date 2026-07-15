function createHotkeyManager(globalShortcut, actions) {
  let active = { capture: '', main: '' };

  function normalized(pair) {
    return {
      capture: String(pair && pair.capture || '').trim(),
      main: String(pair && pair.main || '').trim(),
    };
  }

  function unregisterPair(pair) {
    if (pair.capture) globalShortcut.unregister(pair.capture);
    if (pair.main && pair.main !== pair.capture) globalShortcut.unregister(pair.main);
  }

  function tryRegister(pair) {
    if (!globalShortcut.register(pair.capture, actions.capture)) {
      return { ok: false, failed: 'capture' };
    }
    if (!globalShortcut.register(pair.main, actions.main)) {
      globalShortcut.unregister(pair.capture);
      return { ok: false, failed: 'main' };
    }
    return { ok: true };
  }

  function tryRegisterIndependent(pair) {
    const result = { capture: false, main: false };
    if (pair.capture) result.capture = globalShortcut.register(pair.capture, actions.capture);
    if (pair.main && pair.main !== pair.capture) result.main = globalShortcut.register(pair.main, actions.main);
    return result;
  }

  function registerStartupPair(candidate) {
    const next = normalized(candidate);
    if (!next.capture || !next.main) {
      return { ok: false, kind: 'invalid_accelerator', failed: !next.capture ? 'capture' : 'main', captureRegistered: false, mainRegistered: false, active: { ...active } };
    }
    if (next.capture === next.main) {
      const captureRegistered = globalShortcut.register(next.capture, actions.capture);
      active = { capture: captureRegistered ? next.capture : '', main: '' };
      return { ok: false, kind: 'duplicate_accelerator', failed: 'main', captureRegistered, mainRegistered: false, active: { ...active } };
    }
    const registered = tryRegisterIndependent(next);
    active = {
      capture: registered.capture ? next.capture : '',
      main: registered.main ? next.main : '',
    };
    return {
      ok: registered.capture && registered.main,
      kind: registered.capture && registered.main ? undefined : 'registration_conflict',
      failed: !registered.capture ? 'capture' : (!registered.main ? 'main' : undefined),
      captureRegistered: registered.capture,
      mainRegistered: registered.main,
      active: { ...active },
    };
  }

  function registerPair(candidate) {
    const next = normalized(candidate);
    if (!next.capture || !next.main) return { ok: false, kind: 'invalid_accelerator', active: { ...active } };
    if (next.capture === next.main) return { ok: false, kind: 'duplicate_accelerator', active: { ...active } };

    const previous = { ...active };
    unregisterPair(previous);
    const attempt = tryRegister(next);
    if (attempt.ok) {
      active = next;
      return { ok: true, active: { ...active } };
    }

    unregisterPair(next);
    if (previous.capture || previous.main) {
      const restored = tryRegisterIndependent(previous);
      if ((previous.capture && !restored.capture) || (previous.main && !restored.main)) {
        throw new Error('failed to restore previous global shortcuts');
      }
    }
    active = previous;
    return { ok: false, kind: 'registration_conflict', failed: attempt.failed, active: { ...active } };
  }

  return Object.freeze({
    registerStartupPair,
    registerPair,
    activePair: () => ({ ...active }),
    dispose() { unregisterPair(active); active = { capture: '', main: '' }; },
  });
}

module.exports = { createHotkeyManager };
