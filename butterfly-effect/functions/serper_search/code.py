#input_type_name: SerperSearchInput
#output_type_name: SerperSearchOutput
#function_name: serper_search
#python_packages: httpx

from pydantic import BaseModel
from lemma_sdk import FunctionContext, Pod
from typing import List
import httpx
import json


class SerperSearchInput(BaseModel):
    query: str


class SearchResult(BaseModel):
    index: int
    title: str
    snippet: str
    url: str  # stored here only — NEVER passed to LLM


class SerperSearchOutput(BaseModel):
    results: List[SearchResult]
    error: str = ""


async def serper_search(ctx: FunctionContext, data: SerperSearchInput) -> SerperSearchOutput:
    pod = Pod.from_env()

    # Read Serper API key from pod secrets
    try:
        key_bytes = pod.files.download(path="/secrets/serper_api_key.txt")
        serper_key = key_bytes.decode("utf-8").strip() if isinstance(key_bytes, bytes) else str(key_bytes).strip()
    except Exception as e:
        return SerperSearchOutput(results=[], error=f"Failed to read Serper key: {e}")

    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.post(
                "https://google.serper.dev/search",
                headers={
                    "X-API-KEY": serper_key,
                    "Content-Type": "application/json"
                },
                json={"q": data.query, "num": 5}
            )
            resp.raise_for_status()
            data_json = resp.json()
    except Exception as e:
        return SerperSearchOutput(results=[], error=f"Serper API error: {e}")

    organic = data_json.get("organic", [])
    results = [
        SearchResult(
            index=i,
            title=item.get("title", ""),
            snippet=item.get("snippet", ""),
            url=item.get("link", "")
        )
        for i, item in enumerate(organic[:5])
    ]

    return SerperSearchOutput(results=results)
