import pytest
from utils import round_price

def test_round_price_rounds_down():
    assert round_price(1.2399, price_precision=2) == pytest.approx(1.23)
    assert round_price(1.235, price_precision=2) == pytest.approx(1.23)
