"""Deterministic quality checks for generated evaluation datasets.

The model prompt is useful guidance, but it is not a data-integrity check.  This
module keeps the checks local and dependency free so generation and dataset
review behave the same way in every deployment.
"""
from __future__ import annotations

from difflib import SequenceMatcher
import re
from typing import Iterable


# This is deliberately a warning/retry threshold, rather than a hard semantic
# equivalence claim.  A human can still keep two questions with different
# business conditions after reviewing the warning.
QUESTION_SIMILARITY_THRESHOLD = 0.88
MAX_REPORTED_DUPLICATES = 100
MAX_REPORTED_ANNOTATION_WARNINGS = 100
MIN_GENERATED_QUESTION_CHARS = 5
MAX_GENERATED_QUESTION_CHARS = 20
_QUESTION_STOP_TERMS = {'什么', '如何', '怎么', '怎样', '多少', '哪些', '是否', '可以', '能否', '需要', '请问', '有关'}


def is_concise_generated_question(value: object) -> bool:
    """Keep generated samples within the configured length range."""
    text = str(value or '').strip()
    return bool(text) and MIN_GENERATED_QUESTION_CHARS <= len(text) <= MAX_GENERATED_QUESTION_CHARS


def normalize_question(value: object) -> str:
    """Normalize harmless formatting differences while retaining meaning."""
    text = str(value or '').casefold()
    text = text.translate(str.maketrans({'，': ',', '。': '.', '？': '?', '！': '!', '：': ':',
                                         '；': ';', '（': '(', '）': ')', '“': '"', '”': '"'}))
    return re.sub(r'[\s\W_]+', '', text, flags=re.UNICODE)


def _ngrams(value: str, size: int = 2) -> set[str]:
    if not value:
        return set()
    if len(value) <= size:
        return {value}
    return {value[index:index + size] for index in range(len(value) - size + 1)}


def question_similarity(left: object, right: object) -> float:
    """Return a conservative lexical similarity for short Chinese questions."""
    first, second = normalize_question(left), normalize_question(right)
    if not first or not second:
        return 0.0
    if first == second:
        return 1.0
    sequence = SequenceMatcher(None, first, second).ratio()
    a, b = _ngrams(first), _ngrams(second)
    jaccard = len(a & b) / len(a | b) if a and b else 0.0
    return max(sequence, jaccard)


def _near_duplicate(left: object, right: object, threshold: float) -> bool:
    """Require both shape and content overlap before calling two questions duplicates."""
    first, second = normalize_question(left), normalize_question(right)
    if not first or not second:
        return False
    if first == second:
        return True
    if question_similarity(first, second) < threshold:
        return False
    grams_left, grams_right = _ngrams(first), _ngrams(second)
    overlap = len(grams_left & grams_right) / len(grams_left | grams_right) if grams_left and grams_right else 0.0
    # Long Chinese questions often share a boilerplate date/region/template.
    # A high sequence ratio alone would incorrectly flag those as duplicates.
    return overlap >= 0.82


