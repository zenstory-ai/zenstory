import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

const {
  mockNavigate,
  mockRegister,
  mockGoogleLogin,
  mockAppleLogin,
  mockGetRegistrationPolicy,
  mockToastError,
  mockToastSuccess,
  mockAuthConfig,
  inviteCodePropsHistory,
} = vi.hoisted(() => ({
  mockNavigate: vi.fn(),
  mockRegister: vi.fn(),
  mockGoogleLogin: vi.fn(),
  mockAppleLogin: vi.fn(),
  mockGetRegistrationPolicy: vi.fn(),
  mockToastError: vi.fn(),
  mockToastSuccess: vi.fn(),
  mockAuthConfig: {
    registrationEnabled: true,
    oauthProviders: {
      google: { enabled: false, clientId: undefined },
      apple: { enabled: false, clientId: undefined },
    },
    forgotPasswordEnabled: false,
    inviteCodeOptional: false,
  },
  inviteCodePropsHistory: [] as Array<{ value: string; prefilled?: boolean; required?: boolean }>,
}));

vi.mock("react-router-dom", async () => {
  const actual = await vi.importActual<typeof import("react-router-dom")>("react-router-dom");
  return {
    ...actual,
    useNavigate: () => mockNavigate,
  };
});

vi.mock("react-i18next", () => ({
  useTranslation: () => ({
    t: (key: string, options?: Record<string, unknown>) => {
      if (options && Object.keys(options).length > 0) {
        return `${key}:${JSON.stringify(options)}`;
      }
      return key;
    },
  }),
}));

vi.mock("../../contexts/AuthContext", () => ({
  useAuth: () => ({
    register: mockRegister,
    googleLogin: mockGoogleLogin,
    appleLogin: mockAppleLogin,
  }),
}));

vi.mock("../../components/PublicHeader", () => ({
  PublicHeader: () => <div data-testid="public-header" />,
}));

vi.mock("../../components/LoadingSpinner", () => ({
  LoadingSpinner: () => <div data-testid="loading-spinner" />,
}));

vi.mock("../../components/Logo", () => ({
  LogoMark: () => <div data-testid="logo-mark" />,
}));

vi.mock("../../components/referral/InviteCodeInput", () => ({
  InviteCodeInput: (props: { value: string; prefilled?: boolean; required?: boolean }) => {
    inviteCodePropsHistory.push(props);
    return (
      <div
        data-testid="invite-code-input"
        data-value={props.value}
        data-prefilled={String(Boolean(props.prefilled))}
        data-required={String(Boolean(props.required))}
      />
    );
  },
}));

vi.mock("../../lib/api", () => ({
  authApi: {
    getRegistrationPolicy: mockGetRegistrationPolicy,
  },
}));

vi.mock("../../lib/toast", () => ({
  toast: {
    error: mockToastError,
    success: mockToastSuccess,
  },
}));

vi.mock("../../config/auth", () => ({
  authConfig: mockAuthConfig,
  hasOAuthProviders: () => mockAuthConfig.oauthProviders.google.enabled || mockAuthConfig.oauthProviders.apple.enabled,
}));

import Register from "../Register";

