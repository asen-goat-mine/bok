#!/bin/zsh
set -euo pipefail

PROJECT_DIR="${0:A:h}"
WORKSPACE_DIR="${PROJECT_DIR:h}"
PYTHON_BIN="${PYTHON_BIN:-python3}"
CARGO_BIN="${CARGO_BIN:-$(command -v cargo 2>/dev/null || true)}"
RUSTC_BIN="${RUSTC_BIN:-$(command -v rustc 2>/dev/null || true)}"
VERSION="$("${PYTHON_BIN}" "${PROJECT_DIR}/scripts/release_metadata.py" --workspace "${WORKSPACE_DIR}")"
STAGE_DIR="$(mktemp -d /private/tmp/bok-desktop-macos.XXXXXX)"
BUILD_PROJECT="${STAGE_DIR}/Bok-Desktop"
VENV_DIR="${STAGE_DIR}/pyinstaller-venv"
OUTPUT_DIR="${WORKSPACE_DIR}/_dist/Bok-Desktop-${VERSION}-macOS"
CARGO_CACHE_DIR="${BOK_CARGO_HOME:-${STAGE_DIR}/cargo-home}"
PYINSTALLER_BIN="${BOK_PYINSTALLER_BIN:-}"
PRIVACY_DENY="${BOK_PRIVACY_DENY:-${USER:-local-user}}"

cleanup() {
  local mounted_device
  mounted_device="$(hdiutil info 2>/dev/null | awk -v prefix="${STAGE_DIR}/" '
    /^image-path/ { wanted = index($0, prefix) > 0; next }
    wanted && /^\/dev\/disk/ { print $1; exit }
  ')"
  if [[ -n "${mounted_device}" ]]; then
    hdiutil detach "${mounted_device}" >/dev/null 2>&1 || true
  fi
  rm -rf "${STAGE_DIR}"
}
trap cleanup EXIT

if [[ -z "${CARGO_BIN}" || -z "${RUSTC_BIN}" || ! -x "${CARGO_BIN}" || ! -x "${RUSTC_BIN}" ]]; then
  print -u2 "Rust/Tauri 构建工具缺失。请先安装 Rust stable 和 tauri-cli 2.11.4。"
  exit 1
fi

"${PYTHON_BIN}" "${PROJECT_DIR}/scripts/prepare_share.py" \
  --workspace "${WORKSPACE_DIR}" \
  --deny "${PRIVACY_DENY}"
"${PYTHON_BIN}" "${PROJECT_DIR}/scripts/test_desktop_contracts.py"

mkdir -p "${BUILD_PROJECT}"
ditto "${PROJECT_DIR}" "${BUILD_PROJECT}"
rm -rf "${BUILD_PROJECT}/target" "${BUILD_PROJECT}/src-tauri/binaries"
mkdir -p "${BUILD_PROJECT}/src-tauri/binaries"

if [[ -z "${PYINSTALLER_BIN}" ]]; then
  "${PYTHON_BIN}" -m venv "${VENV_DIR}"
  "${VENV_DIR}/bin/python" -m pip install --disable-pip-version-check --quiet "pyinstaller==6.16.0"
  PYINSTALLER_BIN="${VENV_DIR}/bin/pyinstaller"
fi
if [[ ! -x "${PYINSTALLER_BIN}" ]]; then
  print -u2 "PyInstaller 可执行文件不存在：${PYINSTALLER_BIN}"
  exit 1
fi

SOURCE_ROOT="${BUILD_PROJECT}/build-resources/windows-source"
PYINSTALLER_CONFIG_DIR="${STAGE_DIR}/pyinstaller-cache" "${PYINSTALLER_BIN}" \
  --noconfirm \
  --clean \
  --onefile \
  --name bok-preview \
  --paths "${SOURCE_ROOT}" \
  --distpath "${STAGE_DIR}/sidecar" \
  --workpath "${STAGE_DIR}/pyinstaller-work" \
  --specpath "${STAGE_DIR}" \
  "${SOURCE_ROOT}/web_preview.pyw"

