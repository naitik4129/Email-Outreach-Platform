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

// A designed (table-based, branded) email is much taller than a plain one.
const isDesigned = (html: string) => /<table/i.test(html);

export function EmailFrame({
  html,
  title,
  className,
}: {
  html: string;
  title: string;
  className?: string;
}) {
  const height = className ?? (isDesigned(html) ? "h-[38rem]" : "h-56");
  return (
    <iframe
      title={title}
      sandbox=""
      srcDoc={buildSrcDoc(html)}
      className={`${height} w-full rounded-md border border-slate-200 bg-white`}
    />
  );
}
