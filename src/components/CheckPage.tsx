import { useRef, useState } from "react";
import { api } from "../api";
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
  const [files, setFiles] = useState<File[]>([]);
  const [sending, setSending] = useState(false);
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
    if (accepted.length !== incoming.length) props.onError("Поддерживаются только PDF и DOCX.");
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
      const created = await api.createJobs(body);
      setFiles([]);
      props.onCreated(created);
    } catch (error) { props.onError((error as Error).message); }
    finally { setSending(false); }
  }

  return <section className="page narrow">
    <div className="page-title"><div><h1>Проверка</h1><p>Сначала выбранная модель выделит крупные смысловые фрагменты. Проверка правил начнётся только после вашего подтверждения структуры.</p></div></div>
    {!props.health.configured.openrouter && <div className="inline-error">OpenRouter не настроен: добавьте OPENROUTER_API_KEY в файл .env и перезапустите сервер.</div>}

    <div className="panel form-grid">
      <label><span>Набор правил</span><select value={props.profile} onChange={(event) => props.onProfileChange(event.target.value as CheckProfile)}>
        <option value="core">Ядро · {props.health.knowledge.coreCount}</option><option value="full">Полный набор · {props.health.knowledge.fullCount}</option>
      </select><small>{props.profile === "core" ? "Основные строгие правила." : "Ядро и расширенные рекомендации."}</small></label>
      <label><span>Модель OpenRouter</span><select value={props.model} onChange={(event) => props.onModelChange(event.target.value)}>
        <optgroup label="Бесплатные модели">{freeModels.map((item) => <option key={item.id} value={item.id}>{item.label}</option>)}</optgroup>
        <optgroup label="Для production">{productionModels.map((item) => <option key={item.id} value={item.id}>{item.label}</option>)}</optgroup>
      </select><small>{selectedModel?.note}</small>{selectedModel?.tier === "free"}</label>
    </div>

    <div className="panel prompt-summary">
      <div><strong>Два прозрачных промпта</strong><p>Карта: {props.mapPrompt.length.toLocaleString("ru")} символов; правила: {props.prompt.length.toLocaleString("ru")} символов.</p></div>
      <button className="button secondary" onClick={props.onOpenPrompt}>Открыть промпты</button>
    </div>

    <div className="dropzone" onClick={() => inputRef.current?.click()} onDragOver={(event) => event.preventDefault()} onDrop={(event) => { event.preventDefault(); addFiles(event.dataTransfer.files); }}>
      <input ref={inputRef} type="file" accept=".pdf,.docx" multiple onChange={(event) => event.target.files && addFiles(event.target.files)} />
      <strong>Перетащите PDF или DOCX</strong><span>или нажмите, чтобы выбрать файлы</span>
    </div>

    {files.length > 0 && <div className="panel file-list">
      <div className="row between"><strong>Файлы: {files.length}</strong><button className="text-button" onClick={() => setFiles([])}>Очистить</button></div>
      {files.map((file, index) => <div className="file-item" key={`${file.name}-${file.size}`}><span>{file.name}</span><small>{formatBytes(file.size)}</small><button aria-label="Удалить" onClick={() => setFiles((items) => items.filter((_, itemIndex) => itemIndex !== index))}>×</button></div>)}
    </div>}

    <label className="panel block-label"><span>Дополнительные требования</span><textarea value={props.criteria} onChange={(event) => props.onCriteriaChange(event.target.value)} rows={5} placeholder="Одно правило на строку" /><small>Оставьте поле пустым, если дополнительных правил нет.</small></label>
    <div className="actions end"><button className="button primary" disabled={!files.length || sending} onClick={submit}>{sending ? "Добавление…" : files.length > 1 ? `Подготовить структуру (${files.length})` : "Выделить структуру"}</button></div>
  </section>;
}

function formatBytes(bytes: number) { return new Intl.NumberFormat("ru", { maximumFractionDigits: 1, style: "unit", unit: "megabyte" }).format(bytes / 1024 / 1024); }
