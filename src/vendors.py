from __future__ import annotations

from src.normalize import get_domain

VENDOR_DOMAIN_RULES: dict[str, tuple[str, ...]] = {
    "CivicPlus": ("civicplus.com", "municodeweb.com"),
    "Granicus": ("granicus.com", "civicengage.com"),
    "AxisGIS": ("axisgis.com",),
    "PropertyRecordCards": ("propertyrecordcards.com", "propertycards.com"),
    "MyTaxBill": ("mytaxbill.org",),
    "GovernmentJobs": ("governmentjobs.com",),
    "RecDesk": ("recdesk.com",),
    "GovOffice": ("govoffice.com",),
    "Revize": ("revize.com",),
}

VENDOR_TEXT_RULES: dict[str, tuple[str, ...]] = {
    "CivicPlus": ("powered by civicplus", "civicplus"),
    "Granicus": ("powered by granicus", "granicus", "civicengage"),
    "AxisGIS": ("axisgis",),
    "PropertyRecordCards": ("propertyrecordcards", "property record cards"),
    "MyTaxBill": ("mytaxbill",),
    "GovernmentJobs": ("governmentjobs",),
    "RecDesk": ("recdesk",),
    "GovOffice": ("govoffice", "gov office"),
    "Revize": ("revize", "govoffice by revize"),
}


def detect_vendor(url: str | None, text: str | None = None) -> tuple[str | None, float]:
    domain = get_domain(url)
    text_blob = (text or "").lower()

    if domain:
        for vendor, domain_tokens in VENDOR_DOMAIN_RULES.items():
            for token in domain_tokens:
                if token in domain:
                    return vendor, 0.95

    if text_blob:
        for vendor, text_tokens in VENDOR_TEXT_RULES.items():
            for token in text_tokens:
                if token in text_blob:
                    return vendor, 0.75

    return None, 0.0
