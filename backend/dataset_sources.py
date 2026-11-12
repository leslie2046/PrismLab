"""Knowledge source identities and content-free summaries for dataset views."""

def knowledge_ids(value):
    return list(dict.fromkeys([*(value.get('dify_config_ids') or []), *([value['dify_config_id']] if value.get('dify_config_id') else [])]))


def tag_knowledge(chunks, config):
    identifier = config['id']
    return [{**chunk, 'id': f'dify-{identifier}-{chunk["id"]}',
             'metadata': {**chunk.get('metadata', {}), 'dify_config_id': identifier,
                          'knowledge_name': config.get('name') or config.get('dataset_id') or identifier,
                          'dataset_id': config.get('dataset_id') or chunk.get('metadata', {}).get('dataset_id')}}
            for chunk in chunks]


def summarize_sources(chunks, dataset, configs):
    groups = {}
    for chunk in chunks:
        if 'content' not in chunk:
            continue
        meta = chunk.get('metadata') or {}
        if meta.get('filename'):
            key = ('documents', meta['filename'])
            source = {'kind': 'documents', 'name': meta['filename']}
        elif meta.get('dify_config_id') or meta.get('dataset_id'):
            config_id = meta.get('dify_config_id') or dataset.get('dify_config_id')
            config = configs.get(config_id, {})
            key = ('dify', config_id or meta['dataset_id'])
            source = {'kind': 'dify', 'config_id': config_id,
                      'dataset_id': meta.get('dataset_id') or config.get('dataset_id'),
                      'name': config.get('name') or meta.get('knowledge_name') or meta.get('dataset_id') or config_id or 'Dify'}
        else:
            continue
        group = groups.setdefault(key, {**source, 'chunk_count': 0})
        group['chunk_count'] += 1
    # Initial background imports and legacy datasets may not have a saved snapshot yet.
    for config_id in knowledge_ids(dataset):
        config = configs.get(config_id, {})
        groups.setdefault(('dify', config_id), {'kind': 'dify', 'config_id': config_id,
            'dataset_id': config.get('dataset_id'), 'name': config.get('name') or config.get('dataset_id') or config_id, 'chunk_count': 0})
    if dataset.get('source_filename'):
        name = dataset['source_filename']
        groups.setdefault(('import', name), {'kind': 'import', 'name': name, 'chunk_count': 0})
    if not groups and dataset.get('source_type') in {'manual', 'goldens', 'scratch'}:
        groups[('mode', '')] = {'kind': dataset['source_type'], 'name': dataset['source_type'], 'chunk_count': 0}
    return list(groups.values())
