from sqlalchemy import select

from app.models import Article, Organization, Supplier
from tests.test_smoke import TestingSession, _auth, _signin, client


def test_acme_cannot_read_other_catalog_or_rules_or_change_other_settings():
    acme_admin = _auth(_signin("admin@acme.com"))
    acme_requester = _auth(_signin("requester@acme.com"))
    walmart_admin = _auth(_signin("admin@walmart.com"))
    with TestingSession() as db:
        walmart = db.scalar(select(Organization).where(Organization.slug == "walmart"))
        original_settings = dict(walmart.settings or {})
        supplier = Supplier(organization_id=walmart.id, name="Walmart-only vendor")
        db.add(supplier)
        db.flush()
        article = Article(
            organization_id=walmart.id, supplier_id=supplier.id,
            article_number="WAL-PRIVATE-1", description="Walmart-only toner cartridge cyan",
            unit_price=1, currency="USD", unit="piece", quantity=1,
        )
        db.add(article)
        db.commit()

        try:
            assert client.get("/articles?search=WAL-PRIVATE-1", headers=walmart_admin).json()["total"] == 1
            assert client.get("/articles?search=WAL-PRIVATE-1", headers=acme_admin).json()["total"] == 0
            suggestions = client.get(
                "/articles/suggestions",
                params={"description": "Walmart-only toner cartridge cyan", "unit": "piece"},
                headers=acme_admin,
            )
            assert all(item["articleNumber"] != "WAL-PRIVATE-1" for item in suggestions.json())
            assert client.get("/articles", headers={**acme_admin, "X-Organization-Id": str(walmart.id)}).json()["total"] == client.get("/articles", headers=acme_admin).json()["total"]

            foreign_rules = [{"term": "private test item", "commodity_group_id": 11}]
            assert client.patch(
                "/organizations/me/settings", json={"category_rules": foreign_rules},
                headers=acme_requester,
            ).status_code == 403
            assert client.patch(
                "/organizations/me/settings",
                json={"organization_id": str(walmart.id), "category_rules": foreign_rules},
                headers=acme_admin,
            ).status_code == 422
            assert client.get(f"/organizations/{walmart.id}/settings", headers=acme_admin).status_code == 404
            assert client.get("/organizations/me/settings", headers=acme_admin).json() != client.get(
                "/organizations/me/settings", headers=walmart_admin
            ).json()
            assert client.get("/organizations/me/settings", headers=walmart_admin).json()["category_rules"] == original_settings.get("category_rules", [])
        finally:
            db.delete(article)
            db.delete(supplier)
            walmart.settings = original_settings
            db.commit()


def test_foreign_request_id_is_inaccessible_even_to_acme_admin():
    walmart_admin = _auth(_signin("admin@walmart.com"))
    acme_admin = _auth(_signin("admin@acme.com"))
    payload = {
        "requestorName": "Boundary test", "titleShortDescription": "Private request",
        "vendorName": "Private vendor", "commodityGroupId": 11, "totalCost": 10,
        "orderLines": [{"positionDescription": "Private item", "unitPrice": 10,
                        "amount": 1, "unit": "piece", "totalPrice": 10}],
    }
    created = client.post("/requests", json=payload, headers=walmart_admin)
    assert created.status_code == 201, created.text
    request_id = created.json()["id"]
    try:
        assert client.get(f"/requests/{request_id}", headers=acme_admin).status_code == 404
        assert client.get(f"/requests/{request_id}/activity", headers=acme_admin).status_code == 404
        assert client.get(f"/requests/{request_id}/document", headers=acme_admin).status_code == 404
        assert client.patch(
            f"/requests/{request_id}/approval", json={"decision": "approved"},
            headers=acme_admin,
        ).status_code == 404
        assert client.get(f"/requests/{request_id}", headers=walmart_admin).json()["approval_status"] == "pending"
    finally:
        assert client.delete(f"/requests/{request_id}", headers=walmart_admin).status_code == 204