describe("Register", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    inviteCodePropsHistory.length = 0;
    mockAuthConfig.registrationEnabled = true;
    mockAuthConfig.inviteCodeOptional = false;
    mockAuthConfig.oauthProviders.google.enabled = false;
    mockAuthConfig.oauthProviders.apple.enabled = false;
    mockGetRegistrationPolicy.mockResolvedValue({
      invite_code_optional: false,
      variant: "control_required",
      rollout_percent: 0,
    });
  });

  const renderWithRoute = (route: string) =>
    render(
      <MemoryRouter initialEntries={[route]}>
        <Register />
      </MemoryRouter>
    );

  const fillRegistrationFields = (confirmPassword = "SecurePass123!") => {
    fireEvent.change(screen.getByLabelText("auth:register.usernameLabel"), {
      target: { value: "test_user" },
    });
    fireEvent.change(screen.getByLabelText("auth:register.emailLabel"), {
      target: { value: "test@example.com" },
    });
    fireEvent.change(screen.getByLabelText("auth:register.passwordLabel"), {
      target: { value: "SecurePass123!" },
    });
    fireEvent.change(screen.getByLabelText("auth:register.confirmPasswordLabel"), {
      target: { value: confirmPassword },
    });
  };

  const acceptTerms = async (user: ReturnType<typeof userEvent.setup>) => {
    await act(async () => {
      await user.click(screen.getByRole("checkbox"));
    });
    await waitFor(() => {
      expect((screen.getByRole("checkbox") as HTMLInputElement).checked).toBe(true);
    });
  };

  it("reads plan and prefilled invite code from URL", async () => {
    renderWithRoute("/register?plan=pro&code=abcd1234");

    await waitFor(() => {
      expect(screen.getByTestId("invite-code-input")).toHaveAttribute("data-prefilled", "true");
      expect(screen.getByTestId("invite-code-input")).toHaveAttribute("data-value", "ABCD-1234");
      expect(screen.getByTestId("invite-code-input")).toHaveAttribute("data-required", "true");
    });

    expect(screen.getByRole("link", { name: "auth:register.login" })).toHaveAttribute("href", "/login?plan=pro");
    expect(screen.getByText(/auth:register.planIntentTitle/)).toBeInTheDocument();
  });

  it("passes normalized plan intent through Google registration", async () => {
    mockAuthConfig.oauthProviders.google.enabled = true;
    renderWithRoute("/register?plan=PRO&code=abcd1234");
    await waitFor(() => expect(screen.getByTestId("invite-code-input")).toHaveAttribute("data-value", "ABCD-1234"));

    fireEvent.click(screen.getByRole("button", { name: "auth:register.googleRegister" }));

    expect(mockGoogleLogin).toHaveBeenCalledWith({ inviteCode: "ABCD-1234", planIntent: "pro" });
  });

  it("keeps plan intent when navigating to verify-email after success", async () => {
    mockRegister.mockResolvedValue(undefined);
    const user = userEvent.setup();
    renderWithRoute("/register?plan=pro&code=abcd1234");

    await waitFor(() => {
      expect(screen.getByTestId("invite-code-input")).toHaveAttribute("data-value", "ABCD-1234");
    });

    fillRegistrationFields();
    await acceptTerms(user);
    expect(screen.getByRole("button", { name: "auth:register.submit" })).toBeEnabled();

    await act(async () => {
      fireEvent.submit(screen.getByTestId("register-form"));
    });

    await waitFor(() => {
      expect(mockRegister).toHaveBeenCalledWith("test_user", "test@example.com", "SecurePass123!", "ABCD-1234");
      expect(mockToastSuccess).toHaveBeenCalled();
    });

    await waitFor(() => {
      // Email goes through navigation state so it never lands in page URLs.
      expect(mockNavigate).toHaveBeenCalledWith("/verify-email?plan=pro", {
        state: { email: "test@example.com" },
      });
    }, { timeout: 3000 });
  });

  it("rejects passwords longer than 72 bytes before calling the API", async () => {
    const user = userEvent.setup();
    renderWithRoute("/register?code=abcd1234");

    await waitFor(() => {
      expect(screen.getByTestId("invite-code-input")).toHaveAttribute("data-value", "ABCD-1234");
    });

    // 25 CJK characters = 75 UTF-8 bytes, though only 25 JS characters.
    const longPassword = "密".repeat(25);
    fillRegistrationFields(longPassword);
    fireEvent.change(screen.getByLabelText("auth:register.passwordLabel"), {
      target: { value: longPassword },
    });
    await acceptTerms(user);

    await act(async () => {
      fireEvent.submit(screen.getByTestId("register-form"));
    });

    await waitFor(() => {
      expect(mockToastError).toHaveBeenCalledWith("auth:errors.passwordTooLong");
    });
    expect(mockRegister).not.toHaveBeenCalled();
  });

  it("requires invite code by default when optional flag is off", async () => {
    const user = userEvent.setup();
    renderWithRoute("/register");

    fillRegistrationFields();
    await acceptTerms(user);
    expect(screen.getByRole("button", { name: "auth:register.submit" })).toBeDisabled();

    await act(async () => {
      fireEvent.submit(screen.getByTestId("register-form"));
    });

    expect(mockRegister).not.toHaveBeenCalled();
    expect(mockToastError).toHaveBeenCalledWith("auth:errors.inviteCodeRequired");
  });

  it("shows password mismatch validation error before submitting", async () => {
    const user = userEvent.setup();
    renderWithRoute("/register");

    fillRegistrationFields("DifferentPass456!");
    await acceptTerms(user);

    await act(async () => {
      fireEvent.submit(screen.getByTestId("register-form"));
    });

    expect(mockRegister).not.toHaveBeenCalled();
    expect(mockToastError).toHaveBeenCalledWith("auth:errors.passwordMismatch");
    expect(screen.getByText("auth:errors.passwordMismatch")).toBeInTheDocument();
  });

  it("shows terms acceptance validation when paid intent pre-fills invite code", async () => {
    renderWithRoute("/register?plan=pro&code=abcd1234");

    await waitFor(() => {
      expect(screen.getByTestId("invite-code-input")).toHaveAttribute("data-value", "ABCD-1234");
    });

    fillRegistrationFields();

    await act(async () => {
      fireEvent.submit(screen.getByTestId("register-form"));
    });

    expect(mockRegister).not.toHaveBeenCalled();
    expect(mockToastError).toHaveBeenCalledWith("auth:errors.mustAcceptTerms");
    expect(screen.getByText("auth:errors.mustAcceptTerms")).toBeInTheDocument();
  });

  it("shows loading state while registration request is in flight", async () => {
    const pendingRegistration = new Promise<void>(() => {});
    mockRegister.mockReturnValue(pendingRegistration);
    const user = userEvent.setup();
    renderWithRoute("/register?plan=pro&code=abcd1234");
    const submitButton = screen.getByRole("button", { name: "auth:register.submit" });

    await waitFor(() => {
      expect(screen.getByTestId("invite-code-input")).toHaveAttribute("data-value", "ABCD-1234");
    });

    fillRegistrationFields();
    await acceptTerms(user);

    await act(async () => {
      fireEvent.submit(screen.getByTestId("register-form"));
    });

    await waitFor(() => {
      expect(mockRegister).toHaveBeenCalledWith("test_user", "test@example.com", "SecurePass123!", "ABCD-1234");
    });

    expect(submitButton).toBeDisabled();
    expect(screen.getByTestId("loading-spinner")).toBeInTheDocument();
  });

  it("allows register without invite code for gray-treatment optional policy", async () => {
    mockRegister.mockResolvedValue(undefined);
    mockGetRegistrationPolicy.mockResolvedValue({
      invite_code_optional: true,
      variant: "treatment_optional",
      rollout_percent: 50,
    });

    const user = userEvent.setup();
    renderWithRoute("/register");

    fireEvent.change(screen.getByLabelText("auth:register.usernameLabel"), {
      target: { value: "gray_user" },
    });
    fireEvent.change(screen.getByLabelText("auth:register.emailLabel"), {
      target: { value: "gray_user@example.com" },
    });
    fireEvent.change(screen.getByLabelText("auth:register.passwordLabel"), {
      target: { value: "SecurePass123!" },
    });
    fireEvent.change(screen.getByLabelText("auth:register.confirmPasswordLabel"), {
      target: { value: "SecurePass123!" },
    });

    await user.click(screen.getByRole("checkbox"));

    await waitFor(() => {
      expect(mockGetRegistrationPolicy).toHaveBeenCalledWith({
        email: "gray_user@example.com",
        username: "gray_user",
      });
      expect(screen.getByTestId("invite-code-input")).toHaveAttribute("data-required", "false");
      expect(screen.getByRole("button", { name: "auth:register.submit" })).toBeEnabled();
    });

    await act(async () => {
      fireEvent.submit(screen.getByTestId("register-form"));
    });

    await waitFor(() => {
      expect(mockRegister).toHaveBeenCalledWith(
        "gray_user",
        "gray_user@example.com",
        "SecurePass123!",
        undefined
      );
    });
  });

  it("re-enables submit after duplicate-username error when user edits username (invite required flow)", async () => {
    mockRegister
      .mockRejectedValueOnce(new Error("auth:errors.usernameExists"))
      .mockResolvedValueOnce(undefined);

    const user = userEvent.setup();
    renderWithRoute("/register?code=abcd1234");

    await waitFor(() => {
      expect(screen.getByTestId("invite-code-input")).toHaveAttribute("data-value", "ABCD-1234");
      expect(screen.getByTestId("invite-code-input")).toHaveAttribute("data-required", "true");
    });

    fireEvent.change(screen.getByLabelText("auth:register.usernameLabel"), {
      target: { value: "duplicate_user" },
    });
    fireEvent.change(screen.getByLabelText("auth:register.emailLabel"), {
      target: { value: "duplicate_user@example.com" },
    });
    fireEvent.change(screen.getByLabelText("auth:register.passwordLabel"), {
      target: { value: "SecurePass123!" },
    });
    fireEvent.change(screen.getByLabelText("auth:register.confirmPasswordLabel"), {
      target: { value: "SecurePass123!" },
    });
    await acceptTerms(user);

    const submitButton = screen.getByRole("button", { name: "auth:register.submit" });
    expect(submitButton).toBeEnabled();

    await act(async () => {
      fireEvent.submit(screen.getByTestId("register-form"));
    });

    await waitFor(() => {
      expect(mockRegister).toHaveBeenCalledTimes(1);
    });
    expect(submitButton).toBeEnabled();

    fireEvent.change(screen.getByLabelText("auth:register.usernameLabel"), {
      target: { value: "new_available_user" },
    });
    expect(submitButton).toBeEnabled();

    await act(async () => {
      fireEvent.submit(screen.getByTestId("register-form"));
    });

    await waitFor(() => {
      expect(mockRegister).toHaveBeenCalledTimes(2);
      expect(mockRegister).toHaveBeenNthCalledWith(
        2,
        "new_available_user",
        "duplicate_user@example.com",
        "SecurePass123!",
        "ABCD-1234",
      );
    });
  });

  it("keeps malformed email disabled and marks the field invalid", async () => {
    const user = userEvent.setup();
    renderWithRoute("/register?code=abcd1234");
    fireEvent.change(screen.getByLabelText("auth:register.usernameLabel"), { target: { value: "writer" } });
    fireEvent.change(screen.getByLabelText("auth:register.emailLabel"), { target: { value: "not-an-email" } });
    fireEvent.change(screen.getByLabelText("auth:register.passwordLabel"), { target: { value: "SecurePass123!" } });
    fireEvent.change(screen.getByLabelText("auth:register.confirmPasswordLabel"), { target: { value: "SecurePass123!" } });
    await acceptTerms(user);

    expect(screen.getByLabelText("auth:register.emailLabel")).toHaveAttribute("aria-invalid", "true");
    expect(screen.getByRole("button", { name: "auth:register.submit" })).toBeDisabled();
    expect(mockGetRegistrationPolicy).not.toHaveBeenCalledWith(expect.objectContaining({ email: "not-an-email" }));
  });
});
