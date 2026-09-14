from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sys
from pathlib import Path

from privacy_audit import scan_path


UI_FILES = (
    "index.html",
    "app.js",
    "atlas-physics.js",
    "styles.css",
    "quick-note.html",
    "quick-note-window.js",
    "quick-note-window.css",
)
UI_ASSETS = (
    "LICENSE-OFL",
    "boujoy-knowledge-pop-collage-v2.png",
    "boujoy-knowledge-punk-paper-v4.png",
    "fusion-pixel-10px-proportional-zh_hans.otf.woff2",
    "paper-fibers-dark.svg",
    "paper-fibers.svg",
    "torn-paper-panel.svg",
    "torn-paper-wide-a.svg",
    "torn-paper-wide-b.svg",
)
NATIVE_QUICK_NOTE_FILES = (
    "quick-note.html",
    "quick-note-window.js",
    "quick-note-window.css",
)
NATIVE_QUICK_NOTE_ASSETS = (
    "boujoy-knowledge-pop-collage-v2.png",
    "fusion-pixel-10px-proportional-zh_hans.otf.woff2",
)


def copy_file(source: Path, destination: Path) -> None:
    if not source.is_file() or source.is_symlink():
        raise RuntimeError(f"Required regular file is missing: {source}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, destination)


def copy_clean_tree(source: Path, destination: Path, *, suffixes: set[str] | None = None) -> None:
    if not source.is_dir() or source.is_symlink():
        raise RuntimeError(f"Required directory is missing: {source}")
    for item in sorted(source.rglob("*")):
        relative = item.relative_to(source)
        if any(part in {"__pycache__", ".bok", ".git"} for part in relative.parts):
            continue
        if item.is_symlink():
            raise RuntimeError(f"Symbolic links are not allowed: {item}")
        if item.is_dir():
            (destination / relative).mkdir(parents=True, exist_ok=True)
        elif item.is_file() and (suffixes is None or item.suffix in suffixes):
            copy_file(item, destination / relative)


def manifest(root: Path) -> dict:
    files = []
    for path in sorted(item for item in root.rglob("*") if item.is_file()):
        payload = path.read_bytes()
        files.append(
            {
                "path": path.relative_to(root).as_posix(),
                "size": len(payload),
                "sha256": hashlib.sha256(payload).hexdigest(),
            }
        )
    return {"schema": 1, "files": files}


def main() -> int:
    parser = argparse.ArgumentParser(description="Build Bok Desktop resources from an explicit whitelist.")
    parser.add_argument("--workspace", type=Path, default=Path(__file__).resolve().parents[2])
    parser.add_argument("--output", type=Path, default=Path(__file__).resolve().parents[1] / "build-resources")
    parser.add_argument("--windows-python", type=Path)
    parser.add_argument("--deny", action="append", default=[])
    args = parser.parse_args()

    workspace = args.workspace.resolve()
    project = (workspace / "Bok-Desktop").resolve()
    output = args.output.resolve()
    expected = (project / "build-resources").resolve()
    if output != expected:
        raise RuntimeError(f"Output must be the isolated build directory: {expected}")
    if output.exists():
        shutil.rmtree(output)
    output.mkdir(parents=True)

    ui_source = workspace / "AI-Second-Brain-UI"
    for name in UI_FILES:
        copy_file(ui_source / name, output / "ui" / name)
    for name in UI_ASSETS:
        copy_file(ui_source / "assets" / name, output / "ui" / "assets" / name)

    frontend = project / "frontend"
    for name in NATIVE_QUICK_NOTE_FILES:
        copy_file(ui_source / name, frontend / name)
    for name in NATIVE_QUICK_NOTE_ASSETS:
        copy_file(ui_source / "assets" / name, frontend / "assets" / name)

    copy_clean_tree(project / "starter-vault", output / "starter-vault")
    copy_file(ui_source / "web_preview.pyw", output / "windows-source" / "web_preview.pyw")
    copy_clean_tree(workspace / "Bok" / "bok_core", output / "windows-source" / "bok_core", suffixes={".py"})
    if args.windows_python:
        copy_clean_tree(args.windows_python.resolve(), output / "windows-python")

    (output / "share-manifest.json").write_text(
        json.dumps(manifest(output), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    issues = scan_path(output, [value.encode("utf-8") for value in args.deny if value])
    if issues:
        print("Bok resource privacy audit: FAIL", file=sys.stderr)
        print("\n".join(sorted(set(issues))), file=sys.stderr)
        return 1
    print(f"Prepared Bok Desktop resources: {output}")
    print(f"Files: {len(manifest(output)['files'])}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
