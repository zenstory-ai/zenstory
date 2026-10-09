import React from 'react';
import { useTranslation } from 'react-i18next';
import { useNavigate } from 'react-router-dom';
import { Terminal, ArrowRight } from 'lucide-react';

const AGENT_BADGES = ['Claude Code', 'Codex', 'OpenClaw'];

export function AgentApiSection() {
  const { t } = useTranslation('home');
  const navigate = useNavigate();

  const handleGetStarted = () => {
    navigate('/dashboard', { state: { openSettingsTab: 'agent' } });
  };

  return (
    <section
      className="relative py-14 md:py-20 px-4 sm:px-6 lg:px-8 bg-[hsl(var(--bg-primary))] overflow-hidden"
      aria-label={t('agentApi.sectionLabel', { defaultValue: '接入你的 AI 助手' })}
    >
      {/* Subtle grid overlay matching the homepage hero */}
      <div className="absolute inset-0 pointer-events-none opacity-40">
        <div className="absolute inset-0 bg-[linear-gradient(rgba(255,255,255,0.015)_1px,transparent_1px),linear-gradient(90deg,rgba(255,255,255,0.015)_1px,transparent_1px)] bg-[size:48px_48px]" />
      </div>

      {/* Accent glow -- positioned to the left behind the icon area */}
      <div className="absolute -left-24 top-1/2 -translate-y-1/2 w-[320px] h-[320px] bg-[hsl(var(--accent-primary))] opacity-[0.06] blur-[100px] rounded-full pointer-events-none" />

      <div className="relative max-w-4xl mx-auto">
        <div className="group relative flex flex-col md:flex-row items-stretch gap-0 rounded-2xl border border-[hsl(var(--border-color))] bg-[hsl(var(--bg-secondary))] overflow-hidden transition-shadow duration-500 hover:shadow-[0_0_40px_hsl(var(--accent-primary)/0.08)]">

          {/* Left accent strip */}
          <div className="hidden md:block w-1 shrink-0 bg-gradient-to-b from-[hsl(var(--accent-primary)/0.6)] via-[hsl(var(--accent-primary)/0.2)] to-transparent" />

          {/* Main content */}
          <div className="flex-1 flex flex-col sm:flex-row items-center gap-5 p-5 sm:p-7 md:p-8">
            {/* Terminal icon -- monospace feel */}
            <div className="flex-shrink-0 relative">
              <div
                className="w-12 h-12 md:w-14 md:h-14 rounded-xl bg-[hsl(var(--bg-tertiary))] border border-[hsl(var(--border-color))] flex items-center justify-center group-hover:border-[hsl(var(--accent-primary)/0.35)] transition-colors duration-300"
                aria-hidden="true"
              >
                <Terminal className="w-6 h-6 md:w-7 md:h-7 text-[hsl(var(--accent-primary))]" />
              </div>
              {/* Pulsing dot */}
              <span className="absolute -top-0.5 -right-0.5 flex h-3 w-3">
                <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-[hsl(var(--accent-primary))] opacity-40" />
                <span className="relative inline-flex rounded-full h-3 w-3 bg-[hsl(var(--accent-primary))]" />
              </span>
            </div>

            {/* Text block */}
            <div className="flex-1 text-center sm:text-left">
              <div className="flex items-center gap-2 justify-center sm:justify-start mb-1.5">
                <span className="px-2 py-0.5 rounded-md text-[10px] md:text-xs font-semibold tracking-wide uppercase bg-[hsl(var(--accent-primary)/0.12)] text-[hsl(var(--accent-primary))] border border-[hsl(var(--accent-primary)/0.15)]">
                  {t('agentApi.badge', { defaultValue: '新功能' })}
                </span>
                <h3 className="text-base md:text-lg font-semibold text-[hsl(var(--text-primary))] leading-snug">
                  {t('agentApi.title', { defaultValue: '用 Claude Code 等 AI 助手直接写你的小说' })}
                </h3>
              </div>
              <p className="text-xs md:text-sm text-[hsl(var(--text-secondary))] leading-relaxed mb-3">
                {t('agentApi.subtitle', { defaultValue: '在设置里创建 API 密钥，在终端用 zenstory 命令行工具登录，再给 AI 助手装上 zenstory 技能，Claude Code、Codex、OpenClaw 就能读写你的项目。' })}
              </p>

              {/* Agent badges -- styled like code tags */}
              <div className="flex items-center gap-2 flex-wrap">
                <span className="text-[11px] md:text-xs text-[hsl(var(--text-secondary))]">
                  {t('agentApi.supportedAgents', { defaultValue: '支持' })}
                </span>
                {AGENT_BADGES.map((agent) => (
                  <span
                    key={agent}
                    className="inline-flex items-center gap-1 px-2 py-0.5 rounded text-[11px] md:text-xs font-mono bg-[hsl(var(--bg-tertiary))] text-[hsl(var(--text-secondary))] border border-[hsl(var(--border-color))] tracking-tight"
                  >
                    <span className="text-[hsl(var(--accent-secondary))]" aria-hidden="true">$</span>
                    {agent}
                  </span>
                ))}
              </div>
            </div>

            {/* CTA button */}
            <button
              onClick={handleGetStarted}
              className="flex-shrink-0 group/btn relative h-10 md:h-11 px-5 md:px-6 rounded-xl text-sm font-semibold text-white bg-[hsl(var(--accent-primary))] inline-flex items-center gap-2 overflow-hidden transition-all duration-200 hover:shadow-[0_0_24px_hsl(var(--accent-primary)/0.35)] hover:scale-[1.02] active:scale-[0.98] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[hsl(var(--accent-primary)/0.6)] focus-visible:ring-offset-2 focus-visible:ring-offset-[hsl(var(--bg-primary))]"
              aria-label={t('agentApi.ctaAriaLabel', { defaultValue: '去设置创建 API 密钥' })}
            >
              <span className="relative z-10">{t('agentApi.cta', { defaultValue: '创建 API 密钥' })}</span>
              <ArrowRight size={15} className="relative z-10 group-hover/btn:translate-x-0.5 transition-transform duration-200" />
              {/* Hover shine */}
              <div className="absolute inset-0 bg-gradient-to-r from-transparent via-white/15 to-transparent -translate-x-full group-hover/btn:translate-x-full transition-transform duration-600 pointer-events-none" />
            </button>
          </div>
        </div>
      </div>
    </section>
  );
}

export default AgentApiSection;
