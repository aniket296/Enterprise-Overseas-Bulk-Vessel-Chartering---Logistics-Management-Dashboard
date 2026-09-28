"use client";

import React from "react";
import { Ship, Anchor, Compass, CheckCircle2 } from "lucide-react";

export default function FleetPage() {
  return (
    <div className="space-y-6">
      <div className="pb-4 border-b border-sail-border">
        <div className="flex items-center gap-2 mb-1">
          <span className="text-xs uppercase tracking-widest text-sail-cyan font-mono font-semibold">
            Fleet Logistics
          </span>
          <span className="text-slate-500">|</span>
          <span className="text-xs text-slate-400 font-mono">
            AIS Satellite Tracking & Voyage Telemetry
          </span>
        </div>
        <h1 className="text-2xl font-bold tracking-tight text-white flex items-center gap-2">
          <Ship className="w-6 h-6 text-sail-cyan" />
          Active Bulk Carrier Fleet & Voyage Operations
        </h1>
      </div>

      <div className="p-8 rounded-xl bg-sail-card border border-sail-border text-center space-y-4">
        <div className="w-16 h-16 rounded-full bg-blue-900/40 text-sail-cyan flex items-center justify-center mx-auto border border-blue-600/30">
          <Compass className="w-8 h-8 animate-pulse" />
        </div>
        <div className="max-w-md mx-auto space-y-2">
          <h2 className="text-lg font-bold text-white">Live Voyage & Telemetry Tracking</h2>
          <p className="text-xs text-slate-400 leading-relaxed">
            Real-time geospatial tracking across Capesize, Panamax, and Supramax vessels carrying coking coal and limestone from Australia, Indonesia, and South Africa to Indian discharge ports (Haldia, Paradip, Vizag).
          </p>
        </div>
        <div className="inline-flex items-center gap-2 px-3 py-1.5 rounded-lg bg-emerald-950/40 border border-emerald-500/30 text-emerald-300 text-xs font-mono">
          <CheckCircle2 className="w-4 h-4 text-emerald-400" />
          <span>Integrated with Celery AIS polling worker & DRF Voyage Model</span>
        </div>
      </div>
    </div>
  );
}
