"""Native SDK boundary: official generator, query distribution and source provenance."""
import unittest
from types import SimpleNamespace
from unittest.mock import patch
from backend.ragas_native import generate_items,embed_texts


class NativeRagasTests(unittest.TestCase):
    def test_official_sdk_generator_configuration_and_truthful_source_mapping(self):
        from ragas.testset import TestsetGenerator
        from ragas.testset.synthesizers import SingleHopSpecificQuerySynthesizer
        config={'base_url':'https://fixture/chat','model':'fixture'}
        source={'id':'doc-chunk','content':'Source facts about orders and refunds.','metadata':{'chapter':'Refunds'}}
        sdk_sample=SimpleNamespace(user_input='Refund policy?',reference='30 days',reference_contexts=[source['content']])
        fixture=SimpleNamespace(samples=[SimpleNamespace(eval_sample=sdk_sample,synthesizer_name='single_hop_specific_query_synthesizer')])
        with patch.object(TestsetGenerator,'generate_with_langchain_docs',return_value=fixture) as sdk:
            items=generate_items([source],1,config,'key','Rules',[],options={'ragas_query_distribution':{'single_hop':1},'ragas_personas':[{'name':'Buyer','role_description':'Tracks orders'}]},embedding=(config,'key'))
        self.assertEqual(sdk.call_count,1)
        self.assertEqual(sdk.call_args.kwargs['testset_size'],1)
        self.assertIsInstance(sdk.call_args.kwargs['query_distribution'][0][0],SingleHopSpecificQuerySynthesizer)
        self.assertEqual(items[0]['additional_metadata']['prismlab_generation']['source_ids'],['doc-chunk'])
        self.assertEqual(items[0]['context'],[source['content']])
        sdk_sample.reference_contexts=['A transformed reference without a direct chunk match']
        with patch.object(TestsetGenerator,'generate_with_langchain_docs',return_value=fixture):
            items=generate_items([source],1,config,'key','Rules',[],options={'include_expected_output':False},embedding=(config,'key'))
        self.assertIsNone(items[0]['expected_output'])
        self.assertEqual(items[0]['additional_metadata']['prismlab_generation']['source_mapping'],'unmapped')

    def test_invalid_embedding_response_not_silently_accepted(self):
        from unittest.mock import MagicMock
        reply=MagicMock(content=b'{}');reply.json.return_value={'data':[{'index':0,'embedding':[float('nan')]}]}
        with patch('backend.ragas_native.requests.post',return_value=reply),self.assertRaises(ValueError):
            embed_texts({'base_url':'https://fixture/embedding','model':'embedding'},'key',['test'])

    def test_question_only_uses_sdk_scenarios_without_requesting_answers(self):
        import asyncio
        import json
        from ragas.testset import TestsetGenerator
        from ragas.testset.graph import Node, NodeType
        from ragas.testset.persona import Persona
        from ragas.testset.synthesizers.base import QueryLength, QueryStyle
        from ragas.testset.synthesizers.single_hop.base import SingleHopScenario
        from ragas.testset.synthesizers.multi_hop.base import MultiHopScenario
        config={'base_url':'https://fixture/chat','model':'fixture'}
        source={'id':'chunk','content':'Refund requests need an order number.','metadata':{}}
        usage={'requests':0,'output_tokens':0}
        prompts=[]
        def transport(*args, **kwargs):
            prompts.append(args[3])
            self.assertIn('Reference answer generation is disabled.',kwargs['system_prompt'])
            return {'output_tokens':5}, json.dumps({'query':f'Question {len(prompts)}?'})
        def synthesize(documents, **kwargs):
            samples=[]
            for index,(synth,_) in enumerate(kwargs['query_distribution']):
                prompt=synth.generate_query_reference_prompt
                self.assertEqual(set(prompt.output_model.model_json_schema()['properties']), {'query'})
                self.assertIn('do not generate an answer',prompt.instruction)
                node=Node(type=NodeType.CHUNK,properties={'page_content':source['content']})
                fields={'nodes':[node], 'persona':Persona(name='Customer',role_description='Asks about orders'),
                        'style':QueryStyle.PERFECT_GRAMMAR,'length':QueryLength.SHORT}
                scenario=SingleHopScenario(**fields,term='refund') if index==0 else MultiHopScenario(**fields,combinations=['refund','order'])
                sample=asyncio.run(synth._generate_sample(scenario,[]))
                self.assertIsNone(sample.reference)
                self.assertTrue(sample.reference_contexts)
                samples.append(SimpleNamespace(eval_sample=sample,synthesizer_name=synth.name))
            return SimpleNamespace(samples=samples)
        with patch.object(TestsetGenerator,'generate_with_langchain_docs',side_effect=synthesize), patch('shared.llm_stream.stream_chat',side_effect=transport):
            rows=generate_items([source],3,config,'key','Grounded questions',[],
                options={'include_expected_output':False,'ragas_query_distribution':{'single_hop':1,'multi_hop_abstract':1,'multi_hop_specific':1}},
                usage=usage,embedding=(config,'key'))
        self.assertEqual(len(rows),3)
        self.assertTrue(all(row['expected_output'] is None and row['context'] for row in rows))
        self.assertEqual(usage,{'requests':3,'output_tokens':15})
        self.assertTrue(all('"answer"' not in prompt for prompt in prompts))
