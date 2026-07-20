// python_bridge.js — spawn the Python GUI backend and exchange JSON over stdin/stdout.
// Contract: docs/designs/F001-client-architecture.md §3
const { spawn } = require('child_process');
const path = require('path');
const { app } = require('electron');

// client/ lives directly under the repo root; the Python package is importable from there.
const REPO_ROOT = path.resolve(__dirname, '..');
function backendRoot() {
  return app.isPackaged ? process.resourcesPath : REPO_ROOT;
}

/**
 * Call a backend command. Request payload is sent as JSON on stdin; the final
 * JSON object on stdout is parsed and returned.
 * @param {string} command  one of propose|commit|projects|tasks|timeline|init
 * @param {object} payload  request body
 * @param {string} pythonCmd python executable (from config, default "python")
 * @returns {Promise<object>} parsed JSON response (always has an `ok` field on success path)
 */
function callBackend(command, payload, pythonCmd = 'python') {
  return new Promise((resolve, reject) => {
    const args = ['-m', 'workeventagent.gui', command];
    let child;
    try {
      child = spawn(pythonCmd, args, {
        cwd: backendRoot(),
        env: { ...process.env, PYTHONIOENCODING: 'utf-8', PYTHONUTF8: '1' },
      });
    } catch (err) {
      return reject(new Error(`failed to spawn ${pythonCmd}: ${err.message}`));
    }

    let stdout = '';
    let stderr = '';
    child.stdout.setEncoding('utf-8');
    child.stderr.setEncoding('utf-8');
    child.stdout.on('data', (d) => { stdout += d; });
    child.stderr.on('data', (d) => { stderr += d; });

    child.on('error', (err) =>
      reject(new Error(`failed to spawn ${pythonCmd}: ${err.message}. Is Python on PATH?`)));

    child.on('close', (code) => {
      if (code !== 0) {
        return reject(new Error(
          `backend "${command}" crashed (exit ${code}): ${stderr.trim() || stdout.trim() || '(no output)'}`));
      }
      const trimmed = stdout.trim();
      if (!trimmed) {
        return reject(new Error(`backend "${command}" produced no stdout. stderr: ${stderr.trim()}`));
      }
      resolve(parseLastJson(trimmed, command));
    });

    try {
      child.stdin.write(JSON.stringify(payload || {}), 'utf-8');
      child.stdin.end();
    } catch (err) {
      reject(new Error(`failed to write stdin for "${command}": ${err.message}`));
    }
  });
}

// Be tolerant: backend should print exactly one JSON object, but if stray lines
// leak in, parse the last non-empty line that is valid JSON.
function parseLastJson(text, command) {
  try {
    return JSON.parse(text);
  } catch {
    const lines = text.split(/\r?\n/).map((l) => l.trim()).filter(Boolean);
    for (let i = lines.length - 1; i >= 0; i -= 1) {
      try {
        return JSON.parse(lines[i]);
      } catch {
        /* keep scanning upward */
      }
    }
    throw new Error(`backend "${command}" returned non-JSON output: ${text.slice(0, 400)}`);
  }
}

module.exports = { callBackend, backendRoot, REPO_ROOT };
