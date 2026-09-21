from pydantic import BaseModel, Field
from typing import Optional, List, Dict, Any

class EmailClassification(BaseModel):
    category: str = Field(description="BL_COMPARISON, SI_REQUEST, INVOICE_QUERY, GENERAL, SPAM")
    confidence: float = Field(ge=0.0, le=1.0)
    reasoning: str
    relevant_attachment_names: List[str] = []
    review_required: bool = False

class DocumentAnalysis(BaseModel):
    document_type: str
    confidence: float = Field(ge=0.0, le=1.0)
    detected_title: Optional[str] = None
    shipment_identifiers: Dict[str, str] = {}
    review_required: bool = False

class FieldExtraction(BaseModel):
    field_name: str
    raw_value: str
    normalized_value: Any
    unit: Optional[str] = None
    confidence: float = Field(ge=0.0, le=1.0)
    source_page: Optional[int] = None
    source_text: Optional[str] = None
    source_label: Optional[str] = None
    extraction_reason: Optional[str] = None
    review_required: bool = False

class AIResult(BaseModel):
    status: str  # EXTRACTING, COMPARING, REVIEW, COMPLETE, ERROR
    confidence: float = Field(ge=0.0, le=1.0)
    extracted_fields: Dict[str, FieldExtraction] = {}
    observations: List[str] = []
    review_required: bool = False
    review_reason: Optional[str] = None