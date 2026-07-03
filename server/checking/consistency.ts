import type { RuleResult } from "../types.js";

export function applyConsistencyChecks(results: RuleResult[]) {
  const map = new Map(results.map((item) => [item.ruleId, item]));
  const downgrade = (ruleId: string, dependencies: string[], message: string) => {
    const target = map.get(ruleId);
    if (!target || target.status !== "pass") return;
    const conflicts = dependencies.map((id) => map.get(id)).filter((item) => item && (item.status === "violation" || item.status === "uncertain")) as RuleResult[];
    if (!conflicts.length) return;
    map.set(ruleId, {
      ...target,
      status: "uncertain",
      explanation: `${target.explanation} ${message}`,
      consistencyNotes: [...(target.consistencyNotes || []), `Конфликтует с ${conflicts.map((item) => item.ruleId).join(", ")}.`],
      coverage: target.coverage ? { ...target.coverage, exhaustive: false } : target.coverage
    });
  };

  downgrade("CORE-2-1", ["CORE-2-3", "CORE-15"], "Нельзя подтвердить корректное отличие от прототипа, пока выбор прототипа и его анализ остаются нарушенными или неопределёнными.");
  downgrade("CORE-18", ["CORE-9-1"], "Единый порядок инициалов нельзя подтвердить при выявленном неединообразии авторских записей.");
  downgrade("CORE-8-2", ["CORE-8-1"], "Содержание выводов требует ручной проверки, поскольку формат и границы выводов по главам распознаны с замечаниями.");

  return results.map((item) => map.get(item.ruleId) || item);
}
