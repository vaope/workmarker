const assert = require('assert');
const fs = require('fs');
const os = require('os');
const path = require('path');
const test = require('node:test');

const {
  backendCommands,
  clientCommands,
  verifyClientCommandScope,
  verifyPackagedBackend,
} = require('../scripts/verify-packaged-backend.cjs');

test('extracts literal client calls and Python GUI handlers', () => {
  assert.deepStrictEqual(
    [...clientCommands("callBackend('projects', {});\ncallBackend('complete_task', {});")],
    ['complete_task', 'projects']
  );
  assert.deepStrictEqual(
    [...backendCommands(`
      handlers = {
          "projects": handle_projects,
          "complete_task": handle_complete_task,
      }
    `)],
    ['complete_task', 'projects']
  );
});

test('rejects a package whose backend is older than its client', () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'wea-package-contract-'));
  try {
    const mainPath = path.join(root, 'main.js');
    const guiPath = path.join(root, 'gui.py');
    fs.writeFileSync(
      mainPath,
      "callBackend('projects', {});\ncallBackend('complete_task', {});\n",
      'utf8'
    );
    fs.writeFileSync(
      guiPath,
      'handlers = {\n    "projects": handle_projects,\n}\n',
      'utf8'
    );

    assert.throws(
      () => verifyPackagedBackend(mainPath, guiPath),
      /packaged backend is missing client commands: complete_task/
    );
  } finally {
    fs.rmSync(root, { recursive: true, force: true });
  }
});

test('rejects dynamic and template-string backend commands', () => {
  assert.throws(
    () => clientCommands("const command = 'complete_task';\ncallBackend(command, {});"),
    /callBackend command must be a single- or double-quoted literal/
  );
  assert.throws(
    () => clientCommands('callBackend(`complete_task`, {});'),
    /callBackend command must be a single- or double-quoted literal/
  );
});

test('rejects backend calls outside the production main process boundary', () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'wea-client-scope-'));
  try {
    fs.mkdirSync(path.join(root, 'windows'));
    fs.writeFileSync(
      path.join(root, 'main.js'),
      "callBackend('projects', {});\n",
      'utf8'
    );
    fs.writeFileSync(
      path.join(root, 'python_bridge.js'),
      'function callBackend(command, payload) { return [command, payload]; }\n',
      'utf8'
    );
    fs.writeFileSync(
      path.join(root, 'windows', 'rogue.js'),
      "callBackend('complete_task', {});\n",
      'utf8'
    );

    assert.throws(
      () => verifyClientCommandScope(root),
      /callBackend may only be invoked from client\/main\.js: windows\/rogue\.js/
    );

    fs.writeFileSync(
      path.join(root, 'windows', 'rogue.js'),
      "const bridge = require('../python_bridge');\nvoid bridge;\n",
      'utf8'
    );
    assert.throws(
      () => verifyClientCommandScope(root),
      /callBackend may only be invoked from client\/main\.js: windows\/rogue\.js/
    );
  } finally {
    fs.rmSync(root, { recursive: true, force: true });
  }
});

test('accepts the current source client/backend command contract', () => {
  const clientRoot = path.resolve(__dirname, '..');
  const repoRoot = path.resolve(clientRoot, '..');
  const result = verifyPackagedBackend(
    path.join(clientRoot, 'main.js'),
    path.join(repoRoot, 'workeventagent', 'gui.py')
  );

  assert(result.client.size > 20);
  assert.deepStrictEqual(result.missing, []);
});
