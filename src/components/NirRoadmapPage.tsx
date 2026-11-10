import { useEffect, useState } from "react";
import { useLanguage, type UiLanguage } from "../i18n";
import type { ModelInfo } from "../types";
import { NirCheckPanel, type NirCheckId } from "./NirCheckPanel";

type StageId = "question" | "literature" | "experiment" | "results" | "artifacts";
type CheckPage = NirCheckId;
type Localized = Record<UiLanguage, string>;
type Stage = {
  id: StageId;
  title: Localized;
  description: Localized;
  deliverable: Localized;
  guidance: Localized[];
  coverage: { automatic: Localized[]; remaining: Localized[] };
  template: string;
  check?: { page: CheckPage; label: Localized };
};

const MANUAL = "https://github.com/Industrial-AI-Research-Lab/project-implementation-manual/blob/master";
const STORAGE_KEY = "osaNirRoadmapV1";
const templateUrl = (template: string, language: UiLanguage) =>
  `${MANUAL}/${language === "ru" ? template : template.replace(".ru.md", ".md")}`;
const STAGES: Stage[] = [
  {
    id: "question",
    title: { ru: "Вопрос и вклад", en: "Question and contribution" },
    description: { ru: "Сформулируйте, какую проблему решаете и какой результат хотите получить.", en: "Define the problem and the result you aim to achieve." },
    deliverable: { ru: "Вопрос исследования и описание собственного вклада", en: "Research question and your contribution" },
    guidance: [{ ru: "Укажите, что уже известно и какое конкретное незнание остаётся.", en: "State what is known and the specific gap that remains." }, { ru: "Опишите данные, сравнение и возможные исходы, которые позволят ответить на вопрос.", en: "Describe the data, comparison, and possible outcomes that can answer the question." }],
    coverage: { automatic: [], remaining: [{ ru: "Ясность и значимость вопроса, обоснование пробела и границы собственного вклада.", en: "Clarity and significance of the question, the gap, and the limits of your contribution." }] },
    template: "research-templates/questions.ru.md",
  },
  {
    id: "literature",
    title: { ru: "Обзор аналогов", en: "Related work" },
    description: { ru: "Сравните существующие решения и объясните, какой пробел остаётся.", en: "Compare existing approaches and explain the remaining gap." },
    deliverable: { ru: "Обзор литературы и сравнение аналогов", en: "Literature review and comparison of related work" },
    guidance: [{ ru: "Сохраните запросы, даты поиска и критерии отбора работ.", en: "Keep search queries, dates, and inclusion criteria." }, { ru: "Сравните подходы и объясните оставшийся пробел; проверка ниже оценивает только записи источников в PDF.", en: "Compare approaches and explain the remaining gap; the check below only reviews PDF references." }],
    coverage: { automatic: [{ ru: "Библиографические записи в PDF: существование источников и совпадение основных сведений.", en: "PDF references: source existence and matching key bibliographic details." }], remaining: [{ ru: "Полнота поиска, причины отбора, качество сравнения работ и обоснованность найденного пробела.", en: "Search completeness, selection reasons, quality of comparison, and justification of the gap." }] },
    template: "research-templates/literature-review.ru.md",
    check: { page: "literature", label: { ru: "Проверить источники в PDF", en: "Check references in a PDF" } },
  },
  {
    id: "experiment",
    title: { ru: "План эксперимента", en: "Experiment plan" },
    description: { ru: "Зафиксируйте метод, бейзлайн, данные, метрики и условия сравнения до получения результатов.", en: "Record the method, baseline, data, metrics, and comparison conditions before seeing results." },
    deliverable: { ru: "Протокол эксперимента", en: "Experiment protocol" },
    guidance: [{ ru: "До запуска зафиксируйте метод, данные, сравнение, метрики и число повторов.", en: "Record the method, data, comparator, metrics, and number of runs before starting." }, { ru: "Готовая проверка репозитория может найти данные и сценарии запуска; качество самого протокола пока не оценивается.", en: "The existing repository check can find data and run scripts; protocol quality is not assessed yet." }],
    coverage: { automatic: [{ ru: "Проверка репозитория может обнаружить файлы данных и сценарии эксперимента.", en: "The repository check can detect data files and experiment scripts." }], remaining: [{ ru: "Корректность дизайна, выбор baseline и метрик, достаточность повторов и фиксация плана до результатов.", en: "Design validity, baseline and metric choice, enough repetitions, and recording the plan before seeing results." }] },
    template: "research-templates/experiment.ru.md",
  },
  {
    id: "results",
    title: { ru: "Результаты и анализ", en: "Results and analysis" },
    description: { ru: "Покажите измерения, сравнение и ограничения; свяжите выводы с полученными данными.", en: "Present measurements, comparisons, and limitations; connect conclusions to the data." },
    deliverable: { ru: "Таблицы результатов и анализ", en: "Result tables and analysis" },
    guidance: [{ ru: "Покажите измерения, неопределённость и сравнение с исходным методом.", en: "Show measurements, uncertainty, and comparison with the baseline." }, { ru: "Сверьте технические утверждения отчёта с кодом; научную интерпретацию затем проверьте отдельно.", en: "Compare technical claims with code; review the scientific interpretation separately." }],
    coverage: { automatic: [{ ru: "Для выделенных технических утверждений — найдено ли подтверждение в коде и файлах репозитория.", en: "Whether extracted technical claims have supporting code or repository files." }], remaining: [{ ru: "Корректность вычислений и статистики, полнота анализа, интерпретация и ограничения выводов.", en: "Calculation and statistical validity, completeness of analysis, interpretation, and limits of conclusions." }] },
    template: "research-templates/analysis.ru.md",
    check: { page: "reproducibility", label: { ru: "Сверить отчёт и код", en: "Compare report and code" } },
  },
  {
    id: "artifacts",
    title: { ru: "Вывод и артефакты", en: "Conclusion and artifacts" },
    description: { ru: "Подготовьте вывод и инструкции, по которым другой человек сможет проверить результат.", en: "Prepare the conclusion and instructions another person can use to verify the result." },
    deliverable: { ru: "Отчёт, репозиторий и инструкция по воспроизведению", en: "Report, repository, and reproduction instructions" },
    guidance: [{ ru: "Укажите команду запуска, окружение, версии данных и ожидаемый результат.", en: "Provide run commands, environment, data versions, and expected results." }, { ru: "Проверьте структуру репозитория и документацию готовой проверкой ниже.", en: "Use the existing check below to review repository structure and documentation." }],
    coverage: { automatic: [{ ru: "Признаки качества репозитория: README, лицензия, зависимости, тесты, точка запуска, файлы данных и сценарии эксперимента.", en: "Repository quality signals: README, license, dependencies, tests, entry point, data files, and experiment scripts." }], remaining: [{ ru: "Можно ли по инструкции действительно воспроизвести главный результат в чистом окружении.", en: "Whether the main result can actually be reproduced from the instructions in a clean environment." }, { ru: "Версии и происхождение данных, ожидаемые выходные файлы и допустимые отклонения.", en: "Data versions and provenance, expected output files, and acceptable variation." }, { ru: "Соответствие итогового вывода результатам работы.", en: "Whether the final conclusion follows from the results." }] },
    template: "research-templates/reproducibility.ru.md",
    check: { page: "repository-quality", label: { ru: "Оценить репозиторий", en: "Review repository" } },
  },
];

