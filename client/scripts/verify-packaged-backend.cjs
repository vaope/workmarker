const fs = require('fs');
const path = require('path');

function sorted(values) {
  return new Set([...values].sort());
}

function clientCommands(source) {
  const commands = new Set();
  const pattern = /\bcallBackend\s*\(/g;
  for (const match of source.matchAll(pattern)) {
    const argumentsSource = source.slice(match.index + match[0].length);
    const literal = argumentsSource.match(
      /^\s*(['"])([a-z][a-z0-9_]*)\1\s*,/
    );
    if (!literal) {
      throw new Error(
        'callBackend command must be a single- or double-quoted literal'
      );
    }
    commands.add(literal[2]);
  }
  return sorted(commands);
}

function productionJavaScriptFiles(root) {
  const ignored = new Set(['dist', 'node_modules', 'scripts', 'tests']);
  const files = [];
  function visit(directory) {
    for (const entry of fs.readdirSync(directory, { withFileTypes: true })) {
      if (entry.isDirectory() && ignored.has(entry.name)) continue;
      const entryPath = path.join(directory, entry.name);
      if (entry.isDirectory()) visit(entryPath);
      else if (entry.isFile() && entry.name.endsWith('.js')) files.push(entryPath);
    }
  }
  visit(root);
  return files;
}

function verifyClientCommandScope(clientRoot) {
  const resolvedRoot = path.resolve(clientRoot);
  const mainPath = path.join(resolvedRoot, 'main.js');
  const bridgePath = path.join(resolvedRoot, 'python_bridge.js');
  for (const file of productionJavaScriptFiles(resolvedRoot)) {
    if (path.resolve(file) === mainPath) continue;
    if (path.resolve(file) === bridgePath) continue;
    const source = fs.readFileSync(file, 'utf8');
    if (/\bcallBackend\s*\(/.test(source) || /require\([^)]*python_bridge/.test(source)) {
      const relative = path.relative(resolvedRoot, file).split(path.sep).join('/');
      throw new Error(
        `callBackend may only be invoked from client/main.js: ${relative}`
      );
    }
  }
  return {
    commands: clientCommands(fs.readFileSync(mainPath, 'utf8')),
    mainPath,
  };
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
  const scope = verifyClientCommandScope(path.dirname(mainPath));
  const client = scope.commands;
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
module.exports.verifyClientCommandScope = verifyClientCommandScope;
module.exports.verifyPackagedBackend = verifyPackagedBackend;
