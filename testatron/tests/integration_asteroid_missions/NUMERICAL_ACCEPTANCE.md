# IPOPT asteroid mission numerical acceptance

This case is validated by `testatron/run_asteroid_integration.py` rather than by
byte-for-byte Comparatron output. A passing run must:

- produce the success `.emtg` file and forward ephemeris, with no `FAILURE_` file;
- report the first NLP solve as feasible;
- have absolute worst constraint violation no greater than `1.0e-5`;
- finish with an `LT_rndzvs` event at `A20136163`;
- retain at least `1800 kg` final mass; and
- agree between the final-event and mission-summary masses within `0.01 kg`.

These envelopes are intentionally physical and solver-tolerant. They validate
mission success without requiring IPOPT and SNOPT to produce identical decision
vectors or text output.
