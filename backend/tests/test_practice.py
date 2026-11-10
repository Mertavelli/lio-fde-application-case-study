from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

from app.core.config import settings
from app.services.pdf import extract_text
from tests.test_smoke import _auth, _signin, client


def test_five_quote_pdfs_have_selectable_text():
    for path in (Path(__file__).parents[2] / "fixtures" / "quotes").glob("*"):
        assert extract_text(path.read_bytes()).strip(), path.name


def test_printed_grand_totals_ignore_page_subtotals():
    from app.services.extraction import _printed_grand_total, _printed_items_total, _printed_charges_total, _unselected_alternative_prices

    expected = {
        "QT-4120-Cust-14918.pdf": "1686.14",
        "QT-OF2312380-Cust-57692.pdf": "1413.10",
        "QuoteA0492_23.Pdf": "2042.15",
        "Quote_1__Lio_Technologies_Inc__1x_MBA___2214703918.pdf": "1534.59",
        "Quote_Hartwell_Cleveland_ACME_Grinding_Machine.pdf": "18402.50",
    }
    items = {
        "QT-4120-Cust-14918.pdf": "1337.26",
        "QT-OF2312380-Cust-57692.pdf": "1187.05",
        "QuoteA0492_23.Pdf": "1880.00",
        "Quote_1__Lio_Technologies_Inc__1x_MBA___2214703918.pdf": "1409.06",
        "Quote_Hartwell_Cleveland_ACME_Grinding_Machine.pdf": "17000.00",
    }
    charges = {
        "QT-4120-Cust-14918.pdf": "348.88",
        "QT-OF2312380-Cust-57692.pdf": "226.05",
        "QuoteA0492_23.Pdf": "162.15",
        "Quote_1__Lio_Technologies_Inc__1x_MBA___2214703918.pdf": "125.53",
        "Quote_Hartwell_Cleveland_ACME_Grinding_Machine.pdf": "1402.50",
    }
    for name, total in expected.items():
        pdf = Path(__file__).parents[2] / "fixtures" / "quotes" / name
        text = extract_text(pdf.read_bytes())
        assert _printed_grand_total(text) == Decimal(total)
        assert _printed_items_total(text) == Decimal(items[name])
        assert _printed_charges_total(text) == Decimal(charges[name])
    assert _printed_grand_total("Page subtotal 1,337.26") is None
    greenmantle = extract_text((Path(__file__).parents[2] / "fixtures" / "quotes" / "QT-4120-Cust-14918.pdf").read_bytes())
    assert _unselected_alternative_prices(greenmantle) == {Decimal("430.00")}


def test_extraction_excludes_explicit_alternative_and_uses_supported_seller_brand(monkeypatch):
    from app.services import extraction

    monkeypatch.setattr(settings, "openai_api_key", "test")
    output = {
        "title": "Moss panel", "vendorName": "Greenmantle Moss Studio, LLC",
        "recipientCompanyName": "Lio Technologies", "vatId": "85-3252405",
        "department": "", "orderLines": [
            {"positionDescription": "Moss panel", "unitPrice": 894.08, "amount": 1, "unit": "piece", "totalPrice": 715.26},
            {"positionDescription": "Horizontal logo", "unitPrice": 622, "amount": 1, "unit": "piece", "totalPrice": 622},
            {"positionDescription": "Vertical logo", "unitPrice": 430, "amount": 1, "unit": "piece", "totalPrice": 430},
        ],
        "totalCost": 1867.26, "commodityGroupId": 15,
    }

    class FakeOpenAI:
        def __init__(self, **kwargs):
            pass
        def __getattr__(self, name):
            return self
        def parse(self, **kwargs):
            return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(parsed=SimpleNamespace(model_dump=lambda: output)))])

    monkeypatch.setattr(extraction, "OpenAI", FakeOpenAI)
    greenmantle = extract_text((Path(__file__).parents[2] / "fixtures" / "quotes" / "QT-4120-Cust-14918.pdf").read_bytes())
    result = client.post("/extraction", json={"text": greenmantle}, headers=_auth(_signin("requester@acme.com")))
    assert result.status_code == 200, result.text
    assert result.json()["data"]["totalCost"] == 1686.14
    assert len(result.json()["data"]["orderLines"]) == 2
    assert any("unselected alternative" in warning for warning in result.json()["warnings"])

    output["vendorName"] = "Lio Technologies, Inc."
    output["recipientCompanyName"] = "Acme Corp"
    output["orderLines"] = output["orderLines"][:1]
    verdeform = extract_text((Path(__file__).parents[2] / "fixtures" / "quotes" / "QT-OF2312380-Cust-57692.pdf").read_bytes())
    assert "EIN: 27-1073640" in verdeform
    result = client.post("/extraction", json={"text": verdeform}, headers=_auth(_signin("requester@acme.com")))
    assert result.json()["data"]["vendorName"] == "Bloomcraft Interiors, Inc."
    assert result.json()["data"]["vatId"] == "27-1073640"
    assert any("legal seller name" in warning for warning in result.json()["warnings"])

    output["vendorName"] = "Lio Technologies, Inc."
    output["recipientCompanyName"] = "Lio Technologies, Inc."
    apple = extract_text((Path(__file__).parents[2] / "fixtures" / "quotes" / "Quote_1__Lio_Technologies_Inc__1x_MBA___2214703918.pdf").read_bytes())
    result = client.post("/extraction", json={"text": apple}, headers=_auth(_signin("requester@acme.com")))
    assert result.json()["data"]["vendorName"] == "Apple Business Team"


