from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query

from ..enums import ApiScope
from ..schemas.api import VendorRiskOut, VendorScorecardOut
from ..services.vendor_risk import vendor_scorecards
from .deps import Principal, SessionDep, SettingsDep, require_scope

router = APIRouter(prefix="/api/v1/vendors", tags=["vendors"])


@router.get("/scorecards", response_model=list[VendorScorecardOut])
async def list_vendor_scorecards(
    principal: Annotated[Principal, Depends(require_scope(ApiScope.INVOICES_READ))],
    session: SessionDep,
    settings: SettingsDep,
    vendor_id: Annotated[list[UUID] | None, Query(description="Only these vendors; repeat for several.")] = None,
) -> list[VendorScorecardOut]:
    """Every vendor's audit history, aggregated over the whole ledger, riskiest first.

    The risk tier is HIGH after a rejected invoice or a confirmed duplicate, or when a vendor with
    enough history is held for review or flagged as a duplicate often (thresholds are settings);
    MEDIUM for a noticeable flag rate or any open duplicate; LOW otherwise. `reasons` says why.
    """
    cards = await vendor_scorecards(session, principal.organization_id, settings, vendor_ids=vendor_id)
    return [
        VendorScorecardOut(
            vendor_id=card.vendor_id,
            name=card.name,
            tax_id=card.tax_id,
            first_seen_at=card.first_seen_at,
            last_invoice_at=card.last_invoice_at,
            total_invoices=card.metrics.total_invoices,
            held_for_review=card.metrics.held_for_review,
            flag_rate_percent=card.metrics.flag_rate,
            duplicate_invoices=card.metrics.duplicate_invoices,
            confirmed_duplicates=card.metrics.confirmed_duplicates,
            duplicate_rate_percent=card.metrics.duplicate_rate,
            rejected_invoices=card.metrics.rejected_invoices,
            in_review=card.metrics.in_review,
            risk=VendorRiskOut(tier=card.risk.tier, reasons=card.risk.reasons),
        )
        for card in cards
    ]
