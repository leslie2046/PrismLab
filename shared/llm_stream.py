"""Client-observed streaming latency; chunks are deliberately not treated as tokens."""
import json
import time

import requests

from shared.request_control import current_control, register_response, check_request_active


class StreamDeadlineExceeded(requests.Timeout):
    """The configured total generation time elapsed, including reasoning."""


def iter_chat(endpoint, model, api_key, query, max_tokens=256, timeout=60, *, capture=False, capture_reasoning=True, require_complete=False, allow_partial=False, request_parameters=None, separate_reasoning=False, system_prompt=None, trace_role='generation', trace_recorder=None):
    started = time.perf_counter()
    first = None
    tokens = None
    input_tokens = None
    finished = False
    finish_reason = None
    content = ""
    reasoning = ""
    headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
    messages = []
    if system_prompt and system_prompt.strip():
        messages.append({"role": "system", "content": system_prompt})
    messages.append({"role": "user", "content": query})
    payload = {
        "model": model, "messages": messages,
        "stream": True, "stream_options": {"include_usage": True},
    }
    if max_tokens is not None:
        payload["max_tokens"] = max_tokens
    if request_parameters:
        if set(request_parameters) - {"max_tokens", "temperature", "top_p", "thinking", "enable_thinking", "reasoning_effort", "thinking_budget", "chat_template_kwargs"}:
            raise ValueError("Unsupported LLM request fields")
        payload.update(request_parameters)
    check_request_active()
    if trace_recorder is not None:
        trace_recorder.request(payload, trace_role)
    check_request_active()
    with requests.post(endpoint, headers=headers, json=payload, stream=True, timeout=(min(5, timeout), timeout), allow_redirects=False) as response:
        register_response(response)
        response.raise_for_status()
        response.encoding = "utf-8"
        # Small chunks prevent requests' default buffering from inflating TTFT.
        for line in response.iter_lines(chunk_size=1, decode_unicode=True):
            if time.perf_counter() - started > timeout:
                raise StreamDeadlineExceeded("LLM stream exceeded the request deadline")
            if not line or not line.startswith("data:"):
                continue
            raw = line[5:].strip()
            if raw == "[DONE]":
                finished = True
                break
            event = json.loads(raw)
            if event.get("error"):
                raise ValueError("LLM stream returned an error")
            usage = event.get("usage") or {}
            if type(usage.get("prompt_tokens")) is int and usage["prompt_tokens"] >= 0:
                input_tokens = usage["prompt_tokens"]
            count = usage.get("completion_tokens")
            if isinstance(count, int) and not isinstance(count, bool) and count > 0:
                tokens = count
            choices = event.get("choices") or []
            if not choices:
                continue
            choice = choices[0]
            delta = choice.get("delta") or {}
            parts = [("reasoning", delta.get("reasoning_content")), ("answer", delta.get("content"))] if separate_reasoning else [("answer", delta.get("content") or delta.get("reasoning_content"))]
            for channel, text in parts:
                if text:
                    if first is None:
                        first = time.perf_counter()
                    if capture:
                        if separate_reasoning and channel == "reasoning":
                            reasoning = (reasoning + text)[:100_000]
                        elif capture_reasoning or delta.get("content"):
                            content = (content + text)[:100_000]
                    yield {"type": "delta", "content": text, "channel": channel, "ttft_ms": (first - started) * 1000}
            if choice.get("finish_reason") is not None:
                finish_reason = choice["finish_reason"]
                if require_complete and choice["finish_reason"] not in {"stop"}:
                    raise ValueError("LLM report was truncated or not completed normally")
                finished = True
    ended = time.perf_counter()
    incomplete = False
    if first is None:
        raise ValueError("LLM response was empty, non-streaming, or incomplete")
    if not finished:
        # Some OpenAI-compatible gateways close the connection after the last
        # delta without sending finish_reason or [DONE]. Keep this opt-in so
        # benchmark and dataset calls continue to reject incomplete output.
        if not allow_partial or not (content or reasoning):
            raise ValueError("LLM response was empty, non-streaming, or incomplete")
        incomplete = True
    latency = (ended - started) * 1000
    ttft = (first - started) * 1000
    sample = {"ok": True, "latency_ms": latency, "ttft_ms": ttft}
    if finish_reason is not None:
        sample["finish_reason"] = finish_reason
    if incomplete:
        sample["incomplete"] = True
    if input_tokens is not None:
        sample["input_tokens"] = input_tokens
    if tokens is not None:
        sample["output_tokens"] = tokens
        if tokens > 1:
            sample["tpot_ms"] = (latency - ttft) / (tokens - 1)
            if sample["tpot_ms"] > 0:
                sample["tokens_per_second"] = 1000 / sample["tpot_ms"]
    yield {"type": "complete", "sample": sample, "content": content, "reasoning_content": reasoning}


def stream_chat(*args, **kwargs):
    control = kwargs.pop('_control', None) or current_control.get()
    if control is not None:
        # Bind this call to its batch before its transport thread is scheduled.
        kwargs['trace_recorder'] = getattr(control, 'trace', None)
        scope = current_control.set(control)
        try:
            return control.execute(lambda: _stream_chat(*args, **kwargs), kwargs.get('timeout', 60))
        finally:
            current_control.reset(scope)
    return _stream_chat(*args, **kwargs)


def _stream_chat(*args, **kwargs):
    for event in iter_chat(*args, **kwargs):
        if event["type"] == "complete":
            return event["sample"], event["content"]
