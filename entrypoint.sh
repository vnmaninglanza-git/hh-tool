#!/bin/sh
mkdir -p /app/logs /app/data
[ -f "$HH_CONFIG" ] || echo '{}' > "$HH_CONFIG"
exec python webui.py
