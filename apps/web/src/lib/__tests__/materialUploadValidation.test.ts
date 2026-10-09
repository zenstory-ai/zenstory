import { describe, expect, it, vi } from "vitest";

vi.mock("../errorHandler", async (importOriginal) => ({
  ...(await importOriginal<typeof import("../errorHandler")>()),
  translateError: (code: string) => `translated:${code}`,
}));

import {
  MATERIALS_UPLOAD_MAX_BYTES,
  MATERIALS_UPLOAD_MAX_CHARACTERS,
  prepareMaterialUpload,
  resolveMaterialUploadErrorMessage,
  validateMaterialUploadFile,
} from "../materialUploadValidation";
import { truncateNovelText } from "../novelChapterSplit";
import { ApiError } from "../apiClient";

function createRepeatedGbkFile(charCount: number): File {
  const bytes = new Uint8Array(charCount * 2);
  for (let index = 0; index < bytes.length; index += 2) {
    bytes[index] = 0xd7;
    bytes[index + 1] = 0xd6;
  }

  return new File([bytes], "limit-ok-gbk.txt", { type: "text/plain" });
}

function createRepeatedUtf16LeFile(charCount: number): File {
  const bytes = new Uint8Array(charCount * 2 + 2);
  bytes[0] = 0xff;
  bytes[1] = 0xfe;

  for (let index = 2; index < bytes.length; index += 2) {
    bytes[index] = 0x57;
    bytes[index + 1] = 0x5b;
  }

  return new File([bytes], "limit-ok-utf16.txt", { type: "text/plain" });
}

describe("validateMaterialUploadFile", () => {
  const t = (key: string) => key;

  it("accepts files at the 300k-character limit", async () => {
    const file = new File(
      ["字".repeat(MATERIALS_UPLOAD_MAX_CHARACTERS)],
      "limit-ok.txt",
      { type: "text/plain" },
    );

    await expect(validateMaterialUploadFile(file, t)).resolves.toBeNull();
  });

  it("rejects files over the 300k-character limit and says how long the book is", async () => {
    const file = new File(
      ["字".repeat(MATERIALS_UPLOAD_MAX_CHARACTERS + 1)],
      "limit-too-long.txt",
      { type: "text/plain" },
    );
    const translate = vi.fn((key: string, options?: Record<string, unknown>) =>
      options ? `${key}:${String(options.wan)}` : key,
    );

    await expect(validateMaterialUploadFile(file, translate)).resolves.toBe(
      "materials:uploadModal.errors.tooManyCharactersCounted:30.0",
    );
  });

  it("accepts gbk files at the 300k-character limit", async () => {
    const file = createRepeatedGbkFile(MATERIALS_UPLOAD_MAX_CHARACTERS);

    await expect(validateMaterialUploadFile(file, t)).resolves.toBeNull();
  });

  it("accepts utf-16 files at the 300k-character limit", async () => {
    const file = createRepeatedUtf16LeFile(MATERIALS_UPLOAD_MAX_CHARACTERS);

    await expect(validateMaterialUploadFile(file, t)).resolves.toBeNull();
  });
});

