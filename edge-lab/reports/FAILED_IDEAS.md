# FAILED IDEAS

Failed tests are results. They are recorded here and in
`research/failed_ideas/` so the same dead ends are not retried blindly.

## From the synthetic plumbing run (not market evidence)
- **Gap-up + RVOL continuation (rule grid).** On synthetic random-walk
  data the walk-forward out-of-sample sample was tiny (17 trades), failed
  the >=55%-months-positive rule, was concentrated in one ticker/month, and
  did not survive cost stress. Correctly rejected. This says nothing about
  real markets — it confirms the gate rejects noise.
- **ML ranking (EV + win-prob + dynamic exits).** Negative out-of-sample
  expectancy on synthetic data, as expected when there is no signal.

Real-data failures will be appended here as they occur, each with the
reason it failed and what evidence would change the conclusion.
