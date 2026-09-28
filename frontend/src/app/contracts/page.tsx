"use client";

import React from "react";
import { FileSignature, ShieldCheck, Award, CheckCircle2 } from "lucide-react";

export default function ContractsPage() {
  return (
    <div className="space-y-6">
      <div className="pb-4 border-b border-sail-border">
        <div className="flex items-center gap-2 mb-1">
          <span className="text-xs uppercase tracking-widest text-purple-400 font-mono font-semibold">
            Procurement & Fixtures
          </span>
          <span className="text-slate-500">|</span>
          <span className="text-xs text-slate-400 font-mono">
            Charter Fixture Management & Carrier Scorecards
          </span>
        </div>
        <h1 className="text-2xl font-bold tracking-tight text-white flex items-center gap-2">
          <FileSignature className="w-6 h-6 text-purple-400" />
          Charter Fixtures, Carrier Reliability & Contracts
        </h1>
      </div>

      <div className="p-8 rounded-xl bg-sail-card border border-sail-border text-center space-y-4">
        <div className="w-16 h-16 rounded-full bg-purple-900/40 text-purple-400 flex items-center justify-center mx-auto border border-purple-600/30">
          <Award className="w-8 h-8" />
        </div>
        <div className="max-w-md mx-auto space-y-2">
          <h2 className="text-lg font-bold text-white">Carrier Reliability & Fixture Scorecards</h2>
          <p className="text-xs text-slate-400 leading-relaxed">
            Multi-dimensional evaluation tracking carrier on-time arrival rate, speed/fuel consumption warranty compliance, documentation precision, and CP sanction compliance flags.
          </p>
        </div>
        <div className="inline-flex items-center gap-2 px-3 py-1.5 rounded-lg bg-emerald-950/40 border border-emerald-500/30 text-emerald-300 text-xs font-mono">
          <CheckCircle2 className="w-4 h-4 text-emerald-400" />
          <span>Integrated with CarrierScorecardSnapshot & Sanction Screening</span>
        </div>
      </div>
    </div>
  );
}
