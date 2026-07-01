#input_type_name: StartAnalysisInput
#output_type_name: StartAnalysisOutput
#function_name: start_analysis
#python_packages: httpx

from __future__ import annotations

import json
import re
import threading
import asyncio
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import httpx
from lemma_sdk import FunctionContext, Pod
from pydantic import BaseModel


NIM_URL = "https://integrate.api.nvidia.com/v1/chat/completions"
NIM_MODEL = "meta/llama-3.1-70b-instruct"
SERPER_URL = "https://google.serper.dev/search"
LAYERS = ["Macro", "Micro", "Professional", "Personal"]
PLACEHOLDER_TEXT = {"test", "testing", "placeholder", "demo", "sample", "hello", "hi"}


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _resolve_owner_id(ctx: FunctionContext) -> str:
    auth = getattr(ctx, "auth", None)
    candidates = [
        getattr(auth, "user_id", None),
        getattr(auth, "userId", None),
        getattr(auth, "id", None),
    ]
    user = getattr(auth, "user", None)
    if user is not None:
        candidates.extend([
            getattr(user, "id", None),
            getattr(user, "user_id", None),
            getattr(user, "userId", None),
            getattr(user, "email", None),
        ])
    candidates.append(getattr(auth, "email", None))

    for value in candidates:
        if value:
            return str(value)
    return "dev-user"


class StartAnalysisInput(BaseModel):
    news_event: str

class StartAnalysisOutput(BaseModel):
    job_id: str


class VerifiedSource(BaseModel):
    title: str
    url: str


class LayerOutput(BaseModel):
    status: str
    claim: str = ""
    confidence_score: int = 0
    evidence_extracted: str = ""
    verified_sources: List[VerifiedSource] = []
    reasoning: str = ""
    rejected_claims: List[str] = []


def _extract_json(text: str, fallback: Dict[str, Any]) -> Dict[str, Any]:
    cleaned = text.strip()
    cleaned = cleaned.replace("```json", "").replace("```", "").strip()
    try:
        return json.loads(cleaned)
    except Exception:
        pass

    match = re.search(r"\{.*\}", cleaned, re.S)
    if match:
        try:
            return json.loads(match.group(0))
        except Exception:
            pass
    return fallback


def _is_placeholder_news(news_event: str) -> bool:
    value = news_event.strip().lower()
    if value in PLACEHOLDER_TEXT:
        return True
    if len(value) < 8:
        return True
    if "test headline" in value:
        return True
    return False


def _empty_layer(reason: str) -> LayerOutput:
    return LayerOutput(
        status="compressed",
        claim="",
        confidence_score=0,
        evidence_extracted="",
        verified_sources=[],
        reasoning=reason,
        rejected_claims=[],
    )


def _read_secret(pod: Pod, path: str) -> str:
    content = pod.files.download(path=path)
    return content.decode("utf-8").strip() if isinstance(content, bytes) else str(content).strip()


def _load_user_profile(pod: Pod) -> Dict[str, Any]:
    try:
        items = pod.records.list("user_profiles", limit=1).to_dict().get("items", [])
        return items[0] if items else {}
    except Exception:
        return {}


async def _nim_json(
    nim_key: str,
    system_prompt: str,
    user_prompt: str,
    fallback: Dict[str, Any],
    *,
    temperature: float = 0.2,
    max_tokens: int = 500,
) -> Dict[str, Any]:
    payload = {
        "model": NIM_MODEL,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "temperature": temperature,
        "max_tokens": max_tokens,
    }
    try:
        async with httpx.AsyncClient(timeout=45.0) as client:
            response = await client.post(
                NIM_URL,
                headers={
                    "Authorization": f"Bearer {nim_key}",
                    "Content-Type": "application/json",
                },
                json=payload,
            )
            response.raise_for_status()
        text = response.json()["choices"][0]["message"]["content"]
        return _extract_json(text, fallback)
    except Exception:
        return fallback


async def _serper_search(serper_key: str, query: str) -> List[Dict[str, Any]]:
    if not query.strip():
        return []
    try:
        async with httpx.AsyncClient(timeout=20.0) as client:
            response = await client.post(
                SERPER_URL,
                headers={"X-API-KEY": serper_key, "Content-Type": "application/json"},
                json={"q": query, "num": 5},
            )
            response.raise_for_status()
        organic = response.json().get("organic", [])
        return [
            {
                "index": i,
                "title": item.get("title", ""),
                "snippet": item.get("snippet", ""),
                "url": item.get("link", ""),
            }
            for i, item in enumerate(organic[:5])
        ]
    except Exception:
        return []


