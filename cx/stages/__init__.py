"""cx pipeline stages + build helper (mirrors DATA/pipeline/stages/__init__.py)."""
from cx.stages.provision import EnsureSchemaStage
from cx.stages.api_stages import PairsStage


def build_stages():
    return [
        EnsureSchemaStage(),
        PairsStage(),
    ]


# UI grid layout (one row for now)
STAGE_LAYOUT = [
    ["cx00_provision", "cx10_pairs"],
]
