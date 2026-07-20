# Documents capability -- live verification checklist

Items here require a real LibreOffice install and cannot be automated in the test suite.
Run these manually on a Windows machine that has (or can get) LibreOffice.

## S2 #453 -- LibreOffice dep: setup script + runtime detection

- [ ] Double-click `scripts/setup-libreoffice.ps1` on a machine without LibreOffice.
      Verify: winget installs LibreOffice, script prints SUCCESS and the detected soffice.exe path.
- [ ] Run `scripts/setup-libreoffice.ps1 -NoPause` from a terminal after install.
      Verify: exits 0, prints SUCCESS and version string, no pause prompt.
- [ ] Start Cerebral (`python -m cerebral.main`) with LibreOffice installed.
      Ask Felix: "doc status". Verify: `available: true` and correct `soffice_path`.
- [ ] Start Cerebral with LibreOffice NOT installed (or SOFFICE_PATH seam unset).
      Ask Felix: "doc status". Verify: `available: false` and the setup-libreoffice.ps1 hint.
