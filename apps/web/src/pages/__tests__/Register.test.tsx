import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

const {
  mockNavigate,
  mockRegister,
  mockGoogleLogin,
  mockToastError,
  mockToastSuccess,
  mockAuthConfig,
} = vi.hoisted(() => ({
  mockNavigate: vi.fn(),
  mockRegister: vi.fn(),
  mockGoogleLogin: vi.fn(),
  mockToastError: vi.fn(),
  mockToastSuccess: vi.fn(),
  mockAuthConfig: {
    registrationEnabled: true,
    oauthProviders: {
      google: { enabled: false, clientId: undefined },
    },
    forgotPasswordEnabled: false,
  },
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

vi.mock("../../lib/toast", () => ({
  toast: {
    error: mockToastError,
    success: mockToastSuccess,
  },
}));

vi.mock("../../config/auth", () => ({
  authConfig: mockAuthConfig,
  hasOAuthProviders: () => mockAuthConfig.oauthProviders.google.enabled,
}));

import Register from "../Register";
import { ApiError } from "../../lib/apiClient";

describe("Register", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockAuthConfig.registrationEnabled = true;
    mockAuthConfig.oauthProviders.google.enabled = false;
  });

  const renderWithRoute = (route: string) =>
    render(
      <MemoryRouter initialEntries={[route]}>
        <Register />
      </MemoryRouter>
    );

  const fillRegistrationFields = () => {
    fireEvent.change(screen.getByLabelText("auth:register.emailLabel"), {
      target: { value: "test@example.com" },
    });
    fireEvent.change(screen.getByLabelText("auth:register.passwordLabel"), {
      target: { value: "SecurePass123!" },
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

  it("does not render retired invite code controls and submits cleanly", async () => {
    mockRegister.mockResolvedValueOnce({ email: "test@example.com", email_verified: false, verification_sent: true });
    const user = userEvent.setup();
    renderWithRoute("/register");
    expect(screen.queryByTestId("invite-code-input")).not.toBeInTheDocument();
    fillRegistrationFields();
    await acceptTerms(user);
    await user.click(screen.getByRole("button", { name: "auth:register.submit" }));
    await waitFor(() =>
      expect(mockRegister).toHaveBeenCalledWith("test@example.com", "SecurePass123!"),
    );
  });

  it("does not claim mail was sent when the account exists but delivery failed", async () => {
    mockRegister.mockResolvedValueOnce({ email: 'test@example.com', email_verified: false, verification_sent: false });
    const user = userEvent.setup();
    renderWithRoute('/register?plan=pro');
    fillRegistrationFields();
    await acceptTerms(user);
    await user.click(screen.getByRole('button', { name: 'auth:register.submit' }));
    await waitFor(() => expect(mockNavigate).toHaveBeenCalledWith('/verify-email?plan=pro', {
      state: { email: 'test@example.com', verificationSent: false },
    }));
    expect(mockToastSuccess).not.toHaveBeenCalled();
    expect(mockToastError).not.toHaveBeenCalled();
  });

  it("reads plan intent from URL", () => {
    renderWithRoute("/register?plan=pro");

    expect(screen.getByRole("link", { name: "auth:register.login" })).toHaveAttribute("href", "/login?plan=pro");
    expect(screen.getByText(/auth:register.planIntentTitle/)).toBeInTheDocument();
  });

  it("passes normalized plan intent through Google registration", () => {
    mockAuthConfig.oauthProviders.google.enabled = true;
    renderWithRoute("/register?plan=PRO");

    fireEvent.click(screen.getByRole("button", { name: "auth:register.googleRegister" }));

    expect(mockGoogleLogin).toHaveBeenCalledWith({ planIntent: "pro" });
  });

  it("keeps plan intent when navigating to verify-email after success", async () => {
    mockRegister.mockResolvedValue(undefined);
    const user = userEvent.setup();
    renderWithRoute("/register?plan=pro");

    fillRegistrationFields();
    await acceptTerms(user);
    expect(screen.getByRole("button", { name: "auth:register.submit" })).toBeEnabled();

    await act(async () => {
      fireEvent.submit(screen.getByTestId("register-form"));
    });

    await waitFor(() => {
      expect(mockRegister).toHaveBeenCalledWith("test@example.com", "SecurePass123!");
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
    renderWithRoute("/register");

    // 25 CJK characters = 75 UTF-8 bytes, though only 25 JS characters.
    const longPassword = "密".repeat(25);
    fillRegistrationFields();
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

  it("answers a click on an empty form with a visible reason instead of doing nothing", async () => {
    const user = userEvent.setup();
    renderWithRoute("/register");

    const submitButton = screen.getByRole("button", { name: "auth:register.submit" });
    expect(submitButton).toBeEnabled();
    await act(async () => {
      await user.click(submitButton);
    });

    expect(mockRegister).not.toHaveBeenCalled();
    expect(mockToastError).toHaveBeenCalledWith("auth:errors.invalidEmail");
    expect(screen.getByText("auth:errors.invalidEmail")).toBeInTheDocument();
    expect(submitButton).toBeEnabled();
  });

  it("asks only for an email and one password, which the author can reveal to check", async () => {
    const user = userEvent.setup();
    renderWithRoute("/register");

    expect(screen.queryByLabelText("auth:register.usernameLabel")).not.toBeInTheDocument();
    expect(screen.queryByLabelText("auth:register.confirmPasswordLabel")).not.toBeInTheDocument();
    const password = screen.getByLabelText("auth:register.passwordLabel");
    expect(password).toHaveAttribute("type", "password");

    await user.click(screen.getByRole("button", { name: /auth:register\.showPassword/ }));

    expect(password).toHaveAttribute("type", "text");
    expect(screen.getByRole("button", { name: /auth:register\.hidePassword/ })).toHaveAttribute("aria-pressed", "true");
  });

  it("suggests common mail domains under the field and completes one on click or Enter", async () => {
    const user = userEvent.setup();
    renderWithRoute("/register");
    const email = screen.getByLabelText("auth:register.emailLabel");

    await user.click(email);
    await user.type(email, "lin@g");
    expect(email).toHaveAttribute("aria-expanded", "true");
    const [option] = screen.getAllByRole("option");
    expect(option).toHaveTextContent("lin@gmail.com");
    await user.click(option);

    expect(email).toHaveValue("lin@gmail.com");
    expect(screen.queryByRole("listbox")).not.toBeInTheDocument();
    expect(screen.getByLabelText("auth:register.passwordLabel")).toHaveFocus();

    await user.clear(email);
    await user.type(email, "mo");
    await user.keyboard("{ArrowDown}{ArrowDown}");
    expect(screen.getAllByRole("option")[1]).toHaveAttribute("aria-selected", "true");
    await user.keyboard("{Enter}");
    expect(email).toHaveValue("mo@163.com");
    expect(mockRegister).not.toHaveBeenCalled();
  });

  it("shows terms acceptance validation before registering", async () => {
    renderWithRoute("/register?plan=pro");

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
    renderWithRoute("/register?plan=pro");
    const submitButton = screen.getByRole("button", { name: "auth:register.submit" });

    fillRegistrationFields();
    await acceptTerms(user);

    await act(async () => {
      fireEvent.submit(screen.getByTestId("register-form"));
    });

    await waitFor(() => {
      expect(mockRegister).toHaveBeenCalledWith("test@example.com", "SecurePass123!");
    });

    expect(submitButton).toBeDisabled();
    expect(screen.getByTestId("loading-spinner")).toBeInTheDocument();
  });

  it("re-enables submit after a duplicate-email error when the author edits the email", async () => {
    mockRegister
      .mockRejectedValueOnce(new Error("auth:errors.emailExists"))
      .mockResolvedValueOnce(undefined);

    const user = userEvent.setup();
    renderWithRoute("/register");

    fireEvent.change(screen.getByLabelText("auth:register.emailLabel"), {
      target: { value: "duplicate_user@example.com" },
    });
    fireEvent.change(screen.getByLabelText("auth:register.passwordLabel"), {
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

    fireEvent.change(screen.getByLabelText("auth:register.emailLabel"), {
      target: { value: "new_available_user@example.com" },
    });
    expect(submitButton).toBeEnabled();

    await act(async () => {
      fireEvent.submit(screen.getByTestId("register-form"));
    });

    await waitFor(() => {
      expect(mockRegister).toHaveBeenCalledTimes(2);
      expect(mockRegister).toHaveBeenNthCalledWith(
        2,
        "new_available_user@example.com",
        "SecurePass123!",
      );
    });
  });

  it("releases submit gate after terms rejection and permits ordinary retry", async () => {
    const user = userEvent.setup();
    renderWithRoute("/register");
    fillRegistrationFields();
    await act(async () => { fireEvent.submit(screen.getByTestId("register-form")); });
    await waitFor(() => expect(mockToastError).toHaveBeenCalledWith("auth:errors.mustAcceptTerms"));
    expect(mockRegister).not.toHaveBeenCalled();
    expect(screen.getByTestId("register-form")).toHaveAttribute("aria-busy", "false");
    expect(screen.getByRole("checkbox")).toBeEnabled();
    await acceptTerms(user);
    mockRegister.mockRejectedValueOnce(new Error("offline retry completed"));
    await act(async () => { await user.click(screen.getByRole("button", { name: "auth:register.submit" })); });
    await waitFor(() => expect(mockRegister).toHaveBeenCalledTimes(1));
    await waitFor(() => expect(screen.getByRole("button", { name: "auth:register.submit" })).toBeEnabled());
  });

  it("answers a malformed-email click with an inline error and marks the field invalid", async () => {
    const user = userEvent.setup();
    renderWithRoute("/register");
    fireEvent.change(screen.getByLabelText("auth:register.emailLabel"), { target: { value: "not-an-email" } });
    fireEvent.change(screen.getByLabelText("auth:register.passwordLabel"), { target: { value: "SecurePass123!" } });
    await acceptTerms(user);

    expect(screen.getByLabelText("auth:register.emailLabel")).toHaveAttribute("aria-invalid", "true");
    await act(async () => {
      await user.click(screen.getByRole("button", { name: "auth:register.submit" }));
    });
    expect(mockRegister).not.toHaveBeenCalled();
    expect(mockToastError).toHaveBeenCalledWith("auth:errors.invalidEmail");
    expect(screen.getByText("auth:errors.invalidEmail")).toBeInTheDocument();
  });
  it("gives an existing email both a sign-in and a verification exit", async () => {
    mockRegister.mockRejectedValueOnce(new ApiError(400, 'ERR_AUTH_EMAIL_EXISTS'));
    const user = userEvent.setup();
    renderWithRoute('/register?plan=pro');
    fillRegistrationFields();
    await acceptTerms(user);
    await user.click(screen.getByRole('button', { name: 'auth:register.submit' }));
    expect(await screen.findByText('auth:register.emailExistsHint')).toBeVisible();
    await user.click(screen.getByRole('button', { name: 'auth:register.emailExistsLogin' }));
    expect(mockNavigate).toHaveBeenLastCalledWith('/login?plan=pro', { state: { identifier: 'test@example.com' } });
    await user.click(screen.getByRole('button', { name: 'auth:register.emailExistsVerify' }));
    expect(mockNavigate).toHaveBeenLastCalledWith('/verify-email?plan=pro', { state: { email: 'test@example.com' } });
  });

  it("drops the existing-email exits once the author edits the address", async () => {
    mockRegister.mockRejectedValueOnce(new ApiError(400, 'ERR_AUTH_EMAIL_EXISTS'));
    const user = userEvent.setup();
    renderWithRoute('/register');
    fillRegistrationFields();
    await acceptTerms(user);
    await user.click(screen.getByRole('button', { name: 'auth:register.submit' }));
    await screen.findByRole('button', { name: 'auth:register.emailExistsVerify' });
    fireEvent.change(screen.getByLabelText('auth:register.emailLabel'), { target: { value: 'other@example.com' } });
    expect(screen.queryByRole('button', { name: 'auth:register.emailExistsVerify' })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'auth:register.emailExistsLogin' })).not.toBeInTheDocument();
  });

  it("does not offer existing-email exits for other failures", async () => {
    mockRegister.mockRejectedValueOnce(new ApiError(503, 'ERR_AUTH_RESEND_FAILED'));
    const user = userEvent.setup();
    renderWithRoute('/register');
    fillRegistrationFields();
    await acceptTerms(user);
    await user.click(screen.getByRole('button', { name: 'auth:register.submit' }));
    await screen.findByRole('alert');
    expect(screen.queryByRole('button', { name: 'auth:register.emailExistsVerify' })).not.toBeInTheDocument();
  });
});
