import logging
import re
import uuid
from decimal import Decimal

from openai import OpenAI
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models import CommodityGroup, Organization
from app.schemas.extraction import ExtractedVendorData, ExtractionResponse, QuoteExtraction
from app.services.category_rules import matching_group_ids
from app.services.item_classification import classify_items

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = (
    "You are a data extraction and classification specialist. Always respond with "
    "valid JSON only, no markdown or code blocks. Extract all information accurately "
    "from business documents and classify into the appropriate commodity "
    "group based on the full context of the offer."
)


def _build_prompt(pdf_text: str, commodity_groups_list: str) -> str:
    return f"""You are a data extraction and classification specialist. Extract procurement/vendor offer information from the following US document text AND classify it into the appropriate commodity group.

Document Text:
{pdf_text}

Please extract the following information:
1. Title/Short Description (A brief summary of what this procurement is for - e.g., "Office Equipment Purchase", "Software Licenses", etc.)
2. Vendor Name (Vendor/Supplier/Seller/Company name)
3. Tax ID (Employer Identification Number/EIN/Federal Tax ID/TIN/VAT ID)
4. Department/Customer (recipient's department or bill-to organization; never a customer number)
5. Order Lines/Items (All line items with):
   - Position Description (Description/Product/Item/Article)
   - Unit Price (Unit Price/Price/Rate/Price per unit)
   - Amount (Qty/Quantity/Count)
   - Unit (Unit/UoM - e.g., "licenses", "pieces", "ea.", "items")
   - Total Price (Total/Ext. Price/Amount)
6. Total Cost (Grand Total/Total Cost/Total Offer Cost)
7. Commodity Group Classification - Select the SINGLE most appropriate commodity group from the list below
8. Recipient Company Name - The company receiving the quote, separately from its issuing vendor

Available Commodity Groups (ID | Category | Name):
{commodity_groups_list}

IMPORTANT INSTRUCTIONS:
- For the title, create a SHORT (2-5 words) description based on the main items being purchased
- Extract ALL line items, even if there are many
- For unit prices and totals, extract only the numeric value (remove currency symbols)
- For the unit field, write out abbreviations in plain English (e.g., "ea." -> "pieces", "lic." -> "licenses")
- Be careful with US and European number formatting (e.g., "1,438.00" and "1.438,00" = 1438.00)
- Use null for values absent from the document; never invent an amount, tax ID or total
- The vendor is the company ISSUING the quote (letterhead, sender, "prepared by", seller), not the recipient, bill-to customer or shipping address. If the issuer is unclear, use null.
- First identify the recipient and exclude that name from vendor candidates. When a logo is absent from selectable text, infer the issuer from its own "thank you for your inquiry at ..." sentence, sales-representative email domain or footer; use the trading brand if the legal entity is not visible in the text.
- Include only the priced positions in the selected offer. Exclude items explicitly marked alternative, optional or "U.P."; do not add them to the total. Keep a separately priced delivery or installation position when it is part of the selected offer.
- A line beneath an "Alternative:" heading or marked "(Alt.)" is unselected even when its price and quantity are printed. Never include it in orderLines unless the document explicitly selects it.
- Use the explicitly printed final Grand Total, Total (USD) or Total (gross), including applicable shipping, tax and fees. Do not mistake a page subtotal, carried-forward amount or a sum of alternatives for the final total.
- Distinguish product lines from alternatives, marketing prose, shipping, discounts and tax
- For classification, consider the PRIMARY nature and purpose of the items:
  * Branded office decor/furniture → Office Equipment (15)
  * Traditional promotional items (brochures, giveaways, banners) → Promotional Materials (43)
  * Software/IT products → Software (31) or IT Services (30)
  * If genuinely uncertain, set commodityGroupId to null

Return ONLY valid JSON in this exact format (no markdown, no code blocks):
{{
  "title": "Short description of procurement",
  "vendorName": "Company Name",
  "recipientCompanyName": "Buyer Company",
  "vatId": "12-3456789",
  "department": "Department Name",
  "orderLines": [
    {{
      "positionDescription": "Item description",
      "unitPrice": 150.00,
      "amount": 10,
      "unit": "licenses",
      "totalPrice": 1500.00
    }}
  ],
  "totalCost": 1500.00,
  "commodityGroupId": 15,
  "commodityGroupName": "Office Equipment"
}}"""


