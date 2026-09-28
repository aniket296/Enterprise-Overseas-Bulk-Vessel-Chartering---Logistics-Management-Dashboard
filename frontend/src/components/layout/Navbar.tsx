"use client";

import React from "react";
import { useApp } from "@/context/AppContext";
import { UserRole, PlantCode } from "@/types/dashboard";
import {
  Anchor,
  Building2,
  Shield,
  RefreshCw,
  Activity,
  AlertTriangle,
} from "lucide-react";

const PLANT_LABELS: Record<PlantCode, string> = {
  ALL: "All SAIL Steel Plants",
  BSP: "BSP - Bhilai Steel Plant",
  DSP: "DSP - Durgapur Steel Plant",
  RSP: "RSP - Rourkela Steel Plant",
  BSL: "BSL - Bokaro Steel Plant",
  ISP: "ISP - IISCO Burnpur",
};

const ROLE_LABELS: Record<UserRole, string> = {
  LOGISTICS_DIRECTOR: "Logistics Director",
  EXECUTIVE_DIRECTOR: "Executive Director",
  CHARTERING_MANAGER: "Chartering Manager",
  PORT_OPERATOR: "Port Logistics Operator",
  FINANCE_CONTROLLER: "Finance Controller",
};

export const Navbar: React.FC = () => {
  const {
    role,
    setRole,
    selectedPlant,
    setSelectedPlant,
    isBackendConnected,
    triggerRefresh,
  } = useApp();

  return (
    <header className="h-16 border-b border-sail-border bg-sail-dark px-6 flex items-center justify-between sticky top-0 z-40">
      {/* Brand & Emblem */}
      <div className="flex items-center gap-3">
        <div className="w-10 h-10 rounded-lg bg-gradient-to-br from-sail-accent to-blue-800 flex items-center justify-center shadow-lg border border-blue-400/30">
          <Anchor className="w-6 h-6 text-white" />
        </div>
        <div>
          <div className="flex items-center gap-2">
            <span className="font-extrabold text-sm tracking-widest text-white uppercase">
              SAIL
            </span>
            <span className="text-xs px-2 py-0.5 rounded bg-blue-900/60 text-sail-cyan border border-blue-600/40 font-mono">
              MINISTRY OF STEEL
            </span>
          </div>
          <p className="text-xs text-slate-400 hidden sm:block">
            Overseas Bulk Vessel Chartering & Raw Material Logistics
          </p>
        </div>
      </div>

      {/* Control Bar: Plant Selector + Role Switcher + Health Badge */}
      <div className="flex items-center gap-3">
        {/* Plant Filter */}
        <div className="flex items-center gap-1.5 bg-sail-card px-3 py-1.5 rounded-lg border border-sail-border">
          <Building2 className="w-4 h-4 text-sail-cyan" />
          <select
            value={selectedPlant}
            onChange={(e) => setSelectedPlant(e.target.value as PlantCode)}
            className="bg-transparent text-xs text-slate-200 focus:outline-none cursor-pointer pr-1"
          >
            {Object.entries(PLANT_LABELS).map(([code, label]) => (
              <option key={code} value={code} className="bg-sail-dark text-slate-200">
                {label}
              </option>
            ))}
          </select>
        </div>

        {/* Role Switcher */}
        <div className="flex items-center gap-1.5 bg-sail-card px-3 py-1.5 rounded-lg border border-sail-border">
          <Shield className="w-4 h-4 text-sail-gold" />
          <select
            value={role}
            onChange={(e) => setRole(e.target.value as UserRole)}
            className="bg-transparent text-xs text-slate-200 focus:outline-none cursor-pointer pr-1"
          >
            {Object.entries(ROLE_LABELS).map(([code, label]) => (
              <option key={code} value={code} className="bg-sail-dark text-slate-200">
                {label}
              </option>
            ))}
          </select>
        </div>

        {/* API Health Status */}
        <div className="hidden md:flex items-center gap-2 px-2.5 py-1.5 rounded-lg bg-sail-card border border-sail-border text-xs">
          {isBackendConnected === true ? (
            <>
              <span className="w-2 h-2 rounded-full bg-emerald-400 animate-pulse" />
              <span className="text-emerald-400 font-mono">API LIVE</span>
            </>
          ) : isBackendConnected === false ? (
            <>
              <AlertTriangle className="w-3.5 h-3.5 text-rose-400" />
              <span className="text-rose-400 font-mono">STANDALONE</span>
            </>
          ) : (
            <>
              <Activity className="w-3.5 h-3.5 text-slate-400 animate-spin" />
              <span className="text-slate-400 font-mono">CONNECTING</span>
            </>
          )}
        </div>

        {/* Refresh Action */}
        <button
          onClick={triggerRefresh}
          title="Refresh Data from Backend"
          className="p-2 rounded-lg bg-sail-card border border-sail-border text-slate-300 hover:text-white hover:border-sail-accent transition-colors"
        >
          <RefreshCw className="w-4 h-4" />
        </button>
      </div>
    </header>
  );
};
