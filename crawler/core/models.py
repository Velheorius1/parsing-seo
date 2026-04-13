"""Pydantic models for crawler config and data."""

import enum
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


class AdapterType(str, enum.Enum):
    API = "api"
    HTML = "html"
    SPA = "spa"
    TELEGRAM = "telegram"
    JSONRPC = "jsonrpc"


class FieldMap(BaseModel):
    """Mapping from RawTender fields to source-specific JSON keys."""

    title: str = "title"
    organization: str = "organization"
    price: Optional[str] = "price"
    currency: Optional[str] = "currency"
    deadline: Optional[str] = "deadline"
    date_start: Optional[str] = None
    date_end: Optional[str] = None
    region: Optional[str] = None
    categories: Optional[str] = None
    external_id: Optional[str] = "id"
    source_url_template: Optional[str] = None


class HtmlSelectors(BaseModel):
    """CSS selectors for HTML adapter."""

    container: str
    title: str
    organization: Optional[str] = None
    price: Optional[str] = None
    deadline: Optional[str] = None
    link: Optional[str] = None
    next_page: Optional[str] = None


class PaginationConfig(BaseModel):
    """Pagination settings for API/HTML adapters."""

    type: str = "offset"  # offset | page | cursor
    param: str = "from"
    size_param: Optional[str] = "to"
    page_size: int = 100
    max_pages: int = 10
    total_field: Optional[str] = None
    page_start: int = 0  # first page number (0 or 1)


class SourceConfig(BaseModel):
    """One tender source definition from sources.yaml."""

    id: str
    name: str
    adapter: AdapterType
    enabled: bool = True
    url: str
    method: str = "GET"
    headers: Dict[str, str] = Field(default_factory=dict)
    body: Optional[Dict[str, Any]] = None
    params: Optional[Dict[str, Any]] = None
    rate_limit: float = 2.0  # requests per second
    timeout: int = 15
    id_prefix: str
    field_map: FieldMap = Field(default_factory=FieldMap)
    keywords_fields: List[str] = Field(default_factory=list)
    pagination: Optional[PaginationConfig] = None
    html_selectors: Optional[HtmlSelectors] = None
    # SPA-specific
    wait_selector: Optional[str] = None
    # Telegram-specific
    telegram_channel: Optional[str] = None
    telegram_limit: int = 100
    # Filter (e.g. country code for international sources)
    country_filter: Optional[str] = None
    response_path: Optional[str] = None  # dot-path to array in JSON response
    # JSON-RPC specific (hayotbirja, xt-xarid)
    rpc_ref: Optional[str] = None  # ref name: "ref_reduction_object_public"
    rpc_method: str = "ref"  # JSON-RPC method name
    # Auth (ebirja JWT, etc.)
    auth_platform: Optional[str] = None  # key in session store: "ebirja"
    auth_header_name: str = "Authorization"
    auth_header_prefix: str = "Bearer"
    # Proxy — use residential proxy for geo-restricted sources
    use_proxy: bool = False


class SourcesConfig(BaseModel):
    """Root config loaded from sources.yaml."""

    sources: List[SourceConfig]


class RawTender(BaseModel):
    """Raw tender item produced by adapters — mirrors Node.js RawTenderItem."""

    id: str  # prefixed: 'etender-123'
    external_id: str
    title: str
    organization: str
    price: Optional[float] = None
    currency: str = "UZS"
    deadline: Optional[str] = None
    date_start: Optional[str] = None
    date_end: Optional[str] = None
    region: str = ""
    categories: List[str] = Field(default_factory=list)
    source: str  # display name: 'etender.uzex.uz'
    source_url: str = ""
    status: str = "active"  # active | closed | cancelled | completed
    search_text: str = ""
    collected_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    message_type: str = "tender"  # tender | customer_request | info
    # Result tracking
    winner: Optional[str] = None
    winning_price: Optional[float] = None
    result_date: Optional[str] = None
    group_id: Optional[str] = None
