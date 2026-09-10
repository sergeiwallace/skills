# Presentation skill dependencies

The skill is self-contained below its installed `skills/presentation` directory. It does not import host-repository Python helpers or resolve Node modules from a host repository root.

## Bootstrap

From the installed skill directory, run the following once. The browser download is required for rendering, PDF/PNG export, and deterministic DOM lint; it is deliberately not bundled in the skill archive.

```sh
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -r requirements.txt
npm ci
npx playwright install chromium
```

`scripts/presentation-toolchain.mjs install` performs the final two Node steps with its cache inside the skill directory. Set `PRESENTATION_BROWSER_CACHE` before that command to use a different browser-cache location. The renderer otherwise uses `.cache/presentation/playwright/1.58.2` under the installed skill directory.

## Runtime dependencies

Python standard-library modules need no separate installation. The direct Python packages are pinned in `requirements.txt`; the direct Node packages are pinned in `package.json` and the full resolved Node graph is locked in `package-lock.json`.

| Runtime | Dependency | Resolved version | License | License evidence |
| --- | --- | --- | --- | --- |
| Python | `jsonschema` | `4.26.0` | MIT | Installed distribution metadata and bundled `jsonschema-4.26.0.dist-info/licenses/COPYING` |
| Python | `Pillow` | `11.3.0` | HPND | Installed distribution metadata and bundled `pillow-11.3.0.dist-info/licenses/LICENSE` |
| Node | `@slidev/cli` | `52.19.1` | MIT | Installed `node_modules/@slidev/cli/package.json` `license` field and bundled `LICENSE` |
| Node | `@slidev/theme-default` | `0.25.0` | MIT | Installed `node_modules/@slidev/theme-default/package.json` `license` field and bundled `LICENSE` |
| Node | `playwright` | `1.58.2` | Apache-2.0 | Installed `node_modules/playwright/package.json` `license` field and bundled `LICENSE` |
| Node | `playwright-chromium` | `1.58.2` | Apache-2.0 | Installed `node_modules/playwright-chromium/package.json` `license` field and bundled `LICENSE` |

`tests/test_presentation_dependencies.py` creates clean temporary Python and Node installs and checks that the pinned packages are present. It intentionally uses `npm install --ignore-scripts`: that proves dependency resolution without downloading Chromium; the explicit bootstrap command above performs that separately.

## Visual-review adapter

The required pixel-review gate is provider-neutral. Before running `scripts/critic` without `--review`, set `PRESENTATION_REVIEWER_DISPATCH` to a command (including any fixed arguments) for an adapter that accepts this invocation shape:

```text
$PRESENTATION_REVIEWER_DISPATCH --output-schema <absolute critic-review.schema.json> -o <writable output.json> [-i <contact-sheet.png>] -i <slide-1.png> ... <prompt>
```

The adapter receives one optional contact sheet, every numbered slide PNG via repeatable `-i`, and one final prompt argument. It must write exactly one JSON object to `-o`, conforming to the provided schema, with the supplied render digest and its real reviewer identity. A nonzero exit status or invalid/missing output fails the critic stage; there is no fallback or no-op reviewer. This lets an installer use a local vision model, CLI, or API wrapper without changing the approval gate.
