"""Pinned, security-reviewed embedding model identity."""

MODEL_IDENTIFIER = "jinaai/jina-embeddings-v2-base-code"
MODEL_REVISION = "516f4baf13dec4ddddda8631e019b5737c8bc250"
MODEL_DIMENSION = 768
MODEL_MAX_TOKENS = 8192
MODEL_NORMALIZED = True
MODEL_ALLOW_PATTERNS = (
    "config.json",
    "onnx/model.onnx",
    "special_tokens_map.json",
    "tokenizer.json",
    "tokenizer_config.json",
)
