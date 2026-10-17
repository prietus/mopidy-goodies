#!/bin/sh
# Install mopidy-goodies (and, optionally, mopidy-local and mopidy-tidal) on
# Debian, Ubuntu, Fedora or Arch Linux.
#
#   curl -fsSL https://raw.githubusercontent.com/prietus/mopidy-goodies/master/install.sh | sh -s -- --local --tidal
#
# Options:
#   --local   also install mopidy-local (your own music files)
#   --tidal   also install mopidy-tidal (Tidal streaming); run it again to update Tidal
#   --yes     don't ask before installing
#
# Mopidy itself comes from your distribution; goodies from its latest release
# (a .deb, .rpm or Arch package). Neither mopidy-tidal nor its tidalapi are
# packaged by Debian, Ubuntu or Fedora, so they come from PyPI, with the
# dependencies that won't build from source taken from the distribution.
#
# GOODIES_PACKAGE=/path/to/package installs that file instead of downloading.
set -eu

REPO="prietus/mopidy-goodies"
want_local=false
want_tidal=false
assume_yes=false
for arg in "$@"; do
  case "$arg" in
    --local) want_local=true ;;
    --tidal) want_tidal=true ;;
    --yes | -y) assume_yes=true ;;
    -h | --help) sed -n '2,19p' "$0" 2>/dev/null | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "Unknown option: $arg (try --help)" >&2; exit 2 ;;
  esac
done

say() { printf '\033[1m==> %s\033[0m\n' "$*"; }
warn() { printf '\033[33m!!  %s\033[0m\n' "$*" >&2; }
die() { printf '\033[31mxx  %s\033[0m\n' "$*" >&2; exit 1; }

if [ "$(id -u)" -eq 0 ]; then SUDO=""; else
  command -v sudo >/dev/null || die "Run this as root, or install sudo."
  SUDO="sudo"
fi

# ── which distribution ──────────────────────────────────────────────────
[ -r /etc/os-release ] || die "Can't tell which Linux this is (no /etc/os-release)."
. /etc/os-release
case " ${ID:-} ${ID_LIKE:-} " in
  *" arch "*) distro=arch ;;
  *" fedora "*) distro=fedora ;;
  *" debian "* | *" ubuntu "*) distro=debian ;;
  *) die "Unsupported distribution: ${PRETTY_NAME:-$ID}. Install with pip instead (see the README)." ;;
esac
say "Installing on ${PRETTY_NAME:-$ID}"

extras=""
$want_local && extras="$extras mopidy-local"
$want_tidal && extras="$extras mopidy-tidal"
echo "    mopidy-goodies${extras:+ and$extras}, plus Mopidy itself if it's missing."
if ! $assume_yes && [ -t 0 ]; then
  printf "Continue? [Y/n] "
  read -r answer
  case "$answer" in [nN]*) exit 0 ;; esac
fi

tmp=$(mktemp -d)
trap 'rm -rf "$tmp"' EXIT

# ── the goodies package of the latest release ───────────────────────────
release_asset() {  # $1: filename suffix
  url=$(curl -fsSL "https://api.github.com/repos/$REPO/releases/latest" |
    grep -o "\"browser_download_url\": *\"[^\"]*$1\"" | head -1 | sed 's/.*"\(http[^"]*\)"$/\1/') || true
  [ -n "$url" ] || die "No $1 package in the latest release of $REPO."
  curl -fsSL -o "$tmp/$(basename "$url")" "$url"
  echo "$tmp/$(basename "$url")"
}

goodies_package() {  # $1: suffix
  if [ -n "${GOODIES_PACKAGE:-}" ]; then echo "$GOODIES_PACKAGE"; else release_asset "$1"; fi
}

# pip into the system Python Mopidy runs on. Distributions mark it "externally
# managed"; Mopidy extensions have to live there anyway.
pip_install() {
  $SUDO python3 -m pip install --quiet --root-user-action=ignore --break-system-packages "$@"
}

python_at_least() {  # $1: minor version of 3.x
  python3 -c "import sys; sys.exit(0 if sys.version_info >= (3, $1) else 1)"
}

# ── Debian / Ubuntu ─────────────────────────────────────────────────────
install_debian() {
  export DEBIAN_FRONTEND=noninteractive
  $SUDO apt-get update -qq
  $SUDO apt-get install -y -qq curl ca-certificates >/dev/null
  pkg=$(goodies_package _all.deb)
  say "Installing Mopidy and mopidy-goodies"
  $SUDO apt-get install -y -qq mopidy "$pkg" >/dev/null

  if $want_local || $want_tidal; then
    $SUDO apt-get install -y -qq python3-pip >/dev/null
  fi
  if $want_local; then
    say "Installing mopidy-local"
    $SUDO apt-get install -y -qq mopidy-local >/dev/null
    # Debian's mopidy-local imports imghdr, which Python 3.13 removed.
    if python_at_least 13; then pip_install standard-imghdr; fi
  fi
  if $want_tidal; then
    say "Installing mopidy-tidal"
    # tidalapi's dependencies from the distribution where it has them: pyaes and
    # ratelimit don't build from source with pip's isolation on recent Python.
    deps=""
    for p in python3-requests python3-dateutil python3-pyaes python3-ratelimit; do
      apt-cache show "$p" >/dev/null 2>&1 && deps="$deps $p"
    done
    $SUDO apt-get install -y -qq python3-setuptools $deps >/dev/null
    # The ones it lacks (Ubuntu has no ratelimit) build against the system's setuptools.
    for m in pyaes ratelimit; do
      python3 -c "import $m" 2>/dev/null || pip_install --no-build-isolation "$m"
    done
    # pip can't replace these distribution copies (Debian's isodate reports version 0.0.0,
    # Ubuntu's typing_extensions is too old for tidalapi): it puts its own in /usr/local,
    # which comes first.
    pip_install --ignore-installed "isodate>=0.7.2,<0.8" "typing-extensions>=4.12"
    pip_install --upgrade mopidy-tidal tidalapi
  fi
}

