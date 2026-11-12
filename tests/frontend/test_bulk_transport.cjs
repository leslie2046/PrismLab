const assert = require('node:assert/strict');
const path = require('node:path');
const { api, setWorkspaceId, setCsrfToken } = require(path.resolve(process.argv[2], 'api.js'));
const originalFetch = global.fetch;
const ids = Array.from({ length: 250 }, (_, i) => `id-${i}`);
const response = (value, status = 200) => new Response(JSON.stringify(value), { status });
(async () => {
  setWorkspaceId('team-one'); setCsrfToken('csrf');
  const calls = [];
  global.fetch = async (url, init) => {
    const body = JSON.parse(init.body);
    calls.push(body.ids);
    assert.equal(init.headers.get('X-Workspace-Id'), 'team-one');
    assert.equal(init.headers.get('X-CSRF-Token'), 'csrf');
    return response({ succeeded: body.ids, skipped: [], failed: [] });
  };
  assert.deepEqual((await api.bulkPlans('pause', ids)).succeeded, ids);
  assert.deepEqual(calls.map(x => x.length), [100, 100, 50]);
  let sent = 0;
  global.fetch = async (url, init) => {
    if (++sent === 2) return response({ detail: 'Request failed' }, 503);
    return response({ succeeded: JSON.parse(init.body).ids, skipped: [], failed: [] });
  };
  const partial = await api.bulkRuns('delete', ids);
  assert.equal(sent, 2);
  assert.deepEqual(partial.succeeded, ids.slice(0, 100));
  assert.deepEqual(partial.failed.map(x => x.id), ids.slice(100));
  // Switching workspace while a request is pending must not submit remaining
  // identifiers against the next workspace, even when IDs happen to collide.
  sent = 0;
  global.fetch = async (url, init) => {
    sent += 1; setWorkspaceId('team-two');
    return response({ succeeded: JSON.parse(init.body).ids, skipped: [], failed: [] });
  };
  const switched = await api.bulkPlans('delete', ids);
  assert.equal(sent, 1);
  assert.deepEqual(switched.succeeded, ids.slice(0, 100));
  assert.deepEqual(switched.failed.map(x => x.id), ids.slice(100));
  assert(switched.failed.every(x => x.message.includes('Workspace changed')));
  console.log('PASS complete selection chunking, partial failure recovery, and workspace switch isolation');
})().catch(error => { console.error(error); process.exitCode = 1; }).finally(() => { global.fetch = originalFetch; });
