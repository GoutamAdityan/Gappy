#input_type_name: CreateOrUpdateProfileInput
#output_type_name: CreateOrUpdateProfileOutput
#function_name: create_or_update_profile

from pydantic import BaseModel
from lemma_sdk import FunctionContext, Pod
from typing import Optional
from datetime import datetime, timezone

class CreateOrUpdateProfileInput(BaseModel):
    github_username: str
    resume_file_path: str = ""
    tech_stack: str = ""
    top_repos: str = ""
    job_target: str = ""
    current_projects: str = ""
    upcoming_events: str = ""
    company_context: str = ""
    profile_status: str = "ready"
    profile_error: str = ""

class CreateOrUpdateProfileOutput(BaseModel):
    record_id: str
    action: str

def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()

async def create_or_update_profile(ctx: FunctionContext, data: CreateOrUpdateProfileInput) -> CreateOrUpdateProfileOutput:
    pod = Pod.from_env()

    existing = pod.records.list("user_profiles", limit=1).to_dict()["items"]

    existing_row = existing[0] if existing else {}

    row_data = {
        "github_username": data.github_username,
        "resume_file_path": data.resume_file_path,
        "profile_status": data.profile_status or existing_row.get("profile_status", "ready"),
        "profile_error": data.profile_error if data.profile_error is not None else existing_row.get("profile_error", ""),
        "tech_stack": data.tech_stack or existing_row.get("tech_stack", ""),
        "top_repos": data.top_repos or existing_row.get("top_repos", ""),
        "job_target": data.job_target or existing_row.get("job_target", ""),
        "current_projects": data.current_projects or existing_row.get("current_projects", ""),
        "upcoming_events": data.upcoming_events or existing_row.get("upcoming_events", ""),
        "company_context": data.company_context or existing_row.get("company_context", ""),
        "last_synced_at": _now_iso(),
    }

    if existing:
        record_id = existing[0]["id"]
        pod.table("user_profiles").update(record_id, row_data)
        return CreateOrUpdateProfileOutput(record_id=str(record_id), action="updated")
    else:
        record = pod.table("user_profiles").create(row_data)
        return CreateOrUpdateProfileOutput(record_id=str(record["id"]), action="created")
