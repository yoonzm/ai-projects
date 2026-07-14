from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.indexer import index_paths, sample_paths
from src.model_config import MODEL_NOT_CONFIGURED_MESSAGE, ModelNotConfiguredError, is_model_configured
from src.rag import ask, generate_material
from src.storage import get_enabled_model_config, init_db


def main() -> None:
    init_db()
    config = get_enabled_model_config()
    kb_id = "employee-demo"

    paths = sample_paths()
    assert len(paths) >= 5, f"expected >= 5 sample docs, got {len(paths)}"

    if not is_model_configured(config):
        try:
            index_paths(paths, kb_id, config)
        except ModelNotConfiguredError as exc:
            assert MODEL_NOT_CONFIGURED_MESSAGE in str(exc)
        else:
            raise AssertionError("indexing should fail before model configuration")

        answer = ask(
            question="出差住宿费标准是多少？如果超标怎么审批？",
            knowledge_base_id=kb_id,
            session_id="smoke",
            config=config,
            history=[],
        )
        assert answer.answer == MODEL_NOT_CONFIGURED_MESSAGE
        assert not answer.sources

        material, sources, calls = generate_material(
            material_type="流程清单",
            topic="申请差旅报销",
            knowledge_base_id=kb_id,
            config=config,
        )
        assert material == MODEL_NOT_CONFIGURED_MESSAGE
        assert not sources
        assert not calls

        print("SMOKE_TEST_OK")
        print("model_configured=false")
        return

    print("SMOKE_TEST_SKIPPED_REAL_MODEL")
    print("model_configured=true; configure a safe test key before running end-to-end model calls")


if __name__ == "__main__":
    main()
