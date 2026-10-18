#!/usr/bin/env bash
set -euo pipefail
umask 077

PROJECT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
if [[ -f "$PROJECT_DIR/.env" ]]; then
  set -a
  # .env is trusted local shell configuration, never supplied by an upload.
  source "$PROJECT_DIR/.env"
  set +a
fi

absolute_path() {
  case "$1" in
    /*) printf '%s\n' "$1" ;;
    *) printf '%s/%s\n' "$PROJECT_DIR" "$1" ;;
  esac
}
export KASSA_RUNTIME="$(absolute_path "${KASSA_RUNTIME:-backend/runtime}")"
export KASSA_MODELS_DIR="$(absolute_path "${KASSA_MODELS_DIR:-backend/models}")"
export KASSA_VLM_CHECKPOINT="$(absolute_path "${KASSA_VLM_CHECKPOINT:-backend/models/vlm}")"
export KASSA_UI_DIST="$(absolute_path "${KASSA_UI_DIST:-frontend/dist}")"
export KASSA_API_URL="${KASSA_API_URL:-http://127.0.0.1:${KASSA_API_PORT:-18861}}"
export HF_HOME="${HF_HOME:-$PROJECT_DIR/backend/cache/huggingface}"
export TORCH_HOME="${TORCH_HOME:-$PROJECT_DIR/backend/cache/torch}"
export XDG_CACHE_HOME="${XDG_CACHE_HOME:-$PROJECT_DIR/backend/cache/xdg}"
export TOKENIZERS_PARALLELISM=false
export PYTHONDONTWRITEBYTECODE=1
export PYTHONPATH="$PROJECT_DIR:$PROJECT_DIR/backend${PYTHONPATH:+:$PYTHONPATH}"
export ANNOTATION_DATASET="$(absolute_path "${ANNOTATION_DATASET:-data/dataset}")"
export ANNOTATION_RUNTIME="$(absolute_path "${ANNOTATION_RUNTIME:-$KASSA_RUNTIME/annotations}")"

ACTION="${1:-help}"
if [[ $# -gt 0 ]]; then shift; fi
if [[ "$ACTION" == help || "$ACTION" == -h || "$ACTION" == --help ]]; then
  printf '%s\n' 'Usage: ./run.sh {ui|annotate|demo|api|prepare|smoke|test}'
  exit 0
fi
PYTHON_RUN="${KASSA_PYTHON:-$PROJECT_DIR/.venv/bin/python}"
if [[ ! -x "$PYTHON_RUN" ]]; then
  printf '%s\n' 'Python environment not found. Run ./setup.sh or set KASSA_PYTHON.' >&2
  exit 1
fi
cd "$PROJECT_DIR/backend"

if [[ "$ACTION" == prepare ]]; then
  export HF_HUB_OFFLINE=0 TRANSFORMERS_OFFLINE=0
else
  export HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1
fi

case "$ACTION" in
  annotate)
    exec "$PYTHON_RUN" -m uvicorn annotation_service.app:create_app --factory \
      --host 127.0.0.1 --port "${ANNOTATION_PORT:-18862}" --no-access-log "$@"
    ;;
  demo)
    exec "$PYTHON_RUN" "$PROJECT_DIR/scripts/create_demo.py" --dataset "$ANNOTATION_DATASET" "$@"
    ;;
  api)
    exec "$PYTHON_RUN" -m service.launch --gpu "${KASSA_GPU:-auto}" \
      --host "${KASSA_API_HOST:-127.0.0.1}" --port "${KASSA_API_PORT:-18861}" "$@"
    ;;
  ui)
    exec "$PYTHON_RUN" -m service.ui_gateway --host "${KASSA_UI_HOST:-127.0.0.1}" \
      --port "${KASSA_UI_PORT:-18863}" "$@"
    ;;
  prepare)
    exec "$PYTHON_RUN" prepare.py "$@"
    ;;
  smoke)
    exec "$PYTHON_RUN" -m service.smoke_jury "$@"
    ;;
  test)
    # Tests must never inherit the operator's real annotation workspace.
    unset ANNOTATION_DATASET ANNOTATION_RUNTIME KASSA_RUNTIME KASSA_API_KEY
    "$PYTHON_RUN" -m unittest discover -s tests -p 'test_*.py' "$@"
    cd "$PROJECT_DIR"
    "$PYTHON_RUN" -m unittest discover -s annotation_service/tests -p 'test_*.py' "$@"
    "$PYTHON_RUN" -m unittest discover -s scripts/tests -p 'test_*.py' "$@"
    cd "$PROJECT_DIR/frontend"
    npm test
    npm run build
    ;;
  help|-h|--help)
    printf '%s\n' 'Usage: ./run.sh {ui|annotate|demo|api|prepare|smoke|test}'
    ;;
  *)
    printf 'Unknown command: %s\n' "$ACTION" >&2
    exit 2
    ;;
esac
