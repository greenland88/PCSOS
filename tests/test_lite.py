import json

import pytest

from pcs.providers.hood_trader_provider import HoodTraderProvider, JsonHoodClient


def test_hood_provider_requires_local_client():
    provider = HoodTraderProvider()
    with pytest.raises(NotImplementedError):
        provider.get_accounts()


def test_json_hood_client_is_read_only_adapter():
    payload = {
        "accounts": [{"account_number": "123"}],
        "option_chains": {"QQQ": [{"option_id": "opt1", "strike": 450}]},
        "option_quotes": {"opt1": {"bid": 1.0, "ask": 1.1}},
    }
    provider = HoodTraderProvider(JsonHoodClient(payload))
    assert provider.get_accounts()[0]["account_number"] == "123"
    assert provider.get_option_chain("QQQ")[0]["option_id"] == "opt1"
    assert provider.get_option_quotes(["opt1"])[0]["ask"] == 1.1


def test_lite_has_no_order_methods():
    provider = HoodTraderProvider(JsonHoodClient({}))
    assert not hasattr(provider, "place_order")
    assert not hasattr(provider, "place_option_order")
