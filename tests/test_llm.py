import json
import subprocess
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import MagicMock, patch

from pydantic import ValidationError
from tests.database import create_test_store
from backend.catalog import allowed_provider_ids
from backend.endpoints import endpoint_url, server_root
from shared.llm_stream import stream_chat
from backend.model_discovery import _benchmark
from backend.schemas import PlaygroundRequest, RunCreate, TestPlanCreate
from backend.playground import run_playground
from backend.runner import BenchmarkRunner
from backend.secrets import SecretBox


def event(delta=None, finish=None, usage=None):
    return 'data: ' + json.dumps({"choices": [{"delta": delta or {}, "finish_reason": finish}], "usage": usage})


class StreamTests(unittest.TestCase):
    def response(self, lines):
        response = MagicMock()
        response.__enter__.return_value = response
        response.iter_lines.return_value = lines
        return response

    def test_timing_ignores_role_and_uses_usage_not_chunks(self):
        lines = [event({"role": "assistant"}), event({"content": "hello"}),
                 event({"content": "world"}, "stop"), event(usage={"completion_tokens": 5}), 'data: [DONE]']
        response = self.response(lines)
        # start, role, content deadline, first content, second, usage, done, end
        with patch('shared.llm_stream.requests.post', return_value=response) as post, \
                patch('shared.llm_stream.time.perf_counter', side_effect=[0, .1, .2, .2, .6, .7, .8, .8]):
            sample, text = stream_chat('https://example.com/v1/chat/completions', 'm', 'secret', 'q', capture=True)
        self.assertAlmostEqual(sample['ttft_ms'], 200)
        self.assertAlmostEqual(sample['tpot_ms'], 150)
        self.assertAlmostEqual(sample['tokens_per_second'], 1000 / 150)
        self.assertEqual(sample['output_tokens'], 5)
        self.assertEqual(text, 'helloworld')
        self.assertTrue(post.call_args.kwargs['stream'])
        self.assertEqual(post.call_args.kwargs['json']['stream_options'], {'include_usage': True})
        response.iter_lines.assert_called_once_with(chunk_size=1, decode_unicode=True)
        response.__exit__.assert_called_once()

    def test_missing_usage_and_single_token_have_no_tpot(self):
        for usage in (None, {"completion_tokens": 1}):
            with patch('shared.llm_stream.requests.post', return_value=self.response([
                event({"reasoning_content": "reason"}), event({"content": "text"}, "stop", usage), 'data: [DONE]'])):
                sample, text = stream_chat('http://localhost/v1/chat/completions', 'm', None, 'q')
            self.assertNotIn('tpot_ms', sample)
            self.assertNotIn('tokens_per_second', sample)
            self.assertEqual(text, '')
            self.assertGreaterEqual(sample['ttft_ms'], 0)

    def test_empty_error_and_incomplete_streams_fail(self):
        for lines in ([event(finish='stop'), 'data: [DONE]'], [event({'content': 'partial'})],
                      ['data: {"error":{"message":"secret"}}'], ['data: invalid']):
            with self.subTest(lines=lines), patch('shared.llm_stream.requests.post', return_value=self.response(lines)):
                with self.assertRaises(ValueError):
                    stream_chat('http://localhost/v1/chat/completions', 'm', None, 'q')

    def test_stream_deadline(self):
        import requests
        with patch('shared.llm_stream.requests.post', return_value=self.response([event({'content':'late'})])), \
                patch('shared.llm_stream.time.perf_counter', side_effect=[0, 3]):
            with self.assertRaises(requests.Timeout):
                stream_chat('http://localhost/v1/chat/completions', 'm', None, 'q', timeout=2)

    def test_runner_passes_custom_prompt_model_and_output_limit(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(__file__).resolve().parents[1]
            store = create_test_store(self, Path(directory)/'runs.db')
            runner = BenchmarkRunner(store,root,Path(directory)/'reports',SecretBox.from_data_dir(Path(directory)))
            report = Path(directory)/'result.json'
            command = runner.build_command({'benchmark':'llm','query':'custom prompt','requests_per_scenario':3,
                'timeout_seconds':60,'max_tokens':128}, {'provider':'vllm','model':'fixture','concurrency':2,
                'base_url':'http://localhost/v1/chat/completions'},report)
            self.assertEqual(command[2:4], ['-m', 'cli.perf_llm'])
            self.assertEqual(command[command.index('--max-tokens')+1],'128')
            self.assertEqual(json.loads(report.with_suffix('.input.json').read_text())['query'],'custom prompt')

    def test_llm_provider_catalog_and_plan_persistence(self):
        import asyncio
        from backend import main as app
        from backend.schemas import ServiceConfigCreate
        with tempfile.TemporaryDirectory() as directory:
            data = Path(directory)
            store = create_test_store(self, data/'runs.db')
            with patch.object(app,'store',store), patch.object(app,'secret_box',SecretBox.from_data_dir(data)):
                provider = app.create_service_config(ServiceConfigCreate(name='LLM fixture',provider='vllm',models=[{
                    'name':'fixture','benchmark':'llm','credentials':[{'name':'Default','server_url':'http://localhost:9999'}]
                }]))
                catalog = next(item for item in app.catalog()['benchmarks'] if item['id']=='llm')
                self.assertEqual(catalog['providers'][0]['models'][0]['name'],'fixture')
                plan = app.create_plan(TestPlanCreate(name='LLM plan',benchmark='llm',query='original',max_tokens=128,
                    providers=[{'id':provider['id'],'model':'fixture'}],concurrency_levels=[1,5]))
                request = app._plan_request(plan)
                self.assertEqual(request['max_tokens'],128)
                updated = asyncio.run(app.update_plan(plan['id'],TestPlanCreate(name='updated',benchmark='llm',query='new',
                    max_tokens=512,providers=[{'id':provider['id'],'model':'fixture'}],concurrency_levels=[2])))
                self.assertEqual(store.get_plan(plan['id'])['max_tokens'],512)
                self.assertEqual(updated['status'],'paused')

    def test_llm_schemas_endpoint_and_playground(self):
        self.assertEqual(endpoint_url('https://example.com/v1', 'llm', 'vllm'), 'https://example.com/v1/chat/completions')
        self.assertEqual(server_root('https://example.com/v1/chat/completions'), 'https://example.com')
        self.assertIn('siliconflow', allowed_provider_ids('llm'))
        self.assertEqual(_benchmark({'model_type':'LLM'}), 'llm')
        for schema, values in ((TestPlanCreate, {'name':'p', 'providers':[{'id':'svc'}], 'concurrency_levels':[1]}),
                               (RunCreate, {'name':'p', 'providers':[{'id':'svc'}], 'concurrency_levels':[1]}),
                               (PlaygroundRequest, {'provider_id':'svc'})):
            with self.assertRaises(ValidationError):
                schema(benchmark='llm', **values)
            self.assertEqual(schema(benchmark='llm',query='q',**values).max_tokens, None if schema is PlaygroundRequest else 256)
            with self.assertRaises(ValidationError):
                schema(benchmark='llm',query='q',max_tokens=0,**values)
        request = PlaygroundRequest(benchmark='llm',provider_id='svc',query='q')
        with patch('shared.llm_stream.stream_chat', return_value=({'latency_ms':100,'ttft_ms':30,'tpot_ms':7,'output_tokens':11}, 'answer')):
            result = run_playground(request, {'model':'m','base_url':'https://example.com/v1/chat/completions'}, None)
        self.assertEqual(result['data']['tpot_ms'], 7)
        self.assertEqual(result['data']['content'], 'answer')


class CliIntegrationTests(unittest.TestCase):
    def test_real_streaming_cli_persists_sanitized_metrics(self):
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_POST(self):
                payload = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
                if self.path != '/v1/chat/completions' or not payload.get('stream'):
                    self.send_error(400)
                    return
                self.send_response(200)
                self.send_header('Content-Type','text/event-stream')
                self.end_headers()
                for line in (event({'role':'assistant'}), event({'content':'private answer'}),
                             event({'content':' text'}, 'stop'), event(usage={'completion_tokens':6}), 'data: [DONE]'):
                    self.wfile.write((line+'\n\n').encode())
                    self.wfile.flush()
        server = ThreadingHTTPServer(('127.0.0.1',0),Handler)
        thread = threading.Thread(target=server.serve_forever,daemon=True)
        thread.start()
        try:
            with tempfile.TemporaryDirectory() as directory:
                report = Path(directory)/'report.json'
                result = subprocess.run([sys.executable,'-m','cli.perf_llm','--provider','vllm','--model','fixture',
                    '--base-url',f'http://127.0.0.1:{server.server_port}/v1/chat/completions',
                    '-n','2','-c','1','--json-report',str(report)],cwd=Path(__file__).resolve().parents[1],
                    capture_output=True,text=True,timeout=20)
                self.assertEqual(result.returncode,0,result.stdout+result.stderr)
                data=json.loads(report.read_text())
                self.assertEqual(data['success_count'],2)
                self.assertEqual(data['metrics']['ttft_ms']['count'],2)
                self.assertEqual(data['metrics']['tpot_ms']['count'],2)
                self.assertEqual(data['metrics']['tokens_per_second']['count'],2)
                self.assertNotIn('private answer',report.read_text())
                self.assertNotIn('base_url',data)
        finally:
            server.shutdown()
            server.server_close()
            thread.join()

class PlaygroundPartialStreamTests(unittest.TestCase):
    def response(self, lines):
        response = MagicMock()
        response.__enter__.return_value = response
        response.iter_lines.return_value = lines
        return response

    def test_strict_stream_still_rejects_missing_end_marker(self):
        with patch('shared.llm_stream.requests.post', return_value=self.response([
            event({'content': 'partial'})
        ])):
            with self.assertRaises(ValueError):
                stream_chat('http://localhost/v1/chat/completions', 'm', None, 'q')

    def test_playground_keeps_content_when_gateway_closes_after_delta(self):
        from backend.playground import stream_playground
        from backend.schemas import PlaygroundRequest
        with patch('shared.llm_stream.requests.post', return_value=self.response([
            event({'content': 'partial answer'})
        ])):
            events = [json.loads(line) for line in stream_playground(
                PlaygroundRequest(benchmark='llm', provider_id='fixture', query='q'),
                {'base_url': 'http://fixture/v1/chat/completions', 'model': 'm'}, 'secret')]
        self.assertEqual([item['type'] for item in events], ['delta', 'result'])
        result = events[-1]['result']
        self.assertEqual(result['data']['content'], 'partial answer')
        self.assertTrue(result['data']['incomplete'])
        self.assertTrue(result['truncated'])

    def test_playground_keeps_deltas_when_transport_fails_after_reasoning(self):
        import requests
        from backend.playground import stream_playground
        from backend.schemas import PlaygroundRequest

        def lines():
            yield event({'reasoning_content': 'thinking'})
            raise requests.exceptions.ChunkedEncodingError('upstream closed')

        response = self.response([])
        response.iter_lines.return_value = lines()
        with patch('shared.llm_stream.requests.post', return_value=response):
            events = [json.loads(line) for line in stream_playground(
                PlaygroundRequest(benchmark='llm', provider_id='fixture', query='q'),
                {'base_url': 'http://fixture/v1/chat/completions', 'model': 'm'}, 'secret')]
        self.assertEqual([item['type'] for item in events], ['delta', 'result'])
        self.assertEqual(events[-1]['result']['data']['reasoning_content'], 'thinking')
        self.assertTrue(events[-1]['result']['data']['incomplete'])

    def test_reasoning_longer_than_30_seconds_can_finish_with_answer(self):
        from backend.playground import stream_playground

        clock = [0]

        def lines():
            clock[0] = 1
            yield event({'reasoning_content': 'thinking'})
            clock[0] = 35
            yield event({'content': 'final answer'}, 'stop', {'completion_tokens': 50})
            yield 'data: [DONE]'

        response = self.response(lines())
        request = PlaygroundRequest(benchmark='llm', provider_id='fixture', query='q')
        self.assertEqual(request.timeout_seconds, 300)
        with patch('shared.llm_stream.requests.post', return_value=response) as post, \
                patch('shared.llm_stream.time.perf_counter', side_effect=lambda: clock[0]):
            events = [json.loads(line) for line in stream_playground(
                request, {'base_url': 'http://fixture', 'model': 'm'}, 'secret')]
        result = events[-1]['result']
        self.assertEqual([e['type'] for e in events], ['delta', 'delta', 'result'])
        self.assertTrue(result['ok'])
        self.assertFalse(result['data']['no_answer'])
        self.assertNotIn('incomplete', result['data'])
        self.assertEqual(result['data']['content'], 'final answer')
        self.assertEqual(result['data']['reasoning_content'], 'thinking')
        self.assertEqual(result['data']['finish_reason'], 'stop')
        self.assertEqual(post.call_args.kwargs['timeout'], (5, 300))

    def test_explicit_deadline_retains_reasoning_and_identifies_missing_answer(self):
        from backend.playground import stream_playground

        clock = [0]

        def lines():
            clock[0] = 1
            yield event({'reasoning_content': 'thinking'})
            clock[0] = 31
            yield event({'content': 'too late'}, 'stop')

        request = PlaygroundRequest(benchmark='llm', provider_id='fixture', query='q', timeout_seconds=30)
        with patch('shared.llm_stream.requests.post', return_value=self.response(lines())), \
                patch('shared.llm_stream.time.perf_counter', side_effect=lambda: clock[0]):
            events = [json.loads(line) for line in stream_playground(
                request, {'base_url': 'http://fixture', 'model': 'm'}, 'secret')]
        result = events[-1]['result']
        self.assertFalse(result['ok'])
        self.assertEqual(result['data']['incomplete_reason'], 'deadline')
        self.assertEqual(result['data']['timeout_seconds'], 30)
        self.assertTrue(result['data']['no_answer'])
        self.assertEqual(result['data']['content'], '')
        self.assertEqual(result['data']['reasoning_content'], 'thinking')

    def test_output_limit_during_reasoning_is_not_a_successful_answer(self):
        from backend.playground import stream_playground
        request = PlaygroundRequest(benchmark='llm', provider_id='fixture', query='q')
        with patch('shared.llm_stream.requests.post', return_value=self.response([
                event({'reasoning_content': 'thinking'}, 'length'), 'data: [DONE]'])):
            events = [json.loads(line) for line in stream_playground(
                request, {'base_url': 'http://fixture', 'model': 'm'}, 'secret')]
        result = events[-1]['result']
        self.assertFalse(result['ok'])
        self.assertTrue(result['data']['no_answer'])
        self.assertEqual(result['data']['incomplete_reason'], 'output_limit')

    def test_playground_timeout_limits_preserve_non_llm_default(self):
        self.assertEqual(PlaygroundRequest(benchmark='embedding', provider_id='fixture', text='q').timeout_seconds, 30)
        for timeout in (1, 300, 600):
            self.assertEqual(PlaygroundRequest(benchmark='llm', provider_id='fixture', query='q',
                                              timeout_seconds=timeout).timeout_seconds, timeout)
        for timeout in (0, 601):
            with self.assertRaises(ValidationError):
                PlaygroundRequest(benchmark='llm', provider_id='fixture', query='q', timeout_seconds=timeout)
