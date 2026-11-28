import { render, screen } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

const mocks = vi.hoisted(() => ({
  language: 'en',
}));

vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    t: (key: string, options?: { defaultValue?: string; [name: string]: unknown }) => {
      const template = options?.defaultValue ?? key;
      if (!options) return template;
      const rendered = template.replace(/\{\{(\w+)\}\}/g, (_m, name: string) => String(options[name] ?? ''));
      if (template === key && options.localTime) {
        return `${key}:${String(options.localTime)}`;
      }
      return rendered;
    },
    i18n: {
      get language() {
        return mocks.language;
      },
      get resolvedLanguage() {
        return mocks.language;
      },
    },
  }),
}));

vi.mock('../../../contexts/MobileLayoutContext', () => ({
  useMobileLayout: () => ({ isMobile: false, activePanel: 'chat' }),
}));

vi.mock('../../../hooks/usePaidPlanWhenOpen', () => ({
  usePaidPlanWhenOpen: () => ({ isPaid: false, resolved: true }),
}));

vi.mock('../../../hooks/useUpgradeExposure', () => ({
  useUpgradeExposure: () => ({ current: null }),
}));

vi.mock('../../../hooks/useAnimatedPresence', () => ({
  useAnimatedPresence: (open: boolean) => ({ isMounted: open, isExiting: false }),
}));

vi.mock('../UpgradePromptModal', () => ({
  UpgradePromptModal: ({ open, title, description }: { open: boolean; title: string; description: string }) =>
    open ? (
      <div data-testid="mock-upgrade-modal">
        <h2>{title}</h2>
        <p>{description}</p>
      </div>
    ) : null,
}));

import { ChatQuotaCard } from '../ChatQuotaCard';
import { IdeaQuotaWallModal } from '../IdeaQuotaWallModal';
import { ChatStreamErrorCard } from '../../chat/ChatStatusBanners';

describe('Local timezone quota reset formatting', () => {
  beforeEach(() => {
    mocks.language = 'en';
  });

  it('renders local-time reset copy in ChatQuotaCard for non-zh UI and Beijing-time copy for zh UI', () => {
    const first = render(<ChatQuotaCard open limit={3} resetAt="2026-10-10T00:00:00+08:00" />);
    expect(screen.getByTestId('chat-quota-card')).toHaveTextContent('your time (00:00 UTC+8)');
    first.unmount();

    mocks.language = 'zh-CN';
    const second = render(<ChatQuotaCard open limit={3} resetAt="2026-10-10T00:00:00+08:00" />);
    expect(screen.getByTestId('chat-quota-card')).toHaveTextContent('北京时间 10月10日 00:00 恢复');
    second.unmount();
  });

  it('renders local-time reset copy in IdeaQuotaWallModal for non-zh UI and Beijing-time copy for zh UI', () => {
    const first = render(<IdeaQuotaWallModal open onClose={vi.fn()} resetAt="2026-10-10T00:00:00+08:00" />);
    expect(screen.getByTestId('mock-upgrade-modal')).toHaveTextContent('your time (00:00 UTC+8)');
    first.unmount();

    mocks.language = 'zh-CN';
    const second = render(<IdeaQuotaWallModal open onClose={vi.fn()} resetAt="2026-10-10T00:00:00+08:00" />);
    expect(screen.getByTestId('mock-upgrade-modal')).toHaveTextContent('北京时间 10月10日 00:00 恢复');
    second.unmount();
  });

  it('uses local-time quota hints in ChatStreamErrorCard for non-zh UI and standard hints for zh UI', () => {
    const enAccount = render(
      <ChatStreamErrorCard
        error=""
        errorCode="ERR_QUOTA_AI_CONVERSATIONS_EXCEEDED"
        isAiQuotaLimit
        isNetworkQuotaLimit={false}
        aiSendBlocked
        canRetry={false}
        isMobile={false}
        onRetry={vi.fn()}
        onOpenUpgradeModal={vi.fn()}
      />,
    );
    expect(screen.getByRole('alert')).toHaveTextContent(/chat:panel\.quotaExceededHintLocal:.+/);
    enAccount.unmount();

    const enNetwork = render(
      <ChatStreamErrorCard
        error=""
        errorCode="ERR_NETWORK_FREE_AI_QUOTA_EXCEEDED"
        isAiQuotaLimit={false}
        isNetworkQuotaLimit
        aiSendBlocked
        canRetry={false}
        isMobile={false}
        onRetry={vi.fn()}
        onOpenUpgradeModal={vi.fn()}
      />,
    );
    expect(screen.getByRole('alert')).toHaveTextContent(/chat:panel\.networkQuotaExceededHintLocal:.+/);
    enNetwork.unmount();

    mocks.language = 'zh-CN';
    const zhAccount = render(
      <ChatStreamErrorCard
        error=""
        errorCode="ERR_QUOTA_AI_CONVERSATIONS_EXCEEDED"
        isAiQuotaLimit
        isNetworkQuotaLimit={false}
        aiSendBlocked
        canRetry={false}
        isMobile={false}
        onRetry={vi.fn()}
        onOpenUpgradeModal={vi.fn()}
      />,
    );
    expect(screen.getByRole('alert')).toHaveTextContent('chat:panel.quotaExceededHint');
    expect(screen.getByRole('alert')).not.toHaveTextContent('quotaExceededHintLocal');
    zhAccount.unmount();
  });
});
