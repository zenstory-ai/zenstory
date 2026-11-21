import { useId, useState } from 'react';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import { subscriptionApi } from '../../lib/subscriptionApi';
import { ApiError } from '../../lib/apiClient';
import { handleApiError } from '../../lib/errorHandler';
import { trackEvent } from '../../lib/analytics';
import { useTranslation } from 'react-i18next';
import Modal from '../ui/Modal';
import { Button } from '../ui/Button';
import { CelebrationBurst } from './CelebrationBurst';

interface RedeemCodeModalProps {
  isOpen: boolean;
  onClose: () => void;
  source?: string;
}

export function RedeemCodeModal({ isOpen, onClose, source }: RedeemCodeModalProps) {
  const { t } = useTranslation(['settings', 'dashboard', 'common']);
  const codeInputId = useId();
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

  const submitRedeem = () => {
    if (redeemMutation.isPending) return;
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

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    submitRedeem();
  };

  const handleClose = () => {
    if (redeemMutation.isPending) return;
    setSuccess('');
    setError('');
    setCelebrate(false);
    onClose();
  };

  const footer = success ? (
    <Button type="button" size="touch" onClick={handleClose}>
      {t('settings:subscription.redeemDone', '开始使用')}
    </Button>
  ) : (
    <>
      <Button
        type="button"
        variant="ghost"
        size="touch"
        onClick={handleClose}
        disabled={redeemMutation.isPending}
      >
        {t('common:cancel', '取消')}
      </Button>
      <Button
        type="button"
        size="touch"
        onClick={submitRedeem}
        disabled={redeemMutation.isPending || !code.trim()}
      >
        {redeemMutation.isPending ? t('dashboard:billing.redeeming', '正在兑换…') : t('settings:subscription.redeem', '兑换')}
      </Button>
    </>
  );

  return (
    <Modal
      open={isOpen}
      onClose={handleClose}
      title={t('settings:subscription.redeemTitle', '使用兑换码')}
      size="sm"
      showCloseButton={!redeemMutation.isPending}
      closeOnBackdropClick={!redeemMutation.isPending}
      closeOnEscape={!redeemMutation.isPending}
      footer={footer}
    >
      <form onSubmit={handleSubmit}>
        <Modal.Body className="relative">
          {celebrate && <CelebrationBurst className="top-6" />}
          <div className="mb-4">
            <label htmlFor={codeInputId} className="block text-sm font-medium text-[hsl(var(--text-secondary))] mb-1">
              {t('settings:subscription.codeLabel', '兑换码')}
            </label>
            <input
              id={codeInputId}
              type="text"
              value={code}
              onChange={(e) => setCode(e.target.value.toUpperCase())}
              placeholder="ERG-XXXX-XXXX-XXXXXXXX"
              className="input w-full min-h-11 font-mono text-sm"
              disabled={redeemMutation.isPending}
              autoFocus
            />
          </div>

          {error && (
            <div role="alert" className="mb-4 p-3 bg-[hsl(var(--error)/0.1)] text-[hsl(var(--error-text))] text-sm rounded-xl">
              {error}
            </div>
          )}

          {success && (
            <div
              role="status"
              className={`mb-4 p-3 bg-[hsl(var(--success)/0.1)] text-[hsl(var(--success-text))] text-sm rounded-xl ${celebrate ? 'animate-celebration-pop font-medium' : ''}`}
            >
              {success}
            </div>
          )}
        </Modal.Body>
      </form>
    </Modal>
  );
}
