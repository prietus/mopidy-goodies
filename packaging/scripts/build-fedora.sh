#!/bin/sh
# Build the .rpm from an sdist, inside a Fedora container.
# usage: build-fedora.sh <mopidy_goodies-X.Y.Z.tar.gz> <outdir>
set -eu
sdist=$(realpath "$1"); out=$(realpath "$2")
here=$(dirname "$(realpath "$0")")/..

dnf install -y -q rpm-build python3-devel pyproject-rpm-macros python3-pip python3-setuptools python3-wheel >/dev/null

version=$(basename "$sdist" .tar.gz); version=${version#mopidy_goodies-}
top=$(mktemp -d)
mkdir -p "$top/SOURCES" "$top/SPECS"
cp "$sdist" "$top/SOURCES/"
cp "$here/fedora/mopidy-goodies.spec" "$top/SPECS/"
rpmbuild -bb --define "_topdir $top" --define "pkgver $version" "$top/SPECS/mopidy-goodies.spec" >/dev/null
cp "$top"/RPMS/noarch/mopidy-goodies-*.rpm "$out/"
echo "built $(ls "$out"/mopidy-goodies-*.rpm)"
