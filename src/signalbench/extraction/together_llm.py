from together import Together

from signalbench.extraction.schema import ExtractionResult

DEFAULT_MODEL = "deepseek-ai/DeepSeek-V4-Flash-0731"


class TogetherLLM:
    def __init__(
        self,
        model_version: str = DEFAULT_MODEL,
        api_key: str | None = None,
    ) -> None:
        self.model_version = model_version
        self._client = Together(api_key=api_key) if api_key else Together()

    def complete(self, prompt: str, raw_text: str) -> ExtractionResult:
        response = self._client.chat.completions.create(
            model=self.model_version,
            messages=[
                {
                    "role": "system",
                    "content": f"{prompt}\n\nRespond only in JSON.",
                },
                {"role": "user", "content": raw_text},
            ],
            response_format={
                "type": "json_schema",
                "json_schema": {
                    "name": "extraction_result",
                    "schema": ExtractionResult.model_json_schema(),
                },
            },
            temperature=0,
            reasoning={"enabled": False},
        )
        if not response.choices:
            raise ValueError("Together chat completion returned no choices")
        message = response.choices[0].message
        content = None if message is None else message.content
        if not content:
            raise ValueError("Together chat completion returned empty content")
        return ExtractionResult.model_validate_json(content)
