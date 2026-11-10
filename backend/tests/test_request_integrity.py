from tests.test_smoke import _auth, _signin, client


def _payload():
    return {
        "requestorName": "Case study tester",
        "titleShortDescription": "Cable ties",
        "vendorName": "Chair Supplier",
        "commodityGroupId": 11,
        "totalCost": 2500,
        "orderLines": [{
            "positionDescription": "Cable ties", "unitPrice": 2500,
            "amount": 1, "unit": "piece", "totalPrice": 2500,
        }],
    }


def test_request_money_and_lines_reject_invalid_values():
    token = _auth(_signin("requester@acme.com"))
    for field, value in [("totalCost", -1), ("totalCost", "NaN"), ("totalCost", "Infinity"), ("totalCost", 1999.999)]:
        payload = _payload()
        payload[field] = value
        assert client.post("/requests", json=payload, headers=token).status_code == 422
    for field, value in [("amount", 0), ("amount", -1), ("amount", 1.00001), ("unitPrice", -1), ("unitPrice", 1999.999), ("totalPrice", "NaN")]:
        payload = _payload()
        payload["orderLines"][0][field] = value
        assert client.post("/requests", json=payload, headers=token).status_code == 422
    payload = _payload()
    payload["orderLines"] = []
    assert client.post("/requests", json=payload, headers=token).status_code == 422


def test_request_editor_and_approval_integrity():
    requester = _auth(_signin("requester@acme.com"))
    buyer = _auth(_signin("buyer@acme.com"))
    admin = _auth(_signin("admin@acme.com"))
    payload = _payload()
    response = client.post("/requests", json=payload, headers=requester)
    assert response.status_code == 201, response.text
    request_id = response.json()["id"]
    edit = {**payload, "title": "Changed chairs"}
    edit.pop("titleShortDescription")
    try:
        assert client.put(f"/requests/{request_id}", json=edit, headers=buyer).status_code == 403
        assert client.delete(f"/requests/{request_id}", headers=buyer).status_code == 403
        assert client.patch(
            f"/requests/{request_id}/status", json={"status": "closed"},
            headers=buyer,
        ).status_code == 409
        assert client.put(
            f"/requests/{request_id}/document",
            files={"file": ("offer.pdf", b"%PDF-1.4\n", "application/pdf")},
            headers=buyer,
        ).status_code == 403
        assert client.put(
            f"/requests/{request_id}/document",
            files={"file": ("offer.pdf", b"not a pdf", "application/pdf")},
            headers=requester,
        ).status_code == 422
        assert client.put(
            f"/requests/{request_id}/document",
            files={"file": ("offer.pdf", b"%PDF-1.4\n" + b"x" * (10 * 1024 * 1024), "application/pdf")},
            headers=requester,
        ).status_code == 413
        assert client.patch(
            f"/requests/{request_id}/approval", json={"decision": "approved"},
            headers=buyer,
        ).status_code == 200
        assert client.put(f"/requests/{request_id}", json=edit, headers=requester).status_code == 409
        assert client.put(f"/requests/{request_id}", json=edit, headers=admin).status_code == 409
        assert client.put(
            f"/requests/{request_id}/document",
            files={"file": ("offer.pdf", b"%PDF-1.4\n", "application/pdf")},
            headers=requester,
        ).status_code == 409
        assert client.delete(f"/requests/{request_id}", headers=requester).status_code == 409
        assert client.get(f"/requests/{request_id}", headers=requester).json()["total_cost"] == 2500
    finally:
        assert client.delete(f"/requests/{request_id}", headers=admin).status_code == 204


def test_pdf_page_limit_is_reported_to_user():
    import io
    from pypdf import PdfWriter

    writer = PdfWriter()
    for _ in range(51):
        writer.add_blank_page(width=200, height=200)
    content = io.BytesIO()
    writer.write(content)
    result = client.post(
        "/pdf/parse",
        files={"file": ("many-pages.pdf", content.getvalue(), "application/pdf")},
        headers=_auth(_signin("requester@acme.com")),
    )
    assert result.status_code == 413
    assert "50-page limit" in result.json()["detail"]