def _fallback_claim(
    news_event: str,
    layer: str,
    previous_nodes: List[Dict[str, Any]],
    user_profile: Dict[str, Any],
) -> Dict[str, Any]:
    if _is_placeholder_news(news_event):
        return {
            "layer": layer,
            "claim": "",
            "search_query": "",
            "reasoning": "",
            "skip_layer": True,
            "skip_reason": "Placeholder input is not a real news event.",
        }

    if layer == "Macro":
        return {
            "layer": layer,
            "claim": f"Investors in adjacent markets are likely to reprice competitors after {news_event}.",
            "search_query": news_event[:80],
            "reasoning": "Major product and company news usually reprices nearby markets first.",
        }

    if not previous_nodes:
        return {
            "layer": layer,
            "claim": "",
            "search_query": "",
            "reasoning": "",
            "skip_layer": True,
            "skip_reason": "No confirmed earlier node to extend.",
        }

    if layer == "Micro":
        return {
            "layer": layer,
            "claim": "Suppliers, cloud buyers, or close ecosystem partners are likely to revisit spending plans after this shift.",
            "search_query": f"{news_event} suppliers cloud partners impact",
            "reasoning": "Ecosystem actors usually react after market-level signal appears.",
        }

    if not user_profile:
        return {
            "layer": layer,
            "claim": "",
            "search_query": "",
            "reasoning": "",
            "skip_layer": True,
            "skip_reason": "No user profile available for personalized impact.",
        }

    tech_stack = user_profile.get("tech_stack") or "current stack"
    upcoming_events = user_profile.get("upcoming_events") or ""
    if layer == "Professional":
        return {
            "layer": layer,
            "claim": f"Teams working with {tech_stack} are likely to see new migration, optimization, or architecture discussion triggered by this event.",
            "search_query": f"{news_event} {tech_stack.split(',')[0].strip()} impact",
            "reasoning": "Professional layer should connect ecosystem change to user stack.",
        }

    target = upcoming_events or user_profile.get("job_target") or "near-term work"
    return {
        "layer": layer,
        "claim": f"For {target}, this event likely raises the odds of questions about AI coding tools, model strategy, or developer workflow changes.",
        "search_query": f"{news_event} AI coding tools interview developer workflow",
        "reasoning": "Personal layer should convert professional impact into near-term action tied to the user's upcoming event.",
    }


async def _generate_claim(
    nim_key: str,
    news_event: str,
    layer: str,
    previous_nodes: List[Dict[str, Any]],
    user_profile: Dict[str, Any],
    rejected_claims: List[str],
) -> Dict[str, Any]:
    fallback = _fallback_claim(news_event, layer, previous_nodes, user_profile)
    if not nim_key:
        return fallback

    layer_guidance = {
        "Macro": (
            "MACRO layer = global economic or market-level consequence. "
            "Think: capital markets, regulatory signals, investor repricing, sector rotation, geopolitical ripple. "
            "Do NOT mention the user profile. Be specific about WHO reprice or HOW capital moves. "
            "search_query should target financial news or market analysis."
        ),
        "Micro": (
            "MICRO layer = industry or ecosystem-level consequence. "
            "Think: which specific companies, startups, or platforms are directly hurt OR gain from this. "
            "Name both losers (incumbents disrupted) AND winners (who gains market share or advantage). "
            "Do NOT mention the user profile. Be specific — name companies, not categories. "
            "search_query should name the specific companies or market segments involved."
        ),
        "Professional": (
            "PROFESSIONAL layer = career and skills impact for someone with THIS SPECIFIC tech stack and background. "
            "Think: which skills become more or less valuable, which job categories surge or contract, "
            "what new technical demands appear for developers/engineers in this stack. "
            "Be concrete — not 'teams will discuss' but 'Python ML engineers will face pressure to...' or 'demand for X skill rises because...'. "
            "search_query should target career, hiring, or skills market data related to the event."
        ),
        "Personal": (
            "PERSONAL layer = ONE concrete action or risk for THIS specific person given their projects and context. "
            "Think: how their current projects become more or less relevant, what they should prepare for, "
            "what opportunity opens up in the next 30-60 days. "
            "Reference their actual project names or career stage. Make it actionable, not abstract. "
            "search_query should target practical guides or opportunities related to the event and their domain."
        ),
    }

    system_prompt = f"""Return ONLY valid JSON. No markdown, no prose outside JSON.

You generate ONE downstream consequence node for the {layer} layer of a news cascade.

LAYER DEFINITION:
{layer_guidance.get(layer, '')}

RULES:
- If current_layer is Professional or Personal and user_profile is empty, return skip_layer true.
- If current_layer is not Macro and previous_nodes is empty, return skip_layer true.
- Do not repeat or closely paraphrase claims in rejected_claims.
- claim must be a single declarative sentence, max 25 words.
- search_query must be 4-8 words, specific enough to find real evidence.

OUTPUT (choose one):
{{"layer":"{layer}","claim":"...","search_query":"...","reasoning":"..."}}
or
{{"layer":"{layer}","claim":"","search_query":"","reasoning":"","skip_layer":true,"skip_reason":"..."}}"""

    profile_str = _structured_profile(user_profile) if user_profile else ""
    user_prompt = json.dumps(
        {
            "news_event": news_event,
            "current_layer": layer,
            "previous_nodes": previous_nodes,
            "user_profile_summary": profile_str,
            "rejected_claims": rejected_claims,
        },
        ensure_ascii=False,
    )
    result = await _nim_json(nim_key, system_prompt, user_prompt, fallback, temperature=0.2, max_tokens=400)
    if not isinstance(result, dict) or str(result.get("layer", "")) != layer:
        return fallback
    if not result.get("claim") and not result.get("skip_layer"):
        return fallback
    if result.get("skip_layer") and layer in ("Professional", "Personal") and user_profile:
        return fallback
    return result


