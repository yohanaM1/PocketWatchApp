import pytest

from app.services.merchant import normalize_merchant


# Known description -> merchant cases
@pytest.mark.parametrize(
    "description, expected",
    [
        # Store numbers, cities and states are dropped.
        ("SQ *STARBUCKS #2411 HYATTSVILLE MD", "STARBUCKS"),
        ("STARBUCKS STORE 02606", "STARBUCKS"),
        ("STARBUCKS 12345 COLLEGE PARK MD", "STARBUCKS"),
        ("GIANT 8451 HYATTSVILLE MD", "GIANT"),
        ("TACO BELL #32764", "TACO BELL"),
        ("TARGET T-1234", "TARGET"),
        ("CVS/PHARMACY #41904", "CVS"),
        ("7-ELEVEN 13315", "7 ELEVEN"),
        ("7 ELEVEN 13315 GREENBELT MD", "7 ELEVEN"),
        # Payment processor prefixes before "*".
        ("TST* PANERA BREAD 1234", "PANERA BREAD"),
        ("TM *TICKETMASTER ABC123XYZ", "TICKETMASTER"),
        ("PAYPAL *XJ4HOLDINGS 4029357733", "XJ4HOLDINGS"),
        ("SP * NOVAGADGETS NOVAGADGETS.CO", "NOVAGADGETS"),
        # Merchant before "*", reference code after.
        ("TARGET.COM *351866", "TARGET"),
        ("Prime Video Channels*2K4L1 amzn.com/bill WA", "PRIME VIDEO CHANNELS"),
        # Aliases for merchants with very messy descriptions.
        ("AMZN Mktp US*2K4L1", "AMAZON"),
        ("AMAZON.COM*RT3 AMZN.COM/BILL WA", "AMAZON"),
        ("UBER *TRIP HELP.UBER.COM CA", "UBER"),
        ("UBER   *EATS PENDING", "UBER EATS"),
        ("DD *DOORDASH CAVA", "DOORDASH"),
        ("DOORDASH*WINGSTOP WWW.DOORDASH. CA", "DOORDASH"),
        ("VENMO PAYMENT 1023456789", "VENMO"),
        ("ZELLE PAYMENT TO SAM X1EVUHT6T", "ZELLE"),
        ("APPLE.COM/BILL 866-712-7753 CA", "APPLE"),
        ("WM SUPERCENTER #1234", "WALMART"),
        # Domains and trailing banking words.
        ("NETFLIX.COM 7163083535 CA", "NETFLIX"),
        ("PEPCO BILL PAY 12345678", "PEPCO"),
        ("UMD PAYROLL DIR DEP PPD ID: 8769564683", "UMD PAYROLL"),
        ("LANDMARK COLLEGE PARK RENT ACH", "LANDMARK COLLEGE PARK RENT"),
        ("INTL TXN BZR*DIGITALSVC LONDON GB", "DIGITALSVC LONDON GB"),
        # Possessives: apostrophe, a space where the apostrophe was, or neither all give one name.
        ("TRADER JOE S #097 HYATTSVILLE MD", "TRADER JOES"),
        ("TRADER JOE'S #097", "TRADER JOES"),
        ("Trader Joe’s 123", "TRADER JOES"),
        ("TRADER JOES #057", "TRADER JOES"),
        ("MCDONALD S F1234", "MCDONALDS"),
        # Other spellings of a prefix.
        ("UNIV OF MD DINING", "UMD DINING"),
        ("UNIVERSITY OF MARYLAND DINING 123", "UMD DINING"),
        ("UMD DINING 0146 COLLEGE PARK MD", "UMD DINING"),
        # Whitespace and case don't matter.
        ("  chipotle   1234  ", "CHIPOTLE"),
    ],
)
def test_normalize_merchant(description, expected):
    """Each raw bank description is cleaned up to the expected merchant name."""
    assert normalize_merchant(description) == expected


# Merchant name bounds
def test_never_empty_and_length_capped():
    """The merchant name is never empty and never longer than the 100-character column."""
    assert normalize_merchant("12345 67890") == "12345"
    assert normalize_merchant("*") != ""
    assert len(normalize_merchant("X" * 300)) == 100
