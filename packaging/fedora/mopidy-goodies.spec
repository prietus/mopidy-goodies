# Built by packaging/scripts/build-fedora.sh with --define "pkgver <version>".
Name:           mopidy-goodies
Version:        %{pkgver}
Release:        1%{?dist}
Summary:        Mopidy extension with extra endpoints for hi-fi remote apps
License:        Apache-2.0
URL:            https://github.com/prietus/mopidy-goodies
Source0:        mopidy_goodies-%{version}.tar.gz
BuildArch:      noarch

BuildRequires:  python3-devel
BuildRequires:  pyproject-rpm-macros
BuildRequires:  python3-pip
BuildRequires:  python3-setuptools
BuildRequires:  python3-wheel
# Runtime requirements (mopidy, tornado, mutagen) are generated from the metadata;
# pykka comes with Mopidy.

%description
Adds HTTP endpoints under /goodies/ to a Mopidy server: the real audio format
reaching the DAC and whether the path is bit-perfect, listening statistics,
local library scans, cover and credits for local albums, and (with
mopidy-tidal) Tidal favourites, credits, reviews, artist bios, radio and
playlists. Used by the Pymote and mopytui clients.

%prep
%autosetup -n mopidy_goodies-%{version}

%build
%pyproject_wheel

%install
%pyproject_install
%pyproject_save_files mopidy_goodies

%files -f %{pyproject_files}
%license LICENSE
%doc README.md
