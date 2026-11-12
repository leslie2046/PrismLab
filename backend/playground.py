"""Single interactive requests using the same endpoints as the benchmark scripts."""

import base64
import json
import binascii
import logging
import time
from pathlib import Path
from urllib.parse import quote

import requests

from backend.schemas import PlaygroundRequest
from shared.audio_requests import audio_request

logger = logging.getLogger(__name__)


def llm_result(sample, content):
    incomplete = bool(sample.get("incomplete")) or sample.get("finish_reason") == "length"
    return {"ok": not incomplete and bool(content.strip()), "status_code": 200, "duration_ms": sample["latency_ms"],
            "data": {"content": content, "ttft_ms": sample["ttft_ms"],
                     "tpot_ms": sample.get("tpot_ms"), "output_tokens": sample.get("output_tokens"),
                     "tokens_per_second": sample.get("tokens_per_second"),
                     "no_answer": not bool(content.strip()),
                     **({"finish_reason": sample["finish_reason"]} if sample.get("finish_reason") else {}),
                     **({"incomplete": True, "incomplete_reason": sample.get("incomplete_reason") or
                         ("output_limit" if sample.get("finish_reason") == "length" else "missing_end_marker")} if incomplete else {})},
            "truncated": len(content) >= 100_000 or incomplete}


def stream_playground(request, config, api_key, resolved=None):
    from shared.llm_stream import StreamDeadlineExceeded, iter_chat
    from backend.llm_parameters import resolve_parameters
    wire, snapshot = resolved or resolve_parameters(config, request.llm_parameters, request.max_tokens, api_key)
    started = time.perf_counter()
    partial_content = ""
    partial_reasoning = ""
    partial_ttft = None
    try:
        for event in iter_chat(config["base_url"], config.get("model"), api_key,
                               request.query.strip(), request.max_tokens,
                               request.timeout_seconds, capture=True, request_parameters=wire, separate_reasoning=True,
                               system_prompt=request.system_prompt, allow_partial=True):
            if event["type"] == "delta":
                if partial_ttft is None:
                    partial_ttft = event.get("ttft_ms")
                if event.get("channel") == "reasoning":
                    partial_reasoning = (partial_reasoning + event.get("content", ""))[:100_000]
                else:
                    partial_content = (partial_content + event.get("content", ""))[:100_000]
            if event["type"] == "complete":
                result = llm_result(event["sample"], event["content"])
                result["data"].update(reasoning_content=event["reasoning_content"], parameters=snapshot,
                                      timeout_seconds=request.timeout_seconds,
                                      metric_scope="TTFT: first output (reasoning or answer); TPOT/tokens/s: reported total output tokens")
                result["truncated"] = result["truncated"] or len(event["reasoning_content"]) >= 100_000
                event = {"type": "result", "result": result}
            yield json.dumps(event, ensure_ascii=False) + "\n"
    except (ValueError, requests.RequestException) as exc:
        reason = "deadline" if isinstance(exc, StreamDeadlineExceeded) else "timeout" if isinstance(exc, requests.Timeout) else "upstream_error"
        # Only log categorical diagnostics, never exception text, prompts or keys.
        logger.warning("Playground stream interrupted: reason=%s elapsed_ms=%d limit_seconds=%s answer_chars=%d reasoning_chars=%d",
                       reason, (time.perf_counter() - started) * 1000, request.timeout_seconds,
                       len(partial_content), len(partial_reasoning))
        if partial_content or partial_reasoning:
            # A gateway can close the connection (or report a transport error)
            # after sending useful deltas. Preserve those deltas in Playground
            # while marking the response as incomplete; strict benchmark paths
            # still fail in iter_chat as before.
            sample = {"latency_ms": (time.perf_counter() - started) * 1000,
                      "ttft_ms": partial_ttft or 0, "incomplete": True, "incomplete_reason": reason}
            result = llm_result(sample, partial_content)
            result["data"].update(reasoning_content=partial_reasoning, parameters=snapshot,
                                   timeout_seconds=request.timeout_seconds,
                                   metric_scope="TTFT: first output (reasoning or answer); TPOT/tokens/s: reported total output tokens")
            result["truncated"] = True
            yield json.dumps({"type": "result", "result": result}, ensure_ascii=False) + "\n"
            return
        # Do not send upstream URLs, headers or credentials in streaming errors.
        yield json.dumps({"type": "error", "message": "LLM request timed out before any output; increase the request timeout and retry." if reason in {"deadline", "timeout"} else "LLM stream failed or was incomplete; check the service and retry."}) + "\n"


def run_playground(request: PlaygroundRequest, config: dict, api_key: str | None) -> dict:
    benchmark = request.benchmark
    endpoint = config["base_url"]
    model = config.get("model")
    headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
    kwargs: dict = {"headers": headers, "timeout": (5, request.timeout_seconds)}
    if benchmark == "llm":
        from shared.llm_stream import stream_chat
        from backend.llm_parameters import resolve_parameters
        wire, snapshot = resolve_parameters(config, request.llm_parameters, request.max_tokens, api_key)
        sample, content = stream_chat(endpoint, model, api_key, request.query.strip(),
                                      request.max_tokens, request.timeout_seconds, capture=True, request_parameters=wire,
                                      system_prompt=request.system_prompt)
        result = llm_result(sample, content)
        result["data"]["parameters"] = snapshot
        return result

    if benchmark == "embedding":
        kwargs["json"] = {"model": model, "input": request.text.strip()}
    elif benchmark == "reranker":
        kwargs["json"] = {
            "model": model,
            "query": request.query.strip(),
            "documents": request.documents,
            "return_documents": True,
            "top_n": min(4, len(request.documents)),
        }
    elif benchmark == "audio":
        try:
            audio = base64.b64decode(request.audio_base64, validate=True)
        except (binascii.Error, ValueError) as exc:
            raise ValueError("Invalid audio file encoding") from exc
        if not audio or len(audio) > 5_000_000:
            raise ValueError("Audio file must be between 1 byte and 5 MB")
        name = Path(request.audio_name or "audio.wav").name
        kwargs.update(audio_request(config.get("provider", ""), model, name, audio))
    else:
        if not api_key:
            raise ValueError("Dify API key is required")
        root = endpoint.rstrip("/")
        if not root.endswith("/v1"):
            root += "/v1"
        if benchmark == "dify-retrieve":
            dataset_id = config.get("dataset_id") or request.dataset_id
            if not dataset_id:
                raise ValueError("请在 Dify 配置中填写知识库 ID。")
            endpoint = f"{root}/datasets/{quote(dataset_id, safe='')}/retrieve"
            kwargs["json"] = {"query": request.query.strip()}
        else:
            endpoint = f"{root}/chat-messages"
            kwargs["json"] = {
                "inputs": {}, "query": request.query.strip(),
                "response_mode": "blocking", "conversation_id": "",
                "auto_generate_name": False, "user": "prismlab-playground",
            }

    started = time.perf_counter()
    response = requests.post(endpoint, allow_redirects=False, **kwargs)
    duration_ms = round((time.perf_counter() - started) * 1000, 1)
    truncated = len(response.content) > 100_000
    body = response.content[:100_000].decode(response.encoding or "utf-8", errors="replace")
    if not truncated:
        try:
            import json
            data = json.loads(body)
        except ValueError:
            data = body
    else:
        data = body
    return {
        "ok": 200 <= response.status_code < 300,
        "status_code": response.status_code,
        "duration_ms": duration_ms,
        "data": data,
        "truncated": truncated,
    }
