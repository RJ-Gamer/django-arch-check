function escapeHtml(value: string): string {
  return value
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

export function renderLoadingHtml(workspaceName: string): string {
  return `<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Django Architecture Dashboard</title>
  <style>
    :root {
      --bg:#0a101f;
      --surface:#10192f;
      --surface2:#14203a;
      --fg:#eef4ff;
      --muted:#8fa1bd;
      --accent:#4ec0f9;
      --border:#223254;
    }
    * { box-sizing:border-box; }
    body {
      margin:0;
      min-height:100vh;
      display:grid;
      place-items:center;
      font-family:'Segoe UI',Tahoma,sans-serif;
      color:var(--fg);
      background:
        radial-gradient(circle at 20% 20%, rgba(78,192,249,.16), transparent 22%),
        radial-gradient(circle at 80% 15%, rgba(255,118,84,.12), transparent 20%),
        var(--bg);
    }
    .panel {
      width:min(640px, calc(100vw - 40px));
      padding:32px;
      border:1px solid var(--border);
      border-radius:18px;
      background:linear-gradient(180deg, var(--surface2), var(--surface));
      box-shadow:0 28px 80px rgba(0,0,0,.32);
    }
    .eyebrow {
      color:var(--muted);
      font-size:12px;
      letter-spacing:.12em;
      text-transform:uppercase;
      margin-bottom:10px;
    }
    h1 {
      margin:0 0 12px;
      font-size:30px;
      line-height:1.05;
    }
    p {
      margin:0;
      color:var(--muted);
      font-size:14px;
      line-height:1.6;
    }
    .row {
      margin-top:22px;
      display:flex;
      align-items:center;
      gap:14px;
    }
    .spinner {
      width:18px;
      height:18px;
      border:2px solid rgba(255,255,255,.14);
      border-top-color:var(--accent);
      border-radius:50%;
      animation:spin .8s linear infinite;
      flex-shrink:0;
    }
    code {
      color:var(--accent);
      background:rgba(78,192,249,.08);
      border:1px solid rgba(78,192,249,.18);
      border-radius:6px;
      padding:2px 6px;
    }
    @keyframes spin {
      from { transform:rotate(0deg); }
      to { transform:rotate(360deg); }
    }
  </style>
</head>
<body>
  <main class="panel">
    <div class="eyebrow">Django Arch Check</div>
    <h1>Generating Architecture Dashboard</h1>
    <p>Running <code>django-arch-check analyze --format html</code> for <code>${escapeHtml(workspaceName)}</code>.</p>
    <div class="row">
      <div class="spinner" aria-hidden="true"></div>
      <p>The report will appear here as soon as the CLI finishes.</p>
    </div>
  </main>
</body>
</html>`;
}

export function renderErrorHtml(params: {
  title: string;
  message: string;
  details?: string;
  hint?: string;
}): string {
  const detailsBlock = params.details
    ? `<pre>${escapeHtml(params.details)}</pre>`
    : "";
  const hintBlock = params.hint
    ? `<p class="hint">${escapeHtml(params.hint)}</p>`
    : "";

  return `<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Django Architecture Dashboard</title>
  <style>
    :root {
      --bg:#120c12;
      --surface:#1b1220;
      --surface2:#25162c;
      --fg:#f9eff7;
      --muted:#c6a8c0;
      --danger:#ff7660;
      --border:#4b2234;
    }
    * { box-sizing:border-box; }
    body {
      margin:0;
      min-height:100vh;
      display:grid;
      place-items:center;
      padding:20px;
      font-family:'Segoe UI',Tahoma,sans-serif;
      color:var(--fg);
      background:
        radial-gradient(circle at 18% 18%, rgba(255,118,96,.18), transparent 24%),
        radial-gradient(circle at 84% 20%, rgba(255,204,120,.10), transparent 18%),
        var(--bg);
    }
    .panel {
      width:min(760px, calc(100vw - 40px));
      padding:30px;
      border:1px solid var(--border);
      border-radius:18px;
      background:linear-gradient(180deg, var(--surface2), var(--surface));
      box-shadow:0 28px 80px rgba(0,0,0,.38);
    }
    .eyebrow {
      color:var(--muted);
      font-size:12px;
      letter-spacing:.12em;
      text-transform:uppercase;
      margin-bottom:10px;
    }
    h1 {
      margin:0 0 12px;
      font-size:30px;
      line-height:1.05;
      color:var(--danger);
    }
    p {
      margin:0 0 10px;
      color:var(--muted);
      font-size:14px;
      line-height:1.6;
    }
    .hint {
      color:var(--fg);
      margin-top:18px;
    }
    pre {
      margin:18px 0 0;
      padding:16px;
      overflow:auto;
      border-radius:12px;
      border:1px solid var(--border);
      background:rgba(0,0,0,.22);
      color:#ffd7cf;
      font-size:12px;
      line-height:1.5;
      white-space:pre-wrap;
    }
    code {
      color:#ffd7cf;
    }
  </style>
</head>
<body>
  <main class="panel">
    <div class="eyebrow">Django Arch Check</div>
    <h1>${escapeHtml(params.title)}</h1>
    <p>${escapeHtml(params.message)}</p>
    ${hintBlock}
    ${detailsBlock}
  </main>
</body>
</html>`;
}
 