def test_image_only_pdf_uses_ocr_and_marks_uncertainty():
    from pathlib import Path

    sample = Path(__file__).parents[2] / "fixtures" / "scanned-quote.pdf"
    result = client.post(
        "/pdf/parse",
        files={"file": ("scanned-quote.pdf", sample.read_bytes(), "application/pdf")},
        headers=_auth(_signin("requester@acme.com")),
    )
    assert result.status_code == 200, result.text
    assert "[OCR page 1; verify against PDF]" in result.json()["text"]
    assert "Example Tools Ltd" in result.json()["text"]
    assert "12-3456789" in result.json()["text"]


def test_acme_auto_approval_threshold_and_edit_recheck():
    requester = _auth(_signin("requester@acme.com"))
    buyer = _auth(_signin("buyer@acme.com"))
    admin = _auth(_signin("admin@acme.com"))
    payload = _payload()
    payload["totalCost"] = 1999.99
    payload["orderLines"][0]["unitPrice"] = 1999.99
    payload["orderLines"][0]["totalPrice"] = 1999.99
    created = client.post("/requests", json=payload, headers=requester)
    assert created.status_code == 201, created.text
    request_id = created.json()["id"]
    try:
        assert created.json()["approval_status"] == "approved"
        assert created.json()["approved_by"] is None
        assert created.json()["approved_at"] is not None
        assert "auto_approved" in [a["action"] for a in client.get(
            f"/requests/{request_id}/activity", headers=requester,
        ).json()]
        assert client.put(
            f"/requests/{request_id}/document",
            files={"file": ("offer.pdf", b"%PDF-1.4\n", "application/pdf")},
            headers=requester,
        ).status_code == 204
        assert client.put(
            f"/requests/{request_id}/document",
            files={"file": ("replacement.pdf", b"%PDF-1.4\n", "application/pdf")},
            headers=requester,
        ).status_code == 409

        edit = {**payload, "title": "Office chairs"}
        edit.pop("titleShortDescription")
        edit["totalCost"] = 2000
        edit["orderLines"] = [{**payload["orderLines"][0], "unitPrice": 2000, "totalPrice": 2000}]
        changed = client.put(f"/requests/{request_id}", json=edit, headers=requester)
        assert changed.status_code == 200, changed.text
        assert changed.json()["approval_status"] == "pending"
        assert changed.json()["approved_at"] is None
        assert "approval_required" in [a["action"] for a in client.get(
            f"/requests/{request_id}/activity", headers=requester,
        ).json()]
        decided = client.patch(
            f"/requests/{request_id}/approval", json={"decision": "approved"}, headers=buyer,
        )
        assert decided.status_code == 200
        assert decided.json()["approved_by"] is not None
        assert client.put(f"/requests/{request_id}", json=edit, headers=requester).status_code == 409
    finally:
        assert client.delete(f"/requests/{request_id}", headers=admin).status_code == 204


def test_auto_approval_is_configured_per_tenant():
    acme_admin = _auth(_signin("admin@acme.com"))
    walmart_admin = _auth(_signin("admin@walmart.com"))
    assert client.get("/organizations/me/settings", headers=acme_admin).json()["auto_approve_below"] == 2000
    assert client.get("/organizations/me/settings", headers=walmart_admin).json()["auto_approve_below"] is None
    assert client.patch(
        "/organizations/me/settings", json={"auto_approve_below": 0}, headers=acme_admin,
    ).status_code == 422
    assert client.patch(
        "/organizations/me/settings", json={"auto_approve_below": 1999.999}, headers=acme_admin,
    ).status_code == 422
    payload = _payload()
    payload["totalCost"] = 100
    payload["orderLines"][0]["unitPrice"] = 100
    payload["orderLines"][0]["totalPrice"] = 100
    created = client.post("/requests", json=payload, headers=walmart_admin)
    assert created.status_code == 201
    assert created.json()["approval_status"] == "pending"
    assert client.delete(f"/requests/{created.json()['id']}", headers=walmart_admin).status_code == 204


def test_auto_approval_cannot_use_understated_total_or_line_price():
    requester = _auth(_signin("requester@acme.com"))
    admin = _auth(_signin("admin@acme.com"))
    payload = _payload()
    payload["totalCost"] = 1999
    for line_price, line_total in [(2500, 2500), (2500, 100)]:
        payload["orderLines"][0]["unitPrice"] = line_price
        payload["orderLines"][0]["totalPrice"] = line_total
        created = client.post("/requests", json=payload, headers=requester)
        assert created.status_code == 201, created.text
        try:
            assert created.json()["approval_status"] == "pending"
        finally:
            assert client.delete(f"/requests/{created.json()['id']}", headers=admin).status_code == 204
