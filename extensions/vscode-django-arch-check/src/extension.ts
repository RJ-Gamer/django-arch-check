import * as vscode from "vscode";

import { getSettings } from "./config";
import { analyzeWorkspace, CliExecutionError } from "./services/cli";
import { resolveWorkspaceFolder } from "./services/workspace";
import { DashboardPanel } from "./webview/dashboardPanel";

export function activate(context: vscode.ExtensionContext): void {
  const outputChannel = vscode.window.createOutputChannel("Django Arch Check");
  context.subscriptions.push(outputChannel);

  context.subscriptions.push(
    vscode.commands.registerCommand(
      "djangoArchCheck.openDashboard",
      async (resource?: vscode.Uri) => {
        const workspaceFolder = resolveWorkspaceFolder(resource);
        if (!workspaceFolder) {
          vscode.window.showWarningMessage(
            "Django Arch Check: open a workspace folder before launching the dashboard.",
          );
          return;
        }

        await DashboardPanel.createOrShow(
          context,
          workspaceFolder,
          outputChannel,
        );
      },
    ),
  );

  context.subscriptions.push(
    vscode.commands.registerCommand(
      "djangoArchCheck.refreshDashboard",
      async () => {
        const refreshed = await DashboardPanel.refreshCurrent();
        if (!refreshed) {
          vscode.window.showInformationMessage(
            "Django Arch Check: no dashboard is open yet. Use Open Dashboard first.",
          );
        }
      },
    ),
  );

  context.subscriptions.push(
    vscode.commands.registerCommand(
      "djangoArchCheck.analyzeWorkspace",
      async (resource?: vscode.Uri) => {
        const workspaceFolder = resolveWorkspaceFolder(resource);
        if (!workspaceFolder) {
          vscode.window.showWarningMessage(
            "Django Arch Check: open a workspace folder before running analysis.",
          );
          return;
        }

        await vscode.window.withProgress(
          {
            location: vscode.ProgressLocation.Notification,
            title: `Django Arch Check: analyzing ${workspaceFolder.name}`,
            cancellable: false,
          },
          async () => {
            try {
              const result = await analyzeWorkspace(
                workspaceFolder,
                outputChannel,
              );
              const settings = getSettings();

              if (settings.openDashboardOnAnalyze) {
                await DashboardPanel.createOrShowFromReport(
                  context,
                  workspaceFolder,
                  outputChannel,
                  result.reportPath,
                );
              } else {
                vscode.window.showInformationMessage(
                  `Django Arch Check: report generated at ${result.reportPath}`,
                );
              }
            } catch (error) {
              const message =
                error instanceof CliExecutionError
                  ? error.message
                  : error instanceof Error
                    ? error.message
                    : "Unknown analysis error.";

              if (getSettings().showOutputChannelOnError) {
                outputChannel.show(true);
              }

              vscode.window.showErrorMessage(
                `Django Arch Check: ${message}`,
              );
            }
          },
        );
      },
    ),
  );
}

export function deactivate(): void {
  // No async teardown is required for this extension.
}
