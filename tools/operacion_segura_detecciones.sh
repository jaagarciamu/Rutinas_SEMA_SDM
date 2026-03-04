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
  tools/operacion_segura_detecciones.sh [--new-folder "<URL_o_ID>"] [--show-pending 200]

Opciones:
  --new-folder     URL o ID de la carpeta de Drive (opcional)
                   Si no se envia, usa GOOGLE_DETECCIONES_DRIVE_FOLDER_ID de config/.env
  --show-pending   Cantidad de pendientes a mostrar (default: 200)

Variables opcionales:
  PY_BIN           Interprete Python a usar (default: python)
USAGE
}

NEW_FOLDER=""
SHOW_PENDING="200"

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
  NEW_FOLDER="${GOOGLE_DETECCIONES_DRIVE_FOLDER_ID:-}"
fi
if [[ -z "$NEW_FOLDER" ]]; then
  echo "No se encontro carpeta. Define --new-folder o GOOGLE_DETECCIONES_DRIVE_FOLDER_ID en config/.env" >&2
  usage
  exit 1
fi

cd "$ROOT_DIR"

STAMP="$(date +%Y%m%d_%H%M%S)"
PREVIEW_DIR="data/tmp"
PENDING_CSV="$PREVIEW_DIR/pendientes_preview_${STAMP}.csv"
REGISTRY_CSV="$PREVIEW_DIR/registro_preview_${STAMP}.csv"
ID_CHANGES_CSV="$PREVIEW_DIR/id_changes_preview_${STAMP}.csv"

mkdir -p "$PREVIEW_DIR"

echo "[Paso 1/3] Review (dry-run) + export previews"
"$PY_BIN" tools/sync_detecciones_registry.py \
  --new-folder "$NEW_FOLDER" \
  --show-pending "$SHOW_PENDING" \
  --pending-csv "$PENDING_CSV" \
  --preview-csv "$REGISTRY_CSV" \
  --show-id-changes "$SHOW_PENDING" \
  --id-changes-csv "$ID_CHANGES_CSV"

echo
echo "Preview pendientes: $ROOT_DIR/$PENDING_CSV"
echo "Preview registro : $ROOT_DIR/$REGISTRY_CSV"
echo "Preview cambiosID: $ROOT_DIR/$ID_CHANGES_CSV"
echo
read -r -p "Revisaste los pendientes y quieres aplicar sincronizacion en Google Sheets? (si/no): " CONFIRM_APPLY

if [[ "$CONFIRM_APPLY" != "si" ]]; then
  echo "Operacion cancelada antes de apply."
  exit 0
fi

echo "[Paso 2/3] Apply de sincronizacion"
"$PY_BIN" tools/sync_detecciones_registry.py \
  --new-folder "$NEW_FOLDER" \
  --apply

echo
read -r -p "Quieres ejecutar ahora el pipeline 01_DETECCIONES_SEMA.py? (si/no): " CONFIRM_PIPE

if [[ "$CONFIRM_PIPE" != "si" ]]; then
  echo "Sincronizacion aplicada. Pipeline no ejecutado."
  exit 0
fi

echo "[Paso 3/3] Ejecutando pipeline principal"
"$PY_BIN" pipelines/01_DETECCIONES_SEMA.py

echo "Operacion completa."
