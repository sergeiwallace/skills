#!/usr/bin/env node
import { existsSync, readFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { spawnSync } from 'node:child_process';
import { createRequire } from 'node:module';

const require = createRequire(import.meta.url);
// This skill's own dependencies travel with it via a co-located package.json
// (skills/presentation/package.json) rather than a host repo's root -- a standalone
// skills-catalog checkout has no root package.json of its own.
const presentationDir = dirname(dirname(fileURLToPath(import.meta.url)));
const versionArgument = process.argv.indexOf('--renderer-version');
const expected = versionArgument === -1 ? undefined : process.argv[versionArgument + 1];
const playwrightVersion = require(join(presentationDir, 'package.json')).devDependencies.playwright;
// The browser cache location (a download destination, not a require() resolution
// path) may be redirected for test isolation -- unlike node_modules, which always
// resolves from presentationDir so the skill's own dependencies stay self-contained.
const cache = process.env.PRESENTATION_BROWSER_CACHE
  ?? join(presentationDir, '.cache', 'presentation', 'playwright', playwrightVersion);
const repair = 'node scripts/presentation-toolchain.mjs install';

if (process.argv[2] === 'install') {
  const install = spawnSync('npm', ['ci'], {
    cwd: presentationDir,
    stdio: 'inherit',
    env: { ...process.env, npm_config_cache: join(presentationDir, '.cache', 'presentation', 'npm') },
  });
  if (install.status) process.exit(install.status);
  const browser = spawnSync('npx', ['playwright', 'install', 'chromium'], {
    cwd: presentationDir,
    stdio: 'inherit',
    env: { ...process.env, PLAYWRIGHT_BROWSERS_PATH: cache },
  });
  process.exit(browser.status ?? 1);
}

const browsers = JSON.parse(readFileSync(join(dirname(require.resolve('playwright-core/package.json')), 'browsers.json'))).browsers;
const chromium = browsers.find((browser) => browser.name === 'chromium');

function chromiumExecutable(cache) {
  if (!chromium) return undefined;
  const root = join(cache, `chromium-${chromium.revision}`);
  const candidates = process.platform === 'darwin'
    ? [join(root, 'chrome-mac', 'Chromium.app', 'Contents', 'MacOS', 'Chromium'), join(root, 'chrome-mac-arm64', 'Chromium.app', 'Contents', 'MacOS', 'Chromium')]
    : process.platform === 'win32'
      ? [join(root, 'chrome-win', 'chrome.exe'), join(root, 'chrome-win64', 'chrome.exe')]
      : [join(root, 'chrome-linux', 'chrome'), join(root, 'chrome-linux64', 'chrome')];
  return candidates.find(existsSync);
}
let missing = [];
let slidevVersion;
try { slidevVersion = require('@slidev/cli/package.json').version; } catch { missing.push('local @slidev/cli module'); }
try { require('playwright/package.json'); } catch { missing.push('local playwright module'); }
if (!chromiumExecutable(cache)) missing.push(`Playwright Chromium executable for revision ${chromium?.revision ?? 'unknown'} in ${cache}`);
if (expected && slidevVersion && expected !== slidevVersion) missing.push(`Slidev version mismatch (manifest ${expected}, installed ${slidevVersion})`);
if (missing.length) {
  console.error(`presentation toolchain unavailable: ${missing.join('; ')}. Repair with: ${repair}`);
  process.exit(3);
}
console.log(JSON.stringify({ slidevVersion, playwrightVersion, browserCache: cache }));
