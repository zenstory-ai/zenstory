import { useId, type ReactNode } from "react";
import { useTranslation } from "react-i18next";
import i18n from "../../lib/i18n";
import { materialEnumLabel } from "../../lib/materialEnumLabels";
import type {
  MaterialChapter,
  MaterialCharacter,
  MaterialCharacterRelationship,
  MaterialGoldenFinger,
  MaterialPlot,
  MaterialStory,
  MaterialStoryLine,
  MaterialWorldView,
} from "../../lib/materialsApi";
import { BookOpen, File, GitBranch, Globe, Sparkles, User, Users, Zap } from "../icons";
import { Badge } from "../ui/Badge";
import { MaterialReadingContent as MarkdownContent } from "./MaterialReadingContent";
import { parseThemes, type MaterialTreeItem } from "./materialHelpers";

// Shared structure for the seven reference types; chapter layout stays independent.
function DetailHeader({
  id,
  icon,
  title,
  children,
}: {
  id: string;
  icon: ReactNode;
  title: string;
  children?: ReactNode;
}) {
  return (
    <header className="material-analysis-header">
      <div className="flex items-start gap-3">
        <span className="mt-1.5 shrink-0 text-[hsl(var(--text-secondary))]" aria-hidden="true">
          {icon}
        </span>
        <h2 id={id} className="text-2xl font-semibold leading-snug tracking-tight text-[hsl(var(--text-primary))]">
          {title}
        </h2>
      </div>
      {children}
    </header>
  );
}

function DetailSection({ title, children }: { title: string; children: ReactNode }) {
  const id = useId();
  return (
    <section className="material-analysis-section" aria-labelledby={id}>
      <h3 id={id}>{title}</h3>
      {children}
    </section>
  );
}

function DetailMetadata({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="material-analysis-fact">
      <dt>{label}</dt>
      <dd>{children}</dd>
    </div>
  );
}

function DetailTags({ values }: { values: string[] }) {
  return (
    <div className="flex flex-wrap gap-2">
      {values.map((value, index) => (
        <Badge key={index} variant="neutral" size="md">
          {value}
        </Badge>
      ))}
    </div>
  );
}

export interface MaterialContentDetailProps {
  item: MaterialTreeItem;
}

