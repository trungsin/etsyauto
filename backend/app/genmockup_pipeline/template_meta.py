"""Pydantic schema for template meta.json — defines product, quad, blend mode, etc."""
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field, field_validator


class TemplateMeta(BaseModel):
    product: Literal["tshirt", "poster", "canvas", "pillow"]
    angle: str
    color: str | None = None
    scene_type: Literal["flat", "lifestyle"]
    quad: list[tuple[float, float]]
    mask: str | None = None
    blend: Literal["normal", "multiply", "screen"] = "normal"
    output_size: tuple[int, int] = (2000, 2000)
    marketplace_safe: bool = True

    @field_validator("quad")
    @classmethod
    def quad_must_have_4_points(cls, value: list[tuple[float, float]]) -> list[tuple[float, float]]:
        if len(value) != 4:
            raise ValueError("quad must contain exactly 4 points")
        return value

    @field_validator("mask")
    @classmethod
    def mask_must_be_relative(cls, value: str | None) -> str | None:
        if value and Path(value).is_absolute():
            raise ValueError("mask path must be relative")
        if value and ".." in Path(value).parts:
            raise ValueError("mask path cannot escape template dir")
        return value
