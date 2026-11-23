from backend.dataset_quality import (
    context_supports_question,
    find_annotation_warnings,
    find_duplicate_candidates,
    filter_near_duplicates,
    filter_unrelated_context_items,
    is_concise_generated_question,
    question_similarity,
)


def test_question_similarity_catches_small_chinese_rephrasing():
    first = '截至2026年6月，全省长期护理保险参保人数是多少？'
    second = '截至2026年6月，全省长期护理保险的参保人数是多少？'
    assert question_similarity(first, second) >= 0.95
    assert find_duplicate_candidates([
        {'id': 'a', 'input': first}, {'id': 'b', 'input': second},
    ])[0]['right_id'] == 'b'


def test_filter_keeps_first_and_reports_near_duplicate():
    accepted, rejected = filter_near_duplicates(
        [{'id': 'new', 'input': '如何申请长期护理保险？'}],
        [{'id': 'old', 'input': '如何申请长期护理保险'}],
    )
    assert accepted == []
    assert rejected[0]['matched_id'] == 'old'


def test_template_questions_with_different_regions_are_not_duplicates():
    first = '截至2026年6月，全省生育保险的参保人数、待遇支出金额及享受待遇人次分别是多少？'
    second = '截至2026年6月，泰州市生育保险的参保人数、待遇支出及享受待遇人次分别是多少？'
    assert find_duplicate_candidates([{'id': 'a', 'input': first}, {'id': 'b', 'input': second}]) == []


def test_generated_question_length_and_single_intent_guard():
    assert is_concise_generated_question('江苏省医保基金结算金额是多少？')
    assert not is_concise_generated_question('江苏省医保基金结算金额是多少，同时职工和居民医保报销比例分别是多少？')
    assert not is_concise_generated_question('问题' * 40)


def test_unrelated_generated_reference_context_is_rejected():
    assert not context_supports_question(
        '医保窗口办理业务的营业时间？',
        ['地址：南京市中医院，电话：025-8781555。'],
    )
    accepted, rejected = filter_unrelated_context_items([
        {'input': '医保窗口办理业务的营业时间？', 'context': ['地址：南京市中医院，电话：025-8781555。']},
        {'input': '南通中医院有多少张床位？', 'context': ['南通市中医院共有500张床位。']},
    ])
    assert len(accepted) == 1
    assert rejected[0]['input'].startswith('医保窗口')


def test_context_support_accepts_shared_subject_terms():
    assert context_supports_question(
        '南通中医院有多少张床位？',
        ['南通市中医院共有500张床位。'],
    )


def test_saved_context_is_not_reported_as_missing_annotation():
    samples = [{
        'id': 'sample',
        'input': '截至2026年6月，镇江市共有多少家定点医疗机构？',
        'context': ['截至2026年6月，镇江市共有123家定点医疗机构。'],
        'additional_metadata': {'prismlab_generation': {'source_ids': ['primary']}},
    }]
    sources = [
        {'id': 'one', 'metadata': {'segment_id': 'primary'}, 'content': '截至2026年6月，镇江市共有123家定点医疗机构。'},
        {'id': 'two', 'metadata': {'segment_id': 'other'}, 'content': '截至2026年6月，扬州市共有456家定点医疗机构。'},
    ]
    assert find_annotation_warnings(samples, sources) == []


def test_annotation_warning_suggests_unmarked_equivalent_source():
    samples = [{
        'id': 'sample',
        'input': '截至2026年6月，全省长期护理保险参保人数是多少？',
        'expected_output': '截至2026年6月，全省长护险参保人数达6645.8万人。',
        'annotations': {'relevant_chunk_ids': ['primary']},
    }]
    sources = [
        {'id': 'one', 'metadata': {'segment_id': 'primary'}, 'content': '截至2026年6月，全省长护险参保人数达6645.8万人。'},
        {'id': 'two', 'metadata': {'segment_id': 'duplicate'}, 'content': '（全省26）长期护理保险（截至2026年6月）参保人数6645.8万人。'},
    ]
    warnings = find_annotation_warnings(samples, sources)
    assert warnings[0]['candidate_chunk_ids'] == ['duplicate']
