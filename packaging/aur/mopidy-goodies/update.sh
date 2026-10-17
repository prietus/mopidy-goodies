#!/bin/sh
# Point the stable AUR package at a published release: its version and the
# sdist's checksum (from the release's SHA256SUMS), and regenerate .SRCINFO.
# Run on Arch (needs makepkg). usage: update.sh 0.14.0
set -eu
version=${1:?usage: update.sh <version>}
cd "$(dirname "$0")"
sum=$(curl -fsSL "https://github.com/prietus/mopidy-goodies/releases/download/v$version/SHA256SUMS" |
  awk -v f="mopidy_goodies-$version.tar.gz" '$2 == f { print $1 }')
[ -n "$sum" ] || { echo "No sdist checksum in release v$version" >&2; exit 1; }
sed -i "s/^pkgver=.*/pkgver=$version/; s/^pkgrel=.*/pkgrel=1/; s/^sha256sums=.*/sha256sums=('$sum')/" PKGBUILD
makepkg --printsrcinfo > .SRCINFO
echo "mopidy-goodies $version ($sum). Check with: makepkg -f && namcap mopidy-goodies-*.pkg.tar.zst"
