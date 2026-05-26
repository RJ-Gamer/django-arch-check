import { promises as fs } from "node:fs";
import * as path from "node:path";
import { spawn } from "node:child_process";

import * as vscode from "vscode";

import { getSettings } from "../config";

export interface AnalyzeWorkspaceResult {
  command: string;
  args: string[];
  reportPath: string;
  stdout: string;
  stderr: string;
  exitCode: number | null;
  durationMs: number;
}

export class CliExecutionError extends Error {
  readonly stdout: string;
  readonly stderr: string;
  readonly exitCode: number | null;
  readonly command: string;
  readonly args: string[];
  readonly reportPath: string;

  constructor(
    message: string,
    result: Omit<AnalyzeWorkspaceResult, "durationMs">,
  ) {
    super(message);
    this.name = "CliExecutionError";
    this.stdout = result.stdout;
    this.stderr = result.stderr;
    this.exitCode = result.exitCode;
    this.command = result.command;
    this.args = result.args;
    this.reportPath = result.reportPath;
  }
}

export async function analyzeWorkspace(
  workspaceFolder: vscode.WorkspaceFolder,
  outputChannel: vscode.OutputChannel,
): Promise<AnalyzeWorkspaceResult> {
  const settings = getSettings();
  const reportPath = path.join(workspaceFolder.uri.fsPath, "arch-report.html");
  const args = [
    "analyze",
    "--format",
    "html",
    ...settings.extraArgs,
    workspaceFolder.uri.fsPath,
  ];
  const startedAt = Date.now();

  outputChannel.appendLine(
    `[django-arch-check] Running: ${settings.cliPath} ${args.join(" ")}`,
  );
  outputChannel.appendLine(
    `[django-arch-check] Workspace: ${workspaceFolder.uri.fsPath}`,
  );

  const result = await new Promise<AnalyzeWorkspaceResult>((resolve, reject) => {
    let stdout = "";
    let stderr = "";

    const child = spawn(settings.cliPath, args, {
      cwd: workspaceFolder.uri.fsPath,
      shell: false,
      windowsHide: true,
      env: process.env,
    });

    child.stdout.on("data", (chunk: Buffer | string) => {
      stdout += chunk.toString();
    });

    child.stderr.on("data", (chunk: Buffer | string) => {
      stderr += chunk.toString();
    });

    child.on("error", (error: NodeJS.ErrnoException) => {
      const message =
        error.code === "ENOENT"
          ? `Could not find "${settings.cliPath}". Install django-arch-check or set djangoArchCheck.cliPath.`
          : `Failed to launch "${settings.cliPath}": ${error.message}`;

      reject(
        new CliExecutionError(message, {
          command: settings.cliPath,
          args,
          reportPath,
          stdout,
          stderr,
          exitCode: null,
        }),
      );
    });

    child.on("close", (exitCode) => {
      resolve({
        command: settings.cliPath,
        args,
        reportPath,
        stdout,
        stderr,
        exitCode,
        durationMs: Date.now() - startedAt,
      });
    });
  });

  if (result.stdout.trim()) {
    outputChannel.appendLine("[django-arch-check] stdout:");
    outputChannel.appendLine(result.stdout.trimEnd());
  }

  if (result.stderr.trim()) {
    outputChannel.appendLine("[django-arch-check] stderr:");
    outputChannel.appendLine(result.stderr.trimEnd());
  }

  outputChannel.appendLine(
    `[django-arch-check] Process exited with code ${String(result.exitCode)} in ${result.durationMs}ms`,
  );

  const reportExists = await fileExists(result.reportPath);
  if (!reportExists) {
    throw new CliExecutionError(
      `django-arch-check did not produce ${result.reportPath}.`,
      {
        command: result.command,
        args: result.args,
        reportPath: result.reportPath,
        stdout: result.stdout,
        stderr: result.stderr,
        exitCode: result.exitCode,
      },
    );
  }

  return result;
}

async function fileExists(targetPath: string): Promise<boolean> {
  try {
    await fs.access(targetPath);
    return true;
  } catch {
    return false;
  }
}