# ── Fedora ──────────────────────────────────────────────────────────────
install_fedora() {
  $SUDO dnf install -y -q curl >/dev/null
  pkg=$(goodies_package .noarch.rpm)
  say "Installing Mopidy and mopidy-goodies"
  $SUDO dnf install -y -q mopidy "$pkg" >/dev/null

  if $want_local || $want_tidal; then
    $SUDO dnf install -y -q python3-pip >/dev/null
  fi
  if $want_local; then
    say "Installing mopidy-local (from PyPI: Fedora doesn't package it)"
    pip_install --upgrade mopidy-local
  fi
  if $want_tidal; then
    say "Installing mopidy-tidal"
    $SUDO dnf install -y -q python3-requests python3-dateutil python3-isodate python3-typing-extensions >/dev/null
    $SUDO dnf install -y -q python3-pyaes python3-ratelimit >/dev/null 2>&1 || true
    pip_install --upgrade mopidy-tidal tidalapi
  fi
}

# ── Arch ────────────────────────────────────────────────────────────────
aur_helper() {  # prints yay or paru, or nothing
  for h in yay paru; do command -v "$h" >/dev/null && { echo "$h"; return 0; }; done
  return 0
}

install_arch() {
  $SUDO pacman -S --noconfirm --needed -q curl >/dev/null
  pkg=$(goodies_package -any.pkg.tar.zst)
  say "Installing Mopidy and mopidy-goodies"
  if pacman -Q mopidy4 >/dev/null 2>&1; then
    $SUDO pacman -U --noconfirm --needed --assume-installed mopidy "$pkg" >/dev/null
  else
    $SUDO pacman -S --noconfirm --needed -q mopidy >/dev/null
    $SUDO pacman -U --noconfirm --needed "$pkg" >/dev/null
  fi

  aur=""
  if $want_local || $want_tidal; then
    aur=$(aur_helper)
    if [ -z "$aur" ]; then
      warn "mopidy-local and mopidy-tidal are in the AUR. Install them with an AUR helper (yay, paru):"
      $want_local && warn "    yay -S mopidy-local"
      $want_tidal && warn "    yay -S python-mopidy-tidal-git"
      return
    fi
    [ "$(id -u)" -eq 0 ] && { warn "AUR helpers don't run as root: rerun this script as your user."; return; }
  fi
  if $want_local; then
    say "Installing mopidy-local (AUR)"
    "$aur" -S --noconfirm --needed mopidy-local
  fi
  if $want_tidal; then
    say "Installing mopidy-tidal (AUR, tidalapi from the official repos)"
    $SUDO pacman -S --noconfirm --needed -q python-tidalapi >/dev/null
    "$aur" -S --noconfirm --needed --rebuild python-mopidy-tidal-git
  fi
}

"install_$distro"

# ── restart Mopidy so it loads the extensions ───────────────────────────
restarted=""
if command -v systemctl >/dev/null 2>&1; then
  if systemctl is-active --quiet mopidy 2>/dev/null; then
    say "Restarting Mopidy (system service)"
    $SUDO systemctl restart mopidy && restarted=yes
  elif systemctl --user is-active --quiet mopidy 2>/dev/null; then
    say "Restarting Mopidy (user service)"
    systemctl --user restart mopidy && restarted=yes
  fi
fi

echo
say "Installed:"
mopidy deps 2>/dev/null | grep -E '^(Mopidy|[Mm]opidy-(goodies|local|tidal|Tidal|Local)|tidalapi):' | sed 's/^/    /' || true

if [ -n "$restarted" ]; then
  for _ in $(seq 1 20); do
    if health=$(curl -sf http://127.0.0.1:6680/goodies/_health 2>/dev/null); then
      say "mopidy-goodies is answering: $health"
      break
    fi
    sleep 1
  done
else
  echo
  echo "Restart Mopidy so it loads them, e.g.:  sudo systemctl restart mopidy"
fi

if $want_tidal; then
  cat <<'NEXT'

Tidal: add this to your Mopidy config (/etc/mopidy/mopidy.conf for the system
service, ~/.config/mopidy/mopidy.conf otherwise) and restart Mopidy:

    [tidal]
    enabled = true
    quality = LOSSLESS
    login_method = AUTO

Then open Tidal in Pymote (or mopytui): it shows the link to authorize the
server. To update Tidal later (when it stops working), run this script again
with --tidal.
NEXT
fi