describe("prepareMaterialUpload for the free trial", () => {
  const t = (key: string) => key;
  const sentence = "雾港的灯一盏盏亮起来，他把旧信折好放回怀里。";

  function wholeBook(chapters: number, charsPerChapter: number): string {
    const body = sentence.repeat(Math.ceil(charsPerChapter / sentence.length)).slice(0, charsPerChapter);
    return Array.from({ length: chapters }, (_, index) => `第${index + 1}章 雾港\n${body}`).join("\n");
  }

  it("accepts a whole book over 300k characters and uploads only its first chapters", async () => {
    const text = wholeBook(150, 2_800);
    expect(text.length).toBeGreaterThan(MATERIALS_UPLOAD_MAX_CHARACTERS);
    const file = new File([text], "whole-book.txt", { type: "text/plain" });

    const prepared = await prepareMaterialUpload(file, t, { trialMaxChapters: 20 });

    if (prepared.error !== null) throw new Error(prepared.error);
    expect(prepared.trial).toEqual({ totalChapters: 150, keptChapters: 20 });
    expect(prepared.file.name).toBe("whole-book.txt");
    const bytes = new Uint8Array(await prepared.file.arrayBuffer());
    expect(Array.from(bytes.slice(0, 3))).toEqual([0xef, 0xbb, 0xbf]);
    const uploaded = new TextDecoder("utf-8").decode(bytes);
    expect(uploaded).toContain("第20章 雾港");
    expect(uploaded).not.toContain("第21章");
    expect(truncateNovelText(uploaded, 1_000).totalChapters).toBe(20);
  });

  it("keeps the 300k-character limit for a paid upload of the same book", async () => {
    const file = new File([wholeBook(150, 2_800)], "whole-book.txt", { type: "text/plain" });

    const prepared = await prepareMaterialUpload(file, t);

    expect(prepared.error).toBe("materials:uploadModal.errors.tooManyCharactersCounted");
  });

  it("applies the limit to the first chapters of a trial", async () => {
    const file = new File([wholeBook(5, 160_000)], "huge-chapters.txt", { type: "text/plain" });

    const prepared = await prepareMaterialUpload(file, t, { trialMaxChapters: 2 });

    expect(prepared.error).toBe("materials:uploadModal.errors.trialTooManyCharacters");
  });

  it("uploads a non-UTF-8 book unchanged and lets the server cut it", async () => {
    const file = createRepeatedGbkFile(10);

    const prepared = await prepareMaterialUpload(file, t, { trialMaxChapters: 20 });

    expect(prepared).toMatchObject({ error: null, file });
  });
});

describe("material upload size limit", () => {
  const t = (key: string) => key;

  it("matches the backend 20MB limit", () => {
    expect(MATERIALS_UPLOAD_MAX_BYTES).toBe(20 * 1024 * 1024);
  });

  it("rejects files over 20MB before reading them", async () => {
    const file = new File(["x"], "big.txt", { type: "text/plain" });
    Object.defineProperty(file, "size", { value: MATERIALS_UPLOAD_MAX_BYTES + 1 });

    await expect(validateMaterialUploadFile(file, t)).resolves.toBe(
      "materials:uploadModal.errors.tooLarge",
    );
  });
});

describe("resolveMaterialUploadErrorMessage", () => {
  const t = (key: string) => key;

  it.each([
    "ERR_MATERIAL_NO_CHAPTERS",
    "ERR_MATERIAL_TOO_MANY_CHAPTERS",
    "ERR_FILE_ENCODING_UNSUPPORTED",
  ])("translates the upload pre-check rejection %s", (code) => {
    expect(
      resolveMaterialUploadErrorMessage(new ApiError(400, code), t, "fallback"),
    ).toBe(`translated:${code}`);
  });

  it("maps backend over-limit errors to materials-specific copy", () => {
    const error = new ApiError(400, "ERR_FILE_CONTENT_TOO_LONG");

    expect(
      resolveMaterialUploadErrorMessage(
        error,
        t,
        "materials:uploadError",
      ),
    ).toBe("materials:uploadModal.errors.tooManyCharacters");
  });

  it("uses the counted size the backend sends with an over-limit rejection", () => {
    const translate = (key: string, options?: Record<string, unknown>) =>
      options ? `${key}:${String(options.wan)}:${String(options.chapters)}` : key;
    const trialError = new ApiError(400, "ERR_FILE_CONTENT_TOO_LONG", {
      char_count: 312_000,
      limit: 300_000,
      trial_chapters: 20,
    });

    expect(resolveMaterialUploadErrorMessage(trialError, translate, "fallback")).toBe(
      "materials:uploadModal.errors.trialTooManyCharacters:31.2:20",
    );
  });
});
