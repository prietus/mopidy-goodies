#!/bin/sh
# Install a built package with its distro's own Mopidy, start Mopidy and check
# that goodies loads and answers. Run inside a fresh container of that distro.
# usage: smoke-test.sh <package file>
set -eu
pkg=$(realpath "$1")

case "$pkg" in
  *.deb)
    export DEBIAN_FRONTEND=noninteractive
    apt-get update -qq
    apt-get install -y -qq curl "$pkg" >/dev/null ;;
  *.rpm)
    dnf install -y -q curl "$pkg" >/dev/null ;;
  *.pkg.tar.zst)
    # No download sandbox: it can't start under emulation, and this container is throwaway.
    pacman --disable-sandbox -Sy --noconfirm -q >/dev/null
    pacman --disable-sandbox -U --noconfirm "$pkg" >/dev/null
    pacman --disable-sandbox -S --noconfirm --needed -q curl >/dev/null ;;
esac

echo "mopidy $(mopidy --version 2>&1 | tail -1)"
mkdir -p /tmp/mopidy
cat > /tmp/mopidy/mopidy.conf <<CONF
[core]
data_dir = /tmp/mopidy/data
cache_dir = /tmp/mopidy/cache
config_dir = /tmp/mopidy
[audio]
output = fakesink
[http]
hostname = 127.0.0.1
port = 6680
CONF
mopidy --config /tmp/mopidy/mopidy.conf >/tmp/mopidy/log 2>&1 &

for _ in $(seq 1 90); do
  if health=$(curl -sf http://127.0.0.1:6680/goodies/_health); then
    echo "health: $health"
    curl -sf http://127.0.0.1:6680/goodies/stats/totals >/dev/null && echo "stats: ok"
    curl -sf http://127.0.0.1:6680/goodies/audio/output >/dev/null && echo "audio: ok"
    exit 0
  fi
  sleep 1
done
echo "goodies didn't answer; Mopidy's log:"
cat /tmp/mopidy/log
exit 1
