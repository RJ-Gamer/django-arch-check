import * as vscode from "vscode";

export interface ExtensionSettings {
  cliPath: string;
  extraArgs: string[];
  openDashboardOnAnalyze: boolean;
  showOutputChannelOnError: boolean;
}

export function getSettings(): ExtensionSettings {
  const config = vscode.workspace.getConfiguration("djangoArchCheck");
  const cliPath = config.get<string>("cliPath", "django-arch-check").trim();
  const extraArgs = config.get<string[]>("extraArgs", []);

  return {
    cliPath: cliPath || "django-arch-check",
    extraArgs: Array.isArray(extraArgs) ? extraArgs : [],
    openDashboardOnAnalyze: config.get<boolean>("openDashboardOnAnalyze", true),
    showOutputChannelOnError: config.get<boolean>("showOutputChannelOnError", true),
  };
}
