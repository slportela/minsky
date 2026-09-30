"""Keep lock files pointing at the public registries only.

Developers may install through a private mirror (a company proxy for PyPI or npm). The lock
files they generate then record the mirror's URLs, which must not be committed: they leak
internal hosts and break installs for everyone else. This rewrites every package URL to its
public equivalent. The content hashes in the lock files do not change, so installs stay verified.

- uv.lock: registry -> https://pypi.org/simple; files -> https://files.pythonhosted.org/packages/...
- package-lock.json: resolved -> https://registry.npmjs.org/<name>/-/<file>.tgz

Usage: python scripts/public_lockfiles.py [--check] [FILE ...]   (default: both lock files)
Exit code 1 if a file was changed (fix mode) or would change (--check), or has a URL it can't map.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

PYPI_INDEX = "https://pypi.org/simple"
PYPI_FILES = "https://files.pythonhosted.org/packages/"
NPM_REGISTRY = "https://registry.npmjs.org/"
DEFAULT_FILES = ["uv.lock", "frontend/package-lock.json"]

_UV_REGISTRY = re.compile(r'registry = "(?!https://pypi\.org/simple")[^"]+"')
# PyPI file paths are <2 hex>/<2 hex>/<60 hex>/<filename>; mirrors keep that tail.
_UV_FILE = re.compile(
    r'"https?://(?!files\.pythonhosted\.org/)[^"]*?/packages/([0-9a-f]{2}/[0-9a-f]{2}/[0-9a-f]{60}/[^"/]+)"'
)
_UV_ANY_URL = re.compile(r'"(https?://[^"/]+)/')


def fix_uv_lock(text: str) -> tuple[str, list[str]]:
    text = _UV_REGISTRY.sub(f'registry = "{PYPI_INDEX}"', text)
    text = _UV_FILE.sub(lambda m: f'"{PYPI_FILES}{m.group(1)}"', text)
    public = {"https://pypi.org", "https://files.pythonhosted.org"}
    unmapped = sorted({h for h in _UV_ANY_URL.findall(text) if h not in public})
    return text, unmapped


def fix_package_lock(text: str) -> tuple[str, list[str]]:
    data = json.loads(text)
    unmapped = []
    for key, pkg in data.get("packages", {}).items():
        resolved = pkg.get("resolved")
        if not key or not isinstance(resolved, str) or not resolved.startswith(("http://", "https://")):
            continue  # the root project, links, git or file dependencies
        if resolved.startswith(NPM_REGISTRY):
            continue
        name = key.rsplit("node_modules/", 1)[-1]
        marker = f"/{name}/-/"
        if marker not in resolved:
            unmapped.append(resolved)
            continue
        pkg["resolved"] = NPM_REGISTRY + name + "/-/" + resolved.split(marker, 1)[1]
    return json.dumps(data, indent=2, ensure_ascii=False) + "\n", unmapped


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("files", nargs="*", default=DEFAULT_FILES)
    parser.add_argument("--check", action="store_true", help="report only, do not write")
    args = parser.parse_args(argv)
    status = 0
    for name in args.files:
        path = Path(name)
        if not path.exists():
            continue
        original = path.read_text(encoding="utf-8")
        fixer = fix_package_lock if path.name == "package-lock.json" else fix_uv_lock
        fixed, unmapped = fixer(original)
        for url in unmapped:
            print(f"{path}: cannot map to a public registry: {url}")
            status = 1
        if fixed != original:
            status = 1
            if args.check:
                print(f"{path}: contains non-public registry URLs (run: make lock)")
            else:
                path.write_text(fixed, encoding="utf-8")
                print(f"{path}: rewritten to public registry URLs")
    return status


if __name__ == "__main__":
    sys.exit(main())