async def _audit_claim(
    nim_key: str,
    claim: str,
    snippets: List[Dict[str, Any]],
) -> Dict[str, Any]:
    fallback = {
        "validated": bool(snippets),
        "confidence_score": 3 if snippets else 1,
        "evidence_extracted": snippets[0]["snippet"][:180] if snippets else "No supporting search snippets found.",
        "verified_source_ids": [snippets[0]["index"]] if snippets else [],
    }
    if not nim_key:
        return fallback

    system_prompt = """Return ONLY valid JSON. No markdown, no prose outside JSON.

Validate a claim using ONLY the provided search snippets.

CONFIDENCE RUBRIC:
5 = snippet directly confirms the claim with specific facts or data
4 = strong support — clear causal or factual alignment
3 = plausible support — relevant context but indirect
2 = weak/tangential — only loosely related
1 = unsupported or contradicted

DOMAIN QUALITY FILTER (apply before scoring):
If a snippet is from a domain clearly irrelevant to tech, finance, or industry news — such as:
dentistry, real estate listings, local restaurants, beauty services, personal blogs, religious organizations —
assign that snippet confidence 1 regardless of text content. Do NOT cite it as evidence.

SOURCE AUTHORITY FILTER:
For claims about market movements, investor behavior, company strategy, or technical/engineering topics:
- Social and UGC platforms (youtube.com, x.com, twitter.com, reddit.com, tiktok.com, instagram.com, facebook.com, pinterest.com, tumblr.com) → cap confidence at 2. Not authoritative for these claims.
- Prefer: reuters.com, bloomberg.com, techcrunch.com, wsj.com, ft.com, ieee.org, nature.com, arxiv.org, hbr.org, official company blogs, .gov, .edu domains.
- If ONLY social/UGC sources are available, set confidence_score 2 and validated false.

RULES:
- Only use snippets actually provided. Do not invent evidence.
- confidence_score <= 2 → validated: false
- confidence_score >= 3 → validated: true
- verified_source_ids must contain only integer indices from the provided snippets

OUTPUT:
{"validated":true,"confidence_score":4,"evidence_extracted":"one sentence of direct evidence","verified_source_ids":[0,2]}"""

    payload = {"claim": claim, "search_snippets": snippets}
    result = await _nim_json(nim_key, system_prompt, json.dumps(payload, ensure_ascii=False), fallback, temperature=0.0, max_tokens=250)
    if not isinstance(result, dict):
        return fallback
    score = int(result.get("confidence_score", fallback["confidence_score"]))
    ids = [int(x) for x in result.get("verified_source_ids", []) if isinstance(x, int) or str(x).isdigit()]
    return {
        "validated": bool(score >= 3),
        "confidence_score": max(1, min(score, 5)),
        "evidence_extracted": str(result.get("evidence_extracted", fallback["evidence_extracted"]))[:300],
        "verified_source_ids": ids,
    }


