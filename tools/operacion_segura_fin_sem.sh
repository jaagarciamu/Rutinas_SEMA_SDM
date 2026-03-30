#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PY_BIN="${PY_BIN:-python}"

load_env_folder_default() {
  # shellcheck disable=SC1091
  if [[ -f "$ROOT_DIR/config/.env" ]]; then
    set -a
    source "$ROOT_DIR/config/.env"
    set +a
  elif [[ -f "$ROOT_DIR/.env" ]]; then
    set -a
    source "$ROOT_DIR/.env"
    set +a
  fi
}

usage() {
  cat <<USAGE
Uso:
  tools/operacion_segura_fin_sem.sh [--new-folder "<URL_o_ID>"] [--show-pending 200] [--min-size-mb 50] [--max-size-mb 100] [--target-size-mb 67]

Opciones:
  --new-folder     URL o ID de la carpeta historica (opcional)
                   Si no se envia, usa GOOGLE_DETECCIONES_DRIVE_FOLDER_ID de config/.env
  --show-pending   Cantidad de pendientes FIN SEM a mostrar (default: 200)
  --min-size-mb    Tamano minimo permitido para procesar pendientes (default: 50)
  --max-size-mb    Tamano maximo permitido para procesar pendientes (default: 100)
  --target-size-mb Tamano objetivo para priorizar el archivo canonico (default: 67)

Variables opcionales:
  PY_BIN           Interprete Python a usar (default: python)
USAGE
}

NEW_FOLDER=""
SHOW_PENDING="200"
MIN_SIZE_MB="50"
MAX_SIZE_MB="100"
TARGET_SIZE_MB="67"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --new-folder)
      NEW_FOLDER="${2:-}"
      shift 2
      ;;
    --show-pending)
      SHOW_PENDING="${2:-200}"
      shift 2
      ;;
    --min-size-mb)
      MIN_SIZE_MB="${2:-50}"
      shift 2
      ;;
    --max-size-mb)
      MAX_SIZE_MB="${2:-100}"
      shift 2
      ;;
    --target-size-mb)
      TARGET_SIZE_MB="${2:-67}"
      shift 2
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "Parametro no reconocido: $1" >&2
      usage
      exit 1
      ;;
  esac
done

load_env_folder_default
if [[ -z "$NEW_FOLDER" ]]; then
  NEW_FOLDER="${GOOGLE_DETECCIONES_DRIVE_FOLDER_ID:-${FIN_SEM_SEMA_HISTORICO_DRIVE_FOLDER_ID:-}}"
fi
if [[ -z "$NEW_FOLDER" ]]; then
  echo "No se encontro carpeta. Define --new-folder o GOOGLE_DETECCIONES_DRIVE_FOLDER_ID en config/.env" >&2
  usage
  exit 1
fi

cd "$ROOT_DIR"

STAMP="$(date +%Y%m%d_%H%M%S)"
PREVIEW_DIR="data/tmp"
PENDING_CSV="$PREVIEW_DIR/fin_sem_pendientes_preview_${STAMP}.csv"
REGISTRY_CSV="$PREVIEW_DIR/fin_sem_registro_preview_${STAMP}.csv"
ID_CHANGES_CSV="$PREVIEW_DIR/fin_sem_id_changes_preview_${STAMP}.csv"

mkdir -p "$PREVIEW_DIR"

echo "[Paso 1/3] Review (dry-run) + export previews FIN SEM"
"$PY_BIN" tools/sync_fin_sem_registry.py \
  --new-folder "$NEW_FOLDER" \
  --show-pending "$SHOW_PENDING" \
  --min-size-mb "$MIN_SIZE_MB" \
  --max-size-mb "$MAX_SIZE_MB" \
  --target-size-mb "$TARGET_SIZE_MB" \
  --pending-csv "$PENDING_CSV" \
  --preview-csv "$REGISTRY_CSV" \
  --show-id-changes "$SHOW_PENDING" \
  --id-changes-csv "$ID_CHANGES_CSV"

echo
echo "Filtro tamano pendiente: ${MIN_SIZE_MB} MB a ${MAX_SIZE_MB} MB (objetivo ${TARGET_SIZE_MB} MB)"
echo "Preview pendientes: $ROOT_DIR/$PENDING_CSV"
echo "Preview registro : $ROOT_DIR/$REGISTRY_CSV"
echo "Preview cambiosID: $ROOT_DIR/$ID_CHANGES_CSV"
echo
read -r -p "Revisaste los pendientes FIN SEM y quieres aplicar sincronizacion en Google Sheets? (si/no): " CONFIRM_APPLY

if [[ "$CONFIRM_APPLY" != "si" ]]; then
  echo "Operacion cancelada antes de apply."
  exit 0
fi

echo "[Paso 2/3] Apply de sincronizacion FIN SEM"
"$PY_BIN" tools/sync_fin_sem_registry.py \
  --new-folder "$NEW_FOLDER" \
  --apply

echo
read -r -p "Quieres ejecutar ahora el pipeline 08_FIN_SEM_SEMA.py? (si/no): " CONFIRM_PIPE

if [[ "$CONFIRM_PIPE" != "si" ]]; then
  echo "Sincronizacion aplicada. Pipeline no ejecutado."
  exit 0
fi

echo "[Paso 3/3] Ejecutando pipeline FIN SEM"
export DETECCIONES_SEMA_MIN_SIZE_MB="$MIN_SIZE_MB"
export DETECCIONES_SEMA_MAX_SIZE_MB="$MAX_SIZE_MB"
export DETECCIONES_SEMA_TARGET_SIZE_MB="$TARGET_SIZE_MB"
"$PY_BIN" pipelines/08_FIN_SEM_SEMA.py

echo "Operacion completa."
