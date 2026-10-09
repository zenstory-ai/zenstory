import { ApiError } from "./apiClient";
import { translateError } from "./errorHandler";
import { countCharacters, truncateNovelText } from "./novelChapterSplit";

type Translate = (key: string, options?: Record<string, unknown>) => string;

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

interface DecodedUpload {
  text: string;
  /** Decoded as UTF-8 (strictly or by BOM): safe to re-encode as UTF-8. */
  utf8: boolean;
}

async function readMaterialUploadText(file: File): Promise<DecodedUpload | null> {
  const bytes = new Uint8Array(await file.arrayBuffer());
  const bomEncoding = detectBomEncoding(bytes);
  if (bomEncoding) {
    const text = decodeText(bytes, bomEncoding);
    return text === null ? null : { text, utf8: bomEncoding === "utf-8" };
  }

  for (const encoding of ["utf-8", "gb18030"]) {
    const decoded = decodeText(bytes, encoding);
    if (decoded !== null) {
      return { text: decoded, utf8: encoding === "utf-8" };
    }
  }

  return null;
}

function tooManyCharactersMessage(
  t: Translate,
  charCount: number,
  trialChapters?: number,
): string {
  const counts = {
    wan: (charCount / 10_000).toFixed(1),
    chars: charCount.toLocaleString("en-US"),
  };
  return trialChapters
    ? t("materials:uploadModal.errors.trialTooManyCharacters", { ...counts, chapters: trialChapters })
    : t("materials:uploadModal.errors.tooManyCharactersCounted", counts);
}

// Upload pre-check rejections (nothing is charged) translated via the errors namespace.
const UPLOAD_PRECHECK_ERROR_CODES = new Set([
  "ERR_MATERIAL_NO_CHAPTERS",
  "ERR_MATERIAL_TOO_MANY_CHAPTERS",
  "ERR_FILE_ENCODING_UNSUPPORTED",
]);

/** What a free trial will upload, for the per-book note in the upload dialog. */
export interface MaterialTrialSelection {
  totalChapters: number;
  keptChapters: number;
}

export type PreparedMaterialUpload =
  | { error: string }
  | { error: null; file: File; trial: MaterialTrialSelection | null };

/**
 * Check a picked file before upload and return what to send.
 *
 * With `trialMaxChapters` (free trial), only the first chapters count toward
 * the character limit, and a UTF-8 file with more chapters is cut to those
 * chapters before upload (the server applies the same cut either way).
 */
export async function prepareMaterialUpload(
  file: File,
  t: Translate,
  options: { trialMaxChapters?: number | null } = {},
): Promise<PreparedMaterialUpload> {
  if (!file.name.toLowerCase().endsWith(".txt")) {
    return { error: t("materials:uploadModal.errors.invalidType") };
  }

  if (file.size > MATERIALS_UPLOAD_MAX_BYTES) {
    return { error: t("materials:uploadModal.errors.tooLarge") };
  }

  const trialMaxChapters = options.trialMaxChapters ?? null;
  try {
    const decoded = await readMaterialUploadText(file);
    if (decoded === null) {
      // The backend remains the source of truth for encodings the browser can't read.
      return { error: null, file, trial: null };
    }
    if (trialMaxChapters !== null && trialMaxChapters > 0) {
      const kept = truncateNovelText(decoded.text, trialMaxChapters);
      const keptCharacters = countCharacters(kept.text);
      if (keptCharacters > MATERIALS_UPLOAD_MAX_CHARACTERS) {
        return {
          error: tooManyCharactersMessage(
            t,
            keptCharacters,
            kept.truncated ? kept.keptChapters : undefined,
          ),
        };
      }
      const trial =
        kept.totalChapters > 0
          ? { totalChapters: kept.totalChapters, keptChapters: kept.keptChapters }
          : null;
      if (kept.truncated && decoded.utf8) {
        // The BOM makes the server decode the cut text as UTF-8 unambiguously.
        const cutFile = new File(["﻿", kept.text], file.name, { type: "text/plain" });
        return { error: null, file: cutFile, trial };
      }
      return { error: null, file, trial };
    }
    const characters = countCharacters(decoded.text);
    if (characters > MATERIALS_UPLOAD_MAX_CHARACTERS) {
      return { error: tooManyCharactersMessage(t, characters) };
    }
  } catch {
    // Let the backend remain the source of truth if the browser cannot read the file.
  }

  return { error: null, file, trial: null };
}

export async function validateMaterialUploadFile(
  file: File,
  t: Translate,
): Promise<string | null> {
  return (await prepareMaterialUpload(file, t)).error;
}

export function resolveMaterialUploadErrorMessage(
  error: unknown,
  t: Translate,
  fallback: string,
): string {
  if (error instanceof ApiError) {
    if (error.errorCode === "ERR_FILE_CONTENT_TOO_LONG") {
      const charCount = error.details?.char_count;
      if (typeof charCount === "number") {
        const trialChapters = error.details?.trial_chapters;
        return tooManyCharactersMessage(
          t,
          charCount,
          typeof trialChapters === "number" ? trialChapters : undefined,
        );
      }
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