def _profile_hint(user_profile: Dict[str, Any]) -> str:
    parts = [
        user_profile.get("tech_stack", ""),
        user_profile.get("job_target", ""),
        user_profile.get("current_projects", ""),
        user_profile.get("upcoming_events", ""),
        user_profile.get("company_context", ""),
    ]
    text = ", ".join([part for part in parts if part]).strip(", ")
    return text[:220]


def _structured_profile(user_profile: Dict[str, Any]) -> str:
    tech = user_profile.get("tech_stack") or ""
    job = user_profile.get("job_target") or ""
    projects = user_profile.get("current_projects") or ""
    events = user_profile.get("upcoming_events") or ""
    orgs = user_profile.get("company_context") or ""
    repos = user_profile.get("top_repos") or ""

    lines = []
    if tech:
        lines.append(f"Tech stack: {tech}")
    if job:
        lines.append(f"Career target: {job}")
    elif orgs:
        # Infer career stage from org context
        lines.append(f"Context: {orgs[:200]}")
    if projects:
        lines.append(f"Active projects: {projects}")
    if events:
        lines.append(f"Upcoming events: {events}")
    if repos and repos != "[]":
        try:
            import json as _json
            repo_list = _json.loads(repos) if isinstance(repos, str) else repos
            descs = [f"{r['name']}: {r['description']}" for r in repo_list[:3] if r.get("description")]
            if descs:
                lines.append(f"Key repos: {'; '.join(descs)}")
        except Exception:
            pass
    return "\n".join(lines)[:600]


def _map_sources(search_results: List[Dict[str, Any]], source_ids: List[int]) -> List[VerifiedSource]:
    by_index = {item["index"]: item for item in search_results}
    mapped: List[VerifiedSource] = []
    for source_id in source_ids:
        item = by_index.get(source_id)
        if item and item.get("url"):
            mapped.append(VerifiedSource(title=item.get("title", ""), url=item.get("url", "")))
    return mapped


def _update_job(pod: Pod, job_id: str, values: Dict[str, Any]) -> None:
    job_record = pod.records.list("analysis_jobs", filter=[{"field": "id", "op": "eq", "value": job_id}]).to_dict()["items"]
    if job_record:
        pod.table("analysis_jobs").update(job_record[0]["id"], values)


def _mark_job_failed(pod: Pod, job_id: str, message: str) -> None:
    _update_job(pod, job_id, {
        "status": "failed",
        "error_message": message[:500],
    })


