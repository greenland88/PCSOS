# TSLA v2 controlled cutover — BLOCKED

TSLA cutover was attempted first and stopped before production route activation.

The target `options_v2` source covered 2010-07-08 through 2026-07-31 and had
12,000,536 physical rows, 11,999,222 unique identity keys, 1,314 duplicate
identity rows, and 0 conflicting quote keys. The duplicate-key gate therefore
failed.

The TSLA route was restored to the old canonical route. Route resolution worked,
but read validation of the old canonical period failed because it contains 2,923
ambiguous quote keys. No TSLA or AMZN data was modified, and AMZN cutover was
not attempted.

The existing Safety Suite passed 11 tests. The TSLA cutover verdict is FAIL.
