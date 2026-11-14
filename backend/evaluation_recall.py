"""Freeze Recall@K's cutoff without overriding Dify retrieval requests."""
from urllib.parse import quote

import requests
from fastapi import HTTPException

from backend.dify_names import api_root
from backend.schemas import PlaygroundRequest


def read_dify_top_k(config, key):
    message = '无法读取 Dify 知识库的 Top K，请检查 Dataset API 权限和知识库检索配置后重试。'
    try:
        response = requests.get(
            api_root(config['base_url']) + '/datasets/' + quote(config['dataset_id'], safe=''),
            headers={'Authorization': 'Bearer ' + key} if key else {},
            timeout=(3, 12), allow_redirects=False)
        if not 200 <= response.status_code < 300 or len(response.content) > 2_000_000:
            raise ValueError('invalid response')
        body = response.json()
        if not isinstance(body, dict):
            raise ValueError('invalid response')
        for name in ('retrieval_model_dict', 'retrieval_model'):
            settings = body.get(name)
            k = settings.get('top_k') if isinstance(settings, dict) else None
            if type(k) is int and k > 0:
                return k
    except (requests.RequestException, ValueError, KeyError):
        pass
    # Never silently replace an unknown knowledge-base cutoff with a local default.
    raise HTTPException(502, message)


def recall_settings(target, metrics, resolve_access):
    if target['benchmark'] != 'dify-retrieve' or not any(m['id'] == 'rules.recall_at_k' for m in metrics):
        return None
    if target.get('knowledge_bases'):
        return {'k': target['retrieval_top_k'], 'k_source': 'fusion'}
    selection = target['selection']
    config, key = resolve_access(PlaygroundRequest(
        benchmark='dify-retrieve', provider_id=selection['id'], model=selection.get('model'),
        credential_id=selection.get('credential_id'), query='evaluation retrieval settings'))
    return {'k': read_dify_top_k(config, key), 'k_source': 'dify'}
