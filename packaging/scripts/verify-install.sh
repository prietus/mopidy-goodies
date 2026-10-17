#!/bin/sh
# After install.sh --local --tidal: start Mopidy with both and check that
# goodies, local and tidal all loaded. Run inside the same container.
set -u
mkdir -p /tmp/m /tmp/music
cat > /tmp/m/mopidy.conf <<CONF
[core]
data_dir = /tmp/m/data
cache_dir = /tmp/m/cache
config_dir = /tmp/m
[audio]
output = fakesink
[http]
hostname = 127.0.0.1
[local]
media_dir = /tmp/music
[tidal]
enabled = true
login_method = AUTO
CONF
mopidy --config /tmp/m/mopidy.conf >/tmp/m/log 2>&1 &
for _ in $(seq 1 60); do h=$(curl -sf http://127.0.0.1:6680/goodies/_health) && break; sleep 1; done
[ -n "${h:-}" ] || { echo "goodies didn't answer"; cat /tmp/m/log; exit 1; }
echo "health: $h"
# local loaded (goodies sees its media dir) and mopidy-tidal + tidalapi importable.
echo "$h" | python3 -c '
import json, sys
d = json.load(sys.stdin)
assert d["features"]["local_metadata"], "mopidy-local did not load"
assert d["tidal"]["mopidy_tidal"] and d["tidal"]["tidalapi"], "mopidy-tidal is missing"
assert d["tidal"]["session"] in ("logged_out", "ok"), "mopidy-tidal did not load"
print("ok: goodies", d["version"], "· local · mopidy-tidal", d["tidal"]["mopidy_tidal"], "· tidalapi", d["tidal"]["tidalapi"])'

