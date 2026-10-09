import { useState } from 'react';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import { subscriptionApi } from '../../lib/subscriptionApi';
import { ApiError } from '../../lib/apiClient';
import { handleApiError } from '../../lib/errorHandler';
import { trackEvent } from '../../lib/analytics';
import { useTranslation } from 'react-i18next';
import Modal from '../ui/Modal';
import { CelebrationBurst } from './CelebrationBurst';

interface RedeemCodeModalProps {
  isOpen: boolean;
  onClose: () => void;
  source?: string;
}

export function RedeemCodeModal({ isOpen, onClose, source }: RedeemCodeModalProps) {
  const { t } = useTranslation(['settings', 'dashboard', 'common']);
  const [code, setCode] = useState('');
  const [error, setError] = useState('');
  const [success, setSuccess] = useState('');
  const [celebrate, setCelebrate] = useState(false);
  const queryClient = useQueryClient();

  const redeemMutation = useMutation({
    mutationFn: (redeemCode: string) => subscriptionApi.redeemCode(redeemCode, source),
    onSuccess: (data) => {
      // Never send the code itself: it is a bearer credential.
      trackEvent('redeem_code_succeeded', {
        tier: data.tier ?? undefined,
        duration_days: data.duration_days ?? undefined,
        source,
      });
      // The server's message is English-only; show the localized result instead.
      setSuccess(
        data.duration_days
          ? t('dashboard:billing.redeemSuccessDays', '兑换成功！Pro 已增加 {{days}} 天。', { days: data.duration_days })
          : t('settings:subscription.redeemSuccess', '兑换成功！')
      );
      setError('');
      setCode('');
      setCelebrate(Boolean(data.tier && data.tier !== 'free'));
      queryClient.invalidateQueries({ queryKey: ['subscription-status'] });
      queryClient.invalidateQueries({ queryKey: ['subscription-quota'] });
    },
    onError: (err: unknown) => {
      trackEvent('redeem_code_failed', {
        reason: err instanceof ApiError ? err.errorCode ?? `http_${err.status}` : 'unknown',
        source,
      });
      const normalizedError = handleApiError(err);
      setError(
        normalizedError || t('settings:subscription.redeemFailed', '兑换失败，请检查兑换码')
      );
      setSuccess('');
    },
  });

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    setError('');
    setSuccess('');
    setCelebrate(false);

    const trimmed = code.trim().toUpperCase();
    if (!trimmed) {
      setError(t('settings:subscription.codeRequired', '请输入兑换码'));
      return;
    }

    // Basic format validation
    if (!/^ERG-[A-Z0-9]{2,8}-[A-Z0-9]{4}-[A-Z0-9]{8}$/.test(trimmed)) {
      trackEvent('redeem_code_failed', { reason: 'invalid_format', source });
      setError(t('settings:subscription.invalidFormat', '兑换码格式不对，请检查是否少了字符'));
      return;
    }

    redeemMutation.mutate(trimmed);
  };

  const handleClose = () => {
    setSuccess('');
    setError('');
    setCelebrate(false);
    onClose();
  };

  return (
    <Modal
      open={isOpen}
      onClose={handleClose}
      title={t('settings:subscription.redeemTitle', '使用兑换码')}
      size="sm"
    >
      <form onSubmit={handleSubmit}>
        <Modal.Body className="relative">
          {celebrate && <CelebrationBurst className="top-6" />}
          <div className="mb-4">
            <label className="block text-sm font-medium text-[hsl(var(--text-secondary))] mb-1">
              {t('settings:subscription.codeLabel', '兑换码')}
            </label>
            <input
              type="text"
              value={code}
              onChange={(e) => setCode(e.target.value.toUpperCase())}
              placeholder="ERG-XXXX-XXXX-XXXXXXXX"
              className="w-full px-3 py-2 border border-[hsl(var(--border-color))] rounded-md bg-[hsl(var(--bg-tertiary))] text-[hsl(var(--text-primary))] focus:ring-2 focus:ring-[hsl(var(--accent-primary)/0.3)] focus:border-transparent font-mono text-sm"
              disabled={redeemMutation.isPending}
              autoFocus
            />
          </div>

          {error && (
            <div className="mb-4 p-3 bg-[hsl(var(--error)/0.1)] text-[hsl(var(--error))] text-sm rounded-md">
              {error}
            </div>
          )}

          {success && (
            <div
              role="status"
              className={`mb-4 p-3 bg-[hsl(var(--success)/0.1)] text-[hsl(var(--success))] text-sm rounded-md ${celebrate ? 'animate-celebration-pop font-medium' : ''}`}
            >
              {success}
            </div>
          )}
        </Modal.Body>

        <Modal.Footer>
          {success ? (
            <button
              type="button"
              onClick={handleClose}
              className="px-4 py-2 text-sm bg-[hsl(var(--accent-primary))] text-white rounded-md hover:opacity-90 transition-colors"
            >
              {t('settings:subscription.redeemDone', '开始使用')}
            </button>
          ) : (
            <>
              <button
                type="button"
                onClick={handleClose}
                className="px-4 py-2 text-sm text-[hsl(var(--text-secondary))] hover:bg-[hsl(var(--bg-hover))] rounded-md transition-colors"
              >
                {t('common:cancel', '取消')}
              </button>
              <button
                type="submit"
                disabled={redeemMutation.isPending || !code.trim()}
                className="px-4 py-2 text-sm bg-[hsl(var(--accent-primary))] text-white rounded-md hover:opacity-90 disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
              >
                {redeemMutation.isPending ? t('dashboard:billing.redeeming', '正在兑换...') : t('settings:subscription.redeem', '兑换')}
              </button>
            </>
          )}
        </Modal.Footer>
      </form>
    </Modal>
  );
}
