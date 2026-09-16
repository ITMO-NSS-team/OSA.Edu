import { useMemo, useState } from "react";
import { useLanguage } from "../i18n";
import { uiLabels } from "../labels";
import type { CheckProfile, Rule } from "../types";

interface Props {
  profile: CheckProfile;
  rules: Rule[];
  loading: boolean;
  onProfileChange: (value: CheckProfile) => void;
}

export function RulesPage({ profile, rules, loading, onProfileChange }: Props) {
  const { language } = useLanguage();
  const labels = uiLabels(language);
  const t = language === "ru" ? {
    title: "Правила", subtitle: "Полный реестр с источником, областью и способом проверки.", core: "Ядро", full: "Полный набор", search: "Поиск по ID или тексту правила", all: "Все категории", of: "из", loading: "Загрузка правил…", category: "Категория", check: "Проверка", scope: "Область", source: "Источник", line: "строка", correct: "Корректно", violation: "Нарушение", empty: "Ничего не найдено.",
  } : {
    title: "Rules", subtitle: "Complete rule registry with source, scope, and checking method.", core: "Core", full: "Full set", search: "Search by rule ID or text", all: "All categories", of: "of", loading: "Loading rules…", category: "Category", check: "Check", scope: "Scope", source: "Source", line: "line", correct: "Correct", violation: "Violation", empty: "Nothing found.",
  };
  const [query, setQuery] = useState("");
  const [category, setCategory] = useState("all");
  const categories = useMemo(() => [...new Set(rules.map((rule) => rule.category))].sort((a, b) => a.localeCompare(b, language)), [rules, language]);
  const visible = useMemo(() => {
    const normalized = query.trim().toLocaleLowerCase(language);
    return rules.filter((rule) => {
      if (category !== "all" && rule.category !== category) return false;
      if (!normalized) return true;
      return `${rule.id} ${rule.title} ${rule.requirement}`.toLocaleLowerCase(language).includes(normalized);
    });
  }, [rules, query, category, language]);

  return <section className="page">
    <div className="page-title">
      <div><h1>{t.title}</h1><p>{t.subtitle}</p></div>
      <select className="compact-select" value={profile} onChange={(event) => onProfileChange(event.target.value as CheckProfile)}>
        <option value="core">{t.core}</option><option value="full">{t.full}</option>
      </select>
    </div>

    <div className="toolbar panel">
      <input value={query} onChange={(event) => setQuery(event.target.value)} placeholder={t.search} />
      <select value={category} onChange={(event) => setCategory(event.target.value)}>
        <option value="all">{t.all}</option>{categories.map((item) => <option key={item}>{item}</option>)}
      </select>
      <span>{visible.length} {t.of} {rules.length}</span>
    </div>

    {loading ? <div className="empty">{t.loading}</div> : <div className="rule-list">
      {visible.map((rule) => <details className="rule-card" key={rule.id}>
        <summary><span className="rule-id">{rule.id}</span><span className="rule-title">{rule.title}</span><span className={`severity-dot ${rule.severity}`} title={labels.severity[rule.severity]} /></summary>
        <div className="rule-body">
          <p>{rule.requirement}</p>
          <div className="meta-grid">
            <span><b>{t.category}:</b> {rule.category}</span><span><b>{t.check}:</b> {labels.mode[rule.mode]}</span><span><b>{t.scope}:</b> {labels.scope[rule.scope]}</span><span><b>{t.source}:</b> {rule.sourceLabel}, {t.line} {rule.sourceLine}</span>
          </div>
          {rule.correctExample && <div className="example good"><b>{t.correct}:</b> {rule.correctExample}</div>}
          {rule.incorrectExample && <div className="example bad"><b>{t.violation}:</b> {rule.incorrectExample}</div>}
        </div>
      </details>)}
      {!visible.length && <div className="empty">{t.empty}</div>}
    </div>}
  </section>;
}