def test_catalog_suggestions_are_tenant_scoped():
    params = {"description": "Toner cartridge cyan", "unit": "piece"}
    acme = client.get("/articles/suggestions", params=params, headers=_auth(_signin("requester@acme.com")))
    assert acme.status_code == 200, acme.text
    assert acme.json() and all(item["description"] == "Toner cartridge cyan" for item in acme.json())
    other = client.get("/articles/suggestions", params=params, headers=_auth(_signin("admin@walmart.com")))
    assert other.json() == []
    assert client.get("/articles/suggestions", params=params).status_code == 401
    moss = client.get("/articles/suggestions", params={"description": "Moss art panel, mixed moss 63 x 31.5 in", "unit": "piece"}, headers=_auth(_signin("requester@acme.com")))
    assert any("moss panel" in item["description"].lower() for item in moss.json())
    one_unit = client.get("/articles/suggestions", params={**params, "amount": 1}, headers=_auth(_signin("requester@acme.com")))
    assert one_unit.status_code == 200
    assert all(float(item["packQuantity"]) <= 1 for item in one_unit.json())


def test_semantic_catalog_choices_are_validated_and_tenant_scoped(monkeypatch):
    from app.routers import articles

    monkeypatch.setattr(settings, "openai_api_key", "test")

    class FakeOpenAI:
        def __init__(self, **kwargs):
            pass
        def __getattr__(self, name):
            return self
        def parse(self, **kwargs):
            prompt = kwargs["messages"][0]["content"]
            match = re.search(r"(?m)^(\d+): Toner cartridge cyan$", prompt)
            assert match
            result = SimpleNamespace(indices=[int(match.group(1)), 999999, -1])
            return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(parsed=result))])

    import re
    monkeypatch.setattr(articles, "OpenAI", FakeOpenAI)
    payload = {"description": "Cyan printer cartridge", "unit": "piece", "unit_price": 40, "amount": 1}
    acme = client.post("/articles/suggestions/semantic", json=payload, headers=_auth(_signin("requester@acme.com")))
    assert acme.status_code == 200, acme.text
    assert [item["description"] for item in acme.json()] == ["Toner cartridge cyan"]
    assert float(acme.json()[0]["packQuantity"]) == 1
    walmart = client.post("/articles/suggestions/semantic", json=payload, headers=_auth(_signin("admin@walmart.com")))
    assert walmart.status_code == 200
    assert walmart.json() == []
    assert client.post("/articles/suggestions/semantic", json=payload).status_code == 401


