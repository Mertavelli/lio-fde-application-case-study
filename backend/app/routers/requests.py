import uuid
from datetime import datetime, timezone
from decimal import Decimal
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Response, UploadFile, status
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.core.deps import get_current_membership, require_buyer_or_admin
from app.core.config import settings
from app.db.session import get_db
from app.models import (
    Article,
    CommodityGroup,
    OrderLine,
    Organization,
    OrganizationMember,
    ProcurementRequest,
    RequestActivity,
    RequestDocument,
)
from app.schemas.requests import (
    ApprovalDecision,
    ClassificationReview,
    RequestActivityOut,
    RequestCreate,
    RequestOut,
    RequestUpdate,
    StatusUpdate,
)
from app.schemas.organizations import OrganizationSettings
from app.services.category_rules import matching_group_ids
from app.services.item_classification import classify_items
from app.routers.articles import _unit

router = APIRouter(prefix="/requests", tags=["requests"])

MAX_PDF_BYTES = 10 * 1024 * 1024

# Human-readable labels for the fields we track in the activity log.
_FIELD_LABELS = {
    "requestor_name": "requestor",
    "title": "title",
    "vendor_name": "vendor",
    "vat_id": "tax ID",
    "commodity_group_id": "commodity group",
    "total_cost": "total cost",
    "department": "department",
    "order_lines": "order lines",
}


def _load_request_options():
    return (
        selectinload(ProcurementRequest.order_lines),
        selectinload(ProcurementRequest.commodity_group),
    )


def _get_org_request(
    db: Session, request_id: uuid.UUID, organization_id: uuid.UUID, *, lock: bool = False
) -> ProcurementRequest:
    query = (
        select(ProcurementRequest)
        .options(*_load_request_options())
        # Refresh already-loaded relationships (e.g. after an update commit).
        .execution_options(populate_existing=True)
        .where(
            ProcurementRequest.id == request_id,
            ProcurementRequest.organization_id == organization_id,
        )
    )
    if lock:
        query = query.with_for_update()
    request = db.scalar(query)
    if request is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Request not found or you do not have permission to access it",
        )
    return request


def _require_request_editor(request: ProcurementRequest, membership: OrganizationMember) -> None:
    if membership.role != "admin" and request.created_by != membership.user_id:
        raise HTTPException(status_code=403, detail="Only the creator or an admin can change this request")


def _is_auto_approved(request: ProcurementRequest) -> bool:
    return request.approval_status == "approved" and request.approval_source == "automatic"


def _apply_auto_approval(db: Session, request: ProcurementRequest, actor_id: uuid.UUID) -> None:
    org = db.get(Organization, request.organization_id)
    threshold = OrganizationSettings(**(org.settings or {})).auto_approve_below if org else None
    if (request.classification or {}).get("review_required"):
        if _is_auto_approved(request):
            request.approval_status = "pending"
            request.approved_at = None
            request.approval_source = None
            _log_activity(db, request.id, actor_id, "approval_required",
                          "Automatic approval removed; article review required")
        return
    total = Decimal(str(request.total_cost))
    lines_total = sum((Decimal(str(line.total_price)) for line in request.order_lines), Decimal("0"))
    gross_total = sum((Decimal(str(line.amount)) * Decimal(str(line.unit_price)) for line in request.order_lines), Decimal("0"))
    evaluated_total = max(total, lines_total, gross_total)
    eligible = (threshold is not None and lines_total <= total + Decimal("0.02")
                and evaluated_total < Decimal(str(threshold)))
    if eligible and request.approval_status == "pending":
        request.approval_status = "approved"
        request.approved_by = None
        request.approved_at = datetime.now(timezone.utc)
        request.approval_source = "automatic"
        _log_activity(db, request.id, actor_id, "auto_approved", "Request automatically approved", {"threshold": threshold, "total_cost": float(total), "evaluated_total": float(evaluated_total)})
    elif not eligible and _is_auto_approved(request):
        request.approval_status = "pending"
        request.approved_at = None
        request.approval_source = None
        _log_activity(db, request.id, actor_id, "approval_required", "Automatic approval removed; buyer approval required", {"threshold": threshold, "total_cost": float(total), "evaluated_total": float(evaluated_total)})


