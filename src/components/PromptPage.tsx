import { useState } from "react";

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
  const [kind, setKind] = useState<PromptKind>("rules");
  const value = kind === "rules" ? props.rulePrompt : props.mapPrompt;
  const defaultValue = kind === "rules" ? props.defaultRulePrompt : props.defaultMapPrompt;
  const change = kind === "rules" ? props.onRulePromptChange : props.onMapPromptChange;

  async function copy() {
    try { await navigator.clipboard.writeText(value); }
    catch { props.onError("Не удалось скопировать промпт."); }
  }

  return <section className="page">
    <div className="page-title">
      <div>
        <h1>Промпты</h1>
        <p>Оба промпта доступны для чтения и сохраняются внутри каждой новой задачи.</p>
      </div>
      <div className="actions">
        <button className="button secondary" onClick={copy}>Копировать</button>
        <button className="button secondary" onClick={() => change(defaultValue)}>Вернуть исходный</button>
      </div>
    </div>

    <div className="prompt-tabs">
      <button className={kind === "rules" ? "active" : ""} onClick={() => setKind("rules")}>Проверка правил</button>
      <button className={kind === "map" ? "active" : ""} onClick={() => setKind("map")}>Структурная карта</button>
    </div>

    <div className="panel prompt-editor">
      <div className="row between prompt-meta">
        <span>Источник: <code>{kind === "rules" ? "config/semantic-prompt.txt" : "config/document-map-prompt.txt"}</code></span>
        <span>{value.length.toLocaleString("ru")} символов</span>
      </div>
      <textarea value={value} onChange={(event) => change(event.target.value)} spellCheck={false} />
      <p className="hint">{kind === "rules"
        ? "Промпт применяется к смысловой проверке правил."
        : "Промпт используется при извлечении элементов, построении связей и проверке карты."}</p>
    </div>
  </section>;
}
