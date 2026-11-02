import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";
import ts from "typescript";

// Compile the actual application module with the project's existing TypeScript
// dependency, so these tests also work on the supported Node 20 runtime.
const source = await readFile(new URL("../src/languageReview.ts", import.meta.url), "utf8");
const { outputText } = ts.transpileModule(source, {
  compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ESNext },
});
const { languageStatus, languageProgress, canResumeLanguage, languageFindings } =
  await import(`data:text/javascript;base64,${Buffer.from(outputText).toString("base64")}`);

test("incomplete done results appear as partial, including zero findings", () => {
  assert.equal(languageStatus({ status: "done", result: { complete: false, issues: [] } }), "partial");
  assert.equal(languageStatus({ status: "done", result: { complete: true } }), "done");
  assert.equal(languageStatus({ status: "done", result: null }), "done");
});

test("other job statuses are preserved even when a partial result exists", () => {
  for (const status of ["queued", "running", "cancelling", "partial", "failed", "cancelled"]) {
    assert.equal(languageStatus({ status, result: { complete: false } }), status);
  }
});

test("coverage determines progress ahead of the job progress", () => {
  assert.equal(languageProgress({ progress: 99, result: { coverage: { ratio: 0.456 } } }), 46);
  assert.equal(languageProgress({ progress: 99, result: { coverage: { ratio: 0 } } }), 0);
  assert.equal(languageProgress({ progress: 0, result: { coverage: { ratio: 1 } } }), 100);
});

test("progress falls back to job progress and rounds it", () => {
  assert.equal(languageProgress({ progress: 35.6, result: null }), 36);
  assert.equal(languageProgress({ progress: 35.4, result: {} }), 35);
});

test("progress is bounded and non-finite values cannot reach the UI", () => {
  for (const [value, expected] of [[-1, 0], [130, 100], [NaN, 0], [Infinity, 0], [-Infinity, 0]]) {
    assert.equal(languageProgress({ progress: value }), expected);
    assert.equal(languageProgress({ progress: 50, result: { coverage: { ratio: value / 100 } } }), expected);
  }
});

test("unfinished terminal jobs respect the server's resume capability", () => {
  for (const status of ["partial", "failed", "cancelled", "done"]) {
    const job = { status, result: { complete: false } };
    assert.equal(canResumeLanguage({ ...job, canResume: true }), true);
    assert.equal(canResumeLanguage({ ...job, canResume: false }), false);
    assert.equal(canResumeLanguage(job), false);
  }
});

test("failed or cancelled jobs without results can retry unless explicitly disabled", () => {
  for (const status of ["partial", "failed", "cancelled"]) {
    assert.equal(canResumeLanguage({ status, result: null }), true);
    assert.equal(canResumeLanguage({ status, result: null, canResume: false }), false);
  }
});

test("active jobs and complete results cannot resume", () => {
  for (const status of ["queued", "running", "cancelling", "done"]) {
    assert.equal(canResumeLanguage({ status, result: null, canResume: true }), false);
  }
  for (const status of ["done", "partial", "failed", "cancelled"]) {
    assert.equal(canResumeLanguage({ status, result: { complete: true }, canResume: true }), false);
  }
});

const finding = (id, page, order, start, category = "grammar") =>
  ({ id, page, category, ...(order === undefined && start === undefined ? {} : { source: { order, start } }) });

test("findings sort by page, block order, source offset, then id without mutating input", () => {
  const findings = [finding("last-page", 2, 0, 0), finding("last-block", 1, 2, 0),
    finding("later", 1, 1, 5), finding("b", 1, 1, 0), finding("a", 1, 1, 0)];
  const original = structuredClone(findings);
  const sorted = languageFindings(findings, "all");
  assert.deepEqual(sorted.map(item => item.id), ["a", "b", "later", "last-block", "last-page"]);
  assert.deepEqual(findings, original);
  assert.notEqual(sorted, findings);
});

test("findings filter every language category, keeping style separate", () => {
  const categories = ["spelling", "grammar", "syntax", "punctuation", "style"];
  const findings = categories.map((category, index) => finding(category, 1, index, 0, category));
  for (const category of categories) {
    assert.deepEqual(languageFindings(findings, category).map(item => item.id), [category]);
  }
  assert.equal(languageFindings(findings, "all").length, 5);
  assert.deepEqual(languageFindings([], "all"), []);
  assert.deepEqual(languageFindings(findings.slice(0, 1), "style"), []);
});

test("findings without source coordinates still sort deterministically", () => {
  const findings = [finding("b", 1), finding("c", 1, 1, 0), finding("a", 1, 0)];
  assert.deepEqual(languageFindings(findings, "all").map(item => item.id), ["a", "b", "c"]);
});
