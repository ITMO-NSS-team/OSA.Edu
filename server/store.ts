import fs from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";
import type { Job } from "./types.js";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const dataDir = path.resolve(__dirname, "../data");
const jobsPath = path.join(dataDir, "jobs.json");
let chain: Promise<unknown> = Promise.resolve();

async function ensure() {
  await fs.mkdir(dataDir, { recursive: true });
  try { await fs.access(jobsPath); } catch { await fs.writeFile(jobsPath, "[]", "utf8"); }
}

async function readUnlocked(): Promise<Job[]> {
  await ensure();
  try {
    const raw = await fs.readFile(jobsPath, "utf8");
    const parsed = JSON.parse(raw) as Job[];
    return Array.isArray(parsed) ? parsed : [];
  } catch { return []; }
}

async function writeUnlocked(jobs: Job[]) {
  await ensure();
  const tmp = `${jobsPath}.${process.pid}.tmp`;
  await fs.writeFile(tmp, JSON.stringify(jobs, null, 2), "utf8");
  await fs.rename(tmp, jobsPath);
}

function exclusive<T>(fn: () => Promise<T>): Promise<T> {
  const next = chain.then(fn, fn);
  chain = next.then(() => undefined, () => undefined);
  return next;
}

export async function readJobs(): Promise<Job[]> {
  await chain;
  return readUnlocked();
}

export async function updateJob(id: string, patch: Partial<Job>): Promise<Job | undefined> {
  return exclusive(async () => {
    const jobs = await readUnlocked();
    const index = jobs.findIndex((job) => job.id === id);
    if (index < 0) return undefined;
    jobs[index] = { ...jobs[index], ...patch, updatedAt: new Date().toISOString() };
    await writeUnlocked(jobs);
    return jobs[index];
  });
}

export async function deleteJob(id: string): Promise<Job | undefined> {
  return exclusive(async () => {
    const jobs = await readUnlocked();
    const found = jobs.find((job) => job.id === id);
    if (!found) return undefined;
    await writeUnlocked(jobs.filter((job) => job.id !== id));
    return found;
  });
}

export async function addJobs(newJobs: Job[]) {
  return exclusive(async () => {
    const jobs = await readUnlocked();
    await writeUnlocked([...newJobs, ...jobs]);
  });
}

export async function recoverInterruptedJobs() {
  return exclusive(async () => {
    const jobs = await readUnlocked();
    let changed = false;
    const recovered = jobs.map((job) => {
      if (job.status === "extracting" || job.status === "mapping") {
        changed = true;
        return { ...job, status: "queued" as const, progress: 0, error: "Сервер был перезапущен; построение структуры возвращено в очередь.", updatedAt: new Date().toISOString() };
      }
      if (job.status === "checking" || job.status === "queued_check") {
        changed = true;
        return { ...job, status: "queued_check" as const, progress: 32, error: "Сервер был перезапущен; проверка возвращена в очередь.", updatedAt: new Date().toISOString() };
      }
      return job;
    });
    if (changed) await writeUnlocked(recovered);
    return recovered;
  });
}