def _printed_grand_total(pdf_text: str) -> Decimal | None:
    totals = re.findall(
        r"(?im)^\s*(?:grand\s+total|total\s*\((?:usd|gross)\))\s+\$?\s*(\d[\d,]*\.\d{2})\s*$",
        pdf_text,
    )
    return Decimal(totals[-1].replace(",", "")) if totals else None


def _printed_items_total(pdf_text: str) -> Decimal | None:
    totals = re.findall(
        r"(?im)^\s*(?:items\s+subtotal\s*\(net\)|items\s*\(net\)|net\s+total|subtotal\s*\(net\)|subtotal)\s+\$?\s*(\d[\d,]*\.\d{2})\s*$",
        pdf_text,
    )
    return Decimal(totals[-1].replace(",", "")) if totals else None


def _printed_charges_total(pdf_text: str) -> Decimal:
    charges = Decimal("0")
    for line in pdf_text.splitlines():
        label = line.strip().casefold()
        if not (label.startswith(("shipping", "sales tax", "tax ", "plus sales tax"))
                or label == "tax" or "recycling fee" in label):
            continue
        if label.startswith("tax id"):
            continue
        amounts = re.findall(r"(?<!\w)\d[\d,]*\.\d{2}(?!\w)", line)
        if amounts:
            charges += Decimal(amounts[-1].replace(",", ""))
    return charges


def _unselected_alternative_prices(pdf_text: str) -> set[Decimal]:
    """Prices on numbered lines immediately following an explicit Alternative heading."""
    prices: set[Decimal] = set()
    lines = pdf_text.splitlines()
    for index, line in enumerate(lines):
        if not re.search(r"(?i)\balternative\s*:", line):
            continue
        for following in lines[index + 1:index + 5]:
            if not following.strip():
                continue
            if not re.match(r"^\s*\d+\s+\S", following):
                break
            amounts = re.findall(r"(?<!\w)\d[\d,]*\.\d{2}(?!\w)", following)
            if len(amounts) >= 3:
                prices.add(Decimal(amounts[-1].replace(",", "")))
            break
    return prices


def _prepared_by_company(pdf_text: str) -> str | None:
    """Read a seller from a labelled Prepared by column when it also contains a tax ID."""
    lines = pdf_text.splitlines()
    for index, line in enumerate(lines):
        match = re.search(r"(?i)prepared by\s*:", line)
        if not match:
            continue
        column = match.start()
        cells = [following[column:].strip() for following in lines[index + 1:index + 7] if following[column:].strip()]
        if len(cells) >= 3 and re.search(r"(?i)tax\s*id\s*:", cells[2]):
            return cells[1]
    return None


