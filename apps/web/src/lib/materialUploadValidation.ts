import { ApiError } from "./apiClient";
import { translateError } from "./errorHandler";

// Keep in sync with apps/server/api/materials/constants.py (MAX_FILE_SIZE).
export const MATERIALS_UPLOAD_MAX_BYTES = 20 * 1024 * 1024;
export const MATERIALS_UPLOAD_MAX_CHARACTERS = 300_000;

const UTF8_BOM = [0xef, 0xbb, 0xbf];
const UTF16_LE_BOM = [0xff, 0xfe];
const UTF16_BE_BOM = [0xfe, 0xff];

function hasPrefix(bytes: Uint8Array, prefix: number[]): boolean {
  return prefix.every((value, index) => bytes[index] === value);
}

function detectBomEncoding(bytes: Uint8Array): string | null {
  if (bytes.length >= UTF8_BOM.length && hasPrefix(bytes, UTF8_BOM)) {
    return "utf-8";
  }
  if (bytes.length >= UTF16_LE_BOM.length && hasPrefix(bytes, UTF16_LE_BOM)) {
    return "utf-16le";
  }
  if (bytes.length >= UTF16_BE_BOM.length && hasPrefix(bytes, UTF16_BE_BOM)) {
    return "utf-16be";
  }
  return null;
}

function decodeText(bytes: Uint8Array, encoding: string): string | null {
  try {
    return new TextDecoder(encoding, { fatal: true }).decode(bytes);
  } catch {
    return null;
  }
}

async function readMaterialUploadText(file: File): Promise<string | null> {
  const bytes = new Uint8Array(await file.arrayBuffer());
  const bomEncoding = detectBomEncoding(bytes);
  if (bomEncoding) {
    return decodeText(bytes, bomEncoding);
  }

  for (const encoding of ["utf-8", "gb18030"]) {
    const decoded = decodeText(bytes, encoding);
    if (decoded !== null) {
      return decoded;
    }
  }

  return null;
}

// Upload pre-check rejections (nothing is charged) translated via the errors namespace.
const UPLOAD_PRECHECK_ERROR_CODES = new Set([
  "ERR_MATERIAL_NO_CHAPTERS",
  "ERR_MATERIAL_TOO_MANY_CHAPTERS",
  "ERR_FILE_ENCODING_UNSUPPORTED",
]);

export async function validateMaterialUploadFile(
  file: File,
  t: (key: string) => string,
): Promise<string | null> {
  if (!file.name.toLowerCase().endsWith(".txt")) {
    return t("materials:uploadModal.errors.invalidType");
  }

  if (file.size > MATERIALS_UPLOAD_MAX_BYTES) {
    return t("materials:uploadModal.errors.tooLarge");
  }

  try {
    const content = await readMaterialUploadText(file);
    if (content !== null && content.length > MATERIALS_UPLOAD_MAX_CHARACTERS) {
      return t("materials:uploadModal.errors.tooManyCharacters");
    }
  } catch {
    // Let the backend remain the source of truth if the browser cannot read the file.
  }

  return null;
}

export function resolveMaterialUploadErrorMessage(
  error: unknown,
  t: (key: string) => string,
  fallback: string,
): string {
  if (error instanceof ApiError) {
    if (error.errorCode === "ERR_FILE_CONTENT_TOO_LONG") {
      return t("materials:uploadModal.errors.tooManyCharacters");
    }
    if (error.errorCode === "ERR_FILE_TOO_LARGE") {
      return t("materials:uploadModal.errors.tooLarge");
    }
    if (error.errorCode === "ERR_FILE_TYPE_INVALID") {
      return t("materials:uploadModal.errors.invalidType");
    }
    if (error.errorCode && UPLOAD_PRECHECK_ERROR_CODES.has(error.errorCode)) {
      return translateError(error.errorCode);
    }
  }

  return error instanceof Error ? error.message : fallback;
}
