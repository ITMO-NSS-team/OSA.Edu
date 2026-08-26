import { useMemo, useState } from "react";
import { modeLabel, scopeLabel, severityLabel } from "../labels";
import type { CheckProfile, Rule } from "../types";

interface Props {
  profile: CheckProfile;
  rules: Rule[];
  loading: boolean;
  onProfileChange: (value: CheckProfile) => void;
}

export function RulesPage({ profile, rules, loading, onProfileChange }: Props) {
  const [query, setQuery] = useState("");
  const [category, setCategory] = useState("all");
  const categories = useMemo(() => [...new Set(rules.map((rule) => rule.category))].sort((a, b) => a.localeCompare(b, "ru")), [rules]);
  const visible = useMemo(() => {
    const normalized = query.trim().toLocaleLowerCase("ru");
    return rules.filter((rule) => {
      if (category !== "all" && rule.category !== category) return false;
      if (!normalized) return true;
      return `${rule.id} ${rule.title} ${rule.requirement}`.toLocaleLowerCase("ru").includes(normalized);
    });
  }, [rules, query, category]);

  return <section className="page">
    <div className="page-title">
      <div>
        <h1>Правила</h1>
        <p>Полный реестр с источником, областью и способом проверки.</p>
      </div>
      <select className="compact-select" value={profile} onChange={(event) => onProfileChange(event.target.value as CheckProfile)}>
        <option value="core">Ядро</option>
        <option value="full">Полный набор</option>
      </select>
    </div>

    <div className="toolbar panel">
      <input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Поиск по ID или тексту правила" />
      <select value={category} onChange={(event) => setCategory(event.target.value)}>
        <option value="all">Все категории</option>
        {categories.map((item) => <option key={item}>{item}</option>)}
      </select>
      <span>{visible.length} из {rules.length}</span>
    </div>

    {loading ? <div className="empty">Загрузка правил…</div> : <div className="rule-list">
      {visible.map((rule) => <details className="rule-card" key={rule.id}>
        <summary>
          <span className="rule-id">{rule.id}</span>
          <span className="rule-title">{rule.title}</span>
          <span className={`severity-dot ${rule.severity}`} title={severityLabel[rule.severity]} />
        </summary>
        <div className="rule-body">
          <p>{rule.requirement}</p>
          <div className="meta-grid">
            <span><b>Категория:</b> {rule.category}</span>
            <span><b>Проверка:</b> {modeLabel[rule.mode]}</span>
            <span><b>Область:</b> {scopeLabel[rule.scope]}</span>
            <span><b>Источник:</b> {rule.sourceLabel}, строка {rule.sourceLine}</span>
          </div>
          {rule.correctExample && <div className="example good"><b>Корректно:</b> {rule.correctExample}</div>}
          {rule.incorrectExample && <div className="example bad"><b>Нарушение:</b> {rule.incorrectExample}</div>}
        </div>
      </details>)}
      {!visible.length && <div className="empty">Ничего не найдено.</div>}
    </div>}
  </section>;
}
