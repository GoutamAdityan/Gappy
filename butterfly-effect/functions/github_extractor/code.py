#input_type_name: GithubExtractorInput
#output_type_name: GithubExtractorOutput
#function_name: github_extractor
#python_packages: httpx

from pydantic import BaseModel
from lemma_sdk import FunctionContext
import httpx
import json
import asyncio


class GithubExtractorInput(BaseModel):
    github_username: str


class GithubExtractorOutput(BaseModel):
    tech_stack: str
    top_repos: str
    error: str = ""


async def _fetch_languages(client: httpx.AsyncClient, username: str, repo_name: str, headers: dict) -> dict:
    try:
        resp = await client.get(
            f"https://api.github.com/repos/{username}/{repo_name}/languages",
            headers=headers
        )
        return resp.json() if resp.is_success else {}
    except Exception:
        return {}


async def github_extractor(ctx: FunctionContext, data: GithubExtractorInput) -> GithubExtractorOutput:
    username = data.github_username.strip()
    headers = {"User-Agent": "butterfly-effect-app"}

    async with httpx.AsyncClient(timeout=20.0) as client:
        resp = await client.get(
            f"https://api.github.com/users/{username}/repos?sort=updated&per_page=10",
            headers=headers
        )
        if resp.status_code == 404:
            return GithubExtractorOutput(tech_stack="", top_repos="[]", error=f"GitHub user not found: {username}")
        if not resp.is_success:
            return GithubExtractorOutput(tech_stack="", top_repos="[]", error=f"GitHub API error: {resp.status_code}")

        repos = resp.json()
        top = repos[:6]

        # Fetch per-repo language bytes in parallel
        lang_results = await asyncio.gather(*[
            _fetch_languages(client, username, r["name"], headers) for r in top
        ])

    # Aggregate language bytes across all repos
    lang_bytes: dict = {}
    for lang_map in lang_results:
        for lang, count in lang_map.items():
            lang_bytes[lang] = lang_bytes.get(lang, 0) + count

    # Fall back to repo.language field for repos with no languages API data
    if not lang_bytes:
        for repo in top:
            lang = repo.get("language")
            if lang:
                lang_bytes[lang] = lang_bytes.get(lang, 0) + 1

    # Top languages by byte count, max 8
    tech_stack = ", ".join(
        lang for lang, _ in sorted(lang_bytes.items(), key=lambda x: -x[1])[:8]
    )

    top_repos = json.dumps([
        {
            "name": r.get("name", ""),
            "description": r.get("description") or "",
            "language": r.get("language") or "unknown",
            "stars": r.get("stargazers_count", 0)
        }
        for r in repos[:5]
    ])

    return GithubExtractorOutput(tech_stack=tech_stack, top_repos=top_repos)