def _validate_commodity_group(db: Session, commodity_group_id: int) -> None:
    if db.get(CommodityGroup, commodity_group_id) is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Unknown commodity group id: {commodity_group_id}",
        )


def _enforce_required_fields(
    db: Session, organization_id: uuid.UUID, payload: RequestCreate | RequestUpdate,
    known_classification: dict | None = None,
) -> dict:
    """Enforce the org's configurable required fields (see OrganizationSettings)."""
    org = db.get(Organization, organization_id)
    required = (org.settings or {}).get("required_fields", []) if org else []
    missing = [field for field in required if not getattr(payload, field, None)]
    if missing:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Missing fields required by your organization: {', '.join(missing)}",
        )
    rules = (org.settings or {}).get("category_rules", []) if org else []
    kinds = (known_classification.get("items", []) if known_classification is not None else
             classify_items([line.position_description for line in payload.order_lines], payload.title)) if any(r.get("item_kind") for r in rules) else []
    groups = matching_group_ids(org.settings or {}, payload.title, payload.order_lines, payload.total_cost,
                                [item["kind"] for item in kinds]) if org else set()
    if len(groups) > 1:
        raise HTTPException(status_code=422, detail="Items require different booking groups; create separate requests")
    if groups and payload.commodity_group_id not in groups:
        group = db.get(CommodityGroup, next(iter(groups)))
        raise HTTPException(status_code=422, detail=f"This organization requires commodity group {group.name} for these items")
    if known_classification is not None:
        return known_classification
    return {"version": 1, "model": settings.openai_model if settings.openai_api_key else None,
            "items": kinds, "review_required": any(item["kind"] == "uncertain" for item in kinds),
            "reason": "Article type could not be verified" if any(item["kind"] == "uncertain" for item in kinds) else None}


def _validate_catalog_prices(db: Session, organization_id: uuid.UUID, payload: RequestCreate | RequestUpdate) -> None:
    for line in payload.order_lines:
        if line.catalog_article_id is None:
            continue
        article = db.get(Article, line.catalog_article_id)
        if (article is None or article.organization_id != organization_id or article.currency != "USD"
                or article.quantity != 1 or _unit(article.unit) != _unit(line.unit)
                or " ".join(article.description.casefold().split()) != " ".join(line.position_description.casefold().split())
                or Decimal(str(article.unit_price)) != Decimal(str(line.unit_price))
                or abs(Decimal(str(line.total_price)) - Decimal(str(line.amount)) * Decimal(str(line.unit_price))) > Decimal("0.01")
                or article.supplier is None
                or article.supplier.name.casefold().strip() != payload.vendor_name.casefold().strip()):
            raise HTTPException(status_code=422, detail="Catalog article does not match this request line")


def _log_activity(
    db: Session,
    request_id: uuid.UUID,
    actor_id: uuid.UUID,
    action: str,
    summary: str,
    detail: dict | None = None,
) -> None:
    db.add(
        RequestActivity(
            request_id=request_id,
            actor_id=actor_id,
            action=action,
            summary=summary,
            detail=detail,
        )
    )


def _order_lines_signature(lines) -> list[tuple]:
    return [
        (
            line.position_description,
            float(line.unit_price),
            float(line.amount),
            line.unit,
            float(line.total_price),
        )
        for line in lines
    ]


def _changed_fields(request: ProcurementRequest, payload: RequestUpdate) -> list[str]:
    """Return the field keys whose value differs between the request and payload."""
    changed: list[str] = []
    if request.requestor_name != payload.requestor_name:
        changed.append("requestor_name")
    if request.title != payload.title:
        changed.append("title")
    if request.vendor_name != payload.vendor_name:
        changed.append("vendor_name")
    if (request.vat_id or "") != (payload.vat_id or ""):
        changed.append("vat_id")
    if request.commodity_group_id != payload.commodity_group_id:
        changed.append("commodity_group_id")
    if float(request.total_cost or 0) != float(payload.total_cost or 0):
        changed.append("total_cost")
    if (request.department or "") != (payload.department or ""):
        changed.append("department")
    old_lines = _order_lines_signature(
        sorted(request.order_lines, key=lambda line: line.line_order)
    )
    new_lines = _order_lines_signature(payload.order_lines)
    if old_lines != new_lines:
        changed.append("order_lines")
    return changed


