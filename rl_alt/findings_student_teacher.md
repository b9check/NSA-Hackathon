## Student-Teacher Action Agreement

### Full-action agreement (all 7 slots match)

| Model | offensive | defensive | recon-heavy |
|---|---|---|---|
| bc_200k_scale1 |   0.0% |   1.0% |   0.0% |
| bc_scale10_teacher_100k |   3.2% |   0.0% |   1.2% |


### bc_200k_scale1 per-slot agreement
| Regime | recon_move | recon_pol | strike_move | strike_pol | sam_move | sam_pol | radar |
|---|---|---|---|---|---|---|---|
| offensive |  79.2% | 100.0% |  70.8% |  64.3% |  50.6% | 100.0% |   3.9% |
| defensive |   2.1% | 100.0% |   1.0% | 100.0% |  73.6% | 100.0% | 100.0% |
| recon-heavy |  89.4% | 100.0% |   0.0% |  81.2% |  97.5% | 100.0% |   0.0% |


### bc_scale10_teacher_100k per-slot agreement
| Regime | recon_move | recon_pol | strike_move | strike_pol | sam_move | sam_pol | radar |
|---|---|---|---|---|---|---|---|
| offensive |  20.1% | 100.0% |  23.4% |  66.9% |  52.6% | 100.0% | 100.0% |
| defensive |   2.1% | 100.0% |  68.9% |  83.9% |  89.1% | 100.0% | 100.0% |
| recon-heavy |   3.8% | 100.0% |   2.5% |  83.1% |   3.1% | 100.0% | 100.0% |

### Worst-slot disagreement breakdown (across regimes)

- **bc_200k_scale1** worst slot: `strike_move` agreement= 21.9% (disagree on 396/507). Student picks: 4:  91.2%, 1:   8.6%, 0:   0.3%
- **bc_scale10_teacher_100k** worst slot: `recon_move` agreement=  8.1% (disagree on 466/507). Student picks: 1:  58.2%, 4:  40.6%, 0:   1.3%

### Interpretation

Mean full-action agreement across loaded models/regimes is   0.9%. This fits the bucket: **<60% -> BC is collapsing; student is not imitating the teacher.** Scale-10 (1.5%) and scale-1 (0.3%) are comparable; the scale knob does not materially shift BC fidelity. The worst-slot breakdowns above show *which* head is dropping fidelity, i.e. where targeted BC re-weighting or longer training would help most.
