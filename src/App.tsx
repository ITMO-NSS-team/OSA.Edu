import { useEffect, useState } from "react";
import { api } from "./api";
import { CheckPage } from "./components/CheckPage";
import { PromptPage } from "./components/PromptPage";
import { ReportsPage } from "./components/ReportsPage";
import { RulesPage } from "./components/RulesPage";
import type { CheckProfile, Health, Job, Rule } from "./types";

type Page = "check" | "prompt" | "rules" | "reports";

export default function App() {
  const [page, setPage] = useState<Page>("check");
  const [health, setHealth] = useState<Health | null>(null);
  const [jobs, setJobs] = useState<Job[]>([]);
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
  useEffect(() => { const timer = window.setInterval(() => void loadJobs(), 1800); return () => window.clearInterval(timer); }, []);
  useEffect(() => { if (page === "rules") void loadRules(rulesProfile); }, [page, rulesProfile]);

  async function bootstrap() {
    try {
      const [healthData, jobData] = await Promise.all([api.health(), api.jobs()]);
      setHealth(healthData);
      setJobs(jobData);
      setPrompt(healthData.defaults.prompt);
      setMapPrompt(healthData.defaults.mapPrompt);
      setCriteria(healthData.defaults.additionalCriteria);
      setProfile(healthData.defaults.profile);
      if (healthData.models[0]) setModel(healthData.models[0].id);
    } catch (error) { setError((error as Error).message); }
  }

  async function loadJobs() { try { setJobs(await api.jobs()); } catch (error) { setError((error as Error).message); } }
  async function loadRules(value: CheckProfile) {
    setRulesLoading(true);
    try { setRules(await api.rules(value)); }
    catch (error) { setError((error as Error).message); }
    finally { setRulesLoading(false); }
  }
  async function jobAction(id: string, action: "cancel" | "retry" | "retryFailed" | "delete") {
    try {
      if (action === "cancel") await api.cancel(id);
      if (action === "retry") await api.retry(id);
      if (action === "retryFailed") await api.retryFailed(id);
      if (action === "delete") await api.delete(id);
      await loadJobs();
    } catch (error) { setError((error as Error).message); }
  }

  if (!health) return <main className="loading-screen">{error || "Загрузка…"}</main>;
  const activeJobs = jobs.filter((job) => ["queued", "extracting", "mapping", "queued_check", "checking"].includes(job.status)).length;

  return <main className="app-shell">
    <header className="app-header">
      <button className="brand" onClick={() => setPage("check")}><strong>OSA.Edu</strong><span>содержательная проверка ВКР</span></button>
      <nav>
        <NavButton active={page === "check"} onClick={() => setPage("check")}>Проверка</NavButton>
        <NavButton active={page === "prompt"} onClick={() => setPage("prompt")}>Промпты</NavButton>
        <NavButton active={page === "rules"} onClick={() => setPage("rules")}>Правила</NavButton>
        <NavButton active={page === "reports"} onClick={() => setPage("reports")}>Отчёты{jobs.length ? ` · ${jobs.length}` : ""}</NavButton>
      </nav>
      <div className="queue-state">{activeJobs ? `В работе: ${activeJobs}` : "Очередь свободна"}</div>
    </header>

    {error && <div className="global-error"><span>{error}</span><button onClick={() => setError("")}>×</button></div>}

    {page === "check" && <CheckPage health={health} prompt={prompt} mapPrompt={mapPrompt} profile={profile} model={model} criteria={criteria}
      onProfileChange={setProfile} onModelChange={setModel} onCriteriaChange={setCriteria} onOpenPrompt={() => setPage("prompt")}
      onCreated={(created) => { setJobs((current) => [...created, ...current]); setPage("reports"); }} onError={setError} />}

    {page === "prompt" && <PromptPage rulePrompt={prompt} mapPrompt={mapPrompt} defaultRulePrompt={health.defaults.prompt} defaultMapPrompt={health.defaults.mapPrompt}
      onRulePromptChange={setPrompt} onMapPromptChange={setMapPrompt} onError={setError} />}

    {page === "rules" && <RulesPage profile={rulesProfile} rules={rules} loading={rulesLoading} onProfileChange={setRulesProfile} />}
    {page === "reports" && <ReportsPage jobs={jobs} onAction={jobAction} onChanged={loadJobs} />}
  </main>;
}

function NavButton({ active, children, onClick }: { active: boolean; children: React.ReactNode; onClick: () => void }) {
  return <button className={active ? "active" : ""} onClick={onClick}>{children}</button>;
}
