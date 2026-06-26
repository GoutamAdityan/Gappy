#input_type_name: GithubExtractorInput
#output_type_name: GithubExtractorOutput
#function_name: github_extractor
#python_packages: httpx

from pydantic import BaseModel
from lemma_sdk import FunctionContext
import httpx
import json


class GithubExtractorInput(BaseModel):
    github_username: str


class GithubExtractorOutput(BaseModel):
    tech_stack: str
    top_repos: str
    error: str = ""


async def github_extractor(ctx: FunctionContext, data: GithubExtractorInput) -> GithubExtractorOutput:
    username = data.github_username.strip()
    headers = {"User-Agent": "butterfly-effect-app"}

    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await client.get(
            f"https://api.github.com/users/{username}/repos?sort=updated&per_page=10",
            headers=headers
        )

    if resp.status_code == 404:
        return GithubExtractorOutput(tech_stack="", top_repos="[]", error=f"GitHub user not found: {username}")
    if not resp.is_success:
        return GithubExtractorOutput(tech_stack="", top_repos="[]", error=f"GitHub API error: {resp.status_code}")

    repos = resp.json()

    language_counts: dict = {}
    for repo in repos[:6]:
        lang = repo.get("language")
        if lang:
            language_counts[lang] = language_counts.get(lang, 0) + 1

    tech_stack = ", ".join(
        lang for lang, _ in sorted(language_counts.items(), key=lambda x: -x[1])
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
