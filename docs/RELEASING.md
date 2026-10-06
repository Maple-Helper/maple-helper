# Releasing Maple Helper

Everything runs in GitHub Actions. You decide *when*; CI does the rest.

| Workflow | Runs on | Does |
|---|---|---|
| **CI** (`ci.yml`) | every push to `main`, every PR | lint + tests on Windows and macOS and, alongside them, a full build on each (the installer is compressed lighter than in releases, to save time). Windows: frozen-exe self-test, portable zip, installer install → self-test → uninstall. macOS: `.app` self-test, DMG mount → self-test. The builds are downloadable from the run page (14 days). |
| **Release** (`release.yml`) | pushing a tag `vX.Y.Z` | the same checks with the real knowledge base bundled (both builds run alongside the tests), then, once everything passed, publishes the GitHub Release with the Windows installer + portable zip and the macOS DMG (both platforms or nothing). Installed Windows apps update themselves to it; Mac apps show a download notice. |
| **Update knowledge base** (`kb-update.yml`) | nightly (changed pages), full refresh on Sundays, or the *Run workflow* button | scrapes NiaMeowDB politely, **validates**, and replaces `kb.zip` + `kb-manifest.json` on the latest release when content changed. |

## Cut a release

If the release changes how Claude answers (prompts, `brain.py`, the model), run the Claude answer evals on your
machine first and check for regressions: see [EVALS.md](EVALS.md). CI never runs them (they spend plan usage).

1. Bump `__version__` in `maplehelper/__init__.py` and add that version's notes (Hebrew and English) at the top of
   `assets/notes/whatsnew.json` (players see them after updating; a test fails without them). Commit and merge to `main`.
2. Tag the merged commit and push the tag:
   ```powershell
   git switch main; git pull
   git tag v0.2.0
   git push origin v0.2.0
   ```
3. Watch **Actions → Release**. When it's green, the release is live. Every installed copy downloads
   it in the background, checks its SHA-256 against `SHA256SUMS.txt`, and installs it when the player
   closes the app.

The release fails, and publishes nothing, when:
- the tag doesn't match `__version__`, or the tagged commit isn't on `main`,
- any lint rule or test fails on either OS, or a frozen-app self-test, the installer round trip or the DMG round trip fails,
- the knowledge base fails validation.

To retry after fixing: delete the tag (`git push --delete origin v0.2.0; git tag -d v0.2.0`), then tag again.

A job that fails after about 15 minutes with "The job was not acquired by Runner" never started: GitHub had no
runner free (macOS arm64 runners run short sometimes). Nothing in the code is wrong; click **Re-run failed jobs**.

**Prereleases:** `v0.3.0-beta.1` (with `__version__ = "0.3.0"`) publishes a prerelease. GitHub never
marks it "latest", so the auto-updater and KB updates ignore it. Share its link with testers.
The beta's app reports `0.3.0`, so it never updates itself to the final `0.3.0`: testers install the final
release by hand, or the final goes out as the next patch (`v0.3.1`), which the beta does update to.

