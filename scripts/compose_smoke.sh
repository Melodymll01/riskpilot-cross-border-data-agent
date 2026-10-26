#!/usr/bin/env bash
set -euo pipefail
curl --fail --silent --show-error "http://127.0.0.1:${APP_PORT:-8765}/api/v2/health/ready"
