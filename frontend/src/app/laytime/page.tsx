"use client";

import React from "react";
import { FileSpreadsheet, DollarSign, Clock, CheckCircle2 } from "lucide-react";

export default function LaytimePage() {
  return (
    <div className="space-y-6">
      <div className="pb-4 border-b border-sail-border">
        <div className="flex items-center gap-2 mb-1">
          <span className="text-xs uppercase tracking-widest text-emerald-400 font-mono font-semibold">
            Financial & Legal Settlement
          </span>
          <span className="text-slate-500">|</span>
          <span className="text-xs text-slate-400 font-mono">
            BIMCO Laytime 2013 Compliance Engine
          </span>
        </div>
        <h1 className="text-2xl font-bold tracking-tight text-white flex items-center gap-2">
          <FileSpreadsheet className="w-6 h-6 text-emerald-400" />
          Laytime Calculation & Demurrage / Dispatch Settlements
        </h1>
      </div>

      <div className="p-8 rounded-xl bg-sail-card border border-sail-border text-center space-y-4">
        <div className="w-16 h-16 rounded-full bg-emerald-900/40 text-emerald-400 flex items-center justify-center mx-auto border border-emerald-600/30">
          <DollarSign className="w-8 h-8" />
        </div>
        <div className="max-w-md mx-auto space-y-2">
          <h2 className="text-lg font-bold text-white">Automated Laytime Settlement Engine</h2>
          <p className="text-xs text-slate-400 leading-relaxed">
            Deterministic SOF (Statement of Facts) parsing, NOR turnaround verification, weather working day (WWD) exclusion, and once-on-demurrage always-on-demurrage calculation.
          </p>
        </div>
        <div className="inline-flex items-center gap-2 px-3 py-1.5 rounded-lg bg-emerald-950/40 border border-emerald-500/30 text-emerald-300 text-xs font-mono">
          <CheckCircle2 className="w-4 h-4 text-emerald-400" />
          <span>Python Decimal ROUND_HALF_UP verified against charter party terms</span>
        </div>
      </div>
    </div>
  );
}
