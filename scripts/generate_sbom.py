#!/usr/bin/env python3
"""Generate a CycloneDX 1.5 Software Bill of Materials (SBOM) on request.

Produces a machine-readable JSON SBOM from the ACTUAL resolved dependencies:
  - backend: the installed Python distributions (via importlib.metadata), so the
    SBOM reflects what is really in the environment, not just the pins;
  - frontend: the resolved versions in frontend/package-lock.json.

Standard: CycloneDX 1.5 (https://cyclonedx.org). Output is deterministic
(components sorted) so regenerating in an unchanged environment yields an
identical file — easy to diff and commit.

Usage:
    python scripts/generate_sbom.py                 # -> sbom.cdx.json (repo root)
    python scripts/generate_sbom.py --out /path.json
    python scripts/generate_sbom.py --print         # to stdout

Pair this with the authoritative hand-written SBOM.md (which carries the
standard, scope, and license summary on top).
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SPEC_VERSION = "1.5"


def _purl_pypi(name: str, version: str) -> str:
    return f"pkg:pypi/{name.lower()}@{version}"


def _purl_npm(name: str, version: str) -> str:
    # scoped names (@scope/pkg) keep the @ in the namespace segment
    return f"pkg:npm/{name}@{version}"


def _backend_components() -> list[dict]:
    """Every installed Python distribution in the current environment."""
    from importlib import metadata

    comps = []
    for dist in metadata.distributions():
        name = dist.metadata["Name"]
        if not name:
            continue
        version = dist.version or "0"
        meta = dist.metadata
        lic = meta.get("License") or ""
        # Fall back to the classifier license when the License field is a URL/blob
        if (not lic or len(lic) > 60) and meta.get_all("Classifier"):
            for c in meta.get_all("Classifier"):
                if c.startswith("License ::"):
                    lic = c.split("::")[-1].strip()
                    break
        comp = {
            "type": "library",
            "name": name,
            "version": version,
            "purl": _purl_pypi(name, version),
            "scope": "required",
            "properties": [{"name": "ecosystem", "value": "pypi"}],
        }
        if lic:
            comp["licenses"] = [{"license": {"name": lic[:80]}}]
        comps.append(comp)
    return comps


def _frontend_components() -> list[dict]:
    """Top-level deps + devDeps resolved in package-lock.json."""
    lock_path = ROOT / "frontend" / "package-lock.json"
    if not lock_path.exists():
        return []
    lock = json.loads(lock_path.read_text())
    pkgs = lock.get("packages", {})
    root = pkgs.get("", {})
    wanted = {}
    wanted.update(root.get("dependencies", {}))
    wanted.update(root.get("devDependencies", {}))
    comps = []
    for name in wanted:
        entry = pkgs.get(f"node_modules/{name}", {})
        version = entry.get("version", "0")
        comp = {
            "type": "library",
            "name": name,
            "version": version,
            "purl": _purl_npm(name, version),
            "scope": "required" if name in root.get("dependencies", {}) else "optional",
            "properties": [{"name": "ecosystem", "value": "npm"}],
        }
        lic = entry.get("license")
        if lic:
            comp["licenses"] = [{"license": {"name": str(lic)[:80]}}]
        comps.append(comp)
    return comps


def build_sbom() -> dict:
    version = (ROOT / "VERSION").read_text().strip() if (ROOT / "VERSION").exists() else "0"
    now = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    components = _backend_components() + _frontend_components()
    components.sort(key=lambda c: (c["properties"][0]["value"], c["name"].lower()))
    return {
        "bomFormat": "CycloneDX",
        "specVersion": SPEC_VERSION,
        "version": 1,
        "metadata": {
            "timestamp": now,
            "tools": [{"vendor": "document_aggregator",
                       "name": "generate_sbom.py", "version": version}],
            "component": {
                "type": "application",
                "name": "document-aggregation-platform",
                "version": version,
            },
        },
        "components": components,
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", type=Path, default=ROOT / "sbom.cdx.json")
    ap.add_argument("--print", action="store_true", dest="to_stdout")
    args = ap.parse_args(argv)
    sbom = build_sbom()
    text = json.dumps(sbom, indent=2, sort_keys=False)
    if args.to_stdout:
        print(text)
    else:
        args.out.write_text(text + "\n")
        n = len(sbom["components"])
        print(f"wrote {args.out} — CycloneDX {SPEC_VERSION}, {n} components",
              file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
