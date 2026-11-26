import { useState, useRef, useEffect, useMemo, useCallback } from "react";
import type { KeyboardEvent, RefObject } from "react";
import { MAX_SELECTED_SKILLS, type SelectedSkill } from "../../contexts/SkillTriggerContext";
import { skillsApi } from "../../lib/api";
import { logger } from "../../lib/logger";
import type { Skill } from "../../types";

interface UseSlashSkillMenuOptions {
  selectedSkills: SelectedSkill[];
  selectSkill: (skill: SelectedSkill) => void;
  setInput: (value: string | ((prev: string) => string)) => void;
  layout: "auto" | "fill";
  textareaRef: RefObject<HTMLTextAreaElement | null>;
}

export function useSlashSkillMenu({
  selectedSkills,
  selectSkill,
  setInput,
  layout,
  textareaRef,
}: UseSlashSkillMenuOptions) {
  const [showSkillMenu, setShowSkillMenu] = useState(false);
  const [skills, setSkills] = useState<Skill[]>([]);
  const [skillSearchQuery, setSkillSearchQuery] = useState("");
  const [selectedSkillIndex, setSelectedSkillIndex] = useState(0);
  const [isLoadingSkills, setIsLoadingSkills] = useState(false);
  const [skillsLoadFailed, setSkillsLoadFailed] = useState(false);
  const skillMenuRef = useRef<HTMLDivElement>(null);

  const loadSkills = useCallback(async () => {
    if (skills.length > 0) return;
    setIsLoadingSkills(true);
    setSkillsLoadFailed(false);
    try {
      const response = await skillsApi.list();
      setSkills(response.skills.filter((s) => s.is_active));
    } catch (error) {
      logger.error("Failed to load skills:", error);
      setSkillsLoadFailed(true);
    } finally {
      setIsLoadingSkills(false);
    }
  }, [skills.length]);

  const filteredSkills = useMemo(() => {
    if (!skillSearchQuery) return skills;
    const query = skillSearchQuery.toLowerCase();
    return skills.filter(
      (skill) =>
        skill.name.toLowerCase().includes(query) ||
        skill.triggers.some((trigger) => trigger.toLowerCase().includes(query)) ||
        skill.description?.toLowerCase().includes(query)
    );
  }, [skills, skillSearchQuery]);

  // 已选满 MAX_SELECTED_SKILLS 个时，菜单里未选中的技能不可再选（否则 selectSkill 静默忽略、
  // 输入却被清空，用户看不出发生了什么）；已选中的仍可点，等价于保持原样。
  const skillSelectionFull = selectedSkills.length >= MAX_SELECTED_SKILLS;
  const isSkillPickable = useCallback(
    (skill: Skill) =>
      !skillSelectionFull || selectedSkills.some((selected) => selected.id === skill.id),
    [selectedSkills, skillSelectionFull]
  );

  const closeSkillMenu = useCallback(() => {
    setShowSkillMenu(false);
    setSkillSearchQuery("");
    setSelectedSkillIndex(0);
  }, []);

  const pickSkillFromMenu = useCallback(
    (skill: Skill) => {
      if (!isSkillPickable(skill)) {
        return;
      }
      selectSkill({ id: skill.id, name: skill.name });
      setInput("");
      closeSkillMenu();
      textareaRef.current?.focus();
      if (layout === "auto" && textareaRef.current) {
        textareaRef.current.style.height = "auto";
      }
    },
    [closeSkillMenu, isSkillPickable, layout, selectSkill, setInput, textareaRef]
  );

  useEffect(() => {
    const handleClickOutside = (e: MouseEvent) => {
      if (skillMenuRef.current && !skillMenuRef.current.contains(e.target as Node)) {
        closeSkillMenu();
      }
    };
    if (showSkillMenu) {
      document.addEventListener("mousedown", handleClickOutside);
    }
    return () => document.removeEventListener("mousedown", handleClickOutside);
  }, [showSkillMenu, closeSkillMenu]);

  const syncWithInputValue = useCallback(
    (value: string) => {
      if (value.startsWith("/")) {
        setSkillSearchQuery(value.slice(1));
        setSelectedSkillIndex(0);
        if (!showSkillMenu) {
          setShowSkillMenu(true);
          void loadSkills();
        }
      } else if (showSkillMenu) {
        closeSkillMenu();
      }
    },
    [closeSkillMenu, loadSkills, showSkillMenu]
  );

  const handleSkillMenuKeyDown = useCallback(
    (e: KeyboardEvent<HTMLTextAreaElement>): boolean => {
      if (!showSkillMenu) return false;

      // Escape always dismisses the skill menu (even with 0 matches or while loading/failed)
      if (e.key === "Escape") {
        e.preventDefault();
        closeSkillMenu();
        setInput("");
        textareaRef.current?.focus();
        return true;
      }

      if (filteredSkills.length === 0) return false;

      if (e.key === "ArrowDown") {
        e.preventDefault();
        setSelectedSkillIndex((prev) => (prev < filteredSkills.length - 1 ? prev + 1 : 0));
        return true;
      }
      if (e.key === "ArrowUp") {
        e.preventDefault();
        setSelectedSkillIndex((prev) => (prev > 0 ? prev - 1 : filteredSkills.length - 1));
        return true;
      }
      if (e.key === "Enter" || e.key === "Tab") {
        e.preventDefault();
        pickSkillFromMenu(filteredSkills[selectedSkillIndex]);
        return true;
      }

      return false;
    },
    [closeSkillMenu, filteredSkills, pickSkillFromMenu, selectedSkillIndex, setInput, showSkillMenu, textareaRef]
  );

  return {
    showSkillMenu,
    skillsCount: skills.length,
    filteredSkills,
    selectedSkillIndex,
    isLoadingSkills,
    skillsLoadFailed,
    skillSelectionFull,
    skillMenuRef,
    isSkillPickable,
    loadSkills,
    pickSkillFromMenu,
    syncWithInputValue,
    handleSkillMenuKeyDown,
  };
}
