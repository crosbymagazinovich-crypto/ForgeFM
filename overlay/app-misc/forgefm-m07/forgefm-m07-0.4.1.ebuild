# Copyright 2026 ForgeFM contributors
# Distributed under the terms of the MIT License
EAPI=8

PYTHON_COMPAT=( python3_{11..14} )
inherit python-single-r1

DESCRIPTION="Single-window GTK4 file manager in the Synchroframe M/07 style"
HOMEPAGE="https://github.com/crosbymagazinovich-crypto/synchroframe"
# Local-source overlay: files are shipped in files/, without remote downloads.
SRC_URI=""
S="${WORKDIR}"

LICENSE="MIT"
SLOT="0"
KEYWORDS="~amd64 ~arm64"
REQUIRED_USE="${PYTHON_REQUIRED_USE}"

RDEPEND="
    ${PYTHON_DEPS}
    $(python_gen_cond_dep 'dev-python/pygobject[${PYTHON_USEDEP}]')
    gui-libs/gtk:4[introspection]
"

src_install() {
    local name
    insinto /usr/share/forgefm-m07
    for name in core.py presentation.py launch.py gui_gtk.py tui.py forgefm.py; do
        doins "${FILESDIR}/${name}"
    done

    cat > "${T}/forgefm-m07" <<- EOF
#!/bin/sh
exec "/usr/bin/${EPYTHON}" /usr/share/forgefm-m07/launch.py "\$@"
EOF
    dobin "${T}/forgefm-m07"
    domenu "${FILESDIR}/forgefm-m07.desktop"
    insinto /usr/share/icons/hicolor/scalable/apps
    newins "${FILESDIR}/forgefm.svg" forgefm-m07.svg
    dodoc "${FILESDIR}/README.md"
}
