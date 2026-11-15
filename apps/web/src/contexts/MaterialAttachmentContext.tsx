import React, { createContext, useContext, useState, useCallback, useEffect, useRef } from 'react';
import type { ReactNode } from 'react';

/**
 * Represents an attached material item
 */
export interface AttachedMaterial {
  id: string;
  title: string;
  /** If this is a library material, store the source info */
  librarySource?: {
    novelId: number;
    entityType: string;
    entityId: number;
  };
}

/**
 * Maximum number of materials that can be attached at once
 */
export const MAX_ATTACHED_MATERIALS = 5;

export interface MaterialAttachmentContextType {
  /** List of attached material items */
  attachedMaterials: AttachedMaterial[];
  /** List of attached material IDs (for sending to backend) */
  attachedIds: string[];
  /** Project file IDs only (for backward compatibility) */
  attachedFileIds: string[];
  /** Library material references */
  attachedLibraryMaterials: Array<{
    novel_id: number;
    entity_type: string;
    entity_id: number;
  }>;
  /** Add a material to the attachment list */
  addMaterial: (id: string, title: string, librarySource?: AttachedMaterial['librarySource']) => boolean;
  /** Remove a material from the attachment list */
  removeMaterial: (id: string) => void;
  /** Check if a material is attached */
  isMaterialAttached: (id: string) => boolean;
  /** Clear all attached materials */
  clearMaterials: () => void;
  /** Whether the maximum limit is reached */
  isAtLimit: boolean;
  /** Maximum number of materials that can be attached at once */
  maxMaterials?: number;
}

const MaterialAttachmentContext = createContext<MaterialAttachmentContextType | undefined>(undefined);

const isSameLibrarySource = (
  a?: AttachedMaterial['librarySource'],
  b?: AttachedMaterial['librarySource'],
): boolean => {
  if (!a || !b) return false;
  return a.novelId === b.novelId
    && a.entityType === b.entityType
    && a.entityId === b.entityId;
};

export const MaterialAttachmentProvider: React.FC<{ children: ReactNode }> = ({ children }) => {
  const [attachedMaterials, setAttachedMaterials] = useState<AttachedMaterial[]>([]);
  const attachedMaterialsRef = useRef<AttachedMaterial[]>(attachedMaterials);

  useEffect(() => {
    attachedMaterialsRef.current = attachedMaterials;
  }, [attachedMaterials]);

  // Get list of IDs
  const attachedIds = attachedMaterials.map(m => m.id);

  // Project file IDs only (for backward compatibility)
  const attachedFileIds = attachedMaterials.filter(m => !m.librarySource).map(m => m.id);

  // Library material references
  const attachedLibraryMaterials = attachedMaterials
    .filter(m => m.librarySource)
    .map(m => ({
      novel_id: m.librarySource!.novelId,
      entity_type: m.librarySource!.entityType,
      entity_id: m.librarySource!.entityId,
    }));

  // Check if at limit
  const isAtLimit = attachedMaterials.length >= MAX_ATTACHED_MATERIALS;

  // Add a material (uses functional update to avoid stale closure)
  const addMaterial = useCallback((id: string, title: string, librarySource?: AttachedMaterial['librarySource']): boolean => {
    const current = attachedMaterialsRef.current;
    if (current.some(m => m.id === id)) return false;
    if (librarySource && current.some(m => isSameLibrarySource(m.librarySource, librarySource))) return false;
    if (current.length >= MAX_ATTACHED_MATERIALS) return false;

    const next = [...current, { id, title, librarySource }];
    attachedMaterialsRef.current = next;
    setAttachedMaterials(next);
    return true;
  }, []);

  // Remove a material
  const removeMaterial = useCallback((id: string) => {
    setAttachedMaterials(prev => {
      const next = prev.filter(m => m.id !== id);
      attachedMaterialsRef.current = next;
      return next;
    });
  }, []);

  // Check if material is attached
  const isMaterialAttached = useCallback((id: string): boolean => {
    return attachedMaterials.some(m => m.id === id);
  }, [attachedMaterials]);

  // Clear all materials
  const clearMaterials = useCallback(() => {
    attachedMaterialsRef.current = [];
    setAttachedMaterials([]);
  }, []);

  const value: MaterialAttachmentContextType = {
    attachedMaterials,
    attachedIds,
    attachedFileIds,
    attachedLibraryMaterials,
    addMaterial,
    removeMaterial,
    isMaterialAttached,
    clearMaterials,
    isAtLimit,
    maxMaterials: MAX_ATTACHED_MATERIALS,
  };

  return (
    <MaterialAttachmentContext.Provider value={value}>
      {children}
    </MaterialAttachmentContext.Provider>
  );
};

// eslint-disable-next-line react-refresh/only-export-components
export const useMaterialAttachment = (): MaterialAttachmentContextType => {
  const context = useContext(MaterialAttachmentContext);
  if (context === undefined) {
    throw new Error('useMaterialAttachment must be used within a MaterialAttachmentProvider');
  }
  return context;
};
