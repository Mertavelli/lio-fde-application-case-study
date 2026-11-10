from pydantic import BaseModel, ConfigDict, Field


class PdfParseResponse(BaseModel):
    text: str


class ExtractionRequest(BaseModel):
    text: str = Field(max_length=100_000)


# camelCase on the wire — matches the AI's JSON output format and what the
# frontend's extraction flow already consumes.
class OrderLineData(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    position_description: str | None = Field(default=None, alias="positionDescription")
    unit_price: float | None = Field(default=None, alias="unitPrice", ge=0, le=1_000_000_000, allow_inf_nan=False)
    amount: float | None = Field(default=None, gt=0, le=1_000_000_000, allow_inf_nan=False)
    unit: str | None = None
    total_price: float | None = Field(default=None, alias="totalPrice", ge=0, le=1_000_000_000, allow_inf_nan=False)


class ExtractedVendorData(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    title: str | None = None
    vendor_name: str | None = Field(default=None, alias="vendorName")
    vat_id: str | None = Field(default=None, alias="vatId")
    department: str | None = None
    order_lines: list[OrderLineData] = Field(default_factory=list, alias="orderLines")
    total_cost: float | None = Field(default=None, alias="totalCost", ge=0, le=1_000_000_000, allow_inf_nan=False)
    commodity_group_id: int | None = Field(default=None, alias="commodityGroupId")
    commodity_group_name: str | None = Field(default=None, alias="commodityGroupName")


class QuoteLine(BaseModel):
    positionDescription: str | None
    unitPrice: float | None
    amount: float | None
    unit: str | None
    totalPrice: float | None


class QuoteExtraction(BaseModel):
    """Required keys with nullable values for constrained model output."""
    title: str | None
    vendorName: str | None
    recipientCompanyName: str | None
    vatId: str | None
    department: str | None
    orderLines: list[QuoteLine]
    totalCost: float | None
    commodityGroupId: int | None
    commodityGroupName: str | None


class ExtractionResponse(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    success: bool
    data: ExtractedVendorData | None = None
    error: str | None = None
    missing_fields: list[str] | None = Field(default=None, alias="missingFields")
    warnings: list[str] = Field(default_factory=list)
