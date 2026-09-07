from pydantic import BaseModel, Field
from typing import List, Optional

class CitationModel(BaseModel):
    index: int
    title: str
    url: str
    origin: str

class ResearchRequest(BaseModel):
    question: str = Field(..., min_length=3, max_length=500)
    sources_filter: List[str] = Field(default_factory=lambda: ["wikipedia", "arxiv", "web"])
    bypass_cache: bool = False

class ResearchResponse(BaseModel):
    question: str
    answer: str
    citations: List[CitationModel] = Field(default_factory=list)
    wall_clock_time_seconds: float
    degraded_sources: List[str] = Field(default_factory=list)