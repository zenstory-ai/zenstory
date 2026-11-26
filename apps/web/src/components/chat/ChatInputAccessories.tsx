import React from "react";
import type { AttachedMaterial } from "../../contexts/MaterialAttachmentContext";
import type { SelectedSkill } from "../../contexts/SkillTriggerContext";
import type { Skill, TextQuote } from "../../types";
import { ChatAttachmentChips } from "./ChatAttachmentChips";
import { SlashSkillMenu } from "./SlashSkillMenu";
import { SuggestionChipsBar } from "./SuggestionChipsBar";

interface ChatInputAccessoriesProps {
  attachedMaterials: AttachedMaterial[];
  quotes: TextQuote[];
  matchedSkills: Array<{ name: string; trigger: string }>;
  selectedSkills: SelectedSkill[];
  isFillLayout: boolean;
  isMobile: boolean;
  onRemoveMaterial: (id: string) => void;
  onRemoveQuote: (id: string) => void;
  onRemoveSkill: (id: string) => void;
  showSuggestionRow: boolean;
  isSuggestionLoading: boolean;
  displaySuggestions: string[];
  onSelectSuggestion: (suggestion: string) => void;
  onDismissSuggestion: (suggestion: string) => void;
  onRefreshSuggestions?: () => void;
  isRefreshingSuggestions: boolean;
  showSkillMenu: boolean;
  skillMenuRef: React.RefObject<HTMLDivElement | null>;
  isLoadingSkills: boolean;
  skillsLoadFailed: boolean;
  skillsCount: number;
  filteredSkills: Skill[];
  selectedSkillIndex: number;
  skillSelectionFull: boolean;
  isSkillPickable: (skill: Skill) => boolean;
  onPickSkill: (skill: Skill) => void;
  onRetrySkills: () => void;
}

export const ChatInputAccessories: React.FC<ChatInputAccessoriesProps> = ({
  attachedMaterials,
  quotes,
  matchedSkills,
  selectedSkills,
  isFillLayout,
  isMobile,
  onRemoveMaterial,
  onRemoveQuote,
  onRemoveSkill,
  showSuggestionRow,
  isSuggestionLoading,
  displaySuggestions,
  onSelectSuggestion,
  onDismissSuggestion,
  onRefreshSuggestions,
  isRefreshingSuggestions,
  showSkillMenu,
  skillMenuRef,
  isLoadingSkills,
  skillsLoadFailed,
  skillsCount,
  filteredSkills,
  selectedSkillIndex,
  skillSelectionFull,
  isSkillPickable,
  onPickSkill,
  onRetrySkills,
}) => {
  const hasAccessoryRows =
    attachedMaterials.length > 0 ||
    quotes.length > 0 ||
    matchedSkills.length > 0 ||
    selectedSkills.length > 0 ||
    showSuggestionRow ||
    showSkillMenu;

  const content = (
    <>
      <ChatAttachmentChips
        attachedMaterials={attachedMaterials}
        quotes={quotes}
        matchedSkills={matchedSkills}
        selectedSkills={selectedSkills}
        isFillLayout={isFillLayout}
        isMobile={isMobile}
        onRemoveMaterial={onRemoveMaterial}
        onRemoveQuote={onRemoveQuote}
        onRemoveSkill={onRemoveSkill}
      />

      {showSuggestionRow && (
        <SuggestionChipsBar
          isLoading={isSuggestionLoading}
          suggestions={displaySuggestions}
          isMobile={isMobile}
          onSelectSuggestion={onSelectSuggestion}
          onDismissSuggestion={onDismissSuggestion}
          onRefresh={onRefreshSuggestions}
          isRefreshing={isRefreshingSuggestions}
        />
      )}

      {showSkillMenu && (
        <SlashSkillMenu
          menuRef={skillMenuRef}
          isLoading={isLoadingSkills}
          loadFailed={skillsLoadFailed}
          hasLoadedAnySkills={skillsCount > 0}
          filteredSkills={filteredSkills}
          selectedIndex={selectedSkillIndex}
          selectionFull={skillSelectionFull}
          isSkillPickable={isSkillPickable}
          onPickSkill={onPickSkill}
          onRetry={onRetrySkills}
        />
      )}
    </>
  );

  if (isFillLayout && hasAccessoryRows) {
    return (
      <div className="min-h-0 overflow-y-auto pr-1" data-testid="chat-input-accessories">
        <div className="flex flex-col gap-2.5 pb-1">{content}</div>
      </div>
    );
  }

  return content;
};
