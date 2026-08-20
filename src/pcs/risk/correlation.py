def bucket_for(ticker: str, rules: dict) -> str:
    for bucket, tickers in rules["portfolio"]["buckets"].items():
        if ticker in tickers:
            return bucket
    return "other"

