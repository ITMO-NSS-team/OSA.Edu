"""Runs ONLY in SlideGuard's Python 3.11/3.12 environment.

No OSA config import or load_dotenv(override=True): the parent pins each run's
endpoint/model and passes credentials through the subprocess environment.
"""
from __future__ import annotations

import argparse
import asyncio
import importlib.metadata
import json
import logging
import os
import sys
import threading
import time
from pathlib import Path

from .models import RunOptions

EVENT_PREFIX = 'OSA_SLIDEGUARD_EVENT '


def event(progress, message):
    print(EVENT_PREFIX + json.dumps({'progress': progress, 'message': message}, ensure_ascii=False), flush=True)


def watch_parent():
    """Stop an orphaned POSIX worker after an abrupt backend exit, even during PDF work."""
    parent = os.getenv('OSA_SLIDEGUARD_PARENT_PID', '')
    if os.name != 'posix' or not parent.isdigit():
        return
    expected = int(parent)
    def watch():
        while True:
            time.sleep(2)
            if os.getppid() != expected:
                os._exit(75)
    threading.Thread(target=watch, daemon=True, name='slideguard-parent-watch').start()


def engine_info():
    distribution = importlib.metadata.distribution('slideguard')
    direct = json.loads(distribution.read_text('direct_url.json') or '{}')
    return {'name': 'SlideGuard', 'version': distribution.version,
            'revision': direct.get('vcs_info', {}).get('commit_id')}


def limit_chat_concurrency(llm, maximum):
    """Bound every actual chat call, including upstream's structured-output retries.

    LangGraph's max_concurrency alone does not constrain the per-slide
    asyncio.as_completed fan-out in SlideGuard's cache provider.
    """
    from langchain_core.runnables import RunnableLambda
    original = llm.chat_model
    semaphore = asyncio.Semaphore(maximum)
    async def invoke(value, config=None, **kwargs):
        async with semaphore:
            return await original.ainvoke(value, config=config, **kwargs)
    llm.chat_model = RunnableLambda(invoke)


def dependencies():
    if not (3, 11) <= sys.version_info[:2] < (3, 13):
        raise RuntimeError('SlideGuard requires Python 3.11 or 3.12.')
    from slideguard.criteria import get_default_registry
    from slideguard.criteria.presentation_types import PresentationType
    from slideguard.crew.evaluator import SlideGuardEvaluator
    # Reuse upstream's report renderer. Its UI package imports require SQLAlchemy.
    from slideguard.ui.report_generator import SlideGuardReportGenerator
    from slideguard.ui.app import CRITERIA_LABELS
    registry = get_default_registry()
    criteria = []
    for identifier, info in registry.by_id.items():
        if identifier.is_service_criteria():
            continue
        label = CRITERIA_LABELS.get(identifier, (identifier.value, identifier.value))
        criteria.append({'id': identifier.value, 'labelEn': label[0], 'labelRu': label[1],
                         'target': 'slide' if identifier.is_slide_criteria() else 'deck',
                         'presentationTypes': [t.value for t in PresentationType
                            if identifier in [*registry.get_slide_ids(presentation_type=t),
                                              *registry.get_deck_ids(presentation_type=t)]]})
    return {**engine_info(), 'pythonVersion': sys.version.split()[0], 'criteria': criteria}


def diagnose(evaluation, *, include_description=True):
    failed, applicable = [], 0
    for slide in evaluation.slide_evaluations:
        for name, value in [('slide_type', slide.slide_type), ('slide_description', slide.slide_description),
                            *((key.value, value) for key, value in (slide.evaluations or {}).items())]:
            if name == 'slide_description' and not include_description:
                continue  # Upstream does not request descriptions for slide-only selections.
            if value is None or getattr(value, '__slideguard_not_applicable__', False):
                continue
            if getattr(value, '__slideguard_fallback__', False):
                failed.append({'slideId': slide.slide_id, 'criterion': name})
            elif name not in {'slide_type', 'slide_description'}:
                applicable += 1
    for criterion, value in (evaluation.deck_evaluations.evaluations if evaluation.deck_evaluations else {}).items():
        if getattr(value, '__slideguard_not_applicable__', False):
            continue
        if getattr(value, '__slideguard_fallback__', False):
            failed.append({'slideId': None, 'criterion': criterion.value})
        else:
            applicable += 1
    incomplete_summary = not evaluation.summary or not evaluation.tldr
    outcome = ('failed' if not evaluation.slide_evaluations or (failed and not applicable)
               else 'partial' if failed or incomplete_summary else 'completed')
    return outcome, {'failedCriteria': failed, 'successfulCriteria': applicable,
                     'incompleteSummary': incomplete_summary}


