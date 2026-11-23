"""Handlers for durable jobs executed outside the API request process."""
import logging

from backend.dataset_store import DatasetStore
from backend.datasets import build_dataset, regenerate_answers
from backend.schemas import PlaygroundRequest
from backend.tenant_context import workspace_context

logger = logging.getLogger(__name__)


def _store():
    access = workspace_context.get()
    if not access:
        raise RuntimeError("background job has no workspace context")
    return access[0].store


def _resolve(benchmark: str, selection: dict):
    # Importing main lazily avoids the API module importing its worker handlers
    # while it is constructing the FastAPI application.
    from backend.main import _playground_access

    request = PlaygroundRequest(
        benchmark=benchmark,
        provider_id=selection["id"],
        model=selection.get("model"),
        credential_id=selection.get("credential_id"),
        query="background job",
        text="background job" if benchmark == "embedding" else None,
        documents=["background job"] if benchmark == "reranker" else [],
    )
    return _playground_access(request)


def _dataset_access(dataset, options):
    selection = options.get("llm") or dataset.get("llm")
    config, key = _resolve("llm", selection)
    critic = None
    critic_selection = options.get("critic_llm")
    if critic_selection and options.get("generation_backend", "deepeval") == "deepeval":
        critic_config, critic_key = _resolve("llm", critic_selection)
        critic = (critic_config, critic_key)
    embedding = None
    embedding_selection = options.get("embedding_model")
    if embedding_selection and options.get("generation_backend") == "ragas_native":
        embedding = _resolve("embedding", embedding_selection)
    return config, key, critic, embedding


def _dify_access(store, config_id):
    """Resolve a saved Dify source for a durable dataset worker.

    API requests resolve Dify credentials before enqueueing a job, but a
    durable worker runs without that request context.  Re-resolve the source
    from the workspace store so the build can refresh an initially empty
    source snapshot.
    """
    saved = store.get_service_config(config_id)
    if not saved or saved.get("provider") != "dify":
        raise ValueError("Dify 配置不存在")
    config, key = _resolve("dify-retrieve", {"id": config_id})
    return {**config, "id": config_id, "name": saved.get("name"),
            "dataset_id": config.get("dataset_id") or saved.get("dataset_id")}, key


def run_dataset_job(payload: dict) -> None:
    store = _store()
    repo = DatasetStore(store)
    identifier = payload["dataset_id"]
    dataset = repo.get(identifier)
    if dataset.get("status") != "generating":
        return
    expected_token = payload.get("build_token")
    if expected_token and dataset.get("build_token") != expected_token:
        return
    try:
        options = dataset.get("generation_options") or dataset.get("generation_config") or {}
        config, key, critic, embedding = _dataset_access(dataset, options)
        prompt = dataset.get("prompt")
        if payload.get("kind") == "dataset_answers":
            item_ids = payload.get("item_ids") or dataset.get("build_answer_pending_ids") or []
            regenerate_answers(repo, identifier, item_ids, config, key, prompt, options, dataset.get("build_token"))
            return
        chunks = repo.sources(identifier)
        dify = None
        if dataset.get("source_type") == "dify":
            ids = dataset.get("dify_config_ids") or ([dataset.get("dify_config_id")] if dataset.get("dify_config_id") else [])
            dify = [_dify_access(store, config_id) for config_id in dict.fromkeys(ids)]
        build_dataset(repo, identifier, chunks, dify, config, key, prompt, options, critic, embedding,
                      build_token=dataset.get("build_token"))
    except Exception:
        try:
            repo.mutate(identifier, lambda c, d: d.update(
                status="failed", error="后台 worker 无法完成数据集任务，请检查模型配置后重试。"), generating=True)
        except Exception:
            logger.exception("Could not persist dataset worker failure")
        raise


def run_ai_report_job(payload: dict) -> None:
    store = _store()
    run_id, generation_id = payload["run_id"], payload["generation_id"]
    report = store.ai_report(run_id)
    if report.get("status") != "generating" or report.get("generation_id") != generation_id:
        return
    try:
        from backend.ai_reports import generate_report

        config, key = _resolve("llm", payload["selection"])
        run = store.get(run_id)
        if not run:
            return
        content = generate_report(run, config, key, payload.get("language", "zh-CN"))
        store.finish_ai_report(run_id, generation_id, content)
    except Exception:
        logger.exception("AI report background job failed")
        store.finish_ai_report(run_id, generation_id, None,
                               "AI 分析失败，请检查默认 LLM 的连接、认证或额度后重试。已有报告仍保留。")
