# Eval results

Model: `accounts/fireworks/models/kimi-k3`  ·  2026-10-23T14:59:39+00:00

**18/18 passed (100%)**

By category:

| Category | Passed |
| --- | --- |
| lookup | 7/7 |
| narrative | 6/6 |
| synthesis | 5/5 |

| Question | Category | Scorer | Verdict | Detail |
| --- | --- | --- | --- | --- |
| lookup-01 | lookup | numeric | PASS | numeric ~= 215938000000.0 (±2%) |
| lookup-02 | lookup | numeric | PASS | numeric ~= -18756000000.0 (±2%) |
| lookup-03 | lookup | numeric | PASS | numeric ~= 49.52 (±2%) |
| lookup-04 | lookup | numeric | PASS | numeric ~= 18497000000.0 (±2%) |
| lookup-05 | lookup | numeric | PASS | numeric ~= 149617000000.0 (±2%) |
| lookup-06 | lookup | numeric | PASS | numeric ~= 16635000000.0 (±2%) |
| lookup-07 | lookup | numeric | PASS | numeric ~= 4.9 (±2%) |
| narrative-01 | narrative | judge | PASS | judge 1.00: The system answer captures all key gold facts—export licensing restrictions on advanced da |
| narrative-02 | narrative | judge | PASS | judge 0.85: The system answer thoroughly covers the IDM 2.0 transformation to serve external foundry c |
| narrative-03 | narrative | entity | PASS | entities ['Intel', 'NVIDIA'] |
| narrative-04 | narrative | judge | PASS | judge 0.90: The system answer correctly identifies both reportable segments (Compute & Networking and  |
| narrative-05 | narrative | judge | PASS | judge 0.90: The system answer captures the gold answer's key elements—strong/growing demand for data-c |
| narrative-06 | narrative | judge | PASS | judge 1.00: The system answer fully captures the gold answer's key points—intense competition from AMD |
| synth-01 | synthesis | judge | PASS | judge 1.00: The system answer matches the gold answer on all key facts: NVIDIA fastest at ~+65% (FY202 |
| synth-02 | synthesis | judge | PASS | judge 1.00: The system answer correctly identifies NVIDIA as highest at ~71%, AMD at ~49.5%, and Intel |
| synth-03 | synthesis | judge | PASS | judge 1.00: The system answer matches the gold answer's key figures ($17.1B to $19.7B, ~+15%) and full |
| synth-04 | synthesis | judge | PASS | judge 1.00: The system answer matches the gold answer's key figures (+$1.7B to -$18.8B, ~$20B swing) a |
| synth-05 | synthesis | judge | PASS | judge 1.00: The system answer correctly identifies Intel as having the highest R&D intensity at ~26% o |
