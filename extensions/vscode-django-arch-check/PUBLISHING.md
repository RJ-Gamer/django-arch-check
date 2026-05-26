# Publishing Guide

This guide covers how to package and publish the `Django Arch Check` VS Code extension.

---

## Prerequisites

You need:

- a Visual Studio Marketplace publisher account
- a Personal Access Token for that publisher
- Node.js and npm installed
- extension dependencies installed

From the extension folder:

```bash
cd extensions/vscode-django-arch-check
npm install
```

---

## Step 1 — Create a Publisher

If you have not created one yet:

1. Go to the Visual Studio Marketplace publisher management page
2. Create a publisher
3. Choose the publisher ID you want to use

This publisher ID must match the `publisher` field in `package.json`.

Current value:

```json
"publisher": "Rajatjog"
```

If your actual publisher ID is different, update `package.json` before publishing.

---

## Step 2 — Create a Personal Access Token

Create a Marketplace PAT with permission to publish extensions.

Then log in with `vsce`:

```bash
npx vsce login <publisher-id>
```

Example:

```bash
npx vsce login rj-gamer
```

You will be prompted for the PAT.

---

## Step 3 — Verify Metadata

Before publishing, confirm these files are correct:

- `package.json`
- `README.md`
- `CHANGELOG.md`
- `.vscodeignore`

Check especially:

- `name`
- `displayName`
- `publisher`
- `version`
- `description`
- `repository`
- `homepage`
- `bugs`

---

## Step 4 — Compile

Build the extension:

```bash
npm run compile
```

Make sure the build succeeds before packaging or publishing.

---

## Step 5 — Create a VSIX Package

To generate a local installable package:

```bash
npm run package:vsix
```

This will create a `.vsix` file in the extension folder.

Use this to:

- test the packaged output locally
- share preview builds privately
- validate marketplace packaging before release

---

## Step 6 — Test the VSIX Locally

In VS Code:

1. Open Extensions
2. Click the `...` menu
3. Choose `Install from VSIX...`
4. Select the generated `.vsix`

Then verify:

- the extension activates correctly
- commands are visible
- the dashboard opens
- the README renders well

---

## Step 7 — Publish To Marketplace

When everything looks correct:

```bash
npm run publish:marketplace
```

This runs:

```bash
vsce publish
```

If successful, the extension will appear under your publisher account on the Visual Studio Marketplace.

---

## Versioning Workflow

For each release:

1. Update `package.json` version
2. Update `CHANGELOG.md`
3. Recompile
4. Package locally
5. Smoke test the VSIX
6. Publish

Example:

```bash
npm version patch
npm run compile
npm run package:vsix
npm run publish:marketplace
```

---

## Recommended First Release Checklist

- `package.json` version is correct
- `publisher` is correct
- README is polished
- CHANGELOG has a release entry
- commands work in Extension Development Host
- `npm run compile` passes
- `.vsix` installs cleanly
- dashboard renders from a real Django workspace

---

## Common Problems

## `vsce` login fails

Usually caused by:

- wrong publisher ID
- invalid PAT
- expired PAT

Fix:

- verify publisher name
- generate a new PAT
- run `npx vsce login <publisher-id>` again

## Publish fails because of missing files

Check:

- `.vscodeignore`
- `files` in `package.json`
- compile output exists in `out/`

## README looks wrong on Marketplace

Preview it carefully before publishing.

Common issues:

- unsupported HTML
- broken relative links
- missing images

Recommendation:

- keep README mostly Markdown
- avoid fragile asset references in the first public release

## Wrong publisher in package.json

Marketplace publishing requires the `publisher` field to match the publisher account you logged into with `vsce`.

---

## Suggested Release Process

Use this exact sequence:

```bash
cd extensions/vscode-django-arch-check
npm install
npm run compile
npm run package:vsix
```

Install and test the VSIX locally.

Then publish:

```bash
npx vsce login rj-gamer
npm run publish:marketplace
```

---

## Optional Next Improvements Before Public Release

- add an extension icon
- add marketplace screenshots or GIFs
- add a `LICENSE` file copy in the extension folder if you want stricter package-local clarity
- add automated extension tests later

---

## Final Note

The extension is already set up for the practical release path:

- compile with `npm run compile`
- package with `npm run package:vsix`
- publish with `npm run publish:marketplace`

If you want, the next step can be making the first release more polished by adding:

- an icon
- marketplace screenshots
- a root README section linking to the extension
