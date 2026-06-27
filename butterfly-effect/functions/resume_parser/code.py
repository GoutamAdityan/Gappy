#input_type_name: ResumeParserInput
#output_type_name: ResumeParserOutput
#function_name: resume_parser
#python_packages: httpx

from pydantic import BaseModel
from lemma_sdk import FunctionContext, Pod
import httpx
import json


class ResumeParserInput(BaseModel):
    resume_file_path: str


class ResumeParserOutput(BaseModel):
    job_target: str = ""
    current_projects: str = ""
    upcoming_events: str = ""
    company_context: str = ""
    error: str = ""


def _safe_parse_json(content: str, fallback: dict) -> dict:
    try:
        cleaned = content.replace("```json", "").replace("```", "").strip()
        return json.loads(cleaned)
    except Exception:
        return fallback


async def resume_parser(ctx: FunctionContext, data: ResumeParserInput) -> ResumeParserOutput:
    pod = Pod.from_env()

    # Read resume text from Lemma Files (auto-converted markdown for PDFs, or raw for text/markdown)
    try:
        if data.resume_file_path.lower().endswith('.pdf'):
            try:
                resume_bytes = pod.files.download_markdown(data.resume_file_path)
            except Exception:
                resume_bytes = pod.files.download(data.resume_file_path)
        else:
            resume_bytes = pod.files.download(data.resume_file_path)
        resume_text = resume_bytes.decode("utf-8") if isinstance(resume_bytes, bytes) else str(resume_bytes)
    except Exception as e:
        return ResumeParserOutput(error=f"Failed to read resume file: {e}")

    # Read NIM API key from pod secrets
    try:
        nim_key_bytes = pod.files.download(path="/secrets/nim_api_key.txt")
        nim_api_key = nim_key_bytes.decode("utf-8").strip() if isinstance(nim_key_bytes, bytes) else str(nim_key_bytes).strip()
    except Exception as e:
        return ResumeParserOutput(error=f"Failed to read NIM API key: {e}")

    system_prompt = """You extract structured information from resumes. Return ONLY valid JSON with no markdown formatting or backticks. Extract exactly these fields:
{
  "job_target": "current role or most recent target role, one phrase",
  "current_projects": "comma-separated list of active or recent projects with one-word descriptions",
  "upcoming_events": "any time-sensitive items mentioned like interviews, deadlines, or launches. Empty string if none found.",
  "company_context": "current employer or target companies mentioned"
}"""

    payload = {
        "model": "meta/llama-3.1-70b-instruct",
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": f"Extract structured information from this resume:\n\n{resume_text[:8000]}"}
        ],
        "max_tokens": 500,
        "temperature": 0.1
    }

    fallback = {"job_target": "", "current_projects": "", "upcoming_events": "", "company_context": ""}

    for attempt in range(3):
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                resp = await client.post(
                    "https://integrate.api.nvidia.com/v1/chat/completions",
                    headers={
                        "Authorization": f"Bearer {nim_api_key}",
                        "Content-Type": "application/json"
                    },
                    json=payload
                )
            if resp.status_code == 429:
                import asyncio
                await asyncio.sleep(2 * (attempt + 1))
                continue
            resp.raise_for_status()
            raw = resp.json()["choices"][0]["message"]["content"].strip()
            parsed = _safe_parse_json(raw, fallback)
            return ResumeParserOutput(
                job_target=parsed.get("job_target", ""),
                current_projects=parsed.get("current_projects", ""),
                upcoming_events=parsed.get("upcoming_events", ""),
                company_context=parsed.get("company_context", "")
            )
        except Exception as e:
            if attempt == 2:
                return ResumeParserOutput(error=f"NIM API failed: {e}")

    return ResumeParserOutput(error="NIM API exhausted retries")
