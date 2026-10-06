import { beforeEach, describe, expect, it, vi } from "vitest";
import { act, fireEvent, render, screen } from "@testing-library/react";

import CodeManagement from "../CodeManagement";
import { adminApi } from "../../../lib/adminApi";

const useQueryMock = vi.fn();
const useMutationMock = vi.fn();
const invalidateQueriesMock = vi.fn();
const mutateMock = vi.fn();

vi.mock("react-i18next", () => ({
  useTranslation: () => ({
    t: (key: string, options?: Record<string, unknown>) => {
      if (key === "codes.batchCreateSuccess" && options?.count) {
        return `${key}:${options.count}`;
      }
      return key;
    },
  }),
}));

vi.mock("@tanstack/react-query", () => ({
  useQuery: (...args: unknown[]) => useQueryMock(...args),
  useMutation: (...args: unknown[]) => useMutationMock(...args),
  useQueryClient: () => ({
    invalidateQueries: invalidateQueriesMock,
  }),
}));

vi.mock("../../../lib/toast", () => ({
  toast: {
    success: vi.fn(),
    error: vi.fn(),
  },
}));

describe("CodeManagement", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    useMutationMock.mockReturnValue({
      mutate: mutateMock,
      isPending: false,
    });

    vi.stubGlobal("navigator", {
      clipboard: {
        writeText: vi.fn(),
      },
    });
  });

  it("shows loading state", () => {
    useQueryMock.mockReturnValue({
      data: undefined,
      isLoading: true,
      isFetching: false,
      isError: false,
      error: null,
      refetch: vi.fn(),
    });

    render(<CodeManagement />);
    expect(screen.getByText("common:loading")).toBeInTheDocument();
  });

  it("shows error state and supports retry", () => {
    const refetchMock = vi.fn();
    useQueryMock.mockReturnValue({
      data: undefined,
      isLoading: false,
      isFetching: false,
      isError: true,
      error: new Error("load codes failed"),
      refetch: refetchMock,
    });

    render(<CodeManagement />);
    expect(screen.getByText("load codes failed")).toBeInTheDocument();

    fireEvent.click(screen.getByText("common:retry"));
    expect(refetchMock).toHaveBeenCalledTimes(1);
  });

  it("shows empty state", () => {
    useQueryMock.mockReturnValue({
      data: { items: [], total: 0, page: 1, page_size: 20 },
      isLoading: false,
      isFetching: false,
      isError: false,
      error: null,
      refetch: vi.fn(),
    });

    render(<CodeManagement />);
    expect(screen.getByText("common:noData")).toBeInTheDocument();
  });

  it("confirms deactivation with the code and impact before mutating", () => {
    useQueryMock.mockReturnValue({
      data: {
        items: [
          {
            id: "code-1",
            code: "TESTCODE",
            tier: "pro",
            duration_days: 30,
            code_type: "single_use",
            max_uses: 1,
            current_uses: 0,
            is_active: true,
            notes: null,
            created_at: "2026-03-08T00:00:00Z",
            updated_at: "2026-03-08T00:00:00Z",
          },
        ],
        total: 1,
        page: 1,
        page_size: 20,
      },
      isLoading: false,
      isFetching: false,
      isError: false,
      error: null,
      refetch: vi.fn(),
    });

    render(<CodeManagement />);

    mutateMock.mockClear();
    fireEvent.click(screen.getAllByTitle("codes.deactivate")[0]);

    expect(mutateMock).not.toHaveBeenCalled();
    expect(screen.getByText(/TESTCODE: codes.deactivateConfirmImpact/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "codes.confirmDeactivate" }));

    expect(mutateMock.mock.calls[0][0]).toEqual({
      id: "code-1",
      data: { is_active: false },
    });
  });

  it("disables the affected row while its update is pending", () => {
    let mutationCall = 0;
    useMutationMock.mockImplementation(() => {
      mutationCall += 1;
      return mutationCall === 3
        ? { mutate: mutateMock, isPending: true, variables: { id: "code-1" } }
        : { mutate: mutateMock, isPending: false };
    });
    useQueryMock.mockReturnValue({
      data: {
        items: [{
          id: "code-1",
          code: "INACTIVECODE",
          tier: "pro",
          duration_days: 30,
          code_type: "single_use",
          max_uses: 1,
          current_uses: 0,
          is_active: false,
          notes: null,
          created_at: "2026-03-08T00:00:00Z",
          updated_at: "2026-03-08T00:00:00Z",
        }],
        total: 1,
        page: 1,
        page_size: 20,
      },
      isLoading: false,
      isFetching: false,
      isError: false,
      error: null,
      refetch: vi.fn(),
    });

    render(<CodeManagement />);
    const activationButtons = screen.getAllByTitle("codes.activate");
    expect(activationButtons.every((button) => button.hasAttribute("disabled"))).toBe(true);
  });

  it("submits single and batch create", () => {
    useQueryMock.mockReturnValue({
      data: { items: [], total: 0, page: 1, page_size: 20 },
      isLoading: false,
      isFetching: false,
      isError: false,
      error: null,
      refetch: vi.fn(),
    });

    render(<CodeManagement />);

    const createButtonsBefore = screen.getAllByRole("button", { name: "codes.create" });
    fireEvent.click(createButtonsBefore[0]);
    mutateMock.mockClear();
    const createButtonsAfter = screen.getAllByRole("button", { name: "codes.create" });
    fireEvent.click(createButtonsAfter[createButtonsAfter.length - 1]);

    expect(mutateMock).toHaveBeenCalledWith({
      tier: "pro",
      duration_days: 30,
      code_type: "single_use",
      max_uses: 1,
      notes: "",
    });

    const batchButtonsBefore = screen.getAllByRole("button", { name: "codes.batchCreate" });
    fireEvent.click(batchButtonsBefore[0]);
    mutateMock.mockClear();
    const batchButtonsAfter = screen.getAllByRole("button", { name: "codes.batchCreate" });
    fireEvent.click(batchButtonsAfter[batchButtonsAfter.length - 1]);

    expect(mutateMock).toHaveBeenCalledWith({
      tier: "pro",
      duration_days: 30,
      count: 10,
      code_type: "single_use",
      max_uses: 10,
      notes: "",
    });
  });

  it("asks for a per-code use limit for multi-use batches and sends it", () => {
    useQueryMock.mockReturnValue({
      data: { items: [], total: 0, page: 1, page_size: 20 },
      isLoading: false, isFetching: false, isError: false, error: null, refetch: vi.fn(),
    });

    render(<CodeManagement />);
    fireEvent.click(screen.getAllByRole("button", { name: "codes.batchCreate" })[0]);
    expect(screen.queryByLabelText("codes.maxUsesPerCode")).not.toBeInTheDocument();

    const selects = screen.getAllByRole("combobox");
    fireEvent.change(selects[selects.length - 1], { target: { value: "multi_use" } });
    fireEvent.change(screen.getByLabelText("codes.maxUsesPerCode"), { target: { value: "25" } });
    mutateMock.mockClear();
    const batchButtons = screen.getAllByRole("button", { name: "codes.batchCreate" });
    fireEvent.click(batchButtons[batchButtons.length - 1]);

    expect(mutateMock).toHaveBeenCalledWith(expect.objectContaining({ code_type: "multi_use", max_uses: 25 }));
  });

  it("sends max_uses only for multi-use codes", async () => {
    const createCode = vi.spyOn(adminApi, "createCode").mockResolvedValue({} as never);
    const createCodesBatch = vi.spyOn(adminApi, "createCodesBatch").mockResolvedValue({ codes: [] });
    useQueryMock.mockReturnValue({
      data: { items: [], total: 0, page: 1, page_size: 20 },
      isLoading: false, isFetching: false, isError: false, error: null, refetch: vi.fn(),
    });

    render(<CodeManagement />);
    const [singleOptions, batchOptions] = useMutationMock.mock.calls.slice(0, 2).map(([options]) => options);
    await singleOptions.mutationFn({ tier: "pro", duration_days: 30, code_type: "single_use", max_uses: 9, notes: "" });
    await batchOptions.mutationFn({ tier: "pro", duration_days: 30, count: 2, code_type: "multi_use", max_uses: 9, notes: "" });

    expect(createCode).toHaveBeenCalledWith({ tier: "pro", duration_days: 30, code_type: "single_use", notes: "" });
    expect(createCodesBatch).toHaveBeenCalledWith({
      tier: "pro", duration_days: 30, count: 2, code_type: "multi_use", max_uses: 9, notes: "",
    });
  });

  it("shows the generated codes after a batch with copy-all and CSV download", () => {
    const createObjectURL = vi.fn(() => "blob:codes");
    const revokeObjectURL = vi.fn();
    vi.stubGlobal("URL", { ...URL, createObjectURL, revokeObjectURL });
    const clickSpy = vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(() => {});
    useQueryMock.mockReturnValue({
      data: { items: [], total: 0, page: 1, page_size: 20 },
      isLoading: false, isFetching: false, isError: false, error: null, refetch: vi.fn(),
    });

    render(<CodeManagement />);
    const batchOptions = useMutationMock.mock.calls[1][0];
    act(() => {
      batchOptions.onSuccess(
        { codes: ["ERG-PRO-AAAA-11111111", "ERG-PRO-BBBB-22222222"], count: 2, max_uses: 5 },
        { tier: "pro", duration_days: 30, count: 2, code_type: "multi_use", max_uses: 5, notes: "" },
      );
    });

    const dialog = screen.getByRole("dialog");
    expect(dialog).toHaveTextContent("codes.batchResultTitle");
    expect((screen.getByRole("textbox", { name: "codes.batchResultTitle" }) as HTMLTextAreaElement).value)
      .toBe("ERG-PRO-AAAA-11111111\nERG-PRO-BBBB-22222222");

    fireEvent.click(screen.getByRole("button", { name: "codes.copyAll" }));
    expect(navigator.clipboard.writeText).toHaveBeenCalledWith("ERG-PRO-AAAA-11111111\nERG-PRO-BBBB-22222222");

    fireEvent.click(screen.getByRole("button", { name: "codes.downloadCsv" }));
    expect(createObjectURL).toHaveBeenCalledTimes(1);
    expect(clickSpy).toHaveBeenCalledTimes(1);
    expect(revokeObjectURL).toHaveBeenCalledWith("blob:codes");

    fireEvent.click(screen.getByRole("button", { name: "common:close" }));
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    clickSpy.mockRestore();
  });

  it("labels multi-use codes and asks for a use limit when creating one", () => {
    useQueryMock.mockReturnValue({
      data: {
        items: [{
          id: "code-2", code: "MULTI", tier: "pro", duration_days: 7, code_type: "multi_use",
          max_uses: 50, current_uses: 3, is_active: true, notes: null,
          created_at: "2026-03-08T00:00:00Z", updated_at: "2026-03-08T00:00:00Z",
        }],
        total: 1, page: 1, page_size: 20,
      },
      isLoading: false, isFetching: false, isError: false, error: null, refetch: vi.fn(),
    });

    render(<CodeManagement />);
    expect(screen.getAllByText("codes.typeMulti").length).toBeGreaterThanOrEqual(2);

    fireEvent.click(screen.getAllByRole("button", { name: "codes.create" })[0]);
    const selects = screen.getAllByRole("combobox");
    fireEvent.change(selects[selects.length - 1], { target: { value: "multi_use" } });
    const maxUses = screen.getByLabelText("codes.maxUses") as HTMLInputElement;
    fireEvent.change(maxUses, { target: { value: "" } });
    expect(maxUses.value).toBe("1");
    fireEvent.change(maxUses, { target: { value: "40" } });
    mutateMock.mockClear();
    const createButtons = screen.getAllByRole("button", { name: "codes.create" });
    fireEvent.click(createButtons[createButtons.length - 1]);
    expect(mutateMock).toHaveBeenCalledWith(expect.objectContaining({ code_type: "multi_use", max_uses: 40 }));
  });

  it("falls back to the submitted batch when the response omits counts", async () => {
    const { toast } = await import("../../../lib/toast");
    useQueryMock.mockReturnValue({
      data: { items: [], total: 0, page: 1, page_size: 20 },
      isLoading: false, isFetching: false, isError: false, error: null, refetch: vi.fn(),
    });

    render(<CodeManagement />);
    const batchOptions = useMutationMock.mock.calls[1][0];
    act(() => {
      batchOptions.onSuccess(
        {},
        { tier: "pro", duration_days: 30, count: 3, code_type: "single_use", max_uses: 10, notes: "" },
      );
    });

    expect(toast.success).toHaveBeenCalledWith("codes.batchCreateSuccess:3");
    expect(screen.getByRole("dialog")).toHaveTextContent("codes.typeSingle");
  });

  it("offers grantable plans from the catalog as tiers", () => {
    useQueryMock.mockImplementation(({ queryKey }: { queryKey: unknown[] }) => ({
      data: queryKey[1] === "plans"
        ? [
            { name: "free", display_name: "免费", is_active: true },
            { name: "pro", display_name: "专业版", is_active: true },
            { name: "max", display_name: "旗舰版", is_active: true },
          ]
        : { items: [], total: 0, page: 1, page_size: 20 },
      isLoading: false, isFetching: false, isError: false, error: null, refetch: vi.fn(),
    }));

    render(<CodeManagement />);
    fireEvent.click(screen.getAllByRole("button", { name: "codes.create" })[0]);

    const tierSelect = screen.getAllByRole("combobox")[2] as HTMLSelectElement;
    expect(Array.from(tierSelect.options).map((option) => option.value)).toEqual(["pro", "max"]);
    expect(screen.getAllByText("旗舰版").length).toBeGreaterThan(0);
  });
});
