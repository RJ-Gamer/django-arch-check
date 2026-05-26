# Django Arch Check for VS Code

![Version](https://img.shields.io/badge/VERSION-0.0.1-007acc?style=for-the-badge&logo=visualstudiocode&logoColor=white)
![VS Code](https://img.shields.io/badge/VS%20CODE-1.90%2B-007acc?style=for-the-badge&logo=visualstudiocode&logoColor=white)
![Python CLI](https://img.shields.io/badge/REQUIRES-django--arch--check-4f8ef7?style=for-the-badge)
![License](https://img.shields.io/badge/LICENSE-MIT-yellow?style=for-the-badge)

An interactive architecture dashboard for Django projects inside VS Code.

This extension runs the `django-arch-check` CLI, generates the same `arch-report.html` used by the Python package, and opens that report inside a VS Code Webview. The goal is to keep the editor experience visually rich and fully aligned with the CLI report instead of maintaining a second dashboard implementation in TypeScript.

---

## What It Does

- Runs `django-arch-check analyze --format html` for the current workspace
- Opens the generated `arch-report.html` inside VS Code
- Preserves the report's built-in interactivity:
  - severity filters
  - expandable findings
  - dark/light theme switcher
- Refreshes the same dashboard panel on demand
- Surfaces CLI errors through a dedicated VS Code output channel

---

## Why This Extension Exists

The Python package already provides:

- static architecture analysis
- detector scoring
- JSON and SARIF outputs
- a polished HTML dashboard

This extension reuses that dashboard directly, so:

- the CLI report and editor report stay consistent
- new report design changes automatically benefit the extension
- there is no duplicated analyzer or frontend logic

---

## Requirements

You need all of the following:

- VS Code `1.90+`
- Python with `django-arch-check` installed
- a Django project folder open in VS Code

Install the CLI with:

```bash
pip install django-arch-check
```

If VS Code cannot find the executable automatically, set `djangoArchCheck.cliPath` in Settings.

---

## Quick Start

1. Open a Django project folder in VS Code.
2. Open the Command Palette.
3. Run `Django Arch Check: Open Dashboard`.
4. Wait for the analysis to finish.
5. Explore the dashboard directly inside the editor.

The extension will generate `arch-report.html` in the workspace root and load it into a Webview.

---

## Commands

### `Django Arch Check: Open Dashboard`

Runs the HTML report flow for the current workspace and opens the dashboard panel.

Use this when:

- you want to analyze the project and view the report immediately
- no dashboard is currently open

### `Django Arch Check: Refresh Dashboard`

Re-runs the analysis for the workspace currently associated with the open panel and reloads the dashboard.

Use this when:

- code has changed
- you want an updated report in the same panel

### `Django Arch Check: Analyze Workspace`

Runs the CLI analysis and, depending on settings, either:

- opens the dashboard automatically, or
- only reports the generated HTML path

---

## Settings

The extension contributes the following settings:

### `djangoArchCheck.cliPath`

Default:

```json
"djangoArchCheck.cliPath": "django-arch-check"
```

Use this when the CLI is installed in a location VS Code cannot resolve automatically.

Examples:

```json
"djangoArchCheck.cliPath": "C:\\Users\\you\\AppData\\Roaming\\Python\\Python312\\Scripts\\django-arch-check.exe"
```

or

```json
"djangoArchCheck.cliPath": "/Users/you/.local/bin/django-arch-check"
```

### `djangoArchCheck.extraArgs`

Default:

```json
"djangoArchCheck.extraArgs": []
```

Use this to pass additional CLI flags before the workspace path.

Example:

```json
"djangoArchCheck.extraArgs": ["--ignore-path", "legacy/", "--ignore", "direct_sql"]
```

### `djangoArchCheck.openDashboardOnAnalyze`

Default:

```json
"djangoArchCheck.openDashboardOnAnalyze": true
```

If enabled, `Analyze Workspace` opens or refreshes the dashboard after analysis.

### `djangoArchCheck.showOutputChannelOnError`

Default:

```json
"djangoArchCheck.showOutputChannelOnError": true
```

If enabled, the `Django Arch Check` output channel is shown automatically whenever the CLI fails.

---

## Output Channel

The extension writes execution details to a dedicated VS Code output channel:

- `Django Arch Check`

It includes:

- resolved workspace path
- executed CLI command
- stdout
- stderr
- exit code

This is the first place to check if the extension is not behaving as expected.

---

## Important Exit Code Behavior

`django-arch-check` uses exit code `1` when critical architectural findings are present.

That means:

- exit code `1` does **not** always mean the extension failed
- if `arch-report.html` was generated successfully, the extension still treats the run as usable and loads the dashboard

This is intentional and important for CI compatibility.

---

## How It Works

The extension does not analyze Python code itself.

It simply:

1. resolves the active workspace folder
2. runs:

```bash
django-arch-check analyze --format html <workspace>
```

3. waits for `arch-report.html`
4. reads the generated HTML
5. injects it into a VS Code Webview

This keeps the Python package as the single source of truth for:

- detectors
- scoring
- HTML dashboard rendering

---

## Local Development

This extension lives inside the main `django-arch-check` repository under:

```text
extensions/vscode-django-arch-check/
```

### Install Dependencies

```bash
cd extensions/vscode-django-arch-check
npm install
```

### Compile

```bash
npm run compile
```

### Watch Mode

```bash
npm run watch
```

### Launch Extension Development Host

Open the extension folder or repo in VS Code and press `F5`.

Use the provided launch configuration:

- `Run Django Arch Check Extension`

This opens an `Extension Development Host` window where you can test commands against a real Django workspace.

---

## Packaging

This extension includes packaging scripts in `package.json`.

### Create a VSIX

```bash
npm run package:vsix
```

This creates a local `.vsix` package you can install manually in VS Code.

### Publish to the Visual Studio Marketplace

```bash
npm run publish:marketplace
```

Before publishing, you must:

- create a publisher account
- create a Personal Access Token for the Marketplace
- log in with `vsce`

Detailed instructions are in the repo publish guide:

https://github.com/RJ-Gamer/django-arch-check/blob/main/extensions/vscode-django-arch-check/PUBLISHING.md

---

## Manual Installation From VSIX

After packaging:

1. Open VS Code
2. Go to Extensions
3. Open the `...` menu
4. Choose `Install from VSIX...`
5. Select the generated `.vsix` file

This is the easiest way to test the packaged extension before public release.

---

## Troubleshooting

### The dashboard command says the CLI was not found

Set `djangoArchCheck.cliPath` explicitly in VS Code settings.

### The dashboard opens but analysis seems stale

Run `Django Arch Check: Refresh Dashboard`.

### The CLI works in your terminal but not in VS Code

This usually means the GUI-launched VS Code process has a different `PATH` than your shell.

Fix it by setting:

```json
"djangoArchCheck.cliPath": "full/path/to/django-arch-check"
```

### The command fails even though the project has findings

Open the `Django Arch Check` output channel and verify:

- whether the report file was generated
- whether the CLI emitted a real error
- whether the workspace path is correct

### The dashboard does not update after changing code

Refresh the panel with:

- `Django Arch Check: Refresh Dashboard`

The current extension does not auto-run on every save.

---

## Known Limitations

- The extension depends on the external Python CLI being installed
- It currently focuses on the HTML dashboard, not inline diagnostics
- It generates `arch-report.html` in the workspace root using the current CLI behavior
- It does not yet map dashboard findings back to clickable editor navigation

---

## Roadmap

Planned future improvements include:

- clickable file/line navigation from report findings
- background refresh workflows
- Problems panel integration via JSON or SARIF
- richer workspace-specific configuration
- tighter dashboard/editor linking

The broader implementation plan lives in the repo roadmap:

https://github.com/RJ-Gamer/django-arch-check/blob/main/VSCODE_EXTENSION_ROADMAP.md

---

## Development Notes

- Source lives in `src/`
- Compiled output goes to `out/`
- Webview rendering is driven by the generated `arch-report.html`
- Packaging excludes source and dev-only files via `.vscodeignore`

---

## License

MIT.
