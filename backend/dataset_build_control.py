"""Persisted build generations fence cancellation and every background write."""
import threading
import time
import uuid

import requests

from backend.store import utc_now
from shared.request_control import GenerationStopped, RequestControl

RETRY_LIMIT = 2


def build_state():
    return dict(build_token=uuid.uuid4().hex, build_started_at=utc_now(),
                build_stage='sources', build_attempt=0, build_retry_count=0,
                build_retry_limit=RETRY_LIMIT, build_last_error=None,
                build_requests=0, build_output_tokens=0, build_active_requests=0,
                build_request_started_at=None)


class RequestLimitExceeded(Exception):
    pass


class BuildRepository:
    def __init__(self, repo, token):
        self.repo, self.token = repo, token

    def __getattr__(self, name):
        method = getattr(self.repo, name)
        if name in {'mutate', 'add', 'replace_generated', 'save_sources', 'update_answer'}:
            return lambda *args, **kwargs: method(*args, **kwargs, build_token=self.token)
        return method


class BuildControl(RequestControl):
    def __init__(self, repo, identifier, token):
        self.repo, self.identifier, self.token = repo, identifier, token
        self.lock = threading.RLock()
        self.checked_at = 0
        self.request_failures = 0
        self.blocked_error = None
        self.trace = None

    def check(self, *, force=False):
        with self.lock:
            if self.blocked_error:
                raise RequestLimitExceeded(self.blocked_error)
            if not force and time.monotonic() - self.checked_at < .5:
                return
            dataset = self.repo.get(self.identifier)
            if dataset.get('build_token') != self.token or dataset['status'] != 'generating':
                raise GenerationStopped()
            self.checked_at = time.monotonic()

    def started(self):
        def update(c, d):
            if d['status'] != 'generating':
                raise GenerationStopped()
            active = d.get('build_active_requests', 0)
            d.update(build_requests=d.get('build_requests', 0) + 1,
                     build_active_requests=active + 1, build_stage='generating')
            if not active:
                d['build_request_started_at'] = utc_now()
        self.repo.mutate(self.identifier, update, generating=True, build_token=self.token)

    def finished(self, result):
        tokens = result[0].get('output_tokens', 0) if isinstance(result, tuple) else 0
        self.repo.mutate(self.identifier, lambda c, d: d.update(
            build_output_tokens=d.get('build_output_tokens', 0) + tokens),
            generating=True, build_token=self.token)

    def ended(self):
        try:
            self.repo.mutate(self.identifier, lambda c, d: d.update(
                build_active_requests=max(0, d.get('build_active_requests', 0) - 1)),
                generating=True, build_token=self.token)
        except GenerationStopped:
            pass

    def execute(self, operation, timeout):
        try:
            result = super().execute(operation, timeout)
        except requests.RequestException as error:
            response = getattr(error, 'response', None)
            status = response.status_code if response is not None else None
            with self.lock:
                self.request_failures += 1
                if status is not None and 400 <= status < 500 and status not in {408, 429}:
                    from backend.datasets import model_request_failure
                    self.blocked_error = model_request_failure(error)
                elif self.request_failures > RETRY_LIMIT:
                    reason = '模型请求超时。' if isinstance(error, requests.Timeout) else '模型连接失败或服务暂时不可用。'
                    self.blocked_error = reason + f'连续失败，已达到重试上限（{RETRY_LIMIT} 次）。'
            raise
        else:
            with self.lock:
                self.request_failures = 0
            return result
