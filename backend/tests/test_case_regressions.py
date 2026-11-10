from app.core.config import settings
from app.routers.articles import _exact_description
from app.services.item_classification import classify_items
from tests.test_smoke import _auth, _signin, client


def _payload(description: str, group: int = 15, total: float = 100) -> dict:
    return {"requestorName": "Case review", "titleShortDescription": description,
            "vendorName": "Example Supplier", "commodityGroupId": group, "totalCost": total,
            "orderLines": [{"positionDescription": description, "unitPrice": total,
                            "amount": 1, "unit": "piece", "totalPrice": total}]}


def test_article_kinds_cover_common_quote_wording():
    kinds = [item["kind"] for item in classify_items([
        "Nylon zip ties", "HP 415A cyan toner", "Adobe cloud monthly subscription",
    ])]
    assert kinds == ["cable_tie", "printer_toner", "software_subscription"]


def test_missing_model_configuration_requires_review(monkeypatch):
    monkeypatch.setattr(settings, "openai_api_key", None)
    assert classify_items(["Unknown component ZX-7"])[0]["kind"] == "uncertain"


def test_similar_laptops_cannot_share_a_negotiated_price():
    assert _exact_description("Laptop 14 inch") != _exact_description("Laptop 16 inch")


def test_missing_manual_approver_does_not_turn_manual_decision_into_automatic():
    from types import SimpleNamespace
    from app.routers.requests import _is_auto_approved

    request = SimpleNamespace(approval_status="approved", approved_by=None,
                              approved_at="2026-11-10", approval_source="manual")
    assert _is_auto_approved(request) is False


def test_acme_group_is_enforced_for_toner_model():
    token = _auth(_signin("requester@acme.com"))
    wrong = client.post("/requests", json=_payload("HP 415A cyan toner"), headers=token)
    assert wrong.status_code == 422
    correct = client.post("/requests", json=_payload("HP 415A cyan toner", 2001), headers=token)
    assert correct.status_code == 201, correct.text


def test_model_outage_requires_buyer_review(monkeypatch):
    from app.services import item_classification

    monkeypatch.setattr(settings, "openai_api_key", "test")
    monkeypatch.setattr(item_classification, "OpenAI", lambda **kwargs: (_ for _ in ()).throw(RuntimeError("outage")))
    requester = _auth(_signin("requester@acme.com"))
    buyer = _auth(_signin("buyer@acme.com"))
    created = client.post("/requests", json=_payload("Unfamiliar procurement item"), headers=requester)
    assert created.status_code == 201, created.text
    request_id = created.json()["id"]
    assert created.json()["classification"]["review_required"] is True
    assert created.json()["approval_status"] == "pending"
    assert client.patch(f"/requests/{request_id}/approval", json={"decision": "approved"}, headers=buyer).status_code == 409
    assert client.post(f"/requests/{request_id}/classification-review", json={"kinds": ["other"], "reason": "Checked quoted product"}, headers=requester).status_code == 403
    reviewed = client.post(f"/requests/{request_id}/classification-review", json={"kinds": ["other"], "reason": "Checked quoted product"}, headers=buyer)
    assert reviewed.status_code == 200, reviewed.text
    assert reviewed.json()["classification"]["review_required"] is False
    assert reviewed.json()["approval_status"] == "approved"


def test_unrelated_edit_keeps_human_review_but_new_item_needs_review(monkeypatch):
    monkeypatch.setattr(settings, "openai_api_key", None)
    requester = _auth(_signin("requester@acme.com"))
    buyer = _auth(_signin("buyer@acme.com"))
    admin = _auth(_signin("admin@acme.com"))
    payload = _payload("Unfamiliar procurement item")
    created = client.post("/requests", json=payload, headers=requester).json()
    request_id = created["id"]
    reviewed = client.post(f"/requests/{request_id}/classification-review",
        json={"kinds": ["other"], "reason": "Checked the source quote"}, headers=buyer)
    assert reviewed.status_code == 200, reviewed.text
    update = {**payload, "title": payload["titleShortDescription"], "department": "IT"}
    update.pop("titleShortDescription")
    unchanged = client.put(f"/requests/{request_id}", json=update, headers=requester)
    assert unchanged.status_code == 200, unchanged.text
    assert unchanged.json()["classification"]["review_required"] is False
    assert unchanged.json()["classification"]["reviewed_by"]
    update["orderLines"] = [{**update["orderLines"][0], "positionDescription": "Unknown component ZX-7"}]
    changed = client.put(f"/requests/{request_id}", json=update, headers=requester)
    assert changed.status_code == 200, changed.text
    assert changed.json()["classification"]["review_required"] is True
    assert changed.json()["approval_status"] == "pending"
    assert client.delete(f"/requests/{request_id}", headers=admin).status_code == 204


def test_catalog_price_is_verified_against_tenant_and_current_article():
    token = _auth(_signin("requester@acme.com"))
    articles = client.get("/articles", params={"limit": 100}, headers=token).json()["items"]
    article = next(item for item in articles if float(item["quantity"]) == 1 and all(word not in item["description"].casefold() for word in ("toner", "cable tie", "zip tie", "subscription")))
    payload = _payload(article["description"], total=float(article["unit_price"]))
    payload["vendorName"] = article["supplier_name"]
    payload["orderLines"][0]["unit"] = article["unit"]
    payload["orderLines"][0]["catalogArticleId"] = article["id"]
    payload["orderLines"][0]["unitPrice"] = float(article["unit_price"])
    payload["orderLines"][0]["totalPrice"] = float(article["unit_price"])
    accepted = client.post("/requests", json=payload, headers=token)
    assert accepted.status_code == 201, accepted.text
    activity = client.get(f"/requests/{accepted.json()['id']}/activity", headers=token).json()
    assert activity[0]["detail"]["catalog_prices"][0]["article_id"] == article["id"]
    payload["orderLines"][0]["unitPrice"] += 1
    payload["orderLines"][0]["totalPrice"] += 1
    assert client.post("/requests", json=payload, headers=token).status_code == 422
    payload["orderLines"][0]["catalogArticleId"] = "00000000-0000-0000-0000-000000000000"
    assert client.post("/requests", json=payload, headers=token).status_code == 422
