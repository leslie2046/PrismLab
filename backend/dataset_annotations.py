"""Internal annotation helpers used when freezing evaluation evidence.

Annotations are copied into immutable evaluation snapshots. They are not a
dataset-management API; the review/version management endpoints were removed.
"""
import hashlib
import json


def item_hash(item):
    value = {key: item[key] for key in item if key not in {'id', 'dataset_id', 'created_at', 'updated_at', 'origin'}}
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False).encode()).hexdigest()


def current_annotation(item, value):
    result = dict(value or {})
    provenance = (item.get('additional_metadata') or {}).get('prismlab_generation') or {}
    generated_ids = [str(source_id) for source_id in provenance.get('source_ids') or [] if str(source_id).strip()]
    if generated_ids and not result.get('relevant_chunk_ids'):
        result['source_ids'] = list(dict.fromkeys(result.get('source_ids') or generated_ids))
        result['relevant_chunk_ids'] = list(dict.fromkeys(generated_ids))
        if not result.get('graded_relevance'):
            result['graded_relevance'] = {chunk_id: 1 for chunk_id in generated_ids}
    return result