**First release:** with no earlier release to carry the KB forward from, the Release workflow first
looks for a prerelease tagged `kb-seed` and uses its `kb.zip` (this is how the Hebrew name dictionary,
`aliases.json`, which is generated with Claude and can't be rebuilt in CI, gets into the first release).
Without a seed it scrapes NiaMeowDB itself (about an hour), validates the result, and bundles it.
Publish a seed from a PC with `data\kb`:
```powershell
python tools/kb_release.py validate data/kb --min-entities 500
python tools/kb_release.py pack data/kb dist-kb
gh release create kb-seed dist-kb/kb.zip dist-kb/kb-manifest.json --prerelease --latest=false --title "Knowledge base seed" --notes "Seed for the first release"
```

`tools/release.py` is not a way to release: it publishes Windows assets only (no macOS DMG, no portable zip,
no tag or `main` check), so it refuses a full release unless you add `--force` (an emergency only). The nightly
uses its `--kb-only` mode.

## What every release carries, and why

| Asset | Read by |
|---|---|
| `MapleHelper-Setup.exe` (unversioned name) | players, and the in-app auto-updater (`updater.download_app_update`) |
| `SHA256SUMS.txt` | the auto-updater: **no matching hash, no update** |
| `kb.zip` + `kb-manifest.json` | installed apps' KB updates (`releases/latest/download/kb-manifest.json`) |
| `MapleHelper-X.Y.Z-portable.zip` | players who don't want an installer |
| `MapleHelper-macOS.dmg` (unversioned name) | Mac players; the Mac app links to the release page when a newer version is out |

Apps read these from whatever release is **latest**, so never publish a release by hand without them.
The Release workflow carries the current KB forward automatically.

## Roll back a bad release

The auto-updater only moves *forward* (it installs a release only when its version is higher). So
marking an older release "latest" stops new downloads of the bad one, but it **doesn't** downgrade
players who already have it. Fix forward:

1. On GitHub, edit the previous good release and tick **Set as the latest release** (this stops the spread right away).
2. Fix, bump to a **new** version (e.g. `0.2.1`), and release. Everyone, including players on the bad version, updates to it.

## Knowledge base

- Validation rejects a KB that has fewer than 500 entities, is missing one of the 11 categories, has
  index entries without pages, or lost more than 10% of the previous entity count. A rejected KB is
  never published, and players keep the previous one. The failed run shows why.
- Only real content changes are published (the scraper counts changed pages), so players don't
  re-download identical data.

## Owner setup (needs repository admin)

1. **Branch protection** on `main` (Settings → Branches): require a pull request and the status
   checks **`test / Lint & test`** (green only when the Windows and macOS test jobs both pass),
   **`Build & smoke test`** and **`Build & smoke test (macOS)`** from CI.
2. **Code signing (optional; removes the SmartScreen warning, and recommended now that updates
   install silently):** add a repository secret `MAPLEHELPER_SIGN` holding a sign command with a
   `{file}` placeholder. The build then signs `Maple Helper.exe` and the installer. For example:
   `signtool sign /fd sha256 /tr http://timestamp.digicert.com /td sha256 /f cert.pfx /p <password> "{file}"`.
   Options for open-source apps: [SignPath Foundation](https://signpath.org) (free for OSS) or Azure Trusted Signing.

## Build locally

```powershell
.venv\Scripts\pip install -r requirements-dev.txt
pwsh packaging\build.ps1 -KbDir tests\fixtures\kb -SkipInstaller   # fast; no Inno Setup needed
pwsh packaging\build.ps1 -RequireKb                                # with data\kb + Inno Setup 6
```

Output goes to `dist\release\`. Any build can check itself with
`"dist\Maple Helper\Maple Helper.exe" --selftest report.txt` (exit code 0 = healthy; the report says why not).
`-TestInstaller` really installs and uninstalls the app, so it only runs in CI unless you add `-Force`.

On a Mac:

```bash
.venv/bin/pip install -r requirements-dev.txt
packaging/build-macos.sh --kb-dir tests/fixtures/kb   # fast
packaging/build-macos.sh --require-kb --test-dmg      # what a release runs (needs data/kb)
```

Output goes to `dist/release/MapleHelper-macOS.dmg`; `"dist/Maple Helper.app/Contents/MacOS/Maple Helper" --selftest report.txt`
checks the app.

## macOS notes

- GitHub's `macos-latest` runner is Apple Silicon, so the DMG is `arm64` only. Intel Macs are not supported:
  `numpy` and `ctranslate2` have no universal2 wheels to build a universal app from.
- The app is a menu bar app (`LSUIElement`, no Dock icon). Hotkeys are Carbon `RegisterEventHotKey`, the Mac
  counterpart of `RegisterHotKey`: no key-state polling, no event tap, no Input Monitoring grant. The only grant
  is **Screen Recording** (game window title + screenshot).
- No silent self-update on macOS (the installer is a Windows program): when a newer release is out, the app
  shows a notice and a menu bar entry linking to it. KB updates work the same as on Windows.
- **Signing (not set up yet):** the app is ad-hoc signed, so players click **Open Anyway** in System Settings →
  Privacy & Security on first launch, and macOS may ask for Screen Recording again after an update. Fixing both
  needs an Apple Developer account ($99/year): sign with a *Developer ID Application* certificate (hardened
  runtime + the `com.apple.security.device.audio-input` entitlement), then notarize and staple the DMG with
  `xcrun notarytool` / `xcrun stapler` in `packaging/build-macos.sh`.
- `tools/release.py --force` (the emergency release from a PC) publishes Windows assets only; releases come from
  the Release workflow.

## README snippet

```markdown
[![CI](https://github.com/Maple-Helper/maple-helper/actions/workflows/ci.yml/badge.svg)](https://github.com/Maple-Helper/maple-helper/actions/workflows/ci.yml)

**[Download Maple Helper](https://github.com/Maple-Helper/maple-helper/releases/latest/download/MapleHelper-Setup.exe)** (Windows 10/11). It updates itself.
```
