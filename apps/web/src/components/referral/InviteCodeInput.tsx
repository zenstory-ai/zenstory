import React, { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { useTranslation } from 'react-i18next';
import { referralApi } from '@/lib/referralApi';
import { CheckCircle, XCircle, Loader2 } from 'lucide-react';

interface InviteCodeInputProps {
  value: string;
  onChange: (value: string) => void;
  disabled?: boolean;
  required?: boolean;
  /** Whether the code was pre-filled from URL (auto-touched for validation display) */
  prefilled?: boolean;
}

/**
 * Invite code input component with real-time validation
 * Supports XXXX-XXXX format
 */
export const InviteCodeInput: React.FC<InviteCodeInputProps> = ({
  value,
  onChange,
  disabled,
  required = false,
  prefilled = false,
}) => {
  const { t } = useTranslation(['auth', 'common']);
  // Auto-touch if prefilled from URL, otherwise start as untouched
  const [touched, setTouched] = useState(prefilled);

  // Format input as user types (auto-add dash after 4 characters)
  const handleChange = (inputValue: string) => {
    // Remove any non-alphanumeric characters except dash
    let cleaned = inputValue.toUpperCase().replace(/[^A-Z0-9-]/g, '');

    // Auto-insert dash after 4 characters
    if (cleaned.length === 4 && !cleaned.includes('-')) {
      cleaned = cleaned + '-';
    }

    // Limit to 9 characters (XXXX-XXXX)
    if (cleaned.length > 9) {
      cleaned = cleaned.slice(0, 9);
    }

    onChange(cleaned);
    // Mark as touched when user types
    if (!touched) {
      setTouched(true);
    }
  };

  // Validate invite code when it matches the format
  const { data: validation, isLoading } = useQuery({
    queryKey: ['validateInviteCode', value],
    queryFn: () => referralApi.validateCode(value),
    enabled: value.length === 9 && value.includes('-'), // Only validate when format is complete
    staleTime: 60000, // Cache for 1 minute
    retry: false,
  });

  const showValidation = touched && value.length === 9 && validation;
  const isValid = validation?.valid;
  // 后端 message 是固定英文（Invite code is valid…），中文用户会看到英文；按结果显示本地化文案。
  const validationMessage = isValid
    ? t('auth:register.inviteCodeValid', '邀请码有效')
    : t('auth:register.inviteCodeInvalid', '邀请码无效或已不可用');
  const helperTextId = 'invite-code-helper';
  const validationMessageId = 'invite-code-validation';
  const describedById = showValidation ? validationMessageId : helperTextId;
  const helperText = required
    ? t('auth:register.inviteRequiredHint', '注册需要有效的邀请码')
    : t('auth:register.inviteCodeHint', '填写邀请码可获得积分奖励');

  return (
    <div className="space-y-2">
      <label
        htmlFor="invite_code"
        className="block text-[hsl(var(--text-secondary))] text-sm font-medium mb-2"
      >
        {required
          ? t('auth:register.inviteCodeLabelRequired', '邀请码')
          : t('auth:register.inviteCodeLabel', '邀请码（可选）')}
      </label>
      <div className="relative">
        <input
          id="invite_code"
          type="text"
          value={value}
          onChange={(e) => handleChange(e.target.value)}
          onBlur={() => setTouched(true)}
          placeholder="XXXX-XXXX"
          disabled={disabled}
          required={required}
          maxLength={9}
          autoComplete="off"
          className="input pr-10 font-mono tracking-widest uppercase text-center"
          aria-describedby={describedById}
          aria-invalid={showValidation ? !isValid : undefined}
        />
        {/* Validation indicator */}
        {isLoading && (
          <div className="absolute right-3 top-1/2 -translate-y-1/2">
            <Loader2 className="w-4 h-4 text-[hsl(var(--text-secondary))] animate-spin" />
          </div>
        )}
        {!isLoading && showValidation && (
          <div className="absolute right-3 top-1/2 -translate-y-1/2">
            {isValid ? (
              <CheckCircle className="w-4 h-4 text-[hsl(var(--success))]" />
            ) : (
              <XCircle className="w-4 h-4 text-[hsl(var(--error))]" />
            )}
          </div>
        )}
      </div>
      {/* Validation message */}
      {showValidation && (
        <p
          id={validationMessageId}
          className={`text-sm flex items-center gap-1.5 ${
            isValid ? 'text-[hsl(var(--success))]' : 'text-[hsl(var(--error))]'
          }`}
        >
          {isValid ? (
            <CheckCircle className="w-3.5 h-3.5 shrink-0" />
          ) : (
            <XCircle className="w-3.5 h-3.5 shrink-0" />
          )}
          {validationMessage}
        </p>
      )}
      {/* Helper text */}
      {!showValidation && (
        <p id={helperTextId} className="text-xs text-[hsl(var(--text-secondary))]">
          {helperText}
        </p>
      )}
    </div>
  );
};

export default InviteCodeInput;
