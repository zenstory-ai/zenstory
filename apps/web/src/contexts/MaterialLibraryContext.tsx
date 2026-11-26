/* eslint-disable react-refresh/only-export-components */
import React, { createContext, useContext } from 'react';
import { useMaterialLibrary, type MaterialLibraryState } from '../hooks/useMaterialLibrary';

const MaterialLibraryContext = createContext<MaterialLibraryState | null>(null);

export interface MaterialLibraryProviderProps {
  children: React.ReactNode;
  /**
   * Whether to trigger material library entitlement & summary queries on mount.
   * Defaults to `false` so mounting at the top of ProtectedProviders incurs
   * zero API calls until a Materials pane/consumer calls `activate()`.
   */
  initialEnabled?: boolean;
}

export const MaterialLibraryProvider: React.FC<MaterialLibraryProviderProps> = ({
  children,
  initialEnabled = false,
}) => {
  const state = useMaterialLibrary({ enabled: initialEnabled });
  return (
    <MaterialLibraryContext.Provider value={state}>
      {children}
    </MaterialLibraryContext.Provider>
  );
};

export function useMaterialLibraryContext(): MaterialLibraryState {
  const ctx = useContext(MaterialLibraryContext);
  if (!ctx) {
    throw new Error('useMaterialLibraryContext must be used within MaterialLibraryProvider');
  }
  return ctx;
}
