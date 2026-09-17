import { renderToStaticMarkup } from "react-dom/server";
import { LiteratureReport } from "./components/LiteratureReport";
import reportStyles from "./literature-report.css?inline";
import type { LiteratureResult } from "./types";

function escapeHtml(value: string) {
  return value
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function reportBaseName(result: LiteratureResult) {
  const stem = result.filename.replace(/\.pdf$/i, "").trim() || "literature-review";
  return `${stem}-reference-review`;
}

export function buildLiteratureReportHtml(result: LiteratureResult) {
  const markup = renderToStaticMarkup(<LiteratureReport result={result} exportMode />);
  const title = escapeHtml(`${result.filename} — отчёт по источникам`);
  return `<!doctype html>
<html lang="ru">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>${title}</title>
  <style>
    :root { font-family: Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; color: #202124; background: #f6f7f8; font-synthesis: none; }
    * { box-sizing: border-box; }
    body { margin: 0; padding: 32px 20px; min-width: 320px; background: #f6f7f8; }
    ${reportStyles}
  </style>
</head>
<body>${markup}</body>
</html>`;
}

export function downloadLiteratureReportHtml(result: LiteratureResult) {
  const blob = new Blob([buildLiteratureReportHtml(result)], { type: "text/html;charset=utf-8" });
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = `${reportBaseName(result)}.html`;
  anchor.click();
  URL.revokeObjectURL(url);
}

export function printLiteratureReport(result: LiteratureResult, popupWindow?: Window | null) {
  const popup = popupWindow ?? window.open("", "_blank");
  if (!popup) throw new Error("Браузер заблокировал окно печати. Разрешите всплывающие окна для OSA.Edu.");
  popup.opener = null;
  popup.document.open();
  popup.document.write(buildLiteratureReportHtml(result));
  popup.document.close();
  popup.document.title = reportBaseName(result);
  window.setTimeout(() => {
    popup.focus();
    popup.print();
  }, 150);
}
