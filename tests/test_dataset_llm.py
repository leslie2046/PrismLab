"""Dataset SDKs must use verified nonthinking wire parameters and complete JSON."""
import json
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from deepeval.synthesizer.schema import InputFeedback

from backend.dataset_llm import generation_parameters
from backend.deepeval_generation import WorkspaceLLM
from backend.llm_parameters import describe_parameters
from backend.ragas_generation import generate_items
from backend.ragas_native import generate_items as native_generate
from tests.test_deepeval_datasets import SDKFixture


CONFIG = {'provider': 'siliconflow', 'model': 'Qwen/Qwen3.5-27B',
          'base_url': 'https://fixture.invalid/chat/completions'}
CHUNKS = [{'id': 'source', 'content': 'Refunds are available within seven days.', 'metadata': {}}]


def response(content, finish_reason='stop'):
    value = MagicMock()
    value.__enter__.return_value = value
    event = {'choices': [{'delta': {'content': content}, 'finish_reason': finish_reason}]}
    value.iter_lines.return_value = ['data: ' + json.dumps(event), 'data: [DONE]']
    return value


class DatasetLLMTests(unittest.TestCase):
    def test_verified_uid_and_provider_scoped_wire_fields(self):
        for config, expected in [
            (CONFIG, {'enable_thinking': False}),
            ({**CONFIG, 'provider': 'vllm', 'model': 'Qwen3-8B'},
             {'chat_template_kwargs': {'enable_thinking': False}}),
            ({**CONFIG, 'provider': 'deepseek', 'model': 'deepseek-flash'},
             {'thinking': {'type': 'disabled'}}),
        ]:
            with self.subTest(config=config):
                self.assertEqual(generation_parameters(config, 'key'), {'max_tokens': 8192, **expected})
        # Describing capabilities must not change defaults in the Playground.
        self.assertNotIn('default', describe_parameters(CONFIG)['parameters']['thinking_mode'])

    def test_unknown_models_do_not_receive_invented_thinking_controls(self):
        for config in ({**CONFIG, 'model': 'unknown'}, {**CONFIG, 'provider': 'huaweiyun'},
                       {'model': 'legacy-fixture'}):
            self.assertEqual(generation_parameters(config, 'key'), {'max_tokens': 8192})

    def test_declared_output_capacity_is_respected(self):
        from backend import llm_parameters
        with patch.object(llm_parameters, '_metadata', return_value={'max_output_tokens': 512}):
            self.assertEqual(generation_parameters(CONFIG, 'key'),
                             {'max_tokens': 512, 'enable_thinking': False})

    def test_actual_deepeval_generation_and_review_wire_payloads(self):
        from backend.deepeval_generation import generate_items as deepeval_generate
        sdk = SDKFixture()
        def post(url, **kwargs):
            body = kwargs['json']
            _, answer = sdk(url, body['model'], None, body['messages'][-1]['content'])
            return response(answer)
        with patch('shared.llm_stream.requests.post', side_effect=post) as post:
            rows = deepeval_generate(CHUNKS, 2, CONFIG, 'key', 'Rules', [],
                                    {'num_evolutions': 1, 'max_concurrent': 1},
                                    ({**CONFIG, 'provider': 'vllm', 'model': 'Qwen3-8B'}, 'critic'))
        self.assertEqual(len(rows), 2)
        bodies = [call.kwargs['json'] for call in post.call_args_list]
        generators = [b for b in bodies if b['model'] == CONFIG['model']]
        critics = [b for b in bodies if b['model'] == 'Qwen3-8B']
        self.assertTrue(generators and critics)
        self.assertTrue(all(b['enable_thinking'] is False for b in generators))
        self.assertTrue(all(b['chat_template_kwargs'] == {'enable_thinking': False} for b in critics))
        self.assertTrue(all('"score"' in b['messages'][-1]['content'] for b in critics))

    def test_truncated_or_reasoning_only_json_cannot_become_a_sample(self):
        for reply in (response('{"score":0.8,"feedback":"ok"}', 'length'), response('')):
            with patch('shared.llm_stream.requests.post', return_value=reply), self.assertRaises(ValueError):
                WorkspaceLLM(CONFIG, 'key').generate('Review', InputFeedback)
        reasoning = response('')
        reasoning.iter_lines.return_value = ['data: ' + json.dumps({'choices': [
            {'delta': {'reasoning_content': '{"score":0.8,"feedback":"ok"}'}, 'finish_reason': 'stop'}]}),
            'data: [DONE]']
        with patch('shared.llm_stream.requests.post', return_value=reasoning), self.assertRaises(ValueError):
            WorkspaceLLM(CONFIG, 'key').generate('Review', InputFeedback)

    def test_ragas_compatible_and_native_retrieval_share_policy(self):
        for native in (False, True):
            with self.subTest(native=native):
                content = '{"query":"When can I get a refund?"}' if native else '[{"user_input":"When?","reference":"Seven days"}]'
                with patch('shared.llm_stream.requests.post', return_value=response(content)) as post:
                    if native:
                        rows = native_generate(CHUNKS, 1, CONFIG, 'key', 'Rules', [],
                                               {'include_expected_output': False}, embedding=(CONFIG, 'key'))
                    else:
                        rows = generate_items(CHUNKS, 1, CONFIG, 'key', 'Rules', [])
                self.assertEqual(len(rows), 1)
                self.assertIs(post.call_args.kwargs['json']['enable_thinking'], False)
                self.assertEqual(post.call_args.kwargs['json']['max_tokens'], 1024 if native else 8192)

    def test_native_ragas_sdk_callback_uses_same_policy(self):
        from ragas.testset import TestsetGenerator
        from langchain_core.prompt_values import StringPromptValue
        def sdk(generator, documents, **kwargs):
            # Exercise the real SDK model adapter callback without graph transforms.
            generator.llm.generate_text(StringPromptValue(text='Return JSON'))
            sample = SimpleNamespace(user_input='When?', reference='Seven days',
                                     reference_contexts=[CHUNKS[0]['content']])
            return SimpleNamespace(samples=[SimpleNamespace(eval_sample=sample, synthesizer_name='single_hop')])
        with patch('ragas.testset.transforms.default.default_transforms', return_value=[]), \
             patch.object(TestsetGenerator, 'generate_with_langchain_docs', autospec=True, side_effect=sdk), \
             patch('shared.llm_stream.requests.post', return_value=response('{"answer":"ok"}')) as post:
            rows = native_generate(CHUNKS, 1, CONFIG, 'key', 'Rules', [], embedding=(CONFIG, 'key'))
        self.assertEqual(len(rows), 1)
        self.assertIs(post.call_args.kwargs['json']['enable_thinking'], False)
