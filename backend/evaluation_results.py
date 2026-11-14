"""Deterministic presentation and comparison from stored evidence."""
import csv
import io
import json
import math
from backend.evaluation_contracts import evaluation_type


def summarize_metrics(definitions, results):
    scores = {}
    for metric in definitions:
        values = [m['score'] for m in results if m['metric_id']==metric['id'] and m['status']=='completed'
                  and type(m.get('score')) in {int, float} and math.isfinite(m['score'])]
        scores[metric['id']] = {'mean': sum(values)/len(values) if values else None, 'scored': len(values),
                               'threshold':metric['threshold'], 'implementation_version':metric['implementation_version']}
        if metric['id'] == 'rules.recall_at_k':
            scores[metric['id']]['k'] = metric.get('k')
    return scores


def summarize(run):
    metrics = run['metric_results']
    cases = run['cases']
    scores = summarize_metrics(run['evidence']['metrics'], metrics)
    durations = sorted(c['observation']['duration_ms'] for c in cases if c.get('observation') and isinstance(c['observation'].get('duration_ms'),(int,float)))
    counts = {s:sum(m['status']==s for m in metrics) for s in ('completed','judge_error','missing_data','pending','cancelled')}
    counts['not_passed'] = sum(m['passed'] is False for m in metrics)
    counts['target_error'] = sum(c['status']=='target_error' for c in cases)
    return {'counts':counts, 'metrics':scores,'p95_ms':durations[max(0,math.ceil(len(durations)*0.95)-1)] if durations else None,
            'cost':None, 'usage':run.get('usage',{})}


def comparison_compatible(run,baseline):
    a,b=run['evidence'],baseline['evidence']
    return (evaluation_type(a)==evaluation_type(b) and a['dataset_version_id']==b['dataset_version_id'] and a['metrics']==b['metrics']
            and a.get('result_judge')==b.get('result_judge')
            and sorted(c['sample_id'] for c in run['cases'])==sorted(c['sample_id'] for c in baseline['cases']))


def export_run(run, version, format):
    samples = {s['id']:s for s in version['samples']}
    records = [{'sample':samples[c['sample_id']], 'case':c,
                'metrics':[m for m in run['metric_results'] if m['case_id']==c['id']]} for c in run['cases']]
    if format == 'json':
        return json.dumps({'run':run,'version':version,'summary':summarize(run),'records':records},ensure_ascii=False,indent=2)
    if format == 'jsonl':
        return '\n'.join(json.dumps({'run_id':run['id'],'evidence':run['evidence'],**r},ensure_ascii=False) for r in records)+'\n'
    output = io.StringIO(); writer=csv.writer(output)
    writer.writerow(['run_id','sample_id','input','expected_output','actual_output','reference_context','retrieval_context','case_status','metric_id','status','score','passed','reason','evaluation_type'])
    def cell(v):
        value = v if isinstance(v,str) else json.dumps(v,ensure_ascii=False) if v is not None else ''
        return "'"+value if value.lstrip().startswith(('=','+','-','@')) else value
    for r in records:
        s,c = r['sample'],r['case']; obs=c.get('observation') or {}
        for m in r['metrics'] or [{}]:
            writer.writerow([cell(v) for v in [run['id'],s['id'],s.get('input'),s.get('expected_output'),obs.get('actual_output'),s.get('context'),obs.get('retrieval_context'),c['status'],m.get('metric_id'),m.get('status'),m.get('score'),m.get('passed'),m.get('reason'),evaluation_type(run['evidence'])]])
    return '\ufeff'+output.getvalue()
