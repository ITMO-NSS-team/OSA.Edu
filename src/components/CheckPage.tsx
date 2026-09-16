import { useRef, useState } from "react";
import { api } from "../api";
import { useLanguage } from "../i18n";
import type { CheckProfile, Health, Job } from "../types";

interface Props {
  health: Health;
  prompt: string;
  mapPrompt: string;
  profile: CheckProfile;
  model: string;
  criteria: string;
  onProfileChange: (value: CheckProfile) => void;
  onModelChange: (value: string) => void;
  onCriteriaChange: (value: string) => void;
  onOpenPrompt: () => void;
  onCreated: (jobs: Job[]) => void;
  onError: (message: string) => void;
}

export function CheckPage(props: Props) {
  const { language } = useLanguage();
  const t = language === "ru" ? {
    onlyPdfDocx: "Поддерживаются только PDF и DOCX.", title: "Проверка", developerDesc: "Режим разработчика: карта будет принята автоматически, затем все работы последовательно пройдут полную проверку без вашего участия.", normalDesc: "Сначала выбранная модель выделит крупные смысловые фрагменты. Проверка правил начнётся только после вашего подтверждения структуры.", openrouter: "OpenRouter не настроен: добавьте OPENROUTER_API_KEY в файл .env и перезапустите сервер.", profile: "Набор правил", core: "Ядро", full: "Полный набор", coreHint: "Основные строгие правила.", fullHint: "Ядро и расширенные рекомендации.", model: "Модель OpenRouter", free: "Бесплатные модели", production: "Для production", prompts: "Два прозрачных промпта", map: "Карта", rules: "правила", chars: "символов", openPrompts: "Открыть промпты", developer: "Режим разработчика · автопроверка", developerHint: "Для массовых регрессионных прогонов. Структура каждого файла принимается автоматически и проверка запускается сразу. Неоднозначные элементы карты не скрываются: связанные правила останутся uncertain. Если границы карты недействительны, конкретная работа остановится на ручной проверке.", drop: "Перетащите PDF или DOCX", choose: "или нажмите, чтобы выбрать файлы", files: "Файлы", clear: "Очистить", remove: "Удалить", extra: "Дополнительные требования", onePerLine: "Одно правило на строку", extraHint: "Оставьте поле пустым, если дополнительных правил нет.", adding: "Добавление…", auto: "Запустить автопроверку", prepare: "Подготовить структуру", extract: "Выделить структуру"
  } : {
    onlyPdfDocx: "Only PDF and DOCX files are supported.", title: "Thesis check", developerDesc: "Developer mode: the document map is accepted automatically and all files are checked sequentially without manual confirmation.", normalDesc: "The selected model first extracts major semantic sections. Rule checking starts after you confirm the document structure.", openrouter: "OpenRouter is not configured: add OPENROUTER_API_KEY to .env and restart the server.", profile: "Rule set", core: "Core", full: "Full set", coreHint: "Primary strict rules.", fullHint: "Core rules plus extended recommendations.", model: "OpenRouter model", free: "Free models", production: "Production", prompts: "Two transparent prompts", map: "Map", rules: "rules", chars: "characters", openPrompts: "Open prompts", developer: "Developer mode · automatic check", developerHint: "For regression runs. Each document structure is accepted automatically and checking starts immediately. Ambiguous map elements remain visible and related rules stay uncertain. Invalid boundaries stop that file for manual review.", drop: "Drop PDF or DOCX files here", choose: "or click to choose files", files: "Files", clear: "Clear", remove: "Remove", extra: "Additional requirements", onePerLine: "One rule per line", extraHint: "Leave empty if there are no additional requirements.", adding: "Adding…", auto: "Run automatic check", prepare: "Prepare structure", extract: "Extract structure"
  };
  const [files, setFiles] = useState<File[]>([]);
  const [sending, setSending] = useState(false);
  const [developerMode, setDeveloperMode] = useState(() => localStorage.getItem("osaDeveloperMode") === "1");
  const inputRef = useRef<HTMLInputElement>(null);
  const selectedModel = props.health.models.find((item) => item.id === props.model);
  const freeModels = props.health.models.filter((item) => item.tier === "free");
  const productionModels = props.health.models.filter((item) => item.tier === "production");

  function addFiles(input: FileList | File[]) {
    const incoming = Array.from(input);
    const accepted = incoming.filter((file) => /\.(pdf|docx)$/i.test(file.name));
    setFiles((current) => {
      const next = [...current];
      for (const file of accepted) if (!next.some((item) => item.name === file.name && item.size === file.size)) next.push(file);
      return next.slice(0, 30);
    });
    if (accepted.length !== incoming.length) props.onError(t.onlyPdfDocx);
  }

  async function submit() {
    if (!files.length || sending) return;
    setSending(true);
    try {
      const body = new FormData();
      files.forEach((file) => body.append("files", file));
      body.append("model", props.model);
      body.append("profile", props.profile);
      body.append("prompt", props.prompt);
      body.append("mapPrompt", props.mapPrompt);
      body.append("additionalCriteria", props.criteria);
      body.append("developerMode", developerMode ? "true" : "false");
      const created = await api.createJobs(body);
      setFiles([]);
      props.onCreated(created);
    } catch (error) { props.onError((error as Error).message); }
    finally { setSending(false); }
  }

  return <section className="page narrow">
    <div className="page-title"><div><h1>{t.title}</h1><p>{developerMode ? t.developerDesc : t.normalDesc}</p></div></div>
    {!props.health.configured.openrouter && <div className="inline-error">{t.openrouter}</div>}

    <div className="panel form-grid">
      <label><span>{t.profile}</span><select value={props.profile} onChange={(event) => props.onProfileChange(event.target.value as CheckProfile)}>
        <option value="core">{t.core} · {props.health.knowledge.coreCount}</option><option value="full">{t.full} · {props.health.knowledge.fullCount}</option>
      </select><small>{props.profile === "core" ? t.coreHint : t.fullHint}</small></label>
      <label><span>{t.model}</span><select value={props.model} onChange={(event) => props.onModelChange(event.target.value)}>
        <optgroup label={t.free}>{freeModels.map((item) => <option key={item.id} value={item.id}>{item.label}</option>)}</optgroup>
        <optgroup label={t.production}>{productionModels.map((item) => <option key={item.id} value={item.id}>{item.label}</option>)}</optgroup>
      </select><small>{selectedModel?.note}</small>{selectedModel?.tier === "free"}</label>
    </div>

    <div className="panel prompt-summary">
      <div><strong>{t.prompts}</strong><p>{t.map}: {props.mapPrompt.length.toLocaleString(language === "ru" ? "ru" : "en")} {t.chars}; {t.rules}: {props.prompt.length.toLocaleString(language === "ru" ? "ru" : "en")} {t.chars}.</p></div>
      <button className="button secondary" onClick={props.onOpenPrompt}>{t.openPrompts}</button>
    </div>

    <label className="panel developer-toggle">
      <input type="checkbox" checked={developerMode} onChange={(event) => { const checked = event.target.checked; setDeveloperMode(checked); localStorage.setItem("osaDeveloperMode", checked ? "1" : "0"); }} />
      <span><strong>{t.developer}</strong><small>{t.developerHint}</small></span>
    </label>

    <div className="dropzone" onClick={() => inputRef.current?.click()} onDragOver={(event) => event.preventDefault()} onDrop={(event) => { event.preventDefault(); addFiles(event.dataTransfer.files); }}>
      <input ref={inputRef} type="file" accept=".pdf,.docx" multiple onChange={(event) => event.target.files && addFiles(event.target.files)} />
      <strong>{t.drop}</strong><span>{t.choose}</span>
    </div>

    {files.length > 0 && <div className="panel file-list">
      <div className="row between"><strong>{t.files}: {files.length}</strong><button className="text-button" onClick={() => setFiles([])}>{t.clear}</button></div>
      {files.map((file, index) => <div className="file-item" key={`${file.name}-${file.size}`}><span>{file.name}</span><small>{formatBytes(file.size, language)}</small><button aria-label={t.remove} onClick={() => setFiles((items) => items.filter((_, itemIndex) => itemIndex !== index))}>×</button></div>)}
    </div>}

    <label className="panel block-label"><span>{t.extra}</span><textarea value={props.criteria} onChange={(event) => props.onCriteriaChange(event.target.value)} rows={5} placeholder={t.onePerLine} /><small>{t.extraHint}</small></label>
    <div className="actions end"><button className="button primary" disabled={!files.length || sending} onClick={submit}>{sending ? t.adding : developerMode ? (files.length > 1 ? `${t.auto} (${files.length})` : t.auto) : files.length > 1 ? `${t.prepare} (${files.length})` : t.extract}</button></div>
  </section>;
}

function formatBytes(bytes: number, language: "ru" | "en") { return new Intl.NumberFormat(language === "ru" ? "ru" : "en", { maximumFractionDigits: 1, style: "unit", unit: "megabyte" }).format(bytes / 1024 / 1024); }
