// Offline component smoke test. Uses the esbuild dependency already supplied by Vite.
// Usage: node tools/test_profile_merge_frontend.mjs /tmp/profile-report-fixtures.json
import { build } from "esbuild";
import { mkdtemp, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import path from "node:path";
import { pathToFileURL } from "node:url";

const fixtures = process.argv[2];
if (!fixtures) throw new Error("Provide JSON reports keyed by core/full/vkrcheck/itmo_master.");
const source = `
  import assert from "node:assert/strict";
  import { readFileSync } from "node:fs";
  import { renderToStaticMarkup } from "react-dom/server";
  import { CheckPage } from "./src/components/CheckPage";
  import { RulesPage } from "./src/components/RulesPage";
  import { ReportsPage } from "./src/components/ReportsPage";
  import { LanguageContext } from "./src/i18n";
  globalThis.localStorage = { getItem: () => null, setItem: () => {} };
  globalThis.fetch = () => { throw new Error("HTTP forbidden in frontend smoke test"); };
  const reports = JSON.parse(readFileSync(${JSON.stringify(path.resolve(fixtures))}, "utf8"));
  const profiles = ["core", "full", "vkrcheck", "itmo_master"];
  const health = { models: [], configured: {}, knowledge: { coreCount: 65, fullCount: 230, itmoMasterCount: 77 } };
  const noop = () => {};
  for (const language of ["ru", "en"]) {
    const render = node => renderToStaticMarkup(<LanguageContext.Provider value={{ language, setLanguage: noop }}>{node}</LanguageContext.Provider>);
    for (const profile of profiles) {
      const check = render(<CheckPage health={health} profile={profile} prompt="" mapPrompt="" model=""
        criteria="" onProfileChange={noop} onModelChange={noop} onCriteriaChange={noop} onOpenPrompt={noop} onCreated={noop} onError={noop} />);
      const report = structuredClone(reports[profile]);
      const rules = render(<RulesPage profile={profile} rules={report.ruleCatalog} loading={false} onProfileChange={noop} />);
      for (const html of [check, rules]) {
        for (const value of profiles) assert.ok(html.includes('value="' + value + '"'), "Missing profile " + value);
        assert.ok(new RegExp('<option(?=[^>]*value="' + profile + '")(?=[^>]*selected=)[^>]*>').test(html), "Wrong selected profile");
      }
      assert.equal(check.includes('class="panel vkr-criteria"'), profile === "vkrcheck");
      assert.equal(check.includes('<textarea'), profile !== "vkrcheck", "VkrCheck must retain its fixed six-criterion workflow");
      if (profile === "itmo_master") {
        assert.ok(check.includes(language === "ru" ? "версия 4.0 (2022)" : "v4.0 (2022)"));
        assert.ok(rules.includes('/api/rule-sources/itmo_master.pdf#page='), "Missing normative PDF source");
      }
      // Saved reports created before profileLabel existed still need the right label.
      delete report.profileLabel;
      const job = { id: "fixture", originalName: "fixture.pdf", status: "completed", progress: 100,
        size: 100, createdAt: "2026-11-10T00:00:00Z", attempts: 0, profile, report, prompt: "", mapPrompt: "" };
      const html = render(<ReportsPage jobs={[job]} onAction={async () => {}} onChanged={async () => {}} />);
      const title = html.split('<div class="page-title report-title">')[1]?.split('<p>')[1]?.split('</p>')[0];
      assert.ok(title, "Report header not rendered");
      const label = profile === "vkrcheck" ? "VkrCheck" : profile === "itmo_master" ? "ВКР ИТМО (магистратура)"
        : profile === "core" ? (language === "ru" ? "Ядро" : "Core") : (language === "ru" ? "Полный набор" : "Full set");
      assert.ok(title.startsWith(label), "Wrong report header: " + title);
      assert.ok(html.includes("Page size: 200 x 300 mm"), "Source evidence lost");
      assert.ok(html.includes(language === "ru" ? "Одинаковое отклонение" : "Repeated finding"), "Evidence grouping lost");
      assert.equal(html.includes(language === "ru" ? "Нормативное покрытие" : "Normative coverage"), profile === "itmo_master");
    }
  }
  console.log("Passed: 4 profiles × 2 languages × upload/rules/reports; evidence, PDF links and legacy labels preserved.");
`;
const temporary = await mkdtemp(path.join(tmpdir(), "osa-profile-frontend-"));
try {
  const result = await build({ stdin: { contents: source, loader: "tsx", resolveDir: process.cwd() },
    bundle: true, platform: "node", format: "cjs", jsx: "automatic", write: false,
    define: { "import.meta.env.VITE_API_BASE_URL": '""' }, logLevel: "silent" });
  const target = path.join(temporary, "smoke.cjs");
  await writeFile(target, result.outputFiles[0].text);
  await import(pathToFileURL(target).href);
} finally {
  await rm(temporary, { recursive: true, force: true });
}
