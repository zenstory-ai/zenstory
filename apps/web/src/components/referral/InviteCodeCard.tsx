import React, { useState } from 'react';
import { parseUTCDate } from '@/lib/dateUtils';
import { Copy, Share2, Check, Clock } from 'lucide-react';
import { useTranslation } from 'react-i18next';
import type { InviteCode } from '@/types/referral';
import { Card } from '../ui/Card';
import { Badge } from '../ui/Badge';
import { getLocaleCode } from '../../lib/i18n-helpers';

interface InviteCodeCardProps {
  inviteCode: InviteCode;
}

/**
 * Single invite code card with copy and share functionality
 */
export const InviteCodeCard: React.FC<InviteCodeCardProps> = ({ inviteCode }) => {
  const { t } = useTranslation('referral');
  const [copied, setCopied] = useState(false);

  const handleCopy = async () => {
    try {
      await navigator.clipboard.writeText(inviteCode.code);
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    } catch {
      // Silently fail - clipboard API not available
    }
  };

  const handleShare = async () => {
    const shareText = t('card.shareText', { code: inviteCode.code });
    const shareUrl = `${window.location.origin}/register?code=${inviteCode.code}`;

    if (navigator.share) {
      try {
        await navigator.share({
          title: t('card.shareTitle'),
          text: shareText,
          url: shareUrl,
        });
      } catch {
        // User cancelled share or share failed
      }
    } else {
      // Fallback: copy the full URL
      try {
        await navigator.clipboard.writeText(shareUrl);
        setCopied(true);
        setTimeout(() => setCopied(false), 2000);
      } catch {
        // Silently fail - clipboard API not available
      }
    }
  };

  const formatExpiry = (expiresAt: string | null) => {
    if (!expiresAt) return null;
    const date = parseUTCDate(expiresAt);
    const now = new Date();
    const diffMs = date.getTime() - now.getTime();
    const diffDays = Math.ceil(diffMs / (1000 * 60 * 60 * 24));

    if (diffDays <= 0) return t('card.expired');
    if (diffDays === 1) return t('card.expiresTomorrow');
    if (diffDays <= 7) return t('card.expiresInDays', { count: diffDays });
    return date.toLocaleDateString(getLocaleCode());
  };

  const usageText = `${inviteCode.current_uses}/${inviteCode.max_uses}`;
  const isExhausted = inviteCode.current_uses >= inviteCode.max_uses;
  const isExpired = inviteCode.expires_at
    ? parseUTCDate(inviteCode.expires_at).getTime() <= new Date().getTime()
    : false;
  const expiryText = formatExpiry(inviteCode.expires_at);

  return (
    <Card hoverable className="space-y-3">
      {/* Code display */}
      <div className="flex items-center justify-between">
        <div className="flex-1 min-w-0">
          <div className="text-xs text-[hsl(var(--text-secondary))] mb-1 font-medium">{t('card.inviteCode')}</div>
          <div className="font-mono text-lg font-bold text-[hsl(var(--text-primary))] tracking-widest">
            {inviteCode.code}
          </div>
        </div>

        {/* Action buttons */}
        <div className="flex items-center gap-1.5 ml-3">
          <button
            type="button"
            onClick={handleCopy}
            className="min-h-[44px] min-w-[44px] p-2 rounded-lg transition-all bg-[hsl(var(--bg-tertiary))] hover:bg-[hsl(var(--accent-primary)/0.15)] active:scale-95 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[hsl(var(--accent-primary)/0.6)] focus-visible:ring-offset-2 focus-visible:ring-offset-[hsl(var(--bg-secondary))]"
            title={t('card.copyInviteCode')}
            aria-label={t('card.copyInviteCode')}
          >
            {copied ? (
              <Check size={18} className="text-[hsl(var(--success))]" />
            ) : (
              <Copy size={18} className="text-[hsl(var(--text-secondary))]" />
            )}
          </button>
          <button
            type="button"
            onClick={handleShare}
            className="min-h-[44px] min-w-[44px] p-2 rounded-lg transition-all bg-[hsl(var(--bg-tertiary))] hover:bg-[hsl(var(--accent-primary)/0.15)] active:scale-95 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[hsl(var(--accent-primary)/0.6)] focus-visible:ring-offset-2 focus-visible:ring-offset-[hsl(var(--bg-secondary))]"
            title={t('card.shareInviteCode')}
            aria-label={t('card.shareInviteCode')}
          >
            <Share2 size={18} className="text-[hsl(var(--text-secondary))]" />
          </button>
        </div>
      </div>

      {/* Status indicators */}
      <div className="flex items-center gap-3 text-xs flex-wrap">
        {/* Usage counter */}
        <div className={`flex items-center gap-1.5 ${isExhausted ? 'text-[hsl(var(--warning))]' : 'text-[hsl(var(--text-secondary))]'}`}>
          <span>{t('card.used')}</span>
          <span className="font-semibold">{usageText}</span>
        </div>

        {/* Status badge */}
        {!inviteCode.is_active || isExpired ? (
          <Badge variant="neutral">
            {isExpired ? t('card.expired') : t('card.disabled')}
          </Badge>
        ) : isExhausted ? (
          <Badge variant="warning">
            {t('card.exhausted')}
          </Badge>
        ) : (
          <Badge variant="success">
            {t('card.available')}
          </Badge>
        )}

        {/* Expiry date */}
        {expiryText && (
          <div className="flex items-center gap-1 text-[hsl(var(--text-secondary))]">
            <Clock size={12} />
            <span>{expiryText}</span>
          </div>
        )}
      </div>
    </Card>
  );
};

export default InviteCodeCard;
