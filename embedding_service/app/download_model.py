"""Build-time downloader for only the pinned model artifacts."""

import os
from pathlib import Path

from huggingface_hub import snapshot_download

from app.constants import MODEL_ALLOW_PATTERNS, MODEL_IDENTIFIER, MODEL_REVISION


def main() -> None:
    cache_dir = Path(os.environ.get("EMBEDDING_MODEL_CACHE_DIR", "/models"))
    snapshot_download(
        repo_id=MODEL_IDENTIFIER,
        revision=MODEL_REVISION,
        allow_patterns=list(MODEL_ALLOW_PATTERNS),
        cache_dir=cache_dir,
    )


if __name__ == "__main__":
    main()