@router.get("", response_model=list[RequestOut])
def list_requests(
    membership: OrganizationMember = Depends(get_current_membership),
    db: Session = Depends(get_db),
) -> list[RequestOut]:
    requests = db.scalars(
        select(ProcurementRequest)
        .options(*_load_request_options())
        .where(ProcurementRequest.organization_id == membership.organization_id)
        .order_by(ProcurementRequest.created_at.desc())
    )
    return [RequestOut.model_validate(r) for r in requests]


@router.get("/{request_id}", response_model=RequestOut)
def get_request(
    request_id: uuid.UUID,
    membership: OrganizationMember = Depends(get_current_membership),
    db: Session = Depends(get_db),
) -> RequestOut:
    return RequestOut.model_validate(
        _get_org_request(db, request_id, membership.organization_id)
    )


@router.post("", response_model=RequestOut, status_code=status.HTTP_201_CREATED)
def create_request(
    payload: RequestCreate,
    membership: OrganizationMember = Depends(get_current_membership),
    db: Session = Depends(get_db),
) -> RequestOut:
    _validate_commodity_group(db, payload.commodity_group_id)
    classification = _enforce_required_fields(db, membership.organization_id, payload)
    _validate_catalog_prices(db, membership.organization_id, payload)

    # Vendor fields are already cleaned up during extraction, so we can persist
    # them directly here.
    request = ProcurementRequest(
        user_id=membership.user_id,
        requestor_name=payload.requestor_name,
        title=payload.title,
        vendor_name=payload.vendor_name,
        vat_id=payload.vat_id,
        commodity_group_id=payload.commodity_group_id,
        total_cost=payload.total_cost,
        department=payload.department,
        organization_id=membership.organization_id,
        created_by=membership.user_id,
        status="open",
        classification=classification,
    )
    request.order_lines = [
        OrderLine(
            position_description=line.position_description,
            unit_price=line.unit_price,
            amount=line.amount,
            unit=line.unit,
            total_price=line.total_price,
            line_order=index + 1,
        )
        for index, line in enumerate(payload.order_lines)
    ]
    db.add(request)
    db.flush()
    catalog = [{"line": index + 1, "article_id": str(line.catalog_article_id),
                "unit_price": line.unit_price} for index, line in enumerate(payload.order_lines)
               if line.catalog_article_id]
    _log_activity(db, request.id, membership.user_id, "created", "Request created",
                  {"catalog_prices": catalog} if catalog else None)
    _apply_auto_approval(db, request, membership.user_id)
    db.commit()

    request = _get_org_request(db, request.id, membership.organization_id)
    return RequestOut.model_validate(request)


@router.put("/{request_id}", response_model=RequestOut)
def update_request(
    request_id: uuid.UUID,
    payload: RequestUpdate,
    membership: OrganizationMember = Depends(get_current_membership),
    db: Session = Depends(get_db),
) -> RequestOut:
    before = _get_org_request(db, request_id, membership.organization_id)
    _require_request_editor(before, membership)
    if before.status == "closed":
        raise HTTPException(status_code=409, detail="A closed request cannot be edited; reopen it first")
    if before.approval_status != "pending" and not _is_auto_approved(before):
        raise HTTPException(status_code=409, detail="A decided request cannot be edited; create a new request")
    old_updated_at = before.updated_at
    unchanged_items = (before.title == payload.title and
                       [line.position_description for line in before.order_lines] ==
                       [line.position_description for line in payload.order_lines])
    _validate_commodity_group(db, payload.commodity_group_id)
    classification = _enforce_required_fields(db, membership.organization_id, payload,
                                               before.classification if unchanged_items else None)
    _validate_catalog_prices(db, membership.organization_id, payload)
    request = _get_org_request(db, request_id, membership.organization_id, lock=True)
    if request.updated_at != old_updated_at:
        raise HTTPException(status_code=409, detail="Request changed while it was being edited; reload and try again")
    _require_request_editor(request, membership)
    if request.status == "closed":
        raise HTTPException(status_code=409, detail="A closed request cannot be edited; reopen it first")
    if request.approval_status != "pending" and not _is_auto_approved(request):
        raise HTTPException(status_code=409, detail="A decided request cannot be edited; create a new request")

    changed = _changed_fields(request, payload)

    request.requestor_name = payload.requestor_name
    request.title = payload.title
    request.vendor_name = payload.vendor_name
    request.vat_id = payload.vat_id
    request.commodity_group_id = payload.commodity_group_id
    request.total_cost = payload.total_cost
    request.department = payload.department
    request.classification = classification
    if classification["review_required"] and request.status != "open":
        _log_activity(db, request.id, membership.user_id, "status_changed",
                      "Status returned to open for article review",
                      {"from": request.status, "to": "open"})
        request.status = "open"

    # Replace order lines (same semantics as the original delete-and-insert).
    request.order_lines = [
        OrderLine(
            position_description=line.position_description,
            unit_price=line.unit_price,
            amount=line.amount,
            unit=line.unit,
            total_price=line.total_price,
            line_order=index + 1,
        )
        for index, line in enumerate(payload.order_lines)
    ]

    if changed:
        labels = [_FIELD_LABELS.get(f, f) for f in changed]
        _log_activity(
            db,
            request.id,
            membership.user_id,
            "updated",
            f"Updated {', '.join(labels)}",
            {"changed_fields": changed, "catalog_prices": [
                {"line": index + 1, "article_id": str(line.catalog_article_id),
                 "unit_price": line.unit_price} for index, line in enumerate(payload.order_lines)
                if line.catalog_article_id]},
        )
        _apply_auto_approval(db, request, membership.user_id)
    db.commit()

    request = _get_org_request(db, request_id, membership.organization_id)
    return RequestOut.model_validate(request)


