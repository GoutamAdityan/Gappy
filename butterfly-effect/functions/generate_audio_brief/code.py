#input_type_name: GenerateAudioInput
#output_type_name: GenerateAudioOutput
#function_name: generate_audio_brief
from pydantic import BaseModel
from lemma_sdk import FunctionContext

class GenerateAudioInput(BaseModel):
    text: str

class GenerateAudioOutput(BaseModel):
    audio_base64: str
    error: str = ""

async def generate_audio_brief(ctx: FunctionContext, data: GenerateAudioInput) -> GenerateAudioOutput:
    _ = data
    return GenerateAudioOutput(
        audio_base64="",
        error="Audio generation is disabled in this build. UI button is mock-only."
    )
