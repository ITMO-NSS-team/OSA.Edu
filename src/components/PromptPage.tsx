import { useState } from "react";
import { useLanguage } from "../i18n";

type PromptKind = "rules" | "map";

interface Props {
  rulePrompt: string;
  mapPrompt: string;
  defaultRulePrompt: string;
  defaultMapPrompt: string;
  onRulePromptChange: (value: string) => void;
  onMapPromptChange: (value: string) => void;
  onError: (message: string) => void;
}

export function PromptPage(props: Props) {
  const { language } = useLanguage();
  const t = language === "ru" ? {
    copyError: "Не удалось скопировать промпт.", title: "Промпты", subtitle: "Оба промпта доступны для чтения и сохраняются внутри каждой новой задачи.", copy: "Копировать", restore: "Вернуть исходный", rules: "Проверка правил", map: "Структурная карта", source: "Источник", chars: "символов", rulesHint: "Промпт применяется к смысловой проверке правил.", mapHint: "Промпт используется при извлечении элементов, построении связей и проверке карты.",
  } : {
    copyError: "Could not copy the prompt.", title: "Prompts", subtitle: "Both prompts are readable and are stored with every new job.", copy: "Copy", restore: "Restore default", rules: "Rule checking", map: "Document map", source: "Source", chars: "characters", rulesHint: "This prompt is used for semantic rule checking.", mapHint: "This prompt is used to extract document elements, build relationships, and verify the map.",
  };
  const [kind, setKind] = useState<PromptKind>("rules");
  const value = kind === "rules" ? props.rulePrompt : props.mapPrompt;
  const defaultValue = kind === "rules" ? props.defaultRulePrompt : props.defaultMapPrompt;
  const change = kind === "rules" ? props.onRulePromptChange : props.onMapPromptChange;

  async function copy() {
    try { await navigator.clipboard.writeText(value); }
    catch { props.onError(t.copyError); }
  }

  return <section className="page">
    <div className="page-title">
      <div>
        <h1>{t.title}</h1>
        <p>{t.subtitle}</p>
      </div>
      <div className="actions">
        <button className="button secondary" onClick={copy}>{t.copy}</button>
        <button className="button secondary" onClick={() => change(defaultValue)}>{t.restore}</button>
      </div>
    </div>

    <div className="prompt-tabs">
      <button className={kind === "rules" ? "active" : ""} onClick={() => setKind("rules")}>{t.rules}</button>
      <button className={kind === "map" ? "active" : ""} onClick={() => setKind("map")}>{t.map}</button>
    </div>

    <div className="panel prompt-editor">
      <div className="row between prompt-meta">
        <span>{t.source}: <code>{kind === "rules" ? "config/semantic-prompt.txt" : "config/document-map-prompt.txt"}</code></span>
        <span>{value.length.toLocaleString(language === "ru" ? "ru" : "en")} {t.chars}</span>
      </div>
      <textarea value={value} onChange={(event) => change(event.target.value)} spellCheck={false} />
      <p className="hint">{kind === "rules" ? t.rulesHint : t.mapHint}</p>
    </div>
  </section>;
}
