"""Validate release versions and checksum the actual distributable files."""
from __future__ import annotations

import argparse
import ast
import hashlib
import json
import re
from pathlib import Path


def release_version(workspace: Path) -> str:
    desktop = workspace / "Bok-Desktop"
    config = json.loads((desktop / "src-tauri/tauri.conf.json").read_text(encoding="utf-8"))
    version = config["version"]
    if not re.fullmatch(r"\d+\.\d+\.\d+", version):
        raise ValueError("Desktop release version must be X.Y.Z")
    core = ast.parse((workspace / "Bok/bok_core/version.py").read_text(encoding="utf-8"))
    core_version = next(
        ast.literal_eval(node.value) for node in core.body
        if isinstance(node, ast.Assign)
        and any(isinstance(target, ast.Name) and target.id == "VERSION" for target in node.targets)
    )
    cargo = (desktop / "src-tauri/Cargo.toml").read_text(encoding="utf-8")
    lock = (desktop / "Cargo.lock").read_text(encoding="utf-8")
    cargo_version = re.search(r'^version = "([^"]+)"$', cargo, re.MULTILINE)
    lock_version = re.search(r'name = "bok-desktop"\nversion = "([^"]+)"', lock)
    if not cargo_version or not lock_version or {
        version, core_version, cargo_version[1], lock_version[1]
    } != {version}:
        raise ValueError("Core, Tauri, Cargo manifest and lockfile versions must match")
    return version


def write_checksums(output: Path) -> None:
    artifacts = sorted(path for path in output.iterdir() if path.suffix in {".zip", ".dmg", ".exe"})
    if not artifacts:
        raise ValueError("No release packages to checksum")
    lines = []
    for path in artifacts:
        digest = hashlib.sha256()
        with path.open("rb") as stream:
            for block in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(block)
        lines.append(f"{digest.hexdigest()}  {path.name}\n")
    (output / "SHA256SUMS.txt").write_text("".join(lines), encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", type=Path, default=Path(__file__).resolve().parents[2])
    parser.add_argument("--checksums", type=Path)
    args = parser.parse_args()
    version = release_version(args.workspace)
    if args.checksums:
        write_checksums(args.checksums)
    print(version)


if __name__ == "__main__":
    main()
