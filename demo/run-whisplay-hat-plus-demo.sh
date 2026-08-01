#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."
exec python3 -m demo.whisplay_hat_plus_demo.main "$@"
