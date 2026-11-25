import React from "react";
import { ProjectProvider } from "../contexts/ProjectContext";
import { MaterialAttachmentProvider } from "../contexts/MaterialAttachmentContext";
import { MaterialLibraryProvider } from "../contexts/MaterialLibraryContext";
import { TextQuoteProvider } from "../contexts/TextQuoteContext";
import { SkillTriggerProvider } from "../contexts/SkillTriggerContext";

/**
 * 受保护路由 Providers
 *
 * 用于 Dashboard、项目编辑器等需要认证的路由
 * 包含：
 * - ProjectProvider: 项目管理
 * - MaterialLibraryProvider: 素材库状态（按需懒激活，未打开素材面板时不发请求）
 * - MaterialAttachmentProvider: 要素管理
 * - TextQuoteProvider: 文本引用管理
 *
 * 注意：ThemeProvider、AuthProvider、SEOProvider 都在 App.tsx 顶层统一管理
 */
export function ProtectedProviders({ children }: { children: React.ReactNode }) {
  return (
    <ProjectProvider>
      <MaterialLibraryProvider>
        <MaterialAttachmentProvider>
          <TextQuoteProvider>
            <SkillTriggerProvider>
              {children}
            </SkillTriggerProvider>
          </TextQuoteProvider>
        </MaterialAttachmentProvider>
      </MaterialLibraryProvider>
    </ProjectProvider>
  );
}
