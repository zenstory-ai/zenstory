import { describe, expect, it, vi } from "vitest";

vi.mock("../errorHandler", async (importOriginal) => ({
  ...(await importOriginal<typeof import("../errorHandler")>()),
  translateError: (code: string) => `translated:${code}`,
}));

import {
  MATERIALS_UPLOAD_MAX_BYTES,
  MATERIALS_UPLOAD_MAX_CHARACTERS,
  resolveMaterialUploadErrorMessage,
  validateMaterialUploadFile,
} from "../materialUploadValidation";
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

  it("rejects files over the 300k-character limit", async () => {
    const file = new File(
      ["字".repeat(MATERIALS_UPLOAD_MAX_CHARACTERS + 1)],
      "limit-too-long.txt",
      { type: "text/plain" },
    );

    await expect(validateMaterialUploadFile(file, t)).resolves.toBe(
      "materials:uploadModal.errors.tooManyCharacters",
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
        "materials:uploadModal.errors.uploadFailed",
      ),
    ).toBe("materials:uploadModal.errors.tooManyCharacters");
  });
});
