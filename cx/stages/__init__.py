"""cx pipeline stages + build helper (mirrors DATA/pipeline/stages/__init__.py)."""
from cx.stages.provision import EnsureSchemaStage
from cx.stages.api_stages import (PairsStage, NinjaStage, BackfillStage, UniquesStage,
                                  TradeDictStage)


def build_stages(full: bool = False):
    """The hourly cycle (provision -> pairs + ninja), or with `full` the Actualize
    DAG: the same three plus backfill (after pairs), uniques (after provision)
    and the trade dictionary (independent) -- the runner overlaps what it can."""
    stages = [EnsureSchemaStage(), PairsStage(), NinjaStage()]
    if full:
        stages += [BackfillStage(), UniquesStage(), TradeDictStage()]
    return stages


# UI grid layout
STAGE_LAYOUT = [
    ["cx00_provision", "cx10_pairs", "cx12_ninja", "cx11_backfill"],
    ["cx20_uniques", "cx30_trade_dict"],
]