def _candidate_pairs(items: list[dict]) -> Iterable[tuple[dict, dict]]:
    """Use a small blocking key so reports stay cheap for large datasets."""
    buckets: dict[tuple[str, int], list[dict]] = {}
    for item in items:
        normalized = normalize_question(item.get('input'))
        if not normalized:
            continue
        # Similar questions generally share their first few meaningful chars;
        # the length bucket avoids comparing unrelated long questions.
        key = (normalized[:12], len(normalized) // 12)
        for other in buckets.get(key, []):
            yield other, item
        buckets.setdefault(key, []).append(item)


def find_duplicate_candidates(items: Iterable[dict], threshold: float = QUESTION_SIMILARITY_THRESHOLD) -> list[dict]:
    active = [item for item in items if item.get('status', 'active') == 'active' and str(item.get('input') or '').strip()]
    result = []
    for left, right in _candidate_pairs(active):
        similarity = question_similarity(left.get('input'), right.get('input'))
        if _near_duplicate(left.get('input'), right.get('input'), threshold):
            result.append({'left_id': left.get('id'), 'right_id': right.get('id'),
                           'left_input': left.get('input'), 'right_input': right.get('input'),
                           'similarity': round(similarity, 4)})
            if len(result) >= MAX_REPORTED_DUPLICATES:
                break
    return result


def filter_near_duplicates(items: Iterable[dict], existing: Iterable[dict | str],
                           threshold: float = QUESTION_SIMILARITY_THRESHOLD) -> tuple[list[dict], list[dict]]:
    """Keep the first occurrence and return generated near-duplicates separately."""
    accepted: list[dict] = []
    rejected: list[dict] = []
    existing_items = [{'input': value} if isinstance(value, str) else value for value in existing]
    for item in items:
        question = item.get('input')
        match = next((other for other in [*existing_items, *accepted]
                      if _near_duplicate(question, other.get('input'), threshold)), None)
        if match is None:
            accepted.append(item)
            continue
        rejected.append({'input': question, 'matched_input': match.get('input'),
                         'matched_id': match.get('id'),
                         'similarity': round(question_similarity(question, match.get('input')), 4)})
    return accepted, rejected


def _numbers(value: object) -> set[str]:
    values = re.findall(r'\d+(?:\.\d+)?%?', str(value or ''))
    # Single-digit months, list numbers and page references are too common to
    # identify a factual duplicate on their own.
    return {value for value in values
            if ('.' in value or len(value.rstrip('%')) >= 3)
            and not re.fullmatch(r'(?:19|20)\d{2}', value.rstrip('%'))}


def _terms(value: object) -> set[str]:
    text = normalize_question(value)
    # Two-character grams work for Chinese and also retain useful Latin terms.
    return _ngrams(text, 2)


def context_supports_question(question: object, contexts: object) -> bool:
    """Require a generated question to share a meaningful term with its context."""
    if isinstance(contexts, str):
        context_values = [contexts]
    elif isinstance(contexts, (list, tuple)):
        context_values = [str(value) for value in contexts if str(value).strip()]
    else:
        context_values = []
    question_terms = _terms(question) - _QUESTION_STOP_TERMS
    context_terms = set().union(*(_terms(value) for value in context_values)) if context_values else set()
    return bool(question_terms and question_terms & context_terms)


def filter_unrelated_context_items(items: Iterable[dict]) -> tuple[list[dict], list[dict]]:
    """Drop generated records whose returned reference contexts do not support the question."""
    accepted, rejected = [], []
    for item in items:
        contexts = item.get('context')
        has_context = isinstance(contexts, str) and bool(contexts.strip()) or isinstance(contexts, (list, tuple)) and any(str(value).strip() for value in contexts)
        if has_context and not context_supports_question(item.get('input'), contexts):
            rejected.append(item)
        else:
            accepted.append(item)
    return accepted, rejected


def find_annotation_warnings(samples: Iterable[dict], sources: Iterable[dict]) -> list[dict]:
    """Suggest source IDs that support the same fact but are not annotated.

    This is intentionally a review suggestion.  It never silently changes a
    user's gold labels, because duplicate source chunks can be intentional.
    """
    source_rows = []
    source_by_number: dict[str, list[tuple[str, str, set[str]]]] = {}
    for chunk in sources:
        content = str(chunk.get('content') or '').strip()
        if not content:
            continue
        metadata = chunk.get('metadata') or {}
        source_id = metadata.get('segment_id') or chunk.get('id')
        if source_id:
            source_row = (str(source_id), content, _numbers(content))
            source_rows.append(source_row)
            for number in source_row[2]:
                source_by_number.setdefault(number, []).append(source_row)
    warnings = []
    for sample in samples:
        annotation = sample.get('annotations') or {}
        marked = {str(value) for value in annotation.get('relevant_chunk_ids') or []}
        metadata = sample.get('additional_metadata') or {}
        provenance = metadata.get('prismlab_generation') if isinstance(metadata, dict) else {}
        if isinstance(provenance, dict):
            marked.update(str(value) for value in provenance.get('source_ids') or [])
        answer = sample.get('expected_output') or ''
        reference = ' '.join([str(answer), *[str(value) for value in (sample.get('context') or [])]])
        numbers = _numbers(reference)
        terms = _terms(sample.get('input')) | _terms(reference)
        if not numbers or not terms:
            continue
        candidates = []
        possible_sources = {source_id: (source_id, content, source_numbers)
                            for number in numbers for source_id, content, source_numbers in source_by_number.get(number, [])}
        for source_id, content, source_numbers in possible_sources.values():
            if source_id in marked:
                continue
            # A saved reference context is itself an annotation. Match it to
            # the source snapshot before looking for additional suggestions.
            if any(str(context).strip() and (str(context).strip() in content or content in str(context).strip())
                   for context in (sample.get('context') or [])):
                continue
            source_numbers = _numbers(content)
            number_overlap = numbers & source_numbers
            term_overlap = len(terms & _terms(content))
            # A shared number plus multiple question/reference terms is a
            # useful signal for fact-like chunks, while avoiding common-only
            # words and unrelated numeric passages.
            if number_overlap and term_overlap >= 3:
                candidates.append({'chunk_id': source_id, 'number_overlap': sorted(number_overlap),
                                   'term_overlap': term_overlap})
        if candidates:
            for candidate in candidates:
                source_id = candidate['chunk_id']
                source_content = next((row[1] for row in source_rows if row[0] == source_id), '')
                candidate['content_preview'] = re.sub(r'\s+', ' ', source_content).strip()[:180]
            warnings.append({'sample_id': sample.get('id'), 'input': sample.get('input'),
                             'annotated_chunk_ids': sorted(marked),
                             'candidate_chunk_ids': [item['chunk_id'] for item in candidates[:10]],
                             'candidates': candidates[:10]})
            if len(warnings) >= MAX_REPORTED_ANNOTATION_WARNINGS:
                break
    return warnings


def quality_report(samples: Iterable[dict], sources: Iterable[dict]) -> dict:
    sample_list = list(samples)
    annotation_warnings = find_annotation_warnings(sample_list, sources)
    # Duplicate-question review was intentionally removed from the product
    # quality report. Keep the legacy fields empty for old clients while
    # retaining the standalone helpers used by generation safeguards.
    return {'duplicate_candidates': [],
            'annotation_warnings': annotation_warnings,
            'duplicate_count': 0,
            'duplicate_sample_count': 0,
            'annotation_warning_count': len(annotation_warnings),
            'annotation_warning_sample_count': len({str(item.get('sample_id')) for item in annotation_warnings if item.get('sample_id')}),
            'has_warnings': bool(annotation_warnings)}
