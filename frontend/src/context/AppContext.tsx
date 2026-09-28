"use client";

import React, { createContext, useContext, useState, useEffect, ReactNode, useCallback } from "react";
import { UserRole, PlantCode } from "@/types/dashboard";
import { logisticsApi } from "@/lib/api-client";

interface AppContextType {
  role: UserRole;
  setRole: (role: UserRole) => void;
  selectedPlant: PlantCode;
  setSelectedPlant: (plant: PlantCode) => void;
  isBackendConnected: boolean | null;
  refreshTrigger: number;
  triggerRefresh: () => void;
}

const AppContext = createContext<AppContextType | undefined>(undefined);

export const AppProvider = ({ children }: { children: ReactNode }) => {
  const [role, setRole] = useState<UserRole>("LOGISTICS_DIRECTOR");
  const [selectedPlant, setSelectedPlant] = useState<PlantCode>("ALL");
  const [isBackendConnected, setIsBackendConnected] = useState<boolean | null>(null);
  const [refreshTrigger, setRefreshTrigger] = useState<number>(0);

  const triggerRefresh = useCallback(() => {
    setRefreshTrigger((prev) => prev + 1);
  }, []);

  useEffect(() => {
    let isMounted = true;
    const checkStatus = async () => {
      const connected = await logisticsApi.checkHealth();
      if (isMounted) {
        setIsBackendConnected(connected);
      }
    };
    checkStatus();
    const interval = setInterval(checkStatus, 30000);
    return () => {
      isMounted = false;
      clearInterval(interval);
    };
  }, [refreshTrigger]);

  return (
    <AppContext.Provider
      value={{
        role,
        setRole,
        selectedPlant,
        setSelectedPlant,
        isBackendConnected,
        refreshTrigger,
        triggerRefresh,
      }}
    >
      {children}
    </AppContext.Provider>
  );
};

export const useApp = (): AppContextType => {
  const context = useContext(AppContext);
  if (!context) {
    throw new Error("useApp must be used within an AppProvider");
  }
  return context;
};