def test_acme_booking_rules_and_arithmetic_warnings(monkeypatch):
    from app.services import extraction

    monkeypatch.setattr(settings, "openai_api_key", "test")
    output = {
        "title": "Cable ties", "vendorName": "Test supplier", "recipientCompanyName": "Test supplier", "vatId": None,
        "department": "", "orderLines": [{"positionDescription": "Cable ties",
        "unitPrice": 2, "amount": 3, "unit": "pieces", "totalPrice": 9}],
        "totalCost": 10, "commodityGroupId": 31, "commodityGroupName": "Software",
    }
    class FakeOpenAI:
        def __init__(self, **kwargs):
            pass
        def __getattr__(self, name):
            return self
        def parse(self, **kwargs):
            return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(parsed=SimpleNamespace(model_dump=lambda: output)))])
    monkeypatch.setattr(extraction, "OpenAI", FakeOpenAI)
    acme = client.post("/extraction", json={"text": "Cable ties\nGrand total $12.00"}, headers=_auth(_signin("requester@acme.com")))
    assert acme.status_code == 200, acme.text
    assert acme.json()["data"]["commodityGroupId"] == 11
    assert acme.json()["data"]["totalCost"] == 12
    assert acme.json()["data"]["vendorName"] is None
    assert "Vendor Name" in acme.json()["missingFields"]
    assert len(acme.json()["warnings"]) == 4
    other = client.post("/extraction", json={"text": "Cable ties"}, headers=_auth(_signin("admin@walmart.com")))
    assert other.json()["data"]["commodityGroupId"] == 31
    output["recipientCompanyName"] = "Buyer Co"
    output["vendorName"] = "Wrong supplier"
    brand = client.post(
        "/extraction",
        json={"text": "Thank you very much for your inquiry at verdeform."},
        headers=_auth(_signin("requester@acme.com")),
    )
    assert any("verdeform" in warning for warning in brand.json()["warnings"])

    output["totalCost"] = float("nan")
    invalid = client.post(
        "/extraction", json={"text": "Quote with invalid model number"},
        headers=_auth(_signin("requester@acme.com")),
    )
    assert invalid.status_code == 200
    assert invalid.json()["success"] is False


def test_acme_rule_is_enforced_on_manual_requests():
    token = _signin("requester@acme.com")
    payload = {
        "requestorName": "Practice", "titleShortDescription": "Cable tie packs",
        "vendorName": "Test supplier", "vatId": "", "department": "Ops",
        "commodityGroupId": 31, "totalCost": 20,
        "orderLines": [{"positionDescription": "Cable ties", "unitPrice": 2,
                        "amount": 10, "unit": "pieces", "totalPrice": 20}],
    }
    wrong = client.post("/requests", json=payload, headers=_auth(token))
    assert wrong.status_code == 422
    assert "Electrical Engineering" in wrong.json()["detail"]
    correct = client.post("/requests", json={**payload, "commodityGroupId": 11}, headers=_auth(token))
    assert correct.status_code == 201, correct.text


def test_small_subscription_rule_has_explicit_total_limit():
    from app.services.category_rules import matching_group_ids
    from types import SimpleNamespace
    rule = {"category_rules": [{"term": "software subscription", "commodity_group_id": 2002, "max_total": 500}]}
    lines = [SimpleNamespace(position_description="Software subscriptions")]
    assert matching_group_ids(rule, "", lines, 499.99) == {2002}
    assert matching_group_ids(rule, "", lines, 500.01) == set()


def test_settings_edit_preserves_acme_rules_and_rejects_unknown_group():
    token = _signin("admin@acme.com")
    before = client.get("/organizations/me/settings", headers=_auth(token)).json()
    assert before["category_rules"]
    edited = client.patch("/organizations/me/settings", json={"required_fields": ["department"]}, headers=_auth(token))
    assert edited.status_code == 200
    after = client.get("/organizations/me/settings", headers=_auth(token)).json()
    assert after["category_rules"] == before["category_rules"]
    bad = client.patch("/organizations/me/settings", json={"category_rules": [{"term": "cable ties", "commodity_group_id": 999999}]}, headers=_auth(token))
    assert bad.status_code == 422
    duplicate = client.patch("/organizations/me/settings", json={"category_rules": [
        {"term": "cable ties", "commodity_group_id": 11},
        {"term": "Cable Ties", "commodity_group_id": 2001},
    ]}, headers=_auth(token))
    assert duplicate.status_code == 422
    client.patch("/organizations/me/settings", json={"required_fields": []}, headers=_auth(token))
