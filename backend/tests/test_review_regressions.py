import io
from pathlib import Path

import pytest
from pypdf import PdfReader, PdfWriter

from app.core.config import settings
from app.services.item_classification import classify_items
from app.services.pdf import extract_text
from tests.test_case_regressions import _payload
from tests.test_smoke import _auth, _signin, client

FIXTURES = Path(__file__).parents[2] / 'fixtures'


def test_closed_autoapproved_request_cannot_be_repriced():
    requester = _auth(_signin('requester@acme.com'))
    admin = _auth(_signin('admin@acme.com'))
    payload = _payload('Cable ties', group=11)
    created = client.post('/requests', json=payload, headers=requester)
    assert created.status_code == 201, created.text
    rid = created.json()['id']
    try:
        assert created.json()['approval_status'] == 'approved'
        assert client.patch(f'/requests/{rid}/status', json={'status': 'closed'}, headers=requester).status_code == 200
        edit = {**payload, 'title': 'Cable ties', 'totalCost': 2500,
                'orderLines': [{**payload['orderLines'][0], 'unitPrice': 2500, 'totalPrice': 2500}]}
        edit.pop('titleShortDescription')
        for actor in (requester, admin):
            assert client.put(f'/requests/{rid}', json=edit, headers=actor).status_code == 409
        saved = client.get(f'/requests/{rid}', headers=requester).json()
        assert (saved['status'], saved['approval_status'], saved['total_cost']) == ('closed', 'approved', 100)
        assert client.patch(f'/requests/{rid}/status', json={'status': 'open'}, headers=requester).status_code == 200
        changed = client.put(f'/requests/{rid}', json=edit, headers=requester)
        assert changed.status_code == 200, changed.text
        assert (changed.json()['status'], changed.json()['approval_status']) == ('open', 'pending')
    finally:
        client.delete(f'/requests/{rid}', headers=admin)


@pytest.mark.parametrize('query', ['Whiteboard', 'Strapping PP'])
def test_short_exact_catalog_description_is_found(query):
    token = _auth(_signin('requester@acme.com'))
    catalog = client.get('/articles', params={'search': query, 'limit': 100}, headers=token).json()['items']
    article = next(a for a in catalog if a['description'].startswith(query))
    response = client.get('/articles/suggestions', params={
        'description': '  ' + article['description'].upper() + '  ',
        'unit': article['unit'], 'supplier': article['supplier_name'],
    }, headers=token)
    assert response.status_code == 200, response.text
    assert any(a['id'] == article['id'] and a['match'] == 'exact' for a in response.json())
    wrong_unit = client.get('/articles/suggestions', params={
        'description': article['description'], 'unit': 'incompatible-unit',
    }, headers=token)
    assert wrong_unit.json() == []


def _mixed_pdf(scan_count=1):
    writer = PdfWriter()
    writer.append(PdfReader(FIXTURES / 'quotes' / 'Quote_1__Lio_Technologies_Inc__1x_MBA___2214703918.pdf'))
    for _ in range(scan_count):
        writer.append(PdfReader(FIXTURES / 'scanned-quote.pdf'))
    content = io.BytesIO()
    writer.write(content)
    return content.getvalue()


def test_mixed_pdf_preserves_text_and_scanned_page_in_order():
    text = extract_text(_mixed_pdf())
    assert 'Apple' in text
    assert 'Example Tools Ltd' in text
    assert text.index('Apple') < text.index('Example Tools Ltd')
    assert '[OCR page 2; verify against PDF]' in text


def test_mixed_pdf_ocr_failure_does_not_return_partial_document(monkeypatch):
    from app.services import pdf

    def unavailable(*args, **kwargs):
        raise OSError('OCR unavailable')

    monkeypatch.setattr(pdf, '_ocr_page', unavailable)
    with pytest.raises(ValueError, match='OCR failed'):
        extract_text(_mixed_pdf())


def test_mixed_pdf_respects_ocr_page_limit():
    with pytest.raises(ValueError, match='10-page OCR limit'):
        extract_text(_mixed_pdf(11))


@pytest.mark.parametrize('description', [
    'Cable tie installation tool', 'Toner cartridge recycling service',
    'Cable tie removal pliers', 'Toner cartridge storage cabinet',
    'Recycling service for toner cartridges', 'Cable tie installation training',
])
def test_related_services_and_tools_are_not_certain_product_matches(description, monkeypatch):
    monkeypatch.setattr(settings, 'openai_api_key', None)
    assert classify_items([description])[0]['kind'] == 'uncertain'


def test_related_service_stays_pending_without_classification(monkeypatch):
    monkeypatch.setattr(settings, 'openai_api_key', None)
    token = _auth(_signin('requester@acme.com'))
    response = client.post('/requests', json=_payload('Toner cartridge recycling service'), headers=token)
    assert response.status_code == 201, response.text
    try:
        assert response.json()['approval_status'] == 'pending'
        assert response.json()['classification']['review_required'] is True
    finally:
        client.delete(f"/requests/{response.json()['id']}", headers=_auth(_signin('admin@acme.com')))


@pytest.mark.parametrize(('description', 'expected'), [
    ('Nylon cable ties 200 mm', 'cable_tie'),
    ('Black zip ties, 100 pack', 'cable_tie'),
    ('UV resistant cable tie assortment', 'cable_tie'),
    ('HP 415A cyan toner', 'printer_toner'),
    ('Toner cartridge magenta', 'printer_toner'),
    ('HP LaserJet 415A Cyan CF411A cartridge', 'printer_toner'),
])
def test_explicit_products_still_classify_without_model(description, expected, monkeypatch):
    monkeypatch.setattr(settings, 'openai_api_key', None)
    assert classify_items([description])[0]['kind'] == expected