export function MaterialContentDetail({ item }: MaterialContentDetailProps) {
  const { t } = useTranslation(["materials"]);
  const titleId = useId();

  if (item.type === "chapter") {
    const chapter = item.data as MaterialChapter;
    return (
      <article className="material-detail" aria-labelledby="material-chapter-title">
        <div className="mb-6">
          <div className="flex items-center gap-2 mb-2">
            <BookOpen className="w-5 h-5 text-[hsl(var(--text-secondary))]" />
            <h2 id="material-chapter-title" className="heading-display text-2xl font-semibold leading-snug tracking-tight text-[hsl(var(--text-primary))] md:text-[28px]">
              {chapter.title}
            </h2>
          </div>
          <div className="flex flex-wrap items-center gap-x-4 gap-y-1 text-xs tabular-nums text-[hsl(var(--text-secondary))]">
            <span>{t("materials:detail.chapterNumber")}: {chapter.chapter_number}</span>
            <span>{t("materials:detail.wordCount")}: {chapter.word_count?.toLocaleString()}</span>
          </div>
        </div>

        {chapter.summary && (
          <div className="mb-8 rounded-xl border border-[hsl(var(--separator-color))] bg-[hsl(var(--bg-primary))] p-4">
            <h3 className="text-sm font-semibold text-[hsl(var(--text-secondary))] mb-2">
              {t("materials:detail.summary")}
            </h3>
            <MarkdownContent content={chapter.summary} />
          </div>
        )}

        {chapter.content && (
          <MarkdownContent content={chapter.content} className="material-reader-manuscript" />
        )}
      </article>
    );
  }

  if (item.type === "character") {
    const character = item.data as MaterialCharacter;
    return (
      <article className="material-detail material-analysis" aria-labelledby={titleId}>
        <DetailHeader id={titleId} icon={<User className="w-5 h-5" />} title={character.name}>
          <dl className="material-analysis-facts">
            {character.archetype && (
              <DetailMetadata label={t("materials:detail.characterArchetype")}>
                {t(`materials:enums.archetype.${character.archetype.trim().toLowerCase()}`, { defaultValue: character.archetype })}
              </DetailMetadata>
            )}
            {!!character.aliases?.length && (
              <DetailMetadata label={t("materials:detail.aliases")}>
                {character.aliases.join(i18n.language === "zh" ? "、" : ", ")}
              </DetailMetadata>
            )}
            {character.first_appearance_chapter != null && (
              <DetailMetadata label={t("materials:detail.firstAppearance")}>
                {t("materials:detail.chapter", { number: character.first_appearance_chapter })}
              </DetailMetadata>
            )}
          </dl>
        </DetailHeader>
        {character.description && (
          <DetailSection title={t("materials:detail.characterOverview")}>
            <MarkdownContent content={character.description} />
          </DetailSection>
        )}
      </article>
    );
  }

  if (item.type === "story") {
    const story = item.data as MaterialStory;
    const themes = parseThemes(story.themes);
    return (
      <article className="material-detail material-analysis" aria-labelledby={titleId}>
        <DetailHeader id={titleId} icon={<Sparkles className="w-5 h-5" />} title={story.title}>
          {story.story_type && <Badge variant="neutral" size="md">{materialEnumLabel(t, "storyType", story.story_type)}</Badge>}
          {story.chapter_range && (
            <dl className="material-analysis-facts">
              <DetailMetadata label={t("materials:detail.relatedChapters")}>
                {t("materials:detail.chapterRange", { range: story.chapter_range })}
              </DetailMetadata>
            </dl>
          )}
        </DetailHeader>
        {story.synopsis && (
          <DetailSection title={t("materials:detail.storySynopsis")}>
            <MarkdownContent content={story.synopsis} />
          </DetailSection>
        )}
        {story.core_objective && (
          <DetailSection title={t("materials:detail.coreObjective")}>
            <MarkdownContent content={story.core_objective} />
          </DetailSection>
        )}
        {story.core_conflict && (
          <DetailSection title={t("materials:detail.coreConflict")}>
            <MarkdownContent content={story.core_conflict} />
          </DetailSection>
        )}
        {!!themes.length && (
          <DetailSection title={t("materials:detail.themes")}>
            <DetailTags values={themes} />
          </DetailSection>
        )}
      </article>
    );
  }

  if (item.type === "plot") {
    const plot = item.data as MaterialPlot;
    return (
      <article className="material-detail material-analysis" aria-labelledby={titleId}>
        <DetailHeader id={titleId} icon={<File className="w-5 h-5" />} title={t("materials:detail.plotTitle")}>
          <Badge variant="neutral" size="md">{materialEnumLabel(t, "plotType", plot.plot_type)}</Badge>
        </DetailHeader>
        {plot.description && (
          <DetailSection title={t("materials:detail.plotEvent")}>
            <MarkdownContent content={plot.description} />
          </DetailSection>
        )}
        {!!plot.characters?.length && (
          <DetailSection title={t("materials:detail.involvedCharacters")}>
            <DetailTags values={plot.characters} />
          </DetailSection>
        )}
      </article>
    );
  }

  if (item.type === "storyline") {
    const storyline = item.data as MaterialStoryLine;
    return (
      <article className="material-detail material-analysis" aria-labelledby={titleId}>
        <DetailHeader id={titleId} icon={<GitBranch className="w-5 h-5" />} title={storyline.title}>
          <dl className="material-analysis-facts">
            <DetailMetadata label={t("materials:detail.storiesCount")}>{storyline.stories_count}</DetailMetadata>
          </dl>
        </DetailHeader>
        {storyline.description && (
          <DetailSection title={t("materials:detail.storylineOverview")}>
            <MarkdownContent content={storyline.description} />
          </DetailSection>
        )}
        {!!storyline.main_characters?.length && (
          <DetailSection title={t("materials:detail.mainCharacters")}>
            <DetailTags values={storyline.main_characters} />
          </DetailSection>
        )}
        {!!storyline.themes?.length && (
          <DetailSection title={t("materials:detail.themes")}>
            <DetailTags values={storyline.themes} />
          </DetailSection>
        )}
      </article>
    );
  }

  if (item.type === "relationship") {
    const relationship = item.data as MaterialCharacterRelationship;
    // Known values are localized; free-form historical descriptions remain intact.
    const sentiment =
      relationship.sentiment &&
      t(`materials:enums.sentiment.${relationship.sentiment.trim().toLowerCase()}`, {
        defaultValue: relationship.sentiment,
      });
    return (
      <article className="material-detail material-analysis" aria-labelledby={titleId}>
        <DetailHeader id={titleId} icon={<Users className="w-5 h-5" />} title={`${relationship.character_a_name} - ${relationship.character_b_name}`}>
          <Badge variant="neutral" size="md">{materialEnumLabel(t, "relationshipType", relationship.relationship_type)}</Badge>
        </DetailHeader>
        {relationship.description && (
          <DetailSection title={t("materials:detail.relationshipDescription")}>
            <MarkdownContent content={relationship.description} />
          </DetailSection>
        )}
        {sentiment && (
          <DetailSection title={t("materials:detail.sentiment")}>
            <MarkdownContent content={sentiment} />
          </DetailSection>
        )}
      </article>
    );
  }

  if (item.type === "goldenfinger") {
    const goldenfinger = item.data as MaterialGoldenFinger;
    return (
      <article className="material-detail material-analysis" aria-labelledby={titleId}>
        <DetailHeader id={titleId} icon={<Zap className="w-5 h-5" />} title={goldenfinger.name}>
          <Badge variant="neutral" size="md">{materialEnumLabel(t, "goldenFingerType", goldenfinger.type)}</Badge>
        </DetailHeader>
        {goldenfinger.description && (
          <DetailSection title={t("materials:detail.abilityDescription")}>
            <MarkdownContent content={goldenfinger.description} />
          </DetailSection>
        )}
        {!!goldenfinger.evolution_history?.length && (
          <DetailSection title={t("materials:detail.evolutionHistory")}>
            <ol className="material-analysis-records" role="list">
              {goldenfinger.evolution_history.map((record, index) => (
                <li key={index}>
                  {record.stage && <h4>{record.stage}</h4>}
                  {(record.chapter != null || record.timestamp) && (
                    <dl className="material-analysis-facts">
                      {record.chapter != null && (
                        <DetailMetadata label={t("materials:detail.chapterLabel")}>
                          {record.chapter}
                        </DetailMetadata>
                      )}
                      {record.timestamp && (
                        <DetailMetadata label={t("materials:detail.timeLabel")}>
                          {record.timestamp}
                        </DetailMetadata>
                      )}
                    </dl>
                  )}
                  {record.description && <MarkdownContent content={record.description} />}
                </li>
              ))}
            </ol>
          </DetailSection>
        )}
      </article>
    );
  }

  if (item.type === "worldview") {
    const worldview = item.data as MaterialWorldView;
    return (
      <article className="material-detail material-analysis" aria-labelledby={titleId}>
        <DetailHeader id={titleId} icon={<Globe className="w-5 h-5" />} title={t("materials:detail.worldview")} />
        {worldview.power_system && (
          <DetailSection title={t("materials:detail.powerSystem")}>
            <MarkdownContent content={worldview.power_system} />
          </DetailSection>
        )}
        {worldview.world_structure && (
          <DetailSection title={t("materials:detail.worldStructure")}>
            <MarkdownContent content={worldview.world_structure} />
          </DetailSection>
        )}
        {!!worldview.key_factions?.length && (
          <DetailSection title={t("materials:detail.keyFactions")}>
            <ul className="material-analysis-records" role="list">
              {worldview.key_factions.map((faction, index) => (
                <li key={index}>
                  <h4>{faction.name}</h4>
                  {faction.description && <MarkdownContent content={faction.description} />}
                  {(faction.leader || faction.territory) && (
                    <dl className="material-analysis-facts">
                      {faction.leader && (
                        <DetailMetadata label={t("materials:detail.leader")}>
                          {faction.leader}
                        </DetailMetadata>
                      )}
                      {faction.territory && (
                        <DetailMetadata label={t("materials:detail.territory")}>
                          {faction.territory}
                        </DetailMetadata>
                      )}
                    </dl>
                  )}
                </li>
              ))}
            </ul>
          </DetailSection>
        )}
        {worldview.special_rules && (
          <DetailSection title={t("materials:detail.specialRules")}>
            <MarkdownContent content={worldview.special_rules} />
          </DetailSection>
        )}
      </article>
    );
  }

  return null;
}