@router.patch("/{request_id}/status", response_model=RequestOut)
def update_status(
    request_id: uuid.UUID,
    payload: StatusUpdate,
    membership: OrganizationMember = Depends(get_current_membership),
    db: Session = Depends(get_db),
) -> RequestOut:
    request = _get_org_request(db, request_id, membership.organization_id, lock=True)
    if membership.role not in ("admin", "buyer"):
        _require_request_editor(request, membership)
    if payload.status == "closed" and request.approval_status == "pending":
        raise HTTPException(status_code=409, detail="A pending request cannot be closed")
    if (request.classification or {}).get("review_required") and payload.status != "open":
        raise HTTPException(status_code=409, detail="Article classification needs buyer review")

    if request.status != payload.status:
        _log_activity(
            db,
            request.id,
            membership.user_id,
            "status_changed",
            f"Status changed from {request.status} to {payload.status}",
            {"from": request.status, "to": payload.status},
        )
        request.status = payload.status
        db.commit()

    request = _get_org_request(db, request_id, membership.organization_id)
    return RequestOut.model_validate(request)


@router.patch("/{request_id}/approval", response_model=RequestOut)
def decide_approval(
    request_id: uuid.UUID,
    payload: ApprovalDecision,
    membership: OrganizationMember = Depends(require_buyer_or_admin),
    db: Session = Depends(get_db),
) -> RequestOut:
    """Approve or reject a pending request (buyers and admins only)."""
    request = _get_org_request(db, request_id, membership.organization_id, lock=True)

    if request.approval_status != "pending":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Request has already been {request.approval_status}.",
        )
    if (request.classification or {}).get("review_required") and payload.decision == "approved":
        raise HTTPException(status_code=409, detail="Article classification needs buyer review")

    request.approval_status = payload.decision
    request.approved_by = membership.user_id
    request.approved_at = datetime.now(timezone.utc)
    request.approval_source = "manual" if payload.decision == "approved" else None
    _log_activity(
        db,
        request.id,
        membership.user_id,
        payload.decision,
        f"Request {payload.decision}",
    )
    db.commit()

    request = _get_org_request(db, request_id, membership.organization_id)
    return RequestOut.model_validate(request)


