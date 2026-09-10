# Graphic skill dependencies

The SVG allowlist runs with Python 3's standard library only (`argparse`, `pathlib`, `sys`, and
`xml.etree.ElementTree`). It has no separately installed Python package dependency.

The contact-sheet renderer requires the following Node dependency:

| Dependency | Resolved version | License | License evidence |
| --- | --- | --- | --- |
| `@resvg/resvg-js` | `2.6.2` | `MPL-2.0` | Its installed `package.json` `license` field and bundled `LICENSE` file |

`scripts/package-lock.json` pins the resolved version. Run `(cd scripts && npm install)` from a
clean install to fetch it. `tests/test_graphic_dependencies.py` verifies that installation and
checks the installed package metadata and license file.
