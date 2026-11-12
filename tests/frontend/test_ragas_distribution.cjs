const assert=require('node:assert/strict'),path=require('node:path');
const {queryPercentages,changeQueryPercentage,ragasQueryTypes}=require(path.resolve(process.argv[2],'ragasDistribution.js'));
const old={single_hop:1,multi_hop_abstract:4,multi_hop_specific:6};
assert.deepEqual(queryPercentages(old),{single_hop:9,multi_hop_abstract:36,multi_hop_specific:55});
assert.deepEqual(old,{single_hop:1,multi_hop_abstract:4,multi_hop_specific:6},'Displaying saved weights must not mutate them');
assert.deepEqual(queryPercentages({single_hop:1}),{single_hop:100,multi_hop_abstract:0,multi_hop_specific:0},'Legacy sparse configurations show all three controls');
for(const initial of [old,{single_hop:1},{single_hop:0,multi_hop_abstract:1,multi_hop_specific:1},{}]){
 let weights=initial;
 for(const key of ragasQueryTypes)for(let percent=0;percent<=100;percent++){
  weights=changeQueryPercentage(weights,key,percent);
  assert.equal(weights[key],percent,'The edited slider must match its displayed percentage');
  assert.equal(Object.values(weights).reduce((a,b)=>a+b,0),100,'Every edit preserves a total of 100%');
  assert(Object.values(weights).every(v=>Number.isInteger(v)&&v>=0&&v<=100));
 }
}
assert.deepEqual(changeQueryPercentage({single_hop:1},'multi_hop_specific',25),{single_hop:75,multi_hop_abstract:0,multi_hop_specific:25});
assert.deepEqual(changeQueryPercentage(old,'single_hop',50),{single_hop:50,multi_hop_abstract:20,multi_hop_specific:30},'Other types retain their relative proportion');
assert.deepEqual(changeQueryPercentage(old,'single_hop',100),{single_hop:100,multi_hop_abstract:0,multi_hop_specific:0});
console.log('RAGAS distribution passed: saved weights, sparse defaults, exact totals, boundaries, proportional redistribution and repeated edits');
