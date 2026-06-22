# Packaging the desktop app

Storywell's desktop GUI ships as a native, double-click installer per OS, built with
[Briefcase](https://briefcase.beeware.org). Configuration lives in `[tool.briefcase]` in
`pyproject.toml`; it is independent of the wheel build and of CI.

> **Why a signed build needs your machine:** code-signing and notarization require your
> Apple Developer / Windows certificates and a real desktop OS. The steps below produce an
> _unsigned_ bundle anyone can build for local testing, then cover the signing each OS needs
> for distribution. CI does not build installers.

## What gets bundled (and what doesn't)

Briefcase bundles a private Python plus everything in the app `requires` (Playwright,
pywebview, the Audible SDK, …) and the whole `src/storywell` tree — including
`desktop/web/index.html`.

**Chromium is deliberately not bundled.** On first launch the app calls
`storywell.storygraph.provision.ensure_chromium`, which runs `playwright install chromium`
using the app's own bundled Playwright and downloads the browser to the user's Playwright
cache (`~/Library/Caches/ms-playwright`, `%USERPROFILE%\AppData\Local\ms-playwright`, …).
That keeps the installer small and sidesteps Briefcase resource-bundling of browser binaries.
The download happens once; later launches reuse it. The same code powers
`storywell storygraph-install` for source installs.

## Prerequisites

```sh
make install-packaging      # pip install -e ".[packaging]"  (Briefcase)
```

Plus the platform toolchain Briefcase drives:

- **macOS** — Xcode command-line tools (`xcode-select --install`).
- **Windows** — Briefcase downloads WiX automatically; no manual install.
- **Linux** — the WebKitGTK system packages in `[tool.briefcase.app.storywell.linux]`
  (names vary by distro — Debian/Ubuntu use `webkit2gtk-4.1`; adjust for yours).

## The sdist-only wheelhouse (why `make wheels` exists)

Briefcase installs app requirements for the _target_ platform, which forces wheels only
(`pip install --only-binary :all: --platform …`). Three transitive deps ship **sdist-only**
on PyPI and would otherwise break the build:

- `pbkdf2`, `pyaes` — pulled in by the Audible SDK
- `proxy_tools` — pulled in by pywebview

All three are pure-Python, so `make wheels` builds them into a local `wheels/` directory
(`pip wheel pbkdf2 pyaes proxy_tools -w wheels`) and the packaging targets point pip at it via
`PIP_FIND_LINKS`. The `package-*` targets depend on `wheels`, so this is automatic. `wheels/`
is git-ignored. If a future dependency bump adds another sdist-only package, add it to
`SDIST_ONLY_DEPS` in the `Makefile`.

## Build an unsigned bundle (any contributor)

```sh
make package-create     # briefcase create   — scaffold the bundle for this OS
make package-build      # briefcase build     — compile it
make package-run        # briefcase run       — launch it to smoke-test
make package            # briefcase package --adhoc-sign  — unsigned installer in dist/
```

`make package-create` is the fastest check that `pyproject.toml` is valid — run it after any
`[tool.briefcase]` change. Re-running `create` after dependency changes is safe.

> **Verified:** on Apple Silicon macOS this produces `dist/Storywell-0.1.0a0.dmg` (~73 MB —
> Chromium is _not_ in it; it downloads on first launch). Signing/notarization below were not
> verified here (they need your certificates).

## Signed release builds

### macOS (.dmg, notarized)

Needs a "Developer ID Application" certificate in your keychain and an App Store Connect API
key (or app-specific password) for notarization.

```sh
briefcase package macOS --identity "Developer ID Application: Your Name (TEAMID)"
```

Briefcase signs the bundle (including the bundled Python and Playwright's `node` binary under
the hardened runtime) and notarizes the `.dmg`. Verify with `spctl -a -vvv dist/Storywell.dmg`.

### Windows (.msi)

Sign the built MSI with `signtool` and your code-signing certificate:

```sh
briefcase package windows
signtool sign /fd SHA256 /a dist\Storywell-0.1.0a0.msi
```

Unsigned MSIs trigger SmartScreen on other machines — sign for distribution.

### Linux (AppImage)

```sh
briefcase package linux
```

AppImages are not signed; distribute the file (and optionally a `.zsync` for updates).

## Known gaps / TODO

- **Single-arch macOS only.** `universal_build = false` because a universal build pulls
  binary-only wheels for _both_ arches and the sdist-only deps above have no per-arch wheels.
  The wheelhouse fix likely makes universal work too (the three wheels are `py3-none-any`) —
  untested. An arm64-only `.dmg` will not run on Intel Macs without Rosetta.
- **Signing/notarization unverified** — config and the unsigned build are confirmed; the
  signed path needs your certificates and a real desktop session.
- **Brand fonts are self-hosted** (no Google CDN call from the packaged app — privacy + offline).
  The `latin` + `latin-ext` `woff2` (Fraunces / Newsreader / IBM Plex Mono, all OFL — see
  `src/storywell/desktop/web/fonts/LICENSE.txt`) live under `web/fonts/` with a local
  `fonts.css`. Regenerate after a brand-font change with `python tools/bundle_fonts.py`.
- **App icon** — no `.icns`/`.ico`/`.png` icon set is wired up yet; Briefcase uses a default.
  Add `icon = "..."` under `[tool.briefcase.app.storywell]` once assets exist.
- **Auto-update** — not configured.
- **Linux backend** — `webkit2gtk-4.1` vs `4.0` package naming is distro-specific and the
  least-tested target.
- The first-run Chromium download needs network on first launch; there is no offline installer.
