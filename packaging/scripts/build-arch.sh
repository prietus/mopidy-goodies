#!/bin/sh
# Build the Arch package from an sdist, inside an Arch container.
# usage: build-arch.sh <mopidy_goodies-X.Y.Z.tar.gz> <outdir>
set -eu
sdist=$(realpath "$1"); out=$(realpath "$2")
here=$(dirname "$(realpath "$0")")/..

# pacman's download sandbox can't start under emulation (e.g. amd64 on Apple Silicon),
# and isn't needed in a throwaway container.
pacman --disable-sandbox -Syu --noconfirm --needed -q base-devel python-build python-installer python-wheel python-setuptools >/dev/null

version=$(basename "$sdist" .tar.gz); version=${version#mopidy_goodies-}
id -u builder >/dev/null 2>&1 || useradd -m builder
work=$(mktemp -d)
cp "$sdist" "$work/"
sed "s/^pkgver=.*/pkgver=$version/" "$here/arch/PKGBUILD" > "$work/PKGBUILD"
chown -R builder "$work"
# -d: the runtime deps (mopidy…) aren't needed to build; the makedepends are installed above.
su builder -c "cd $work && makepkg -d --noconfirm" >/dev/null
cp "$work"/mopidy-goodies-"$version"-*.pkg.tar.zst "$out/"
echo "built $(ls "$out"/mopidy-goodies-*.pkg.tar.zst)"
