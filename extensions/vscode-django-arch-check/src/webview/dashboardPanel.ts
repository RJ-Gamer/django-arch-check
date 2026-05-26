import { promises as fs } from "node:fs";

import * as vscode from "vscode";

import { getSettings } from "../config";
import {
  analyzeWorkspace,
  CliExecutionError,
} from "../services/cli";
import { renderErrorHtml, renderLoadingHtml } from "./views";

export class DashboardPanel {
  private static currentPanel: DashboardPanel | undefined;

  static async createOrShow(
    context: vscode.ExtensionContext,
    workspaceFolder: vscode.WorkspaceFolder,
    outputChannel: vscode.OutputChannel,
  ): Promise<DashboardPanel> {
    if (DashboardPanel.currentPanel) {
      DashboardPanel.currentPanel.workspaceFolder = workspaceFolder;
      DashboardPanel.currentPanel.panel.title = `Django Architecture Dashboard: ${workspaceFolder.name}`;
      DashboardPanel.currentPanel.panel.reveal(vscode.ViewColumn.Active);
      await DashboardPanel.currentPanel.refresh();
      return DashboardPanel.currentPanel;
    }

    const panel = vscode.window.createWebviewPanel(
      "djangoArchCheck.dashboard",
      `Django Architecture Dashboard: ${workspaceFolder.name}`,
      vscode.ViewColumn.Active,
      {
        enableScripts: true,
        retainContextWhenHidden: true,
      },
    );

    DashboardPanel.currentPanel = new DashboardPanel(
      context,
      panel,
      workspaceFolder,
      outputChannel,
    );
    await DashboardPanel.currentPanel.refresh();
    return DashboardPanel.currentPanel;
  }

  static async createOrShowFromReport(
    context: vscode.ExtensionContext,
    workspaceFolder: vscode.WorkspaceFolder,
    outputChannel: vscode.OutputChannel,
    reportPath: string,
  ): Promise<DashboardPanel> {
    if (DashboardPanel.currentPanel) {
      DashboardPanel.currentPanel.workspaceFolder = workspaceFolder;
      DashboardPanel.currentPanel.panel.title = `Django Architecture Dashboard: ${workspaceFolder.name}`;
      DashboardPanel.currentPanel.panel.reveal(vscode.ViewColumn.Active);
      await DashboardPanel.currentPanel.loadReportHtml(reportPath);
      return DashboardPanel.currentPanel;
    }

    const panel = vscode.window.createWebviewPanel(
      "djangoArchCheck.dashboard",
      `Django Architecture Dashboard: ${workspaceFolder.name}`,
      vscode.ViewColumn.Active,
      {
        enableScripts: true,
        retainContextWhenHidden: true,
      },
    );

    DashboardPanel.currentPanel = new DashboardPanel(
      context,
      panel,
      workspaceFolder,
      outputChannel,
    );
    await DashboardPanel.currentPanel.loadReportHtml(reportPath);
    return DashboardPanel.currentPanel;
  }

  static async refreshCurrent(): Promise<boolean> {
    if (!DashboardPanel.currentPanel) {
      return false;
    }

    await DashboardPanel.currentPanel.refresh();
    return true;
  }

  private constructor(
    private readonly context: vscode.ExtensionContext,
    private readonly panel: vscode.WebviewPanel,
    private workspaceFolder: vscode.WorkspaceFolder,
    private readonly outputChannel: vscode.OutputChannel,
  ) {
    this.panel.onDidDispose(() => {
      if (DashboardPanel.currentPanel === this) {
        DashboardPanel.currentPanel = undefined;
      }
    });
  }

  async refresh(): Promise<void> {
    this.panel.webview.html = renderLoadingHtml(this.workspaceFolder.name);

    try {
      const result = await analyzeWorkspace(
        this.workspaceFolder,
        this.outputChannel,
      );
      await this.loadReportHtml(result.reportPath);
    } catch (error) {
      const settings = getSettings();
      const cliError =
        error instanceof CliExecutionError
          ? error
          : new CliExecutionError(
              error instanceof Error ? error.message : "Unknown dashboard error.",
              {
                command: "django-arch-check",
                args: [],
                reportPath: "",
                stdout: "",
                stderr: "",
                exitCode: null,
              },
            );

      const details = [cliError.stderr, cliError.stdout]
        .filter((value) => value.trim())
        .join("\n\n");

      this.panel.webview.html = renderErrorHtml({
        title: "Unable to open dashboard",
        message: cliError.message,
        details,
        hint:
          'Make sure "django-arch-check" is installed and reachable from VS Code, or set djangoArchCheck.cliPath in Settings.',
      });

      if (settings.showOutputChannelOnError) {
        this.outputChannel.show(true);
      }

      vscode.window.showErrorMessage(
        `Django Arch Check: ${cliError.message}`,
      );
    }
  }

  private async loadReportHtml(reportPath: string): Promise<void> {
    const html = await fs.readFile(reportPath, "utf8");
    this.panel.webview.html = html;
    this.panel.title = `Django Architecture Dashboard: ${this.workspaceFolder.name}`;
    this.outputChannel.appendLine(
      `[django-arch-check] Loaded dashboard from ${reportPath}`,
    );
  }
}
