#!/bin/sh
# Build the .deb (Debian, Ubuntu) from an sdist, inside a Debian/Ubuntu container.
# usage: build-debian.sh <mopidy_goodies-X.Y.Z.tar.gz> <outdir>
set -eu
sdist=$(realpath "$1"); out=$(realpath "$2")
here=$(dirname "$(realpath "$0")")/..

export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -qq --no-install-recommends \
  build-essential debhelper dh-python pybuild-plugin-pyproject python3-all python3-setuptools python3-wheel >/dev/null

work=$(mktemp -d)
tar -xzf "$sdist" -C "$work"
cd "$work"/mopidy_goodies-*
version=${PWD##*-}
cp -r "$here/debian" debian
cat > debian/changelog <<CHANGELOG
mopidy-goodies ($version) unstable; urgency=medium

  * Release $version.

 -- Carlos Prieto <cprieto.ortiz@gmail.com>  $(date -R)
CHANGELOG
dpkg-buildpackage -us -uc -b >/dev/null
cp ../mopidy-goodies_"$version"_all.deb "$out/"
echo "built $out/mopidy-goodies_${version}_all.deb"
