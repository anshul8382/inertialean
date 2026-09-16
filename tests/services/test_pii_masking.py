from services.pii_masking import mask_client_name, mask_email


def test_mask_email():
    assert mask_email("john.doe@example.com") == "j***@example.com"
    assert mask_email("") == ""
    assert mask_email(None) == ""


def test_mask_client_name():
    assert mask_client_name("Shobhit Khare") == "Sh***** Kh***"
    assert mask_client_name("Al") == "A*"
    assert mask_client_name("") == ""
