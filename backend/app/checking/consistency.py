from __future__ import annotations


def apply_consistency_checks(results:list[dict])->list[dict]:
    by={x.get('ruleId'):x for x in results}
    notes={}
    # Keep cross-rule notes conservative; do not overwrite evidence-backed statuses.
    pairs=[('CORE-8-1','CORE-8-2'),('CORE-9-1','CORE-9-4'),('CORE-6-3','CORE-6-4')]
    for left,right in pairs:
        a,b=by.get(left),by.get(right)
        if not a or not b: continue
        if a.get('status')=='not_applicable' and b.get('status') in {'pass','violation'}:
            notes.setdefault(right,[]).append(f'{left} не применимо, поэтому результат {right} следует интерпретировать отдельно.')
        if a.get('status')=='uncertain' and b.get('status')=='pass':
            notes.setdefault(right,[]).append(f'Связанное правило {left} осталось неопределённым.')
    out=[]
    for item in results:
        if item.get('ruleId') in notes: item={**item,'consistencyNotes':[*(item.get('consistencyNotes') or []),*notes[item['ruleId']]]}
        out.append(item)
    return out
