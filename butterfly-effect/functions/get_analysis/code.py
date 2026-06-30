#input_type_name: GetAnalysisInput
#output_type_name: GetAnalysisOutput
#function_name: get_analysis

from pydantic import BaseModel
from lemma_sdk import FunctionContext, Pod
import json

class GetAnalysisInput(BaseModel):
    job_id: str

class GetAnalysisOutput(BaseModel):
    status: str
    current_layer: str = ""
    progress_pct: int = 0
    error_message: str = ""
    updated_at: str = ""
    completed_at: str = ""
    layers: dict

async def get_analysis(ctx: FunctionContext, data: GetAnalysisInput) -> GetAnalysisOutput:
    pod = Pod.from_env()
    
    # Check job
    jobs = pod.records.list("analysis_jobs", filter=[{"field":"id","op":"eq","value":data.job_id}]).to_dict()["items"]
    if not jobs:
        return GetAnalysisOutput(status="not_found", layers={})
        
    job = jobs[0]
    status = job.get("status", "running")
    
    # Get layers
    layer_records = pod.records.list("analysis_results", filter=[{"field":"job_id","op":"eq","value":data.job_id}]).to_dict()["items"]
    
    layers_dict = {}
    for r in layer_records:
        lname = r.get("layer_name")
        ldata = r.get("layer_data")
        
        if ldata and ldata != "{}":
            try:
                parsed = json.loads(ldata)
                parsed["status"] = r.get("status")
                layers_dict[lname] = parsed
            except:
                layers_dict[lname] = {"status": r.get("status")}
        else:
            layers_dict[lname] = {"status": r.get("status")}
            
    return GetAnalysisOutput(
        status=status,
        current_layer=job.get("current_layer", "") or "",
        progress_pct=int(job.get("progress_pct") or 0),
        error_message=job.get("error_message", "") or "",
        updated_at=job.get("updated_at", "") or "",
        completed_at=job.get("completed_at", "") or "",
        layers=layers_dict,
    )
