import { cp, mkdir, readFile, readdir, realpath, rename, rm, symlink, writeFile } from 'node:fs/promises';
import { spawn } from 'node:child_process';
import { createHash } from 'node:crypto';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const repo = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const source = path.join(repo, 'apps', 'web');
const buildRoot = path.join(repo, 'work', 'build');
const stage = path.join(buildRoot, 'static-web');
const output = path.join(source, 'out');

async function removeGenerated(target, parent) {
  if (path.dirname(path.resolve(target)) !== path.resolve(parent)) throw new Error('Unsafe build cleanup target');
  await rm(target, { recursive: true, force: true });
}

async function digestFiles(directory, base = directory) {
  const files = [];
  for (const entry of await readdir(directory, { withFileTypes: true })) {
    const file = path.join(directory, entry.name);
    if (entry.isDirectory()) files.push(...await digestFiles(file, base));
    else {
      // Size and checksum must describe the same read, even if another process
      // replaces an asset while the build manifest is being generated.
      const contents = await readFile(file);
      files.push({ path: path.relative(base, file).replaceAll('\\', '/'), bytes: contents.byteLength,
        sha256: createHash('sha256').update(contents).digest('hex') });
    }
  }
  return files.sort((a, b) => a.path.localeCompare(b.path));
}

await mkdir(buildRoot, { recursive: true });
// Serial build lock: this directory only contains copies and compiler output.
const lock = path.join(buildRoot, 'static-web.lock');
try { await mkdir(lock); } catch { throw new Error('Another static build is active (work/build/static-web.lock)'); }
try {
  await removeGenerated(stage, buildRoot);
  await mkdir(path.join(stage, 'app'), { recursive: true });
  for (const name of ['components', 'lib', 'public', 'next.config.mjs', 'package.json'])
    await cp(path.join(source, name), path.join(stage, name), { recursive: true });
  for (const name of ['stocks', 'layout.tsx', 'styles.css', 'icon.png'])
    await cp(path.join(source, 'app', name), path.join(stage, 'app', name), { recursive: true });
  await writeFile(path.join(stage, 'app', 'page.tsx'), 'export { default } from "./stocks/page";\n');
  const tsconfig = JSON.parse(await readFile(path.join(source, 'tsconfig.json'), 'utf8'));
  tsconfig.include = ['next-env.d.ts', 'app/**/*.ts', 'app/**/*.tsx', 'components/**/*.ts', 'components/**/*.tsx', 'lib/**/*.ts', 'lib/**/*.tsx', '.next/types/**/*.ts'];
  await writeFile(path.join(stage, 'tsconfig.json'), JSON.stringify(tsconfig, null, 2));
  await symlink(await realpath(path.join(source, 'node_modules')), path.join(stage, 'node_modules'), process.platform === 'win32' ? 'junction' : 'dir');
  const next = path.join(source, 'node_modules', 'next', 'dist', 'bin', 'next');
  const status = await new Promise((resolve, reject) => {
    const child = spawn(process.execPath, [next, 'build', '--webpack'], { cwd: stage, stdio: 'inherit',
      env: { ...process.env, MARKET_WEB_OUTPUT: 'static', NEXT_BUILD_DIR: '.next', NEXT_TELEMETRY_DISABLED: '1' } });
    child.once('error', reject); child.once('exit', resolve);
  });
  if (status !== 0) throw new Error(`Static build failed (${status}); previous output is preserved`);
  const built = path.join(stage, 'out');
  await cp(path.join(repo, 'ops', 'cloudflare', 'legacy-routes.mjs'), path.join(built, 'legacy-routes.mjs'));
  await writeFile(path.join(built, 'legacy-entry.mjs'), 'import { legacyDestination } from "/legacy-routes.mjs";\nconst target = legacyDestination(new URL(location.href));\nif (target) location.replace(target);\n');
  await writeFile(path.join(built, 'legacy.html'), '<!doctype html><html lang="ko"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>주식담</title><script type="module" src="/legacy-entry.mjs"></script><a href="/stocks">주식담 열기</a></html>');
  await writeFile(path.join(built, '_headers'), '/*\n  X-Content-Type-Options: nosniff\n  Referrer-Policy: strict-origin-when-cross-origin\n/_next/static/*\n  Cache-Control: public, max-age=31536000, immutable\n/stocks\n  Cache-Control: no-cache\n/stocks/sw.js\n  Cache-Control: no-cache\n/legacy*\n  Cache-Control: no-cache\n');
  const files = await digestFiles(built);
  await writeFile(path.join(built, 'build-manifest.json'), JSON.stringify({ profile: 'static', release: process.env.MARKET_RELEASE || 'local',
    built_at: new Date().toISOString(), files, bytes: files.reduce((total, file) => total + file.bytes, 0) }, null, 2));
  // Publish only after compilation and manifest validation. Original app source,
  // standalone builds, dependencies and environment files are never moved.
  const previous = path.join(source, 'out.previous');
  await removeGenerated(previous, source);
  let hadPrevious = false;
  try { await rename(output, previous); hadPrevious = true; } catch (error) { if (error.code !== 'ENOENT') throw error; }
  try { await rename(built, output); }
  catch (error) { if (hadPrevious) await rename(previous, output); throw error; }
  await removeGenerated(previous, source);
  console.log(JSON.stringify({ output, files: files.length, bytes: files.reduce((total, file) => total + file.bytes, 0) }));
} finally {
  await removeGenerated(stage, buildRoot);
  await removeGenerated(lock, buildRoot);
}
