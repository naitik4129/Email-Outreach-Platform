"use client";

// Generated email bodies are already sanitized on the server, but they still
// render in a fully sandboxed iframe (no scripts, no same-origin access), exactly
// like the sequence editor's preview.
function buildSrcDoc(bodyHtml: string) {
  return `<!doctype html><html><head><meta charset="utf-8"><style>
body{margin:12px;font-family:Arial,Helvetica,sans-serif;font-size:14px;line-height:1.5;color:#0f172a}
p{margin:0 0 .75em}
</style></head><body>${bodyHtml}</body></html>`;
}

export function EmailFrame({ html, title }: { html: string; title: string }) {
  return (
    <iframe
      title={title}
      sandbox=""
      srcDoc={buildSrcDoc(html)}
      className="h-56 w-full rounded-md border border-slate-200 bg-white"
    />
  );
}