async def evaluate(source: Path, output: Path, raw_options: dict):
    from slideguard.criteria import get_registry_provider
    from slideguard.criteria.presentation_types import PresentationType
    from slideguard.crew.controlled_llm import AppLanguage, create_llm_from_config
    from slideguard.crew.evaluator import SlideGuardEvaluator
    from slideguard.schemes import Criteria
    from slideguard.utils.config import SlideGuardConfig
    from slideguard.utils.file_manager import FileManager
    from slideguard.utils.cache_manager import CacheManager

    options = RunOptions.model_validate(raw_options['options'])
    presentation_type = PresentationType(options.presentationType)
    provider = get_registry_provider()
    registry = provider.get_for_user()
    allowed_slide = registry.get_slide_ids(presentation_type=presentation_type)
    allowed_deck = registry.get_deck_ids(presentation_type=presentation_type)
    selected = [Criteria(key) for key in options.criteria]
    if selected and not set(selected) <= set(allowed_slide + allowed_deck):
        raise ValueError('Selected criteria are not applicable to this presentation type.')
    slide = [key for key in selected if key.is_slide_criteria()] if selected else allowed_slide
    deck = [key for key in selected if key.is_deck_criteria()] if selected else allowed_deck
    config = SlideGuardConfig(max_concurrency=raw_options['concurrency'])
    if not config.api_key or not config.api_base:
        raise ValueError('SlideGuard LLM credentials are missing.')
    llm = create_llm_from_config(config, language=AppLanguage(options.language))
    if llm is None:
        raise RuntimeError('SlideGuard LLM is not configured.')
    llm.max_retries = raw_options['llmRetries']
    limit_chat_concurrency(llm, config.max_concurrency)
    evaluator = SlideGuardEvaluator(
        file_manager=FileManager(config.file_cache_dir),
        cache_manager=CacheManager(config.evaluations_cache_dir), llm=llm,
        max_concurrency=config.max_concurrency, registry_provider=provider)
    event(15, 'SlideGuard анализирует слайды и презентацию.')
    evaluation = await evaluator.evaluate_presentation(
        presentation_path=str(source), slide_criterias=slide, deck_criterias=deck,
        presentation_type=presentation_type, langfuse_client=None)
    outcome, diagnostics = diagnose(evaluation, include_description=bool(deck))
    event(90, 'Сохраняем результаты SlideGuard.')
    payload = {'schemaVersion': 1, 'engine': engine_info(), 'options': options.model_dump(),
               'outcome': outcome, 'evaluation': evaluation.model_dump(mode='json'),
               'diagnostics': diagnostics}
    # Persist useful JSON even if upstream's optional PDF renderer fails.
    save_json(output / 'result.json', payload)
    try:
        from .reporting import save_report
        save_report(evaluation, output / 'report.pdf', name=raw_options['originalName'],
                    language=options.language, outcome=outcome)
    except Exception as exc:
        logging.exception('SlideGuard PDF export failed')
        diagnostics['reportError'] = f'PDF export failed ({type(exc).__name__}); JSON is available.'
        (output / 'report.pdf').unlink(missing_ok=True)
        save_json(output / 'result.json', payload)
    event(100, 'SlideGuard завершил обработку.')


def save_json(path, value):
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False), encoding='utf-8')
    os.replace(temporary, path)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--inspect', action='store_true')
    parser.add_argument('--input', type=Path)
    parser.add_argument('--output', type=Path)
    parser.add_argument('--options', type=Path)
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(name)s %(levelname)s %(message)s')
    if args.inspect:
        print(json.dumps(dependencies(), ensure_ascii=False))
        return
    if args.input is None or args.output is None or args.options is None:
        parser.error('--input, --output and --options are required')
    args.output.mkdir(parents=True, exist_ok=True)
    watch_parent()
    asyncio.run(evaluate(args.input, args.output, json.loads(args.options.read_text(encoding='utf-8'))))


if __name__ == '__main__':
    main()
