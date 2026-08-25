# PCS Safety Regression Suite

`tests/test_safety_regression.py` is the permanent, validation-only safety gate. It uses the canonical `PCSDataAccess` boundary and isolated temporary stores; it does not alter strategy thresholds, historical data, ticker profiles, or production configuration.

The versioned golden fixture is `tests/fixtures/safety/golden_cases.json`. It contains representative ENTRY and REJECT cases for NVDA, TSLA, MU, AMZN, AMD, META, and QQQ, including lifecycle and fractional-strike evidence where applicable.

Run with `python -m pytest tests/test_safety_regression.py -q`. A failure is evidence for investigation and must be classified as strategy, data, infrastructure, or test-harness related before any change is made.
