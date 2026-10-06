import React, { useState, useEffect } from 'react';
import { useTranslation } from 'react-i18next';
import { BarChart3, Zap, Users } from 'lucide-react';
import { skillsApi } from '../lib/api';
import { Modal } from './ui/Modal';
import type { SkillUsageStats } from '../types';
import { logger } from "../lib/logger";

interface SkillStatsDialogProps {
  isOpen: boolean;
  onClose: () => void;
  projectId: string;
}

export const SkillStatsDialog: React.FC<SkillStatsDialogProps> = ({
  isOpen,
  onClose,
  projectId,
}) => {
  const { t } = useTranslation('skills');
  const [stats, setStats] = useState<SkillUsageStats | null>(null);
  const [loading, setLoading] = useState(false);
  const [days, setDays] = useState(30);

  useEffect(() => {
    let cancelled = false;
    if (isOpen && projectId) {
      const loadStats = async () => {
        setLoading(true);
        try {
          const data = await skillsApi.getStats(projectId, days);
          if (!cancelled) setStats(data);
        } catch (error) {
          if (!cancelled) logger.error('Failed to load skill stats:', error);
        } finally {
          if (!cancelled) setLoading(false);
        }
      };
      loadStats();
    }
    return () => { cancelled = true; };
  }, [isOpen, projectId, days]);

  const maxDailyCount = stats?.daily_usage
    ? Math.max(...stats.daily_usage.map(d => d.count), 1)
    : 1;

  const headerContent = (
    <div className="flex items-center justify-between w-full">
      <div className="flex items-center gap-2">
        <BarChart3 size={18} className="text-[hsl(var(--accent))]" />
        <span className="text-sm font-medium text-[hsl(var(--text-primary))]">
          {t('stats.title')}
        </span>
      </div>
      <select
        value={days}
        onChange={(e) => setDays(Number(e.target.value))}
        className="text-xs px-2 py-1 rounded bg-[hsl(var(--bg-tertiary))] text-[hsl(var(--text-secondary))] border-none"
      >
        <option value={7}>{t('stats.days7')}</option>
        <option value={30}>{t('stats.days30')}</option>
        <option value={90}>{t('stats.days90')}</option>
      </select>
    </div>
  );

  return (
    <Modal
      open={isOpen}
      onClose={onClose}
      title={headerContent}
      size="lg"
      showCloseButton={true}
      closeOnBackdropClick={true}
      closeOnEscape={true}
      className="!p-0"
    >
      <div className="p-4">
        {loading ? (
          <div className="flex items-center justify-center h-40">
            <div className="animate-spin rounded-full h-6 w-6 border-2 border-[hsl(var(--accent))] border-t-transparent" />
          </div>
        ) : stats ? (
          <div className="space-y-6">
            {/* Summary Cards */}
            <div className="grid grid-cols-2 md:grid-cols-3 gap-3">
              <StatCard
                icon={<Zap size={16} />}
                label={t('stats.totalUses')}
                value={stats.total_triggers}
              />
              <StatCard
                icon={<BarChart3 size={16} />}
                label={t('stats.builtinCount')}
                value={stats.builtin_count}
              />
              <StatCard
                icon={<Users size={16} />}
                label={t('stats.userCount')}
                value={stats.user_count}
              />
            </div>

            {/* Top Skills */}
            {stats.top_skills.length > 0 && (
              <div>
                <h3 className="text-xs font-medium text-[hsl(var(--text-secondary))] mb-2">
                  {t('stats.topSkills')}
                </h3>
                <div className="space-y-2">
                  {stats.top_skills.slice(0, 5).map((skill, index) => (
                    <div
                      key={`${skill.skill_source}:${skill.skill_id}`}
                      className="flex items-center gap-3 p-2 rounded bg-[hsl(var(--bg-secondary))]"
                    >
                      <span className="text-xs text-[hsl(var(--text-tertiary))] w-4">
                        #{index + 1}
                      </span>
                      <span className="flex-1 text-sm text-[hsl(var(--text-primary))]">
                        {skill.skill_name}
                      </span>
                      <span className={`text-xs px-1.5 py-0.5 rounded ${
                        skill.skill_source === 'builtin'
                          ? 'bg-[hsl(var(--accent)/0.1)] text-[hsl(var(--accent))]'
                          : 'bg-[hsl(var(--success)/0.1)] text-[hsl(var(--success))]'
                      }`}>
                        {skill.skill_source === 'builtin' ? t('stats.builtin') : t('stats.user')}
                      </span>
                      <span className="text-sm font-medium text-[hsl(var(--text-primary))]">
                        {skill.count}
                      </span>
                    </div>
                  ))}
                </div>
              </div>
            )}

            {/* Daily Usage Chart */}
            {stats.daily_usage.length > 0 && (
              <div>
                <h3 className="text-xs font-medium text-[hsl(var(--text-secondary))] mb-2">
                  {t('stats.dailyUsage')}
                </h3>
                <div className="h-32 flex items-end gap-1">
                  {stats.daily_usage.slice(-14).map((day) => (
                    <div
                      key={day.date}
                      className="flex-1 flex flex-col items-center gap-1"
                      title={`${day.date}: ${day.count}`}
                    >
                      <div
                        className="w-full bg-[hsl(var(--accent))] rounded-t opacity-70 hover:opacity-100 transition-opacity"
                        style={{
                          height: `${Math.max((day.count / maxDailyCount) * 100, 4)}%`,
                          minHeight: day.count > 0 ? '4px' : '2px',
                        }}
                      />
                      <span className="text-[10px] text-[hsl(var(--text-tertiary))] rotate-45 origin-left">
                        {day.date.slice(5)}
                      </span>
                    </div>
                  ))}
                </div>
              </div>
            )}

            {/* Empty State */}
            {stats.total_triggers === 0 && (
              <div className="text-center py-8 text-[hsl(var(--text-tertiary))]">
                <Zap size={32} className="mx-auto mb-2 opacity-50" />
                <p className="text-sm">{t('stats.noData')}</p>
              </div>
            )}
          </div>
        ) : null}
      </div>
    </Modal>
  );
};

interface StatCardProps {
  icon: React.ReactNode;
  label: string;
  value: string | number;
}

const StatCard: React.FC<StatCardProps> = ({ icon, label, value }) => (
  <div className="p-3 rounded-lg bg-[hsl(var(--bg-secondary))]">
    <div className="flex items-center gap-2 text-[hsl(var(--text-tertiary))] mb-1">
      {icon}
      <span className="text-xs">{label}</span>
    </div>
    <div className="text-lg font-semibold text-[hsl(var(--text-primary))]">
      {value}
    </div>
  </div>
);

export default SkillStatsDialog;
