#input_type_name: WriteUserProfileInput
#output_type_name: WriteUserProfileOutput
#function_name: write_user_profile

from pydantic import BaseModel
from lemma_sdk import FunctionContext, Pod
from typing import Optional


class WriteUserProfileInput(BaseModel):
    github_username: str
    tech_stack: str = ""
    top_repos: str = ""
    job_target: str = ""
    current_projects: str = ""
    upcoming_events: str = ""
    company_context: str = ""
    resume_file_path: str = ""


class WriteUserProfileOutput(BaseModel):
    record_id: str
    action: str  # "created" or "updated"


async def write_user_profile(ctx: FunctionContext, data: WriteUserProfileInput) -> WriteUserProfileOutput:
    pod = Pod.from_env()

    row_data = {
        "github_username": data.github_username,
        "tech_stack": data.tech_stack,
        "top_repos": data.top_repos,
        "job_target": data.job_target,
        "current_projects": data.current_projects,
        "upcoming_events": data.upcoming_events,
        "company_context": data.company_context,
        "resume_file_path": data.resume_file_path,
    }

    # Check if profile already exists for this username
    existing = pod.records.list(
        "user_profiles",
        filter=[{"field": "github_username", "op": "eq", "value": data.github_username}],
        limit=1
    ).to_dict()["items"]

    if existing:
        record_id = existing[0]["id"]
        pod.table("user_profiles").update(record_id, row_data)
        return WriteUserProfileOutput(record_id=str(record_id), action="updated")
    else:
        record = pod.table("user_profiles").create(row_data)
        return WriteUserProfileOutput(record_id=str(record["id"]), action="created")