HOST_TRIPLE="$("${RUSTC_BIN}" -vV | awk '/^host:/ { print $2 }')"
case "${HOST_TRIPLE}" in
  aarch64-apple-darwin) PACKAGE_ARCH=arm64 ;;
  x86_64-apple-darwin) PACKAGE_ARCH=x64 ;;
  *) print -u2 "Unsupported macOS host: ${HOST_TRIPLE}"; exit 1 ;;
esac
cp "${STAGE_DIR}/sidecar/bok-preview" "${BUILD_PROJECT}/src-tauri/binaries/bok-preview-${HOST_TRIPLE}"
chmod +x "${BUILD_PROJECT}/src-tauri/binaries/bok-preview-${HOST_TRIPLE}"

cd "${BUILD_PROJECT}"
if command -v tauri >/dev/null 2>&1; then
  CARGO_HOME="${CARGO_CACHE_DIR}" \
  CARGO_HTTP_MULTIPLEXING=false \
  CARGO_NET_RETRY=10 \
  RUSTFLAGS="--remap-path-prefix=${BUILD_PROJECT}=Bok-Desktop --remap-path-prefix=${HOME}=LOCAL_BUILD_HOME" \
  tauri build --bundles app,dmg
else
  CARGO_HOME="${CARGO_CACHE_DIR}" \
  CARGO_HTTP_MULTIPLEXING=false \
  CARGO_NET_RETRY=10 \
  RUSTFLAGS="--remap-path-prefix=${BUILD_PROJECT}=Bok-Desktop --remap-path-prefix=${HOME}=LOCAL_BUILD_HOME" \
  "${CARGO_BIN}" tauri build --bundles app,dmg
fi

APP_PATH="$(find "${BUILD_PROJECT}/target/release/bundle/macos" -maxdepth 1 -name '*.app' -print -quit)"
DMG_PATH="$(find "${BUILD_PROJECT}/target/release/bundle/dmg" -maxdepth 1 -name '*.dmg' -print -quit)"
if [[ -z "${APP_PATH}" || -z "${DMG_PATH}" ]]; then
  print -u2 "Tauri 构建完成但没有找到 .app 或 .dmg。"
  exit 1
fi

rm -rf "${OUTPUT_DIR}"
mkdir -p "${OUTPUT_DIR}"
ditto --norsrc --noextattr --noqtn --noacl \
  "${DMG_PATH}" "${OUTPUT_DIR}/Bok-${VERSION}-macOS-${PACKAGE_ARCH}.dmg"
ditto --norsrc --noextattr --noqtn --noacl -c -k --keepParent \
  "${APP_PATH}" "${OUTPUT_DIR}/Bok-${VERSION}-macOS-${PACKAGE_ARCH}.zip"

if unzip -Z1 "${OUTPUT_DIR}/Bok-${VERSION}-macOS-${PACKAGE_ARCH}.zip" | \
  grep -Eq '(^|/)\._|^__MACOSX/'; then
  print -u2 "ZIP 中出现了 AppleDouble 冗余文件，已终止发布。"
  exit 1
fi
VERIFY_DIR="${STAGE_DIR}/verify-archive"
mkdir -p "${VERIFY_DIR}"
ditto -x -k "${OUTPUT_DIR}/Bok-${VERSION}-macOS-${PACKAGE_ARCH}.zip" "${VERIFY_DIR}"
codesign --verify --deep --strict --verbose=2 "${VERIFY_DIR}/Bok.app"
"${PYTHON_BIN}" "${PROJECT_DIR}/scripts/privacy_audit.py" \
  "${APP_PATH}" \
  "${OUTPUT_DIR}/Bok-${VERSION}-macOS-${PACKAGE_ARCH}.zip" \
  --deny "${PRIVACY_DENY}"

"${PYTHON_BIN}" "${PROJECT_DIR}/scripts/release_metadata.py" \
  --workspace "${WORKSPACE_DIR}" --checksums "${OUTPUT_DIR}"

print "Bok macOS 分享版已生成：${OUTPUT_DIR}"
