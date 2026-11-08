import assert from 'node:assert/strict';
import * as fs from 'node:fs/promises';
import { tmpdir } from 'node:os';
import path from 'node:path';
import { verifyEmptyDirectories } from '../../tools/workbuddy_managed_catalog_driver.mjs';
// Canonicalize a Windows short-path tmp alias before physical-path assertions.
const root = await fs.realpath(await fs.mkdtemp(path.join(tmpdir(), 'managed-catalog-loader-')));
try {
  const directories = ['home', 'config', 'temp', 'appdata', 'localappdata', 'workspace'].map(name => path.join(root, name));
  for (const directory of directories) await fs.mkdir(directory);
  assert.equal(await verifyEmptyDirectories(directories, fs), true);
  for (const directory of directories) {
    const marker = path.join(directory, 'synthetic-only'); await fs.writeFile(marker, 'fixture');
    await assert.rejects(verifyEmptyDirectories(directories, fs)); await fs.unlink(marker);
  }
  await assert.rejects(verifyEmptyDirectories([path.join(root, 'missing')], fs));
  let read = false;
  await assert.rejects(verifyEmptyDirectories([root], {lstat:async()=>({isSymbolicLink:()=>true}),readdir:async()=>{read=true;return[];}}));
  assert.equal(read, false);
  console.log('managed catalog loader synthetic fixtures passed');
} finally { await fs.rm(root, {recursive:true, force:true}); }
