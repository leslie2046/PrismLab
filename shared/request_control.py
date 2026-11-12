"""Optional request lifetime controls, propagated through SDK async/thread calls."""
from contextvars import ContextVar, copy_context
from queue import Queue, Empty
import socket
import threading
import time

import requests


class GenerationStopped(Exception):
    """A cancelled or superseded build must perform no further work."""


current_control = ContextVar('generation_request_control', default=None)


class RequestControl:
    def check(self, *, force=False):
        pass

    def started(self):
        pass

    def finished(self, result):
        pass

    def ended(self):
        pass

    def execute(self, operation, timeout):
        self.check()
        self.started()
        outcome = Queue(maxsize=1)
        context = copy_context()
        stopped = threading.Event()
        responses = []
        response_lock = threading.Lock()

        def register(response):
            with response_lock:
                if stopped.is_set():
                    response.close()
                    raise GenerationStopped()
                responses.append(response)

        def check_active():
            if stopped.is_set():
                raise GenerationStopped()
            self.check(force=True)

        def run():
            check_handle = active_request_check.set(check_active)
            handle = active_response.set(register)
            try:
                outcome.put((True, operation()))
            except BaseException as error:
                outcome.put((False, error))
            finally:
                active_response.reset(handle)
                active_request_check.reset(check_handle)

        threading.Thread(target=lambda: context.run(run), daemon=True).start()
        deadline = time.monotonic() + timeout
        try:
            while True:
                self.check()
                if time.monotonic() >= deadline:
                    raise requests.Timeout('Model request exceeded its deadline')
                try:
                    ok, result = outcome.get(timeout=min(.2, max(.001, deadline - time.monotonic())))
                except Empty:
                    continue
                self.check()
                if not ok:
                    raise result
                self.finished(result)
                return result
        except BaseException:
            stopped.set()
            # Closing a streaming response alone can wait on its read lock.
            # Shutdown the socket first so an in-flight read wakes promptly.
            def close_responses():
                with response_lock:
                    for response in responses:
                        try:
                            response.raw._fp.fp.raw._sock.shutdown(socket.SHUT_RDWR)
                        except (AttributeError, OSError):
                            pass
                        try:
                            response.close()
                        except Exception:
                            pass
            threading.Thread(target=close_responses, daemon=True).start()
            raise
        finally:
            self.ended()


active_response = ContextVar('generation_active_response', default=None)
active_request_check = ContextVar('generation_active_request_check', default=None)


def check_request_active():
    check = active_request_check.get()
    if check:
        check()


def register_response(response):
    register = active_response.get()
    if register:
        register(response)
