#!/usr/bin/env bash

# VPS base URL (no trailing slash).
# Use https:// for remote/public deployments.
export VPS_BASE_URL="http://127.0.0.1:8000"

# Optional API token (leave empty if backend API_TOKEN is not set).
export REMOTE_API_TOKEN=""

# Capture margins in inches.
export DPI="96"
export TOP_IN="1.5"
export LEFT_IN="0.5"
export RIGHT_IN="0.5"
export BOTTOM_IN="1.5"