type RoadmapState = { title: string; repository: string; evidence: Record<StageId, string> };

function readRoadmap(): RoadmapState {
  const evidence = Object.fromEntries(STAGES.map((stage) => [stage.id, ""])) as Record<StageId, string>;
  try {
    const saved = JSON.parse(localStorage.getItem(STORAGE_KEY) || "null");
    if (saved && typeof saved === "object") {
      for (const stage of STAGES) {
        if (typeof saved.evidence?.[stage.id] === "string") evidence[stage.id] = saved.evidence[stage.id];
      }
      return { title: typeof saved.title === "string" ? saved.title : "", repository: typeof saved.repository === "string" ? saved.repository : "", evidence };
    }
  } catch { /* Start with an empty draft if browser storage is unavailable. */ }
  return { title: "", repository: "", evidence };
}

function exportMarkdown(state: RoadmapState, language: UiLanguage) {
  const title = state.title.trim() || (language === "ru" ? "Научный проект" : "Scientific project");
  const lines = [`# ${title}`, "", language === "ru" ? "Тип: научный проект" : "Type: scientific project", `${language === "ru" ? "Репозиторий" : "Repository"}: ${state.repository || "—"}`, ""];
  for (const stage of STAGES) {
    const references = state.evidence[stage.id].split("\n").map((line) => line.trim()).filter(Boolean);
    lines.push(`## ${stage.title[language]}`, "", `${language === "ru" ? "Ожидаемый материал" : "Expected material"}: ${stage.deliverable[language]}`);
    lines.push(`${language === "ru" ? "Шаблон" : "Template"}: ${templateUrl(stage.template, language)}`, "");
    lines.push(`${language === "ru" ? "Что покрывают готовые проверки" : "Covered by existing checks"}:`, ...(stage.coverage.automatic.length ? stage.coverage.automatic.map((item) => `- ${item[language]}`) : [language === "ru" ? "- Нет автоматической проверки." : "- No automated check."]), "");
    lines.push(`${language === "ru" ? "Что остаётся проверить по руководству" : "Still to review against the guide"}:`, ...stage.coverage.remaining.map((item) => `- ${item[language]}`), "");
    if (references.length) {
      lines.push(`${language === "ru" ? "Указанные материалы" : "Listed materials"}:`, ...references.map((reference) => `- ${reference}`));
    } else {
      lines.push(language === "ru" ? "Материал пока не указан." : "No material listed yet.");
    }
    lines.push("");
  }
  const url = URL.createObjectURL(new Blob([lines.join("\n")], { type: "text/markdown;charset=utf-8" }));
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = "nir-roadmap.md";
  document.body.append(anchor);
  anchor.click();
  anchor.remove();
  window.setTimeout(() => URL.revokeObjectURL(url), 1000);
}

