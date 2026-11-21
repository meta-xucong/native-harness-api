/** Hosted native managed-catalog probe. No authentication or model request. */
import { open, lstat, realpath } from 'node:fs/promises';
import { homedir, tmpdir } from 'node:os';
import path from 'node:path';
import { pathToFileURL } from 'node:url';
import { createRequire } from 'node:module';
import { verifyPhysicalFile, verifyVirtualFile, loadPhysicalProduct } from './workbuddy_library_driver.mjs';
import { runManagedCatalog } from './beta15_managed_catalog.mjs';

const PUBLIC_SHA = '8bfbfd68b6f4c04c70a94e1943ecc2a391dd5618e4a1cff087f77ea9d5402733';
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

/** Check all disposable roots before native import, without loading vendor code. */
export async function verifyEmptyDirectories(directories, fs) {
  for (const directory of directories) {
    await physicalDirectory(directory, fs);
    if ((await fs.readdir(directory)).length !== 0) throw new TypeError();
  }
  return true;
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
    output = await physicalFs.open(path.join(outputDir, 'managed-catalog-summary.json'), 'wx', 0o600);
    const archive = path.join(resources, 'app.asar');
    const relative = path.join('node_modules', '@genie', 'agent-harness', 'lib', 'public.js');
    const entry = path.join(resources, 'app.asar.unpacked', relative);
    const virtualEntry = path.join(archive, relative);
    const result = await runManagedCatalog(() => import(pathToFileURL(virtualEntry).href), {
      configDir: config,
      verifyEmptyRoots: () => verifyEmptyDirectories([home, config, temp, appdata, localappdata, workspace], physicalFs),
      loadProduct: () => loadPhysicalProduct(path.join(resources, 'app.asar.unpacked', 'cli', 'product.json'), physicalFs),
      verifyEntry: async () => {
        const size = await verifyPhysicalFile(entry, PUBLIC_SHA, 1024 * 1024, physicalFs);
        await verifyPhysicalFile(archive, ASAR_SHA, 1024 ** 3, physicalFs);
        await verifyVirtualFile(virtualEntry, PUBLIC_SHA, size, virtualFs);
      }
    });
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
