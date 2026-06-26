#input_type_name: UrlMapperInput
#output_type_name: UrlMapperOutput
#function_name: url_mapper

from pydantic import BaseModel
from lemma_sdk import FunctionContext
from typing import List, Any


class UrlMapperInput(BaseModel):
    verified_source_ids: List[int]
    search_results: List[Any]  # list of {index, title, snippet, url}


class VerifiedSource(BaseModel):
    title: str
    url: str


class UrlMapperOutput(BaseModel):
    verified_sources: List[VerifiedSource]


async def url_mapper(ctx: FunctionContext, data: UrlMapperInput) -> UrlMapperOutput:
    if not data.verified_source_ids:
        return UrlMapperOutput(verified_sources=[])

    # Build index → result map
    result_map = {}
    for r in data.search_results:
        if isinstance(r, dict):
            idx = r.get("index")
            if idx is not None:
                result_map[idx] = r

    verified_sources = []
    for sid in data.verified_source_ids:
        r = result_map.get(sid)
        if r:
            verified_sources.append(VerifiedSource(
                title=r.get("title", ""),
                url=r.get("url", "")
            ))

    return UrlMapperOutput(verified_sources=verified_sources)
