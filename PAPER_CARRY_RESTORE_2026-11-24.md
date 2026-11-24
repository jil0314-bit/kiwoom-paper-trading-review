# PAPER zero-stop restoration fix

Base review revision: `46f535f31bb2ae05a5adcbaeea3559ca04d1817e`.

When automatic stops are disabled, PAPER positions persist `stop_price: 0.0`.
The inherited loader rejected zero as an invalid exchange price, leaving no
restored holdings. The continuing engine could subsequently save that incomplete
state. This patch accepts zero only for the stop-price field during restoration.
Positive stops retain their existing tick alignment; zero targets, negative
stops, and non-finite prices remain invalid. A reported load failure now aborts
engine construction before downstream initialization can overwrite the state.

The adapter reuses the locked parent loader with a private globals dictionary.
The shared price validator, locked parent hashes, trading methods, UI and Kiwoom
paths are unchanged. It installs before engine construction and is idempotent.

## Validation

- Current production source: 11 restoration tests and 13 existing no-stop tests
  passed in isolation. No network, GUI or orders are used by these tests.
- Covered the original failure, repeated next-day/rest-of-week/weekend restarts,
  in-process date rollover, retained quantities/costs/dates/targets/tranches,
  actual PAPER sell persistence with no resurrection, same-day PnL, backup
  fallback, corrupt-state startup failure, and unchanged price validation.
- A private recovery rehearsal preserved the current-day positions, cash,
  realized PnL, sequence and historical fills while adding only ledger-proven
  missing holdings. Repeated restoration on four dates retained all positions.
- Existing production classes and functions are AST-identical to their prior
  versions; only the installer, its call, and the FunctionType import were added.
- Production syntax/import and all 148 manifest entries verified successfully.

The public repository is an older review snapshot, not a copy of the current
operating directory. Only this adapter and synthetic regression tests are
published here. The split-buy persistence test is explicitly skipped on this
older snapshot, which lacks that subsystem; it passes on current production.
No credentials, operational ledgers, personal notes or recovery snapshots are
included. Historical tick-level extrema unavailable after the prior failed
startup were not represented as fully recovered.

Run the public regression with:

```text
python -B -m unittest discover -s tests -p test_paper_carry_restore.py -v
python -B SOURCE/chain_manifest.py
```

Future calendar days are simulated verification, not a claim of live observations
on those future dates. This does not validate real-account trading or profitability.
