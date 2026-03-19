FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    DEBIAN_FRONTEND=noninteractive \
    PYTHONPATH=/app/src

ARG INSTALL_EXTRAS=server
ARG TORCH_CPU_INDEX_URL=https://download.pytorch.org/whl/cpu
ARG TORCH_VERSION=2.9.1+cpu
ARG TORCHVISION_VERSION=0.24.1+cpu
ENV PIP_EXTRA_INDEX_URL=${TORCH_CPU_INDEX_URL}

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates \
    libgl1 \
    libglib2.0-0 \
    tesseract-ocr \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml supabase_schema.sql README.md AGENTS_EXECUTION_PLAN.md COMMANDS_REFERENCE.md CAG_CHUNKING_STRATEGY.md ./
COPY prompts ./prompts
COPY domain ./domain
COPY src ./src
COPY scripts/use_env.sh /app/use_env.sh
COPY scripts/domain_wizard.py /app/scripts/domain_wizard.py

RUN pip install --upgrade pip setuptools wheel \
    && printf "torch==%s\ntorchvision==%s\n" "${TORCH_VERSION}" "${TORCHVISION_VERSION}" > /tmp/pip-constraints.txt \
    && pip install --no-cache-dir \
       --extra-index-url "${TORCH_CPU_INDEX_URL}" \
       "torch==${TORCH_VERSION}" \
       "torchvision==${TORCHVISION_VERSION}" \
    && pip install --no-cache-dir -c /tmp/pip-constraints.txt ".[${INSTALL_EXTRAS}]" \
    && chmod +x /app/use_env.sh

# Enforce CPU-only ML wheels in server images.
RUN python - <<'PY'
from importlib import metadata, util

if util.find_spec("torch") is not None:
    import torch

    if getattr(torch.version, "cuda", None):
        raise SystemExit(
            f"CUDA-enabled torch detected ({torch.__version__}); "
            "use CPU-only wheels for VPS builds."
        )

nvidia_pkgs = sorted(
    dist.metadata.get("Name", "")
    for dist in metadata.distributions()
    if (dist.metadata.get("Name", "") or "").startswith("nvidia-")
)
if nvidia_pkgs:
    raise SystemExit(
        "CUDA NVIDIA python packages detected in image: " + ", ".join(nvidia_pkgs)
    )
PY

RUN groupadd -r app && useradd -r -g app app && chown -R app:app /app
USER app
