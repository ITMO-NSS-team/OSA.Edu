"""Render the human-readable ITMO registry from its canonical local catalogs."""
import json
from pathlib import Path


def main():
    root=Path(__file__).resolve().parents[1]
    data=json.loads((root/'config/itmo_master/catalog.json').read_text())
    labels={'mandatory':'обязательное','recommendation':'рекомендация','conditional':'условное','program':'определяется ОП'}
    lines=['# Реестр «ВКР ИТМО (магистратура)»','',
           'Сгенерировано `python -m tools.render_itmo_registry`. Нормативный источник: предоставленный PDF «Требования к ВКР», версия 4.0, 2022, 31 страница. Указаны физические страницы PDF, совпадающие с печатными страницами норматива. Это реестр требований, а не заявление о полном автоматическом покрытии.','',
           'Сведения об обязательности структурных элементов проверены по полужирному начертанию в п. 3.2, а не по одному текстовому слою. Пункты 2.2–2.3 относятся к другим уровням обучения; 4.5.1 описывает виды иллюстраций; страницы 29–31 содержат согласование и учёт изменений и не вводят дополнительных правил для PDF ВКР. Примеры приложения 1 иллюстрируют правила раздела 4, сноска 5 на стр. 28 учтена отдельно. Внутри PDF имеются неодинаковые коды документа в колонтитулах; источник идентифицируется по титулу, версии и решению от 29.11.2022.','']
    for r in data['rules']:
        lines += [f"## {r['id']} — {r['title']}",'',
                  f"- Источник: {r['sourceLabel']} (точная ссылка на пункт).",
                  f"- Статус: {labels[r['obligation']]}; применимость: {r['condition']}.",
                  f"- Критерий: {r['criteria']}",
                  f"- Доказательства: {r['evidenceRequired']}.",
                  f"- Метод: `{r['verificationMethod']}`.",
                  f"- Ограничения: {r['limitations']}",'']
    (root/'docs/itmo_master_rules.md').write_text('\n'.join(lines),encoding='utf-8')


if __name__=='__main__':main()
