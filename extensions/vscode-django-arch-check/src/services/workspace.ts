import * as vscode from "vscode";

export function resolveWorkspaceFolder(
  resource?: vscode.Uri,
): vscode.WorkspaceFolder | undefined {
  if (resource) {
    return vscode.workspace.getWorkspaceFolder(resource);
  }

  const activeResource = vscode.window.activeTextEditor?.document.uri;
  if (activeResource) {
    const activeFolder = vscode.workspace.getWorkspaceFolder(activeResource);
    if (activeFolder) {
      return activeFolder;
    }
  }

  return vscode.workspace.workspaceFolders?.[0];
}
