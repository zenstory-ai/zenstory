import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const { mockNavigate, mockVerifyEmail, mockResendVerification, mockCheckVerification, mockCorrectEmail } = vi.hoisted(() => ({
  mockNavigate: vi.fn(),
  mockVerifyEmail: vi.fn(),
  mockResendVerification: vi.fn(),
  mockCheckVerification: vi.fn(),
  mockCorrectEmail: vi.fn(),
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
      if (key === "auth:verifyEmail.subtitle" && options?.email) {
        return `verify-email:${String(options.email)}`;
      }
      return key;
    },
  }),
}));

vi.mock("../../contexts/AuthContext", () => ({
  useAuth: () => ({
    verifyEmail: mockVerifyEmail,
    resendVerification: mockResendVerification,
  }),
}));

vi.mock("../../lib/api", () => ({
  authApi: {
    checkVerification: mockCheckVerification,
    correctRegistrationEmail: mockCorrectEmail,
  },
}));

vi.mock("../../components/PublicHeader", () => ({
  PublicHeader: () => <div data-testid="public-header" />,
}));

import VerifyEmail from "../VerifyEmail";
import { ApiError } from "../../lib/apiClient";

describe("VerifyEmail", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockCheckVerification.mockResolvedValue({
      email_verified: false,
      resend_cooldown_seconds: 60,
      verification_code_ttl_seconds: 300,
    });
  });

  const renderPage = (email: string, planIntent?: "free" | "pro" | null) =>
    render(
      <MemoryRouter>
        <VerifyEmail email={email} planIntent={planIntent} />
      </MemoryRouter>
    );

  it("corrects an unfinished account's address with a password and preserves plan intent", async () => {
    mockCorrectEmail.mockResolvedValueOnce({ email: 'corrected@example.com', verification_sent: true });
    renderPage('writer@example.c0m', 'pro');
    fireEvent.click(screen.getByRole('button', { name: 'auth:verifyEmail.changeEmail' }));
    fireEvent.change(screen.getByLabelText('auth:verifyEmail.newEmail'), { target: { value: 'corrected@example.com' } });
    fireEvent.change(screen.getByLabelText('auth:verifyEmail.registrationPassword'), { target: { value: 'OwnedPassword1!' } });
    fireEvent.click(screen.getByRole('button', { name: 'auth:verifyEmail.confirmChange' }));
    await waitFor(() => expect(mockCorrectEmail).toHaveBeenCalledWith(expect.objectContaining({
      email: 'writer@example.c0m', new_email: 'corrected@example.com', password: 'OwnedPassword1!',
    })));
    expect(mockNavigate).toHaveBeenCalledWith('/verify-email?plan=pro', {
      replace: true, state: { email: 'corrected@example.com', verificationSent: true },
    });
    expect(mockVerifyEmail).not.toHaveBeenCalled();
  });

  it("focuses the correction field and restores the trigger when returning to verification", () => {
    renderPage('writer@example.c0m');
    fireEvent.click(screen.getByRole('button', { name: 'auth:verifyEmail.changeEmail' }));
    expect(screen.getByLabelText('auth:verifyEmail.newEmail')).toHaveFocus();
    fireEvent.change(screen.getByLabelText('auth:verifyEmail.registrationPassword'), { target: { value: 'DoNotKeepThisPassword' } });
    fireEvent.click(screen.getByRole('button', { name: 'auth:verifyEmail.cancelChange' }));
    expect(screen.getByRole('button', { name: 'auth:verifyEmail.changeEmail' })).toHaveFocus();
    fireEvent.click(screen.getByRole('button', { name: 'auth:verifyEmail.changeEmail' }));
    expect(screen.getByLabelText('auth:verifyEmail.registrationPassword')).toHaveValue('');
    expect(mockCorrectEmail).not.toHaveBeenCalled();
    expect(mockVerifyEmail).not.toHaveBeenCalled();
  });

  it("announces a correction failure and associates it with the current fields", async () => {
    mockCorrectEmail.mockRejectedValueOnce(new Error('The address could not be updated.'));
    renderPage('writer@example.c0m');
    fireEvent.click(screen.getByRole('button', { name: 'auth:verifyEmail.changeEmail' }));
    const address = screen.getByLabelText('auth:verifyEmail.newEmail');
    const password = screen.getByLabelText('auth:verifyEmail.registrationPassword');
    fireEvent.change(address, { target: { value: 'corrected@example.com' } });
    fireEvent.change(password, { target: { value: 'OwnedPassword1!' } });
    fireEvent.click(screen.getByRole('button', { name: 'auth:verifyEmail.confirmChange' }));
    const alert = await screen.findByRole('alert');
    expect(alert).toHaveTextContent('The address could not be updated.');
    expect(address).toHaveAccessibleDescription('The address could not be updated.');
    expect(password).toHaveAccessibleDescription('The address could not be updated.');
    expect(address).toBeEnabled();
    expect(mockNavigate).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole('button', { name: 'auth:verifyEmail.cancelChange' }));
    expect(screen.queryByRole('alert')).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'auth:verifyEmail.changeEmail' })).toHaveFocus();
  });

  it("shows the failed-delivery recovery path without saying a code was sent", () => {
    render(<MemoryRouter initialEntries={[{ pathname: '/verify-email', state: { verificationSent: false } }]}>
      <VerifyEmail email="writer@example.com" />
    </MemoryRouter>);
    expect(screen.getByText('auth:verifyEmail.deliveryPending')).toBeVisible();
    expect(screen.queryByText('verify-email:writer@example.com')).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'auth:verifyEmail.changeEmail' })).toBeVisible();
  });

  describe("establishment completion errors", () => {
    beforeEach(() => vi.useFakeTimers());
    afterEach(() => {
      cleanup();
      vi.clearAllTimers();
      vi.useRealTimers();
    });

    async function submitPending() {
      let reject!: (error: unknown) => void;
      mockVerifyEmail.mockReturnValueOnce(new Promise<void>((_resolve, decline) => { reject = decline; }));
      await act(async () => { renderPage("offline@example.invalid", "pro"); });
      const inputs = screen.getAllByRole("textbox");
      inputs.forEach((input, index) => fireEvent.change(input, { target: { value: String(index + 1) } }));
      await act(async () => { vi.advanceTimersByTime(100); });
      expect(mockVerifyEmail).toHaveBeenCalledWith("offline@example.invalid", "123456");
      expect(inputs[0]).toBeDisabled();
      return { inputs, reject };
    }

    it("ignores cancellation without success, navigation, error or code clearing", async () => {
      const { inputs, reject } = await submitPending();
      await act(async () => { reject(new DOMException("Auth establishment superseded", "AbortError")); });
      expect(screen.queryByText("Auth establishment superseded")).not.toBeInTheDocument();
      expect(inputs.map(input => (input as HTMLInputElement).value)).toEqual(["1", "2", "3", "4", "5", "6"]);
      expect(screen.getAllByRole("textbox")).toHaveLength(6);
      inputs.forEach(input => expect(input).toBeEnabled());
      await act(async () => { vi.advanceTimersByTime(1600); });
      expect(mockNavigate).not.toHaveBeenCalled();
      expect(mockVerifyEmail).toHaveBeenCalledTimes(1);
    });

    it.each([
      new ApiError(400, "offline-api-error"),
      new TypeError("offline-type-error"),
      { name: "AbortError", message: "ordinary-object-abort-name" },
      new DOMException("ordinary-other-dom-error", "InvalidStateError"),
    ])("preserves ordinary error UI/code clearing for %s", async failure => {
      const { inputs, reject } = await submitPending();
      await act(async () => { reject(failure); });
      expect(screen.getByText(failure.message)).toBeInTheDocument();
      expect(inputs.map(input => (input as HTMLInputElement).value)).toEqual(["", "", "", "", "", ""]);
      inputs.forEach(input => expect(input).toBeEnabled());
      await act(async () => { vi.advanceTimersByTime(1600); });
      expect(mockNavigate).not.toHaveBeenCalled();
    });
  });

  it("asks for the address and offers to send a code when the page has no email", async () => {
    renderPage("");

    expect(screen.getByLabelText("auth:verifyEmail.emailLabel")).toBeVisible();
    expect(screen.getByRole("button", { name: "auth:verifyEmail.sendCode" })).toBeVisible();
    expect(screen.getByRole("button", { name: "auth:verifyEmail.goToRegister" })).toBeInTheDocument();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    // Only the address field, no code digits yet.
    expect(screen.queryAllByRole("textbox")).toHaveLength(1);
    expect(mockCheckVerification).not.toHaveBeenCalled();
    expect(mockVerifyEmail).not.toHaveBeenCalled();
    expect(mockResendVerification).not.toHaveBeenCalled();
  });

  it("sends a code to the typed address and continues to code entry with plan intent", async () => {
    mockResendVerification.mockResolvedValueOnce(undefined);
    renderPage("", "pro");
    fireEvent.change(screen.getByLabelText("auth:verifyEmail.emailLabel"), { target: { value: " pending@example.com " } });
    fireEvent.click(screen.getByRole("button", { name: "auth:verifyEmail.sendCode" }));
    await waitFor(() => expect(mockResendVerification).toHaveBeenCalledWith("pending@example.com"));
    expect(mockNavigate).toHaveBeenCalledWith("/verify-email?plan=pro", {
      replace: true, state: { email: "pending@example.com", verificationSent: true },
    });
  });

  it("continues to code entry when a code was already sent moments ago", async () => {
    mockResendVerification.mockRejectedValueOnce(
      new ApiError(429, "ERR_AUTH_VERIFICATION_COOLDOWN", { retry_after_seconds: 40 }),
    );
    renderPage("");
    fireEvent.change(screen.getByLabelText("auth:verifyEmail.emailLabel"), { target: { value: "pending@example.com" } });
    fireEvent.click(screen.getByRole("button", { name: "auth:verifyEmail.sendCode" }));
    await waitFor(() => expect(mockNavigate).toHaveBeenCalledWith("/verify-email", {
      replace: true, state: { email: "pending@example.com", verificationSent: true },
    }));
  });

  it("keeps the typed address and explains a failed send", async () => {
    mockResendVerification.mockRejectedValueOnce(new ApiError(400, "ERR_AUTH_RESEND_FAILED"));
    renderPage("");
    const field = screen.getByLabelText("auth:verifyEmail.emailLabel");
    fireEvent.change(field, { target: { value: "pending@example.com" } });
    fireEvent.click(screen.getByRole("button", { name: "auth:verifyEmail.sendCode" }));
    expect(await screen.findByRole("alert")).toBeVisible();
    expect(field).toHaveValue("pending@example.com");
    expect(field).toBeEnabled();
    expect(mockNavigate).not.toHaveBeenCalled();
  });

  it("rejects a malformed address without calling the server", () => {
    renderPage("");
    fireEvent.change(screen.getByLabelText("auth:verifyEmail.emailLabel"), { target: { value: "not-an-email" } });
    fireEvent.click(screen.getByRole("button", { name: "auth:verifyEmail.sendCode" }));
    expect(screen.getByRole("alert")).toHaveTextContent("auth:errors.invalidEmail");
    expect(mockResendVerification).not.toHaveBeenCalled();
  });

  it("says how long to wait and where to look when a resend is still cooling down", async () => {
    mockCheckVerification.mockResolvedValue({
      email_verified: false, resend_cooldown_seconds: 0, verification_code_ttl_seconds: 0,
    });
    mockResendVerification.mockRejectedValueOnce(
      new ApiError(429, "ERR_AUTH_VERIFICATION_COOLDOWN", { retry_after_seconds: 40 }),
    );
    renderPage("user@example.com");
    fireEvent.click(await screen.findByRole("button", { name: "auth:verifyEmail.resendButton" }));
    // Guidance, not a failure: a neutral status line, while the button carries the live countdown.
    expect(await screen.findByRole("status")).toHaveTextContent("auth:verifyEmail.resendCooldownHint");
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "auth:verifyEmail.resendButtonWithCount" })).toBeDisabled();
  });

  it("tells the author where else the code may have landed", async () => {
    renderPage("user@example.com");
    expect(screen.getByText("auth:verifyEmail.notReceivedHint")).toBeVisible();
  });

  it("does not send the author to look for a code that was never sent", async () => {
    render(
      <MemoryRouter initialEntries={[{ pathname: "/verify-email", state: { email: "user@example.com", verificationSent: false } }]}>
        <VerifyEmail email="user@example.com" />
      </MemoryRouter>
    );
    expect(screen.getByText("auth:verifyEmail.deliveryPending")).toBeVisible();
    expect(screen.queryByText("auth:verifyEmail.notReceivedHint")).not.toBeInTheDocument();
    await waitFor(() => expect(mockCheckVerification).toHaveBeenCalledWith("user@example.com"));
  });

  it("keeps paid plan query when navigating from missing-email guard actions", async () => {
    renderPage("", "pro");

    fireEvent.click(screen.getByRole("button", { name: "auth:verifyEmail.goToRegister" }));
    expect(mockNavigate).toHaveBeenCalledWith("/register?plan=pro");

    fireEvent.click(screen.getByRole("button", { name: "auth:verifyEmail.backToLogin" }));
    expect(mockNavigate).toHaveBeenCalledWith("/login?plan=pro");
  });

  it("keeps paid plan query when navigating back to login in normal state", async () => {
    renderPage("user@example.com", "pro");

    await waitFor(() => {
      expect(mockCheckVerification).toHaveBeenCalledWith("user@example.com");
    });

    fireEvent.click(screen.getByRole("button", { name: "auth:verifyEmail.backToLogin" }));
    expect(mockNavigate).toHaveBeenCalledWith("/login?plan=pro");
  });

  it("keeps plan intent when navigating from missing-email guard actions", async () => {
    renderPage("", "pro");

    fireEvent.click(screen.getByRole("button", { name: "auth:verifyEmail.goToRegister" }));
    fireEvent.click(screen.getByRole("button", { name: "auth:verifyEmail.backToLogin" }));

    expect(mockNavigate).toHaveBeenNthCalledWith(1, "/register?plan=pro");
    expect(mockNavigate).toHaveBeenNthCalledWith(2, "/login?plan=pro");
  });

  it("keeps plan intent when navigating back to login in normal state", async () => {
    renderPage("user@example.com", "pro");

    await waitFor(() => {
      expect(mockCheckVerification).toHaveBeenCalledWith("user@example.com");
    });

    fireEvent.click(screen.getByRole("button", { name: "auth:verifyEmail.backToLogin" }));
    expect(mockNavigate).toHaveBeenCalledWith("/login?plan=pro");
  });

  it("redirects to billing for paid plan intent after successful verification", async () => {
    mockVerifyEmail.mockResolvedValue(undefined);
    renderPage("user@example.com", "pro");

    const inputs = screen.getAllByRole("textbox");
    expect(inputs).toHaveLength(6);

    fireEvent.change(inputs[0], { target: { value: "1" } });
    fireEvent.change(inputs[1], { target: { value: "2" } });
    fireEvent.change(inputs[2], { target: { value: "3" } });
    fireEvent.change(inputs[3], { target: { value: "4" } });
    fireEvent.change(inputs[4], { target: { value: "5" } });
    fireEvent.change(inputs[5], { target: { value: "6" } });

    await waitFor(() => {
      expect(mockVerifyEmail).toHaveBeenCalledWith("user@example.com", "123456");
    });

    await waitFor(() => {
      expect(mockNavigate).toHaveBeenCalledWith("/dashboard/billing?plan=pro&source=auth_plan_intent");
    }, { timeout: 3000 });
  });

  it("allows resend when server reports ttl expired and cooldown finished", async () => {
    mockCheckVerification.mockResolvedValue({
      email_verified: false,
      resend_cooldown_seconds: 0,
      verification_code_ttl_seconds: 0,
    });

    renderPage("user@example.com");

    await waitFor(() => {
      expect(mockCheckVerification).toHaveBeenCalledWith("user@example.com");
    });

    const resendButton = await screen.findByRole("button", {
      name: "auth:verifyEmail.resendButton",
    });
    expect(resendButton).toBeEnabled();
  });

  it("refreshes countdown from server after resend succeeds", async () => {
    mockCheckVerification
      .mockResolvedValueOnce({
        email_verified: false,
        resend_cooldown_seconds: 0,
        verification_code_ttl_seconds: 0,
      })
      .mockResolvedValueOnce({
        email_verified: false,
        resend_cooldown_seconds: 45,
        verification_code_ttl_seconds: 300,
      });
    mockResendVerification.mockResolvedValue(undefined);

    renderPage("user@example.com");

    const resendButton = await screen.findByRole("button", {
      name: "auth:verifyEmail.resendButton",
    });
    expect(resendButton).toBeEnabled();

    fireEvent.click(resendButton);

    await waitFor(() => {
      expect(mockResendVerification).toHaveBeenCalledWith("user@example.com");
    });
    await waitFor(() => {
      expect(mockCheckVerification).toHaveBeenCalledTimes(2);
    });
    expect(
      screen.getByRole("button", { name: "auth:verifyEmail.resendButtonWithCount" })
    ).toBeDisabled();
  });
});
