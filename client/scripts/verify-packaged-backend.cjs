const fs = require('fs');
const path = require('path');

function sorted(values) {
  return new Set([...values].sort());
}

function clientCommands(source) {
  const commands = new Set();
  const pattern = /\bcallBackend\(\s*['"]([a-z][a-z0-9_]*)['"]/g;
  for (const match of source.matchAll(pattern)) commands.add(match[1]);
  return sorted(commands);
}

function backendCommands(source) {
  const start = source.indexOf('handlers = {');
  if (start < 0) throw new Error('Python GUI command handler table was not found');
  const tail = source.slice(start);
  const end = tail.search(/^\s*}\s*$/m);
  if (end < 0) throw new Error('Python GUI command handler table is unterminated');

  const commands = new Set();
  const pattern = /^\s*"([a-z][a-z0-9_]*)":\s*handle_[A-Za-z0-9_]+,\s*$/gm;
  for (const match of tail.slice(0, end).matchAll(pattern)) commands.add(match[1]);
  return sorted(commands);
}

function verifyPackagedBackend(mainPath, guiPath) {
  const client = clientCommands(fs.readFileSync(mainPath, 'utf8'));
  const backend = backendCommands(fs.readFileSync(guiPath, 'utf8'));
  const missing = [...client].filter((command) => !backend.has(command));
  if (missing.length) {
    throw new Error(
      `packaged backend is missing client commands: ${missing.join(', ')}`
    );
  }
  return { client, backend, missing };
}

async function verifyAfterBuild(context) {
  const clientRoot = path.resolve(__dirname, '..');
  const outDir = path.resolve(context.outDir);
  const packagedGui = path.join(
    outDir,
    'win-unpacked',
    'resources',
    'workeventagent',
    'gui.py'
  );
  if (!fs.existsSync(packagedGui)) {
    throw new Error(`packaged Python backend was not found: ${packagedGui}`);
  }
  const result = verifyPackagedBackend(
    path.join(clientRoot, 'main.js'),
    packagedGui
  );
  process.stdout.write(
    `Verified ${result.client.size} client/backend commands in packaged resources.\n`
  );
  return [];
}

module.exports = verifyAfterBuild;
module.exports.backendCommands = backendCommands;
module.exports.clientCommands = clientCommands;
module.exports.verifyPackagedBackend = verifyPackagedBackend;
