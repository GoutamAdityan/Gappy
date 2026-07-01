#input_type_name: UploadResumeInput
#output_type_name: UploadResumeOutput
#function_name: upload_resume

import base64
import os
import tempfile
from pydantic import BaseModel
from lemma_sdk import FunctionContext, Pod


class UploadResumeInput(BaseModel):
    file_content_base64: str
    filename: str


class UploadResumeOutput(BaseModel):
    path: str
    error: str = ""


async def upload_resume(ctx: FunctionContext, data: UploadResumeInput) -> UploadResumeOutput:
    try:
        safe_name = data.filename.replace(" ", "_")
        dest_path = f"/resumes/{safe_name}"
        file_bytes = base64.b64decode(data.file_content_base64)
        pod = Pod.from_env()

        suffix = os.path.splitext(safe_name)[1] or ".pdf"
        with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
            tmp.write(file_bytes)
            tmp_path = tmp.name

        try:
            pod.files.upload(local_path=tmp_path, path=dest_path)
        finally:
            os.unlink(tmp_path)

        return UploadResumeOutput(path=dest_path)
    except Exception as e:
        return UploadResumeOutput(path="", error=str(e))
