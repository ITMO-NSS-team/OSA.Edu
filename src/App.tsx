import { useEffect, useState, type ReactNode } from "react";
import { api } from "./api";
import { LanguageContext, type UiLanguage } from "./i18n";
import { CheckPage } from "./components/CheckPage";
import { LiteraturePage } from "./components/LiteraturePage";
import { NormControlPage } from "./components/NormControlPage";
import { PromptPage } from "./components/PromptPage";
import { ReportsPage } from "./components/ReportsPage";
import { RulesPage } from "./components/RulesPage";
import { ReproducibilityPage } from "./components/ReproducibilityPage";
import { RepositoryQualityPage } from "./components/RepositoryQualityPage";
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
  | "repository-quality"
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
  const [language, setLanguageState] = useState<UiLanguage>(() => localStorage.getItem("osaUiLanguage") === "en" ? "en" : "ru");
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
  useEffect(() => {
    localStorage.setItem("osaUiLanguage", language);
    document.documentElement.lang = language;
  }, [language]);
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

  const copy = language === "ru" ? {
    loading: "Загрузка…",
    checking: "Проверяется",
    waiting: "ожидает",
    normcontrol: "нормоконтроль",
    queueFree: "Очередь свободна",
    brand: "содержательная проверка ВКР",
    check: "Формальная и смысловая проверка",
    prompts: "Промпты",
    rules: "Правила",
    reports: "Отчёты",
    norm: "Нормоконтроль",
    literature: "Проверка литературы",
    reproducibility: "Проверка воспроизводимости",
    repositoryQuality: "Качество репозитория",
  } : {
    loading: "Loading…",
    checking: "running",
    waiting: "queued",
    normcontrol: "formal review",
    queueFree: "Queue is empty",
    brand: "thesis quality checks",
    check: "Thesis check",
    prompts: "Prompts",
    rules: "Rules",
    reports: "Reports",
    norm: "Formal review",
    literature: "Literature check",
    reproducibility: "Reproducibility",
    repositoryQuality: "Repository quality",
  };

  function setLanguage(value: UiLanguage) { setLanguageState(value); }

  if (!health) return <LanguageContext.Provider value={{ language, setLanguage }}><main className="loading-screen">{error || copy.loading}</main></LanguageContext.Provider>;
  const activeJobs = jobs.filter((job) => ["queued", "extracting", "mapping", "queued_check", "checking"].includes(job.status)).length;
  const runningJobs = jobs.filter((job) => ["extracting", "mapping", "checking"].includes(job.status)).length;
  const queuedJobs = jobs.filter((job) => ["queued", "queued_check"].includes(job.status)).length;
  const activeNormControl = normControlJobs.filter((job) => ACTIVE_NORMCONTROL.includes(job.status)).length;
  const queueState = [activeJobs ? `${runningJobs ? `${copy.checking}: ${runningJobs}` : ""}${runningJobs && queuedJobs ? " · " : ""}${queuedJobs ? `${copy.waiting}: ${queuedJobs}` : ""}` : "", activeNormControl ? `${copy.normcontrol}: ${activeNormControl}` : ""].filter(Boolean).join(" · ") || copy.queueFree;

  return <LanguageContext.Provider value={{ language, setLanguage }}><main className="app-shell">
    <header className="app-header">
      <button className="brand" onClick={() => setPage("check")}><strong>OSA.Edu</strong><span>{copy.brand}</span></button>
      <nav>
        <div className="nav-group">
          <NavButton active={page === "check"} onClick={() => setPage("check")}>{copy.check}</NavButton>
          <NavButton active={page === "prompt"} onClick={() => setPage("prompt")}>{copy.prompts}</NavButton>
          <NavButton active={page === "rules"} onClick={() => setPage("rules")}>{copy.rules}</NavButton>
          <NavButton active={page === "reports"} onClick={() => setPage("reports")}>{copy.reports}{jobs.length ? ` · ${jobs.length}` : ""}</NavButton>
        </div>
        <div className="nav-group nav-group-secondary">
          <NavButton active={page === "normcontrol"} onClick={() => setPage("normcontrol")}>{copy.norm}{normControlJobs.length ? ` · ${normControlJobs.length}` : ""}</NavButton>
          <NavButton active={page === "literature"} onClick={() => setPage("literature")}>{copy.literature}</NavButton>
          <NavButton active={page === "reproducibility"} onClick={() => setPage("reproducibility")}>{copy.reproducibility}</NavButton>
          <NavButton active={page === "repository-quality"} onClick={() => setPage("repository-quality")}>{copy.repositoryQuality}</NavButton>
        </div>
      </nav>
      <div className="header-tools">
        <div className="language-toggle" role="group" aria-label="Interface language">
          <button className={language === "ru" ? "active" : ""} onClick={() => setLanguage("ru")} type="button">RU</button>
          <button className={language === "en" ? "active" : ""} onClick={() => setLanguage("en")} type="button">EN</button>
        </div>
        <div className="queue-state">{queueState}</div>
      </div>
    </header>

    {error && <div className="global-error"><span>{error}</span><button onClick={() => setError("")}>×</button></div>}

    {page === "check" && <CheckPage health={health} prompt={prompt} mapPrompt={mapPrompt} profile={profile} model={model} criteria={criteria}
      onProfileChange={setProfile} onModelChange={setModel} onCriteriaChange={setCriteria} onOpenPrompt={() => setPage("prompt")}
      onCreated={(created) => { setJobs((current) => [...created, ...current]); setPage("reports"); }} onError={setError} />}
    <div hidden={page !== "literature"}><LiteraturePage models={health.models} /></div>
    {page === "reproducibility" && <ReproducibilityPage />}
    {page === "repository-quality" && <RepositoryQualityPage />}

    {page === "prompt" && <PromptPage rulePrompt={prompt} mapPrompt={mapPrompt} defaultRulePrompt={health.defaults.prompt} defaultMapPrompt={health.defaults.mapPrompt}
      onRulePromptChange={setPrompt} onMapPromptChange={setMapPrompt} onError={setError} />}

    {page === "normcontrol" && <NormControlPage health={health} jobs={normControlJobs}
      onCreated={(created) => setNormControlJobs((current) => [created, ...current])} onChanged={loadNormControlJobs} onError={setError} />}

    {page === "rules" && <RulesPage profile={rulesProfile} rules={rules} loading={rulesLoading} onProfileChange={setRulesProfile} />}
    {page === "reports" && <ReportsPage jobs={jobs} onAction={jobAction} onChanged={loadJobs} />}
  </main></LanguageContext.Provider>;
}

function NavButton({ active, children, onClick }: { active: boolean; children: ReactNode; onClick: () => void }) {
  return <button className={active ? "active" : ""} onClick={onClick}>{children}</button>;
}
