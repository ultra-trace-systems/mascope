from pydantic import BaseModel, Field, model_validator


class FileDownloadBody(BaseModel):
    sample_file_ids: list[str] = Field(
        ..., description="The sample file IDs to download"
    )


class MzMLExportBody(BaseModel):
    sample_file_ids: list[str] = Field(
        default_factory=list, description="The sample files to write whole"
    )
    sample_item_ids: list[str] = Field(
        default_factory=list,
        description="The samples to write the time windows of: each one's scans "
        "of its polarity between its t0 and t1",
    )

    @model_validator(mode="after")
    def _names_something(self) -> "MzMLExportBody":
        if not self.sample_file_ids and not self.sample_item_ids:
            raise ValueError("Give at least one sample file or sample")
        return self
