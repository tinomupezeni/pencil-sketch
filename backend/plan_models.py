"""
The versioned wire contract for POST /api/sketch's drawing plan.

A DrawingPlan is the API's source of truth: it is constructed once by
planner.build_drawing_plan() and never mutated afterwards, so every instance
that exists has already passed _check_plan_invariants below. That function
encodes the executable-sequence rules (pen state, stroke coverage, id
references) that no single field's type can express alone.

Backward compatibility: `strokes` keeps the same points/width/shade/kind
shape the pre-planner API returned (just reordered + annotated), so the
existing frontend (frontend/main.js), which only reads those four stroke
fields plus top-level width/height, keeps working unmodified against a
planner-produced response. `schemaVersion`, `actions`, `metadata`, and the
per-stroke annotation fields are purely additive.
"""

from __future__ import annotations

import math
import re
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field, field_validator, model_validator

SCHEMA_VERSION = "1.1.0"  # Phase 9: added optional per-stroke `color`

_HEX_COLOR_RE = re.compile(r"^#[0-9a-fA-F]{6}$")


class StrokeKind(str, Enum):
    LINE = "line"
    HATCH = "hatch"


class ActionType(str, Enum):
    LIFT = "lift"   # pen DOWN -> UP, no movement
    MOVE = "move"   # pen UP, travel to (x, y) without drawing
    DRAW = "draw"   # pen UP -> DOWN, traces the referenced stroke, ends DOWN
    PAUSE = "pause"  # no pen-state change, no movement; just elapsed time


def _is_finite(value: float) -> bool:
    return math.isfinite(value)


class StrokeAnnotation(BaseModel):
    id: int
    points: list[tuple[float, float]]
    width: float
    shade: float
    kind: StrokeKind
    centroid: tuple[float, float]
    arc_length: float
    bbox: tuple[float, float, float, float]  # (min_x, min_y, max_x, max_y)
    region_id: str
    stage: int = Field(ge=1, le=6)
    importance: float = Field(ge=0, le=1)
    estimated_duration: float = Field(ge=0)
    session_id: int = -1  # -1 means "not assigned to a work session"; real plans always set it
    is_construction: bool = False
    color: Optional[str] = None  # "#rrggbb"; None means neutral graphite (default, backward compatible)

    @field_validator("color")
    @classmethod
    def _color_is_hex(cls, v: Optional[str]) -> Optional[str]:
        if v is not None and not _HEX_COLOR_RE.match(v):
            raise ValueError("color must be a '#rrggbb' hex string")
        return v

    @field_validator("points")
    @classmethod
    def _points_valid(cls, v: list[tuple[float, float]]) -> list[tuple[float, float]]:
        if len(v) < 2:
            raise ValueError("stroke must have at least 2 points")
        for x, y in v:
            if not (_is_finite(x) and _is_finite(y)):
                raise ValueError("stroke point coordinates must be finite")
        return v

    @field_validator("centroid", "bbox")
    @classmethod
    def _geometry_finite(cls, v: tuple[float, ...]) -> tuple[float, ...]:
        if not all(_is_finite(c) for c in v):
            raise ValueError("geometry values must be finite")
        return v

    @field_validator("arc_length", "estimated_duration", "width", "shade")
    @classmethod
    def _scalar_finite(cls, v: float) -> float:
        if not _is_finite(v):
            raise ValueError("value must be finite")
        return v


class Action(BaseModel):
    type: ActionType
    duration: float = Field(ge=0)
    stroke_id: Optional[int] = None
    x: Optional[float] = None
    y: Optional[float] = None

    @field_validator("duration")
    @classmethod
    def _duration_finite(cls, v: float) -> float:
        if not _is_finite(v):
            raise ValueError("duration must be finite")
        return v

    @model_validator(mode="after")
    def _check_discriminant_shape(self) -> "Action":
        if self.type == ActionType.DRAW:
            if self.stroke_id is None:
                raise ValueError("draw action requires stroke_id")
            if self.x is not None or self.y is not None:
                raise ValueError("draw action must not carry x/y")
        elif self.type == ActionType.MOVE:
            if self.x is None or self.y is None:
                raise ValueError("move action requires x and y")
            if not (_is_finite(self.x) and _is_finite(self.y)):
                raise ValueError("move coordinates must be finite")
            if self.stroke_id is not None:
                raise ValueError("move action must not carry stroke_id")
        else:  # lift, pause: no payload
            if self.stroke_id is not None or self.x is not None or self.y is not None:
                raise ValueError(f"{self.type.value} action must not carry stroke_id/x/y")
        return self


class DrawingPlan(BaseModel):
    schemaVersion: str = SCHEMA_VERSION
    width: int = Field(gt=0)
    height: int = Field(gt=0)
    strokes: list[StrokeAnnotation]
    actions: list[Action]
    metadata: dict

    @model_validator(mode="after")
    def _check_invariants(self) -> "DrawingPlan":
        errors = check_plan_invariants(self)
        if errors:
            raise ValueError("invalid drawing plan: " + "; ".join(errors))
        return self


def check_plan_invariants(plan: DrawingPlan) -> list[str]:
    """Whole-plan rules no single field validator can express alone.

    Returns a list of human-readable problems; empty means the plan is a
    valid, executable action sequence that draws every stroke exactly once.
    """
    errors: list[str] = []

    stroke_ids = [s.id for s in plan.strokes]
    stroke_id_set = set(stroke_ids)
    if len(stroke_ids) != len(stroke_id_set):
        errors.append("duplicate stroke ids in strokes list")

    pen_state = "up"  # pencil starts lifted, at a nominal rest position
    drawn_ids: list[int] = []
    for i, action in enumerate(plan.actions):
        if action.type == ActionType.DRAW:
            if pen_state != "up":
                errors.append(f"action[{i}] draw occurs while pen is already down (missing lift)")
            if action.stroke_id not in stroke_id_set:
                errors.append(f"action[{i}] draws unknown stroke_id {action.stroke_id}")
            else:
                drawn_ids.append(action.stroke_id)
            pen_state = "down"
        elif action.type == ActionType.LIFT:
            if pen_state != "down":
                errors.append(f"action[{i}] lift occurs while pen is already up")
            pen_state = "up"
        elif action.type == ActionType.MOVE:
            if pen_state != "up":
                errors.append(f"action[{i}] move occurs while pen is down")
        # pause: no state requirement

    if len(drawn_ids) != len(set(drawn_ids)):
        errors.append("at least one stroke is drawn more than once")

    missing = stroke_id_set - set(drawn_ids)
    if missing:
        preview = sorted(missing)[:10]
        errors.append(f"{len(missing)} stroke(s) generated but never scheduled to draw: {preview}")

    extra = set(drawn_ids) - stroke_id_set
    if extra:
        errors.append(f"actions reference stroke ids absent from strokes: {sorted(extra)[:10]}")

    return errors
