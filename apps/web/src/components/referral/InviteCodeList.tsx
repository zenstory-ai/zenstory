import React from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { Plus, Loader2 } from 'lucide-react';
import { useTranslation } from 'react-i18next';
import { useAuth } from '@/contexts/AuthContext';
import { referralApi } from '@/lib/referralApi';
import { InviteCodeCard } from './InviteCodeCard';
import type { InviteCode } from '@/types/referral';

const DEFAULT_MAX_INVITE_CODES = 3;

/**
 * List of user's invite codes with creation button
 */
export const InviteCodeList: React.FC = () => {
  const { t } = useTranslation('referral');
  const queryClient = useQueryClient();
  const { user } = useAuth();
  const isSuperuser = Boolean(user?.is_superuser);

  // Fetch invite codes
  const { data: codes = [], isLoading, isFetching, error } = useQuery({
    queryKey: ['inviteCodes'],
    queryFn: referralApi.getInviteCodes,
  });
  const isCodesLoading = isLoading || (isFetching && codes.length === 0);

  // Create new invite code mutation
  const createMutation = useMutation({
    mutationFn: referralApi.createInviteCode,
    onSuccess: (newCode) => {
      queryClient.setQueryData<InviteCode[]>(['inviteCodes'], (old) => [
        ...(old || []),
        newCode,
      ]);
    },
  });

  const activeCodesCount = codes.filter((code) => code.is_active).length;

  const handleCreateCode = () => {
    if (!isSuperuser && activeCodesCount >= DEFAULT_MAX_INVITE_CODES) return;
    createMutation.mutate();
  };

  if (isCodesLoading) {
    return (
      <div className="flex items-center justify-center py-8">
        <Loader2 size={24} className="animate-spin text-[hsl(var(--text-secondary))]" />
      </div>
    );
  }

  if (error) {
    return (
      <div className="text-center py-8 text-[hsl(var(--error))]">
        {t('inviteCodes.loadError')}
      </div>
    );
  }

  const canCreateMore = isSuperuser || activeCodesCount < DEFAULT_MAX_INVITE_CODES;

  return (
    <div className="space-y-4">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h3 className="text-base font-semibold text-[hsl(var(--text-primary))]">{t('inviteCodes.title')}</h3>
          <p className="text-xs text-[hsl(var(--text-secondary))] mt-0.5">
            {isSuperuser
              ? t('inviteCodes.unlimitedHint', '邀请码数量不限')
              : t('inviteCodes.maxHint', { count: DEFAULT_MAX_INVITE_CODES })}
          </p>
        </div>

        {/* Create button */}
        <button
          onClick={handleCreateCode}
          disabled={!canCreateMore || createMutation.isPending}
          className="btn btn-primary flex items-center gap-2"
        >
          {createMutation.isPending ? (
            <Loader2 size={16} className="animate-spin" />
          ) : (
            <Plus size={16} />
          )}
          <span>{t('inviteCodes.generateButton')}</span>
        </button>
      </div>

      {/* Codes list */}
      {codes.length > 0 ? (
        <div className="space-y-3">
          {codes.map((code) => (
            <InviteCodeCard key={code.id} inviteCode={code} />
          ))}
        </div>
      ) : (
        <div className="text-center py-10 bg-[hsl(var(--bg-secondary))] border border-[hsl(var(--border-color))] rounded-xl">
          <p className="text-[hsl(var(--text-secondary))] mb-1">{t('inviteCodes.noCodes')}</p>
          <p className="text-xs text-[hsl(var(--text-secondary)/0.7)] mb-4">{t('inviteCodes.noCodesHint')}</p>
          <button
            onClick={handleCreateCode}
            className="btn btn-primary inline-flex items-center gap-2"
          >
            <Plus size={16} />
            {t('inviteCodes.createFirst')}
          </button>
        </div>
      )}

      {/* Error message */}
      {createMutation.isError && (
        <div className="text-sm text-[hsl(var(--error))]">
          {t('inviteCodes.createError')}
        </div>
      )}
    </div>
  );
};

export default InviteCodeList;
