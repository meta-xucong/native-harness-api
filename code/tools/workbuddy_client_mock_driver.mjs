/** Hosted public Client + custom in-memory transport probe. No server/provider. */
import { open, lstat, realpath } from 'node:fs/promises';
import { homedir, tmpdir } from 'node:os';
import path from 'node:path';
import { pathToFileURL } from 'node:url';
import { createRequire } from 'node:module';
import { verifyPhysicalFile, verifyVirtualFile } from './workbuddy_library_driver.mjs';
import { runClientMock } from './beta15_client_mock.mjs';

export const PINS = Object.freeze({
  'package.json': '9f7aaf7ba9cf0f8bb961e22a80c9e7ac24c2fcb4232b145e91eb39bcecb539a4',
  'lib/client/public.js': 'fe0b38691349e73e52f4a9ab2e26d085f65a03d17a412b25b70324153a2cae41',
  'lib/transport/effect.js': 'abb6ecab760ea4a1bc5298b4b492b336539e7d775d9b89ccb6ad9e7f74eb566d'
});
const ASAR_SHA = 'dcabcd90f07a627a06f1fd55bc872a1c25a79aab43c3012930b6287840ab3d20';

async function physicalDirectory(value, fs) {
  if (!path.isAbsolute(value) || path.resolve(value) !== value) throw new TypeError();
  for (let current = value;; current = path.dirname(current)) {
    const stat = await fs.lstat(current);
    if (stat.isSymbolicLink() || !stat.isDirectory()) throw new TypeError();
    if (path.dirname(current) === current) break;
  }
  if ((await fs.realpath(value)).toLowerCase() !== value.toLowerCase()) throw new TypeError();
}

/** Checks physical pins and Electron's virtual view without importing vendor code. */
export async function verifyClientEntries(resources, physicalFs, virtualFs,
  verifyPhysical = verifyPhysicalFile, verifyVirtual = verifyVirtualFile) {
  const archive = path.join(resources, 'app.asar');
  const relative = path.join('node_modules', '@genie', 'agent-harness');
  const physicalRoot = path.join(resources, 'app.asar.unpacked', relative);
  const virtualRoot = path.join(archive, relative);
  await verifyPhysical(archive, ASAR_SHA, 1024 ** 3, physicalFs);
  for (const [member, pin] of Object.entries(PINS)) {
    const size = await verifyPhysical(path.join(physicalRoot, member), pin, 1024 * 1024, physicalFs);
    await verifyVirtual(path.join(virtualRoot, member), pin, size, virtualFs);
  }
  // Effect is already a Client/Transport dependency. Its public entry is also
  // used explicitly to settle the transport's idempotent Effect-based close.
  await verifyVirtual(path.join(archive, 'node_modules', 'effect', 'package.json'),
    'ae50c2b29f19efa6891c4b008c69db711f5b4415765fc1c87996279fbea71c35', 2087, virtualFs);
  await verifyVirtual(path.join(archive, 'node_modules', 'effect', 'dist', 'index.js'),
    'fa0b1ffb925b4c771d8ca81012cdf47e25a8060b4bcddd7e6416bbe9cb23db98', 12944, virtualFs);
}

async function main(argv) {
  let output;
  try {
    if (process.platform !== 'win32' || process.env.GITHUB_ACTIONS !== 'true' ||
        process.env.RUNNER_ENVIRONMENT !== 'github-hosted' || process.versions.electron !== '37.10.3') throw new TypeError();
    if (argv.length !== 2 || argv[0] !== '--run-root') throw new TypeError();
    const require = createRequire(import.meta.url);
    if (require.resolve('original-fs') !== 'original-fs') throw new TypeError();
    const physicalFs = require('original-fs').promises;
    const virtualFs = { open, lstat, realpath };
    const root = argv[1], resources = path.join(root, 'official', 'resources');
    const home = path.join(root, 'isolated', 'home');
    const config = path.join(root, 'isolated', 'config');
    const temp = path.join(root, 'isolated', 'temp');
    const appdata = path.join(root, 'isolated', 'appdata');
    const localappdata = path.join(root, 'isolated', 'localappdata');
    const workspace = path.join(root, 'workspace');
    const outputDir = path.join(root, 'output');
    for (const value of [root, home, config, temp, appdata, localappdata, workspace, outputDir]) await physicalDirectory(value, physicalFs);
    if (path.resolve(homedir()).toLowerCase() !== home.toLowerCase() ||
        path.resolve(tmpdir()).toLowerCase() !== temp.toLowerCase() ||
        process.env.USERPROFILE !== home || process.env.HOME !== home ||
        process.env.TEMP !== temp || process.env.TMP !== temp ||
        process.env.APPDATA !== appdata || process.env.LOCALAPPDATA !== localappdata ||
        process.env.CODEBUDDY_CONFIG_DIR !== config || process.env.WORKBUDDY_CONFIG_DIR !== config ||
        process.cwd() !== workspace) throw new TypeError();
    output = await physicalFs.open(path.join(outputDir, 'client-mock-summary.json'), 'wx', 0o600);
    const virtualRoot = path.join(resources, 'app.asar', 'node_modules', '@genie', 'agent-harness');
    const result = await runClientMock(async () => {
      // Exact targets of the pinned package's ./client and ./transport exports.
      // No root/server barrel, native Harness, Product loader or auth provider.
      const { createClient } = await import(pathToFileURL(path.join(virtualRoot, 'lib', 'client', 'public.js')).href);
      const { fromAcpStream } = await import(pathToFileURL(path.join(virtualRoot, 'lib', 'transport', 'effect.js')).href);
      const { Effect } = await import(pathToFileURL(path.join(resources, 'app.asar', 'node_modules', 'effect', 'dist', 'index.js')).href);
      return { createClient, fromAcpStream, closeTransport: transport => Effect.runPromise(transport.close()) };
    }, { verifyEntry: () => verifyClientEntries(resources, physicalFs, virtualFs) });
    await output.writeFile(JSON.stringify(result) + '\n');
    await output.sync();
    process.exitCode = result.success === true ? 0 : 1;
  } catch {
    process.exitCode = 1;
  } finally { if (output) await output.close().catch(() => { process.exitCode = 1; }); }
}

if (process.argv[1] && pathToFileURL(path.resolve(process.argv[1])).href === import.meta.url) {
  await main(process.argv.slice(2));
}
