from app.core.sms import normalize_iran_phone


def test_normalize_iran_phone_variants():
    assert normalize_iran_phone("09121234567") == "989121234567"
    assert normalize_iran_phone("+989121234567") == "989121234567"
    assert normalize_iran_phone("989121234567") == "989121234567"
    assert normalize_iran_phone("00989121234567") == "989121234567"
