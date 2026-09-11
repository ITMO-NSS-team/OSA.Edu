import { useEffect, useState, type ReactNode } from "react";
import { api } from "./api";
import { CheckPage } from "./components/CheckPage";
import { LiteraturePage } from "./components/LiteraturePage";
import { NormControlPage } from "./components/NormControlPage";
import { PromptPage } from "./components/PromptPage";
import { ReportsPage } from "./components/ReportsPage";
import { RulesPage } from "./components/RulesPage";
import { LiteraturePage } from "./components/LiteraturePage";
import { ReproducibilityPage } from "./components/ReproducibilityPage";
import type {
  CheckProfile,
  Health,
  Job,
  NormControlJob,
  Rule,
} from "./types";

type Page =
  | "check"
  | "normcontrol"
  | "literature"
  | "reproducibility"
  | "prompt"
  | "rules"
  | "reports";

const ACTIVE_NORMCONTROL: NormControlJob["status"][] = [
  "queued",
  "queued_report",
  "submitting",
  "running",
  "reporting",
  "downloading",
];

export default function App() {
  const [page, setPage] = useState<Page>("check");
  const [health, setHealth] = useState<Health | null>(null);
  const [jobs, setJobs] = useState<Job[]>([]);
  const [normControlJobs, setNormControlJobs] = useState<NormControlJob[]>([]);
  const [rules, setRules] = useState<Rule[]>([]);
  const [rulesLoading, setRulesLoading] = useState(false);
  const [rulesProfile, setRulesProfile] = useState<CheckProfile>("core");
  const [profile, setProfile] = useState<CheckProfile>("core");
  const [model, setModel] = useState("nvidia/nemotron-3-super-120b-a12b:free");
  const [prompt, setPrompt] = useState("");
  const [mapPrompt, setMapPrompt] = useState("");
  const [criteria, setCriteria] = useState("");
  const [error, setError] = useState("");

  useEffect(() => { void bootstrap(); }, []);
  useEffect(() => { const timer = window.setInterval(() => { void loadJobs(); void loadNormControlJobs(); }, 1800); return () => window.clearInterval(timer); }, []);
  useEffect(() => { if (page === "rules") void loadRules(rulesProfile); }, [page, rulesProfile]);

  async function bootstrap() {
    try {
      const [healthData, jobData, normControlData] = await Promise.all([api.health(), api.jobs(), api.normControlJobs()]);
      setHealth(healthData);
      setJobs(jobData);
      setNormControlJobs(normControlData);
      setPrompt(healthData.defaults.prompt);
      setMapPrompt(healthData.defaults.mapPrompt);
      setCriteria(healthData.defaults.additionalCriteria);
      setProfile(healthData.defaults.profile);
      if (healthData.models[0]) setModel(healthData.models[0].id);
    } catch (error) { setError((error as Error).message); }
  }

  async function loadJobs() { try { setJobs(await api.jobs()); } catch (error) { setError((error as Error).message); } }
  async function loadNormControlJobs() { try { setNormControlJobs(await api.normControlJobs()); } catch (error) { setError((error as Error).message); } }
  async function loadRules(value: CheckProfile) {
    setRulesLoading(true);
    try { setRules(await api.rules(value)); }
    catch (error) { setError((error as Error).message); }
    finally { setRulesLoading(false); }
  }
  async function jobAction(id: string, action: "cancel" | "retry" | "retryFailed" | "restart" | "delete") {
    try {
      if (action === "cancel") await api.cancel(id);
      if (action === "retry") await api.retry(id);
      if (action === "retryFailed") await api.retryFailed(id);
      if (action === "restart") await api.restart(id);
      if (action === "delete") await api.delete(id);
      await loadJobs();
    } catch (error) { setError((error as Error).message); }
  }

  if (!health) return <main className="loading-screen">{error || "Загрузка…"}</main>;
  const activeJobs = jobs.filter((job) => ["queued", "extracting", "mapping", "queued_check", "checking"].includes(job.status)).length;
  const runningJobs = jobs.filter((job) => ["extracting", "mapping", "checking"].includes(job.status)).length;
  const queuedJobs = jobs.filter((job) => ["queued", "queued_check"].includes(job.status)).length;
  const activeNormControl = normControlJobs.filter((job) => ACTIVE_NORMCONTROL.includes(job.status)).length;
  const queueState = [activeJobs ? `${runningJobs ? `Проверяется: ${runningJobs}` : ""}${runningJobs && queuedJobs ? " · " : ""}${queuedJobs ? `ожидает: ${queuedJobs}` : ""}` : "", activeNormControl ? `нормоконтроль: ${activeNormControl}` : ""].filter(Boolean).join(" · ") || "Очередь свободна";

  return <main className="app-shell">
    <header className="app-header">
      <button className="brand" onClick={() => setPage("check")}><strong>OSA.Edu</strong><span>содержательная проверка ВКР</span></button>
      <nav>
        <div className="nav-group">
          <NavButton active={page === "check"} onClick={() => setPage("check")}>Формальная и смысловая проверка</NavButton>
          <NavButton active={page === "prompt"} onClick={() => setPage("prompt")}>Промпты</NavButton>
          <NavButton active={page === "rules"} onClick={() => setPage("rules")}>Правила</NavButton>
          <NavButton active={page === "reports"} onClick={() => setPage("reports")}>Отчёты{jobs.length ? ` · ${jobs.length}` : ""}</NavButton>
        </div>
        <div className="nav-group nav-group-secondary">
          <NavButton active={page === "normcontrol"} onClick={() => setPage("normcontrol")}>Нормоконтроль{normControlJobs.length ? ` · ${normControlJobs.length}` : ""}</NavButton>
          <NavButton active={page === "literature"} onClick={() => setPage("literature")}>Проверка литературы</NavButton>
           <NavButton active={page === "reproducibility"} onClick={() => setPage("reproducibility")}>Проверка воспроизводимости</NavButton>
        </div>
      </nav>
      <div className="queue-state">{queueState}</div>
    </header>

    {error && <div className="global-error"><span>{error}</span><button onClick={() => setError("")}>×</button></div>}

    {page === "check" && <CheckPage health={health} prompt={prompt} mapPrompt={mapPrompt} profile={profile} model={model} criteria={criteria}
      onProfileChange={setProfile} onModelChange={setModel} onCriteriaChange={setCriteria} onOpenPrompt={() => setPage("prompt")}
      onCreated={(created) => { setJobs((current) => [...created, ...current]); setPage("reports"); }} onError={setError} />}
    <div hidden={page !== "literature"}><LiteraturePage models={health.models} /></div>
    {page === "reproducibility" && <ReproducibilityPage />}

    {page === "prompt" && <PromptPage rulePrompt={prompt} mapPrompt={mapPrompt} defaultRulePrompt={health.defaults.prompt} defaultMapPrompt={health.defaults.mapPrompt}
      onRulePromptChange={setPrompt} onMapPromptChange={setMapPrompt} onError={setError} />}

    {page === "normcontrol" && <NormControlPage health={health} jobs={normControlJobs}
      onCreated={(created) => setNormControlJobs((current) => [created, ...current])} onChanged={loadNormControlJobs} onError={setError} />}

    {page === "rules" && <RulesPage profile={rulesProfile} rules={rules} loading={rulesLoading} onProfileChange={setRulesProfile} />}
    {page === "reports" && <ReportsPage jobs={jobs} onAction={jobAction} onChanged={loadJobs} />}
  </main>;
}

function NavButton({ active, children, onClick }: { active: boolean; children: ReactNode; onClick: () => void }) {
  return <button className={active ? "active" : ""} onClick={onClick}>{children}</button>;
}