export function NirRoadmapPage({ models, onOpenCheck }: { models: ModelInfo[]; onOpenCheck: (page: CheckPage, jobId?: string, repository?: string) => void }) {
  const { language } = useLanguage();
  const [roadmap, setRoadmap] = useState(readRoadmap);
  const [file, setFile] = useState<File | null>(null);
  const [fileError, setFileError] = useState("");
  const [storageAvailable, setStorageAvailable] = useState(true);
  const listedCount = STAGES.filter((stage) => roadmap.evidence[stage.id].trim()).length;
  const nextStage = STAGES.find((stage) => !roadmap.evidence[stage.id].trim());

  useEffect(() => {
    try { localStorage.setItem(STORAGE_KEY, JSON.stringify(roadmap)); setStorageAvailable(true); }
    catch { setStorageAvailable(false); }
  }, [roadmap]);

  function updateEvidence(id: StageId, value: string) {
    setRoadmap((current) => ({ ...current, evidence: { ...current.evidence, [id]: value } }));
  }

  return <section className="page nir-roadmap">
    <div className="page-title">
      <div><h1>{language === "ru" ? "Маршрут НИР" : "Research roadmap"}</h1><p>{language === "ru" ? "Сверяйте материалы с руководством Михаила и запускайте уже готовые проверки OSA.Edu." : "Compare your materials with Mikhail's guide and run existing OSA.Edu checks."}</p></div>
    </div>

    <div className="panel nir-overview">
      <div className="nir-overview-main">
        <span className="nir-kicker">{language === "ru" ? "Руководство + готовые проверки" : "Guide + existing checks"}</span>
        <label><span>{language === "ru" ? "Название проекта" : "Project title"}</span><input value={roadmap.title} onChange={(event) => setRoadmap((current) => ({ ...current, title: event.target.value }))} placeholder={language === "ru" ? "Например: сравнение методов классификации…" : "For example: comparing classification methods…"} /></label>
        <div className="nir-inputs"><label><span>{language === "ru" ? "Репозиторий проекта" : "Project repository"}</span><input type="url" value={roadmap.repository} onChange={(event) => setRoadmap((current) => ({ ...current, repository: event.target.value }))} placeholder="https://github.com/owner/project" /></label>
          <label><span>{language === "ru" ? "PDF отчёта или черновика" : "Report or draft PDF"}</span><input type="file" accept=".pdf,application/pdf" onChange={(event) => { const chosen = event.target.files?.[0] ?? null; if (chosen && !/\.pdf$/i.test(chosen.name)) { setFile(null); setFileError(language === "ru" ? "Выберите PDF-файл." : "Choose a PDF file."); } else { setFile(chosen); setFileError(""); } }} /></label></div>
        {fileError && <p className="nir-check-error" role="alert">{fileError}</p>}
        <p>{language === "ru" ? "PDF нужен для проверки источников и сверки с кодом; после обновления страницы его нужно выбрать снова. Ссылки на материалы ниже служат для вашей карты проекта и сами по себе не проверяются." : "The PDF is used for reference checking and comparison with code; select it again after reloading. Material links below are for your project map and are not checked on their own."}</p>
      </div>
      <div className="nir-progress">
        <strong>{listedCount} / {STAGES.length}</strong>
        <span>{language === "ru" ? "этапов с материалами" : "stages with materials"}</span>
        <progress max={STAGES.length} value={listedCount} />
      </div>
    </div>

    <div className="nir-stage-list">
      {STAGES.map((stage, index) => {
        const hasEvidence = Boolean(roadmap.evidence[stage.id].trim());
        return <article className="panel nir-stage" key={stage.id}>
          <div className="nir-stage-top"><span className="nir-stage-number">{String(index + 1).padStart(2, "0")}</span><div><h2>{stage.title[language]}</h2><p>{stage.description[language]}</p></div><span className={`nir-stage-status${hasEvidence ? " listed" : ""}`}>{hasEvidence ? (language === "ru" ? "Материал указан" : "Material listed") : (language === "ru" ? "Нет материала" : "No material")}</span></div>
          <p className="nir-deliverable"><strong>{language === "ru" ? "Что подготовить:" : "Prepare:"}</strong> {stage.deliverable[language]}</p>
          <div className="nir-guidance"><strong>{language === "ru" ? "На что обратить внимание" : "What to review"}</strong><ul>{stage.guidance.map((item) => <li key={item.ru}>{item[language]}</li>)}</ul></div>
          <label><span>{language === "ru" ? "Где лежит ваш материал" : "Where to find your material"}</span><textarea rows={2} value={roadmap.evidence[stage.id]} onChange={(event) => updateEvidence(stage.id, event.target.value)} placeholder={language === "ru" ? "Ссылка или путь в репозитории, по одному на строку" : "Link or repository path, one per line"} /></label>
          <div className="nir-stage-actions"><a className="button secondary" href={templateUrl(stage.template, language)} target="_blank" rel="noopener noreferrer">{language === "ru" ? "Открыть шаблон ↗" : "Open template ↗"}</a></div>
          {stage.check ? <NirCheckPanel id={stage.check.page} repository={roadmap.repository} file={file} models={models} onOpenCheck={onOpenCheck} /> : <p className="nir-no-check">{language === "ru" ? "Отдельной автоматической проверки этого этапа пока нет." : "There is no separate automated check for this stage yet."}</p>}
          <div className="nir-coverage"><strong>{language === "ru" ? "Что покрывают готовые проверки" : "What existing checks cover"}</strong><ul>{stage.coverage.automatic.length ? stage.coverage.automatic.map((item) => <li key={item.ru}>{item[language]}</li>) : <li>{language === "ru" ? "Автоматической проверки пока нет." : "No automated check yet."}</li>}</ul><strong>{language === "ru" ? "Что остаётся сверить по руководству" : "What still needs review against the guide"}</strong><ul>{stage.coverage.remaining.map((item) => <li key={item.ru}>{item[language]}</li>)}</ul>{stage.id === "experiment" && <a className="text-button" href="#nir-check-repository-quality">{language === "ru" ? "К проверке репозитория ↓" : "Go to repository check ↓"}</a>}</div>
        </article>;
      })}
    </div>

    <div className="panel nir-next">
      <div><strong>{nextStage ? (language === "ru" ? "Следующий материал" : "Next material") : (language === "ru" ? "Материалы указаны для всех этапов" : "Materials listed for every stage")}</strong><p>{nextStage ? nextStage.deliverable[language] : (language === "ru" ? "Изучите замечания в готовых проверках и сверьте остальные пункты с шаблонами." : "Review findings from existing checks and compare the remaining items with the templates.")}</p><small>{storageAvailable ? (language === "ru" ? "Карта сохраняется в этом браузере." : "Map saved in this browser.") : (language === "ru" ? "Браузер не разрешил сохранить карту; выгрузите сводку." : "Browser storage is unavailable; export your summary.")}</small></div>
      <button className="button primary" type="button" onClick={() => exportMarkdown(roadmap, language)}>{language === "ru" ? "Скачать сводку .md" : "Download summary .md"}</button>
    </div>
    <p className="nir-source">{language === "ru" ? "Руководство задаёт ориентиры для работы. Автоматические проверки охватывают только часть требований и не заменяют чтение работы." : "The guide sets expectations. Automated checks cover only part of them and do not replace reading the work."} <a href={`${MANUAL}/README${language === "ru" ? ".ru" : ""}.md`} target="_blank" rel="noopener noreferrer">{language === "ru" ? "Открыть руководство ↗" : "Open manual ↗"}</a></p>
  </section>;
}
