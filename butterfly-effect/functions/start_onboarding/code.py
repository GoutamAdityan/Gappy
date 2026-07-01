#input_type_name: StartOnboardingInput
#output_type_name: StartOnboardingOutput
#function_name: start_onboarding
#python_packages: httpx, pypdf

from __future__ import annotations
import asyncio
import base64
import io
import json
import threading
from datetime import datetime, timezone
from typing import Optional

import httpx
from pydantic import BaseModel
from lemma_sdk import FunctionContext, Pod


NIM_URL = "https://integrate.api.nvidia.com/v1/chat/completions"
NIM_MODEL = "meta/llama-3.1-70b-instruct"


class StartOnboardingInput(BaseModel):
    github_username: str
    file_content_base64: str
    filename: str


class StartOnboardingOutput(BaseModel):
    status: str  # "started"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _read_secret(pod: Pod, path: str) -> str:
    raw = pod.files.download(path=path)
    return raw.decode("utf-8").strip() if isinstance(raw, bytes) else str(raw).strip()


def _extract_pdf_text(raw_bytes: bytes) -> str:
    import pypdf
    reader = pypdf.PdfReader(io.BytesIO(raw_bytes))
    return "\n".join(page.extract_text() or "" for page in reader.pages).strip()


async def _fetch_github(username: str) -> dict:
    headers = {"User-Agent": "butterfly-effect-app"}
    async with httpx.AsyncClient(timeout=20.0) as client:
        resp = await client.get(
            f"https://api.github.com/users/{username}/repos?sort=updated&per_page=10",
            headers=headers
        )
        if not resp.is_success:
            return {"tech_stack": "", "top_repos": "[]", "error": f"GitHub API {resp.status_code}"}
        repos = resp.json()
        top = repos[:6]
        lang_results = await asyncio.gather(*[
            _fetch_repo_langs(client, username, r["name"], headers) for r in top
        ])

    lang_bytes: dict = {}
    for lm in lang_results:
        for lang, count in lm.items():
            lang_bytes[lang] = lang_bytes.get(lang, 0) + count
    if not lang_bytes:
        for r in top:
            l = r.get("language")
            if l:
                lang_bytes[l] = lang_bytes.get(l, 0) + 1

    tech_stack = ", ".join(k for k, _ in sorted(lang_bytes.items(), key=lambda x: -x[1])[:8])
    top_repos = json.dumps([
        {"name": r.get("name",""), "description": r.get("description") or "", "language": r.get("language") or "unknown", "stars": r.get("stargazers_count",0)}
        for r in repos[:5]
    ])
    return {"tech_stack": tech_stack, "top_repos": top_repos, "error": ""}


async def _fetch_repo_langs(client, username, repo_name, headers) -> dict:
    try:
        r = await client.get(f"https://api.github.com/repos/{username}/{repo_name}/languages", headers=headers)
        return r.json() if r.is_success else {}
    except Exception:
        return {}


async def _nim_extract(nim_key: str, resume_text: str) -> dict:
    fallback = {"job_target": "", "current_projects": "", "upcoming_events": "", "company_context": ""}
    system = """Extract structured info from resume. Return ONLY valid JSON, no markdown:
{"job_target":"...","current_projects":"...","upcoming_events":"...","company_context":"..."}"""
    payload = {
        "model": NIM_MODEL,
        "messages": [{"role":"system","content":system},{"role":"user","content":f"Resume:\n\n{resume_text[:8000]}"}],
        "max_tokens": 500, "temperature": 0.1
    }
    for attempt in range(3):
        try:
            async with httpx.AsyncClient(timeout=40.0) as client:
                resp = await client.post(NIM_URL, headers={"Authorization": f"Bearer {nim_key}", "Content-Type":"application/json"}, json=payload)
            if resp.status_code == 429:
                await asyncio.sleep(2 * (attempt + 1))
                continue
            resp.raise_for_status()
            raw = resp.json()["choices"][0]["message"]["content"].strip()
            cleaned = raw.replace("```json","").replace("```","").strip()
            return json.loads(cleaned)
        except Exception:
            if attempt == 2:
                return fallback
    return fallback


def _upsert_profile(pod: Pod, data: dict) -> None:
    existing = pod.records.list("user_profiles", limit=1).to_dict()["items"]
    if existing:
        pod.table("user_profiles").update(existing[0]["id"], data)
    else:
        pod.table("user_profiles").create(data)


async def _background(pod: Pod, github_username: str, file_content_base64: str, filename: str) -> None:
    # Mark as processing
    _upsert_profile(pod, {
        "github_username": github_username,
        "profile_status": "processing",
        "profile_error": "",
        "last_synced_at": _now_iso(),
    })

    errors = []
    try:
        # GitHub + PDF extract in parallel
        raw_bytes = base64.b64decode(file_content_base64)
        github_task = asyncio.create_task(_fetch_github(github_username))

        if raw_bytes[:4] == b"%PDF":
            resume_text = _extract_pdf_text(raw_bytes)
        else:
            resume_text = raw_bytes.decode("utf-8", errors="replace")

        github_result = await github_task

        if github_result.get("error"):
            errors.append(github_result["error"])

        # Read NIM key
        nim_key = ""
        try:
            nim_key = _read_secret(pod, "/secrets/nim_api_key.txt")
        except Exception as e:
            errors.append(f"NIM key: {e}")

        nim_result = await _nim_extract(nim_key, resume_text) if nim_key and resume_text else {}

        _upsert_profile(pod, {
            "github_username": github_username,
            "tech_stack": github_result.get("tech_stack", ""),
            "top_repos": github_result.get("top_repos", "[]"),
            "job_target": nim_result.get("job_target", ""),
            "current_projects": nim_result.get("current_projects", ""),
            "upcoming_events": nim_result.get("upcoming_events", ""),
            "company_context": nim_result.get("company_context", ""),
            "resume_file_path": f"/resumes/{filename}",
            "profile_status": "failed" if errors else "ready",
            "profile_error": " | ".join(errors),
            "last_synced_at": _now_iso(),
        })
    except Exception as exc:
        _upsert_profile(pod, {
            "github_username": github_username,
            "profile_status": "failed",
            "profile_error": str(exc)[:500],
            "last_synced_at": _now_iso(),
        })


def _thread_target(pod: Pod, github_username: str, file_content_base64: str, filename: str) -> None:
    asyncio.run(_background(pod, github_username, file_content_base64, filename))


async def start_onboarding(ctx: FunctionContext, data: StartOnboardingInput) -> StartOnboardingOutput:
    pod = Pod.from_env()
    t = threading.Thread(
        target=_thread_target,
        args=(pod, data.github_username, data.file_content_base64, data.filename),
        daemon=True
    )
    t.start()
    return StartOnboardingOutput(status="started")