def extract_vendor_data(pdf_text: str, db: Session, organization_id: uuid.UUID) -> ExtractionResponse:
    """Extract vendor data and classify commodity group in one AI call.

    Port of ``src/actions/extraction.ts`` — same prompt, model and validation.
    """
    if not pdf_text or not pdf_text.strip():
        return ExtractionResponse(success=False, error="No text provided for extraction")

    if not settings.openai_api_key:
        return ExtractionResponse(
            success=False,
            error="OPENAI_API_KEY is not configured on the server",
        )

    commodity_groups = list(
        db.scalars(select(CommodityGroup).order_by(CommodityGroup.id))
    )
    if not commodity_groups:
        return ExtractionResponse(
            success=False,
            error="Failed to load commodity groups for classification",
        )

    # Give the model the full commodity catalog so it has complete context to
    # pick the most accurate match in a single pass.
    commodity_groups_list = "\n".join(
        f"{g.id} | {g.category} | {g.name}" for g in commodity_groups
    )

    try:
        client = OpenAI(api_key=settings.openai_api_key, timeout=40, max_retries=1)
        response = client.chat.completions.parse(
            model=settings.openai_model,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": _build_prompt(pdf_text, commodity_groups_list)},
            ],
            max_completion_tokens=4000,
            temperature=0.3,
            response_format=QuoteExtraction,
        )
        parsed = response.choices[0].message.parsed
        if parsed is None:
            return ExtractionResponse(success=False, error="The quote could not be extracted")
        raw = parsed.model_dump()
        extracted = ExtractedVendorData.model_validate(raw)
    except Exception:  # noqa: BLE001
        logger.exception("Error extracting vendor data")
        return ExtractionResponse(success=False, error="Could not extract this quote. Please enter its details manually.")

    recipient_name = raw.get("recipientCompanyName")
    vendor_is_recipient = (
        isinstance(recipient_name, str)
        and isinstance(extracted.vendor_name, str)
        and bool(recipient_name.strip())
        and bool(extracted.vendor_name.strip())
        and re.sub(r"[^a-z0-9]", "", recipient_name.casefold())
        == re.sub(r"[^a-z0-9]", "", extracted.vendor_name.casefold())
    )
    if vendor_is_recipient:
        extracted.vendor_name = None

    # Check which fields are missing or empty (same rules as the original).
    missing_fields: list[str] = []
    if not (extracted.title or "").strip():
        missing_fields.append("Title/Short Description")
    if not (extracted.vendor_name or "").strip():
        missing_fields.append("Vendor Name")
    if not (extracted.vat_id or "").strip():
        missing_fields.append("Tax ID")
    if not (extracted.department or "").strip():
        missing_fields.append("Department")
    if not extracted.order_lines:
        missing_fields.append("Order Lines")
    if extracted.total_cost is None:
        missing_fields.append("Total Cost")
    if not extracted.commodity_group_id or not extracted.commodity_group_name:
        missing_fields.append("Commodity Group")

    warnings: list[str] = []
    if vendor_is_recipient:
        warnings.append("The AI selected the quote recipient as vendor; verify the supplier in the PDF.")
    if not extracted.vendor_name:
        prepared_by = _prepared_by_company(pdf_text)
        if prepared_by:
            extracted.vendor_name = prepared_by
            missing_fields = [field for field in missing_fields if field != "Vendor Name"]
            warnings.append(f"Used the seller from the PDF's Prepared by column ({prepared_by}); verify it before submitting.")
    issuer_hint = re.search(r"(?i)thank you(?: very much)? for your inquiry at\s+([a-z][a-z0-9-]+)", pdf_text)
    if issuer_hint and extracted.vendor_name and issuer_hint.group(1).casefold() not in extracted.vendor_name.casefold():
        brand = issuer_hint.group(1)
        sales_email = re.search(r"(?i)\b[\w.+-]+@" + re.escape(brand) + r"\.[a-z]{2,}\b", pdf_text)
        document_header = "\n".join(pdf_text.splitlines()[:12]).casefold()
        if sales_email and extracted.vendor_name.casefold() in document_header:
            extracted.vendor_name = brand
            warnings.append(f"The seller brand {brand} appears in the quote and sales email; verify its legal supplier name in the PDF.")
        else:
            warnings.append(f"The quote names {brand} as its seller brand; verify the extracted vendor.")

    footer = re.search(r"(?im)^\[OCR footer evidence; verify against PDF\]\n([^\n]+)\n([^\n]+)", pdf_text)
    if footer:
        legal_name = footer.group(1).split("|")[0].strip()
        tax_id = re.search(r"(?i)\b(?:EIN|Tax\s*ID)\s*:\s*(\d{2}[- ]\d{7})\b", footer.group(2))
        if tax_id:
            extracted_digits = re.sub(r"\D", "", extracted.vat_id or "")
            printed_digits = re.sub(r"\D", "", tax_id.group(1))
            selectable = pdf_text.split("[OCR footer evidence;")[0]
            printed_elsewhere = bool(extracted_digits and re.search(
                rf"\b{extracted_digits[:2]}[-\s]?{extracted_digits[2:]}\b", selectable
            ))
            if not extracted.vat_id or (extracted_digits != printed_digits and not printed_elsewhere):
                extracted.vat_id = tax_id.group(1)
                missing_fields = [field for field in missing_fields if field != "Tax ID"]
                warnings.append("Tax ID was read from the image footer by OCR; verify every digit in the PDF.")
            elif extracted_digits != printed_digits:
                warnings.append("Two different tax IDs appear in the quote; verify which belongs to the seller.")
        if re.search(r"(?i)\b(?:Inc\.?|LLC|Ltd\.?|GmbH)\b", legal_name):
            brand_name = issuer_hint.group(1).casefold() if issuer_hint else ""
            if not extracted.vendor_name or extracted.vendor_name.casefold() == brand_name:
                extracted.vendor_name = legal_name
                missing_fields = [field for field in missing_fields if field != "Vendor Name"]
                warnings.append("The legal seller name was read from the image footer by OCR; verify it in the PDF.")
            elif legal_name.casefold() != extracted.vendor_name.casefold():
                warnings.append(f"OCR footer names {legal_name} as a possible legal seller; verify the supplier.")
    if "[OCR page " in pdf_text:
        warnings.append("This PDF needed page OCR; verify all extracted fields against the image.")

    items_total = _printed_items_total(pdf_text)
    printed_total = _printed_grand_total(pdf_text)
    if items_total is not None and extracted.order_lines and all(line.total_price is not None for line in extracted.order_lines):
        lines_total = sum(Decimal(str(line.total_price)) for line in extracted.order_lines)
        excess = lines_total - items_total
        if excess > Decimal("0.02") and excess in _unselected_alternative_prices(pdf_text):
            possible = [line for line in extracted.order_lines if Decimal(str(line.total_price)) == excess]
            if len(possible) == 1:
                extracted.order_lines.remove(possible[0])
                warnings.append(f"Excluded an explicitly marked, unselected alternative priced at ${excess:.2f}; verify the selected lines in the PDF.")
    for index, line in enumerate(extracted.order_lines, start=1):
        if line.amount is None or line.unit_price is None or line.total_price is None:
            warnings.append(f"Line {index}: missing quantity or price; check the PDF.")
        elif abs(Decimal(str(line.amount)) * Decimal(str(line.unit_price)) - Decimal(str(line.total_price))) > Decimal("0.02"):
            warnings.append(f"Line {index}: quantity × unit price differs from quoted line total; check discount or extraction.")
    if extracted.total_cost is not None and extracted.order_lines and all(line.total_price is not None for line in extracted.order_lines):
        lines_total = sum(Decimal(str(line.total_price)) for line in extracted.order_lines)
        if abs(lines_total - Decimal(str(extracted.total_cost))) > Decimal("0.02"):
            charges = _printed_charges_total(pdf_text)
            if (items_total is not None and printed_total is not None
                    and abs(lines_total - items_total) <= Decimal("0.02")
                    and charges > 0 and abs(items_total + charges - printed_total) <= Decimal("0.02")):
                warnings.append(f"The printed total includes ${charges:.2f} in shipping, tax or fees outside the selected lines; keep the document total.")
            else:
                warnings.append("Quote total differs from the sum of lines; review shipping, discounts and tax.")
        if items_total is not None and lines_total > items_total + Decimal("0.02"):
            warnings.append("Selected lines exceed the printed items subtotal; remove unselected alternatives before creating a request.")

    if printed_total is not None:
        if extracted.total_cost is None:
            extracted.total_cost = float(printed_total)
            missing_fields = [field for field in missing_fields if field != "Total Cost"]
        elif abs(printed_total - Decimal(str(extracted.total_cost))) > Decimal("0.02"):
            warnings.append(f"Model total {extracted.total_cost:.2f} differs from the printed grand total ({printed_total:.2f}); the printed amount was used. Review the quote.")
            extracted.total_cost = float(printed_total)

    org = db.get(Organization, organization_id)
    kinds = classify_items([line.position_description or "" for line in extracted.order_lines], extracted.title or "") if org and any(r.get("item_kind") for r in (org.settings or {}).get("category_rules", [])) else []
    required_groups = matching_group_ids(org.settings or {}, extracted.title, extracted.order_lines,
                                         extracted.total_cost, [item["kind"] for item in kinds]) if org else set()
    if any(item["kind"] == "uncertain" for item in kinds):
        warnings.append("One or more article types could not be verified; buyer review is required.")
    if len(required_groups) == 1:
        group = next((g for g in commodity_groups if g.id in required_groups), None)
        if group:
            extracted.commodity_group_id = group.id
            extracted.commodity_group_name = group.name
    elif len(required_groups) > 1:
        extracted.commodity_group_id = None
        extracted.commodity_group_name = None
        warnings.append("This quote spans multiple required booking groups; create separate requests.")

    # Validate that the returned commodity group id actually exists.
    if extracted.commodity_group_id is not None:
        valid_groups = {g.id: g for g in commodity_groups}
        if extracted.commodity_group_id not in valid_groups:
            logger.warning(
                "Invalid commodity group ID returned: %s", extracted.commodity_group_id
            )
            extracted.commodity_group_id = None
            extracted.commodity_group_name = None
            if "Commodity Group" not in missing_fields:
                missing_fields.append("Commodity Group")
        else:
            extracted.commodity_group_name = valid_groups[extracted.commodity_group_id].name

    if extracted.commodity_group_id is not None and extracted.commodity_group_name:
        missing_fields = [field for field in missing_fields if field != "Commodity Group"]
    elif "Commodity Group" not in missing_fields:
        missing_fields.append("Commodity Group")

    return ExtractionResponse(
        success=True,
        data=extracted,
        missing_fields=missing_fields or None,
        warnings=warnings,
    )