async def _background_run(pod: Pod, owner_id: str, job_id: str, news_event: str):
    try:
        user_profile = _load_user_profile(pod)

        try:
            nim_key = _read_secret(pod, "/secrets/nim_api_key.txt")
        except Exception:
            nim_key = ""
        try:
            serper_key = _read_secret(pod, "/secrets/serper_api_key.txt")
        except Exception:
            serper_key = ""

        previous_nodes: List[Dict[str, Any]] = []
        _update_job(pod, job_id, {
            "status": "running",
            "started_at": _now_iso(),
            "current_layer": "Macro",
            "progress_pct": 0,
            "attempt_count": 0,
            "error_message": "",
        })

        for index, layer in enumerate(LAYERS):
            # Write evaluating state to DB
            pod.table("analysis_results").create({
                "owner_id": owner_id,
                "job_id": job_id,
                "layer_name": layer,
                "layer_order": index,
                "attempt_number": 1,
                "status": "evaluating",
                "layer_data": "{}",
            })

            _update_job(pod, job_id, {
                "current_layer": layer,
                "progress_pct": int((index / max(len(LAYERS), 1)) * 100),
            })
            
            if layer in ("Professional", "Personal") and not user_profile:
                final_layer = _empty_layer("No user profile available for personalized layer.")
                existing = pod.records.list("analysis_results", filter=[{"field":"job_id","op":"eq","value":job_id},{"field":"layer_name","op":"eq","value":layer}]).to_dict()["items"]
                if existing:
                    pod.table("analysis_results").update(existing[0]["id"], {
                        "status": final_layer.status,
                        "layer_data": json.dumps(final_layer.model_dump()),
                    })
                _update_job(pod, job_id, {
                    "attempt_count": index + 1,
                })
                continue

            rejected_claims: List[str] = []
            final_layer: Optional[LayerOutput] = None

            for _attempt in range(3):
                candidate = await _generate_claim(
                    nim_key,
                    news_event,
                    layer,
                    previous_nodes,
                    user_profile,
                    rejected_claims,
                )

                if candidate.get("skip_layer"):
                    final_layer = _empty_layer(str(candidate.get("skip_reason", "No causal path found.")))
                    break

                claim = str(candidate.get("claim", "")).strip()
                search_query = str(candidate.get("search_query", "")).strip() or claim
                reasoning = str(candidate.get("reasoning", "")).strip()
                search_results = await _serper_search(serper_key, search_query) if serper_key else []

                if not search_results:
                    if layer in ("Professional", "Personal") and user_profile:
                        final_layer = LayerOutput(
                            status="low_confidence",
                            claim=claim,
                            confidence_score=2,
                            evidence_extracted=_profile_hint(user_profile) or "Profile-based consequence with limited public search support.",
                            verified_sources=[],
                            reasoning=reasoning or "Generated from user profile because public search support was limited.",
                            rejected_claims=rejected_claims.copy(),
                        )
                        break
                    rejected_claims.append(claim)
                    continue

                snippets = [{"index": item["index"], "title": item["title"], "snippet": item["snippet"]} for item in search_results]
                audit = await _audit_claim(nim_key, claim, snippets)
                sources = _map_sources(search_results, audit.get("verified_source_ids", []))

                if layer in ("Professional", "Personal") and user_profile and not audit["validated"]:
                    final_layer = LayerOutput(
                        status="low_confidence",
                        claim=claim,
                        confidence_score=max(2, int(audit["confidence_score"])),
                        evidence_extracted=str(audit["evidence_extracted"]) or (_profile_hint(user_profile) or "Profile-based consequence."),
                        verified_sources=sources,
                        reasoning=reasoning or "Profile-based personalized consequence with limited search support.",
                        rejected_claims=rejected_claims.copy(),
                    )
                    break

                final_layer = LayerOutput(
                    status="confirmed" if audit["validated"] else "low_confidence",
                    claim=claim,
                    confidence_score=int(audit["confidence_score"]),
                    evidence_extracted=str(audit["evidence_extracted"]),
                    verified_sources=sources,
                    reasoning=reasoning,
                    rejected_claims=rejected_claims.copy(),
                )

                if audit["validated"]:
                    break

                rejected_claims.append(claim)

            if final_layer is None:
                final_layer = _empty_layer("No supported claim found after retries.")

            if final_layer.status in {"confirmed", "low_confidence"} and final_layer.claim:
                previous_nodes.append(
                    {
                        "layer": layer,
                        "claim": final_layer.claim,
                        "confidence_score": final_layer.confidence_score,
                    }
                )
                
            # Update layer result in DB
            existing = pod.records.list("analysis_results", filter=[{"field":"job_id","op":"eq","value":job_id},{"field":"layer_name","op":"eq","value":layer}]).to_dict()["items"]
            if existing:
                pod.table("analysis_results").update(existing[0]["id"], {
                    "status": final_layer.status,
                    "layer_data": json.dumps(final_layer.model_dump()),
                })

            _update_job(pod, job_id, {
                "attempt_count": index + 1,
                "progress_pct": int(((index + 1) / len(LAYERS)) * 100),
            })

        _update_job(pod, job_id, {
            "status": "completed",
            "current_layer": "Personal",
            "progress_pct": 100,
            "completed_at": _now_iso(),
            "error_message": "",
        })
    except Exception as exc:
        _mark_job_failed(pod, job_id, f"{type(exc).__name__}: {exc}")


def _thread_target(pod: Pod, owner_id: str, job_id: str, news_event: str):
    try:
        asyncio.run(_background_run(pod, owner_id, job_id, news_event))
    except Exception as exc:
        _mark_job_failed(pod, job_id, f"{type(exc).__name__}: {exc}")


async def start_analysis(ctx: FunctionContext, data: StartAnalysisInput) -> StartAnalysisOutput:
    pod = Pod.from_env()
    owner_id = _resolve_owner_id(ctx)
        
    job = pod.table("analysis_jobs").create({
        "owner_id": owner_id,
        "news_event": data.news_event,
        "status": "queued",
        "current_layer": "Macro",
        "progress_pct": 0,
        "attempt_count": 0,
    })
    
    job_id = str(job["id"])
    
    # Spawn thread
    t = threading.Thread(target=_thread_target, args=(pod, owner_id, job_id, data.news_event))
    t.daemon = True
    t.start()

    pod.table("analysis_jobs").update(job["id"], {
        "status": "running",
        "started_at": _now_iso(),
    })
    
    return StartAnalysisOutput(job_id=job_id)