@router.post("/{request_id}/classification-review", response_model=RequestOut)
def review_classification(
    request_id: uuid.UUID,
    payload: ClassificationReview,
    membership: OrganizationMember = Depends(require_buyer_or_admin),
    db: Session = Depends(get_db),
) -> RequestOut:
    request = _get_org_request(db, request_id, membership.organization_id, lock=True)
    if request.approval_status not in ("pending", "approved") or (request.approval_status == "approved" and not _is_auto_approved(request)):
        raise HTTPException(status_code=409, detail="Request has already been decided")
    if not (request.classification or {}).get("review_required"):
        raise HTTPException(status_code=409, detail="No classification review is required")
    if len(payload.kinds) != len(request.order_lines):
        raise HTTPException(status_code=422, detail="One article kind is required for every line")
    org = db.get(Organization, membership.organization_id)
    groups = matching_group_ids(org.settings or {}, request.title, request.order_lines,
                                request.total_cost, payload.kinds)
    if len(groups) > 1:
        raise HTTPException(status_code=422, detail="Items require different booking groups; create separate requests")
    if groups:
        request.commodity_group_id = next(iter(groups))
    request.classification = {"items": [
        {"index": index, "kind": kind, "evidence": "Buyer review"}
        for index, kind in enumerate(payload.kinds)], "review_required": False,
        "reviewed_by": str(membership.user_id), "reason": payload.reason}
    _log_activity(db, request.id, membership.user_id, "classification_reviewed",
                  "Article classification reviewed", {"kinds": payload.kinds, "reason": payload.reason})
    _apply_auto_approval(db, request, membership.user_id)
    db.commit()
    return RequestOut.model_validate(_get_org_request(db, request_id, membership.organization_id))


@router.get("/{request_id}/activity", response_model=list[RequestActivityOut])
def get_activity(
    request_id: uuid.UUID,
    membership: OrganizationMember = Depends(get_current_membership),
    db: Session = Depends(get_db),
) -> list[RequestActivityOut]:
    _get_org_request(db, request_id, membership.organization_id)  # scope check
    activity = db.scalars(
        select(RequestActivity)
        .where(RequestActivity.request_id == request_id)
        .order_by(RequestActivity.created_at.desc())
    )
    return [RequestActivityOut.model_validate(a) for a in activity]


@router.put("/{request_id}/document", status_code=status.HTTP_204_NO_CONTENT)
async def upload_document(
    request_id: uuid.UUID,
    file: UploadFile,
    membership: OrganizationMember = Depends(get_current_membership),
    db: Session = Depends(get_db),
) -> None:
    """Attach (or replace) the original PDF for a request."""
    request = _get_org_request(db, request_id, membership.organization_id, lock=True)
    _require_request_editor(request, membership)
    if request.approval_status != "pending" and not _is_auto_approved(request):
        raise HTTPException(status_code=409, detail="A manually decided request cannot receive a new document")

    if file.content_type not in ("application/pdf", "application/x-pdf"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Please upload a PDF file"
        )
    content = await file.read(MAX_PDF_BYTES + 1)
    if len(content) > MAX_PDF_BYTES:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail="PDF exceeds the 10MB limit",
        )
    if not content.startswith(b"%PDF-"):
        raise HTTPException(status_code=422, detail="File is not a PDF")

    document = db.scalar(
        select(RequestDocument).where(RequestDocument.request_id == request.id)
    )
    if document is not None and request.approval_status != "pending":
        raise HTTPException(status_code=409, detail="An approved request's document cannot be replaced")
    if document is None:
        document = RequestDocument(request_id=request.id)
        db.add(document)
    document.filename = (file.filename or "document.pdf")[:255]
    document.content_type = "application/pdf"
    document.content = content

    _log_activity(
        db,
        request.id,
        membership.user_id,
        "document_attached",
        f"Attached document {document.filename}",
    )
    db.commit()


@router.get("/{request_id}/document")
def get_document(
    request_id: uuid.UUID,
    membership: OrganizationMember = Depends(get_current_membership),
    db: Session = Depends(get_db),
) -> Response:
    _get_org_request(db, request_id, membership.organization_id)  # scope check
    document = db.scalar(
        select(RequestDocument).where(RequestDocument.request_id == request_id)
    )
    if document is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="No document attached"
        )
    return Response(
        content=document.content,
        media_type=document.content_type,
        headers={"Content-Disposition": f"inline; filename*=UTF-8''{quote(document.filename, safe='')}"},
    )


@router.delete("/{request_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_request(
    request_id: uuid.UUID,
    membership: OrganizationMember = Depends(get_current_membership),
    db: Session = Depends(get_db),
) -> None:
    request = _get_org_request(db, request_id, membership.organization_id, lock=True)
    _require_request_editor(request, membership)
    if request.approval_status != "pending" and membership.role != "admin":
        raise HTTPException(status_code=409, detail="Only an admin can delete a decided request")
    db.delete(request)  # order lines, activity, document cascade
    db.commit()
