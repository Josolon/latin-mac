#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
DDK_BIN="/Applications/XcodeAdditionalTools/Utilities/DictionaryDevelopmentKit/bin/build_dict.sh"
DDK_BIN_SPACED="/Applications/Additional Tools for Xcode/Utilities/DictionaryDevelopmentKit/bin/build_dict.sh"

echo "[1/5] Checking platform"
if [[ "$(uname -s)" != "Darwin" ]]; then
  echo "This installer is for macOS only."
  exit 1
fi

echo "[2/5] Checking DictionaryDevelopmentKit"
if [[ ! -x "${DDK_BIN}" ]]; then
  echo "Missing DictionaryDevelopmentKit build tool: ${DDK_BIN}"
  if [[ -x "${DDK_BIN_SPACED}" ]]; then
    echo "Detected Xcode tools in a path with spaces."
    echo "Rename it so build tools use a space-free path:"
    echo "  sudo mv \"/Applications/Additional Tools for Xcode\" \"/Applications/XcodeAdditionalTools\""
  else
    echo "Install Xcode Additional Tools and try again."
  fi
  exit 1
fi

echo "[3/5] Checking source data (see README.md, Step 1, for download instructions)"
missing=0
for f in \
  "data/lewis_short/lat.ls.perseus-eng2.xml" \
  "data/analyses/latin-lemmata.txt" \
  "data/ramshorn/ramshorn_1841_djvu.txt"; do
  if [[ ! -f "${REPO_ROOT}/${f}" ]]; then
    echo "  Missing ${f}"
    missing=1
  fi
done
if [[ "${missing}" -eq 1 ]]; then
  echo "Download the missing source data first (README.md, Step 1)."
  exit 1
fi

echo "[4/5] Building databases and dictionary XML"
cd "${REPO_ROOT}"
python3 scripts/build_dbs.py
python3 scripts/build_xml.py

echo "[5/5] Compiling and installing the bundle"
cd "${REPO_ROOT}/src"
make install

echo "Done. Open Dictionary.app Settings and enable 'Latin (Lewis & Short)'."
