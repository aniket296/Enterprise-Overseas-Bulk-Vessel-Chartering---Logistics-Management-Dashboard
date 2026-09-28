"use client";

import React, { useEffect, useState, useCallback, useMemo } from "react";
import Link from "next/link";
import { useApp } from "@/context/AppContext";
import { logisticsApi } from "@/lib/api-client";
import {
  PlantStarvationProfile,
  StarvationMitigationSimulation,
  DiversionOption,
  EmergencyRecharterOption,
  EmergencyInterPlantTransferOption,
} from "@/types/dashboard";
import {
  AlertOctagon,
  RefreshCw,
  Ship,
  Train,
  Anchor,
  Flame,
  AlertTriangle,
  ArrowRight,
  TrendingDown,
  ShieldCheck,
  CheckCircle2,
  Clock,
  DollarSign,
  Scale,
  Award,
  Sparkles,
  Zap,
} from "lucide-react";

const MATERIAL_CATEGORIES = [
  { value: "COKING_COAL", label: "Coking Coal (Imported)" },
  { value: "IRON_ORE", label: "Iron Ore / Pellets" },
  { value: "MET_COKE", label: "Metallurgical Coke" },
  { value: "LIMESTONE", label: "Limestone / Flux" },
];

export default function StarvationWarningPage() {
  const { role, refreshTrigger } = useApp();

  const [material, setMaterial] = useState<string>("COKING_COAL");
  const [matrix, setMatrix] = useState<PlantStarvationProfile[]>([]);
  const [selectedPlantCode, setSelectedPlantCode] = useState<string>("BSP");
  const [simulation, setSimulation] = useState<StarvationMitigationSimulation | null>(null);

  const [loading, setLoading] = useState<boolean>(true);
  const [simulating, setSimulating] = useState<boolean>(false);
  const [error, setError] = useState<string | null>(null);

  // 1. Fetch multi-plant starvation matrix
  const fetchMatrix = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const data = await logisticsApi.getStarvationMatrix(role, material);
      setMatrix(data);
      if (data.length > 0 && !selectedPlantCode) {
        setSelectedPlantCode(data[0].plant_code);
      }
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : "Failed to load starvation matrix";
      setError(msg);
    } finally {
      setLoading(false);
    }
  }, [role, material, selectedPlantCode]);

  useEffect(() => {
    fetchMatrix();
  }, [fetchMatrix, refreshTrigger]);

  // 2. Run emergency mitigation simulation for selected plant
  const runSimulation = useCallback(
    async (plantCode: string) => {
      setSelectedPlantCode(plantCode);
      setSimulating(true);
      try {
        const sim = await logisticsApi.simulateMitigation(plantCode, material, role);
        setSimulation(sim);
      } catch (err: unknown) {
        console.error(err);
      } finally {
        setSimulating(false);
      }
    },
    [material, role]
  );

  useEffect(() => {
    if (selectedPlantCode) {
      runSimulation(selectedPlantCode);
    }
  }, [selectedPlantCode, runSimulation]);

  const formatNumber = (val: string | number | null | undefined, decimals = 0) => {
    if (val === undefined || val === null) return "0";
    const num = typeof val === "string" ? parseFloat(val) : val;
    return isNaN(num)
      ? "0"
      : num.toLocaleString("en-US", { minimumFractionDigits: decimals, maximumFractionDigits: decimals });
  };

  const getSeverityBadge = (severity: string) => {
    switch (severity) {
      case "CRITICAL_URGENT":
        return <span className="px-2 py-0.5 rounded text-[11px] font-bold bg-rose-500/20 text-rose-300 border border-rose-500/40 animate-pulse">CRITICAL URGENT (&lt;5d)</span>;
      case "CRITICAL":
        return <span className="px-2 py-0.5 rounded text-[11px] font-bold bg-orange-500/20 text-orange-300 border border-orange-500/30">CRITICAL (5-7d)</span>;
      case "PRECAUTIONARY":
        return <span className="px-2 py-0.5 rounded text-[11px] font-bold bg-amber-500/20 text-amber-300 border border-amber-500/30">PRECAUTIONARY (7-14d)</span>;
      case "WATCH":
        return <span className="px-2 py-0.5 rounded text-[11px] font-bold bg-blue-500/20 text-blue-300 border border-blue-500/30">WATCH (14-21d)</span>;
      case "ADEQUATE":
        return <span className="px-2 py-0.5 rounded text-[11px] font-bold bg-emerald-500/20 text-emerald-300 border border-emerald-500/30">ADEQUATE (21-45d)</span>;
      case "SURPLUS":
      default:
        return <span className="px-2 py-0.5 rounded text-[11px] font-bold bg-teal-500/20 text-teal-300 border border-teal-500/30">SURPLUS (&gt;45d)</span>;
    }
  };

  // High-level summary metrics across the 5 plants
  const criticalCount = useMemo(
    () => matrix.filter((p) => p.risk_severity === "CRITICAL_URGENT" || p.risk_severity === "CRITICAL").length,
    [matrix]
  );
  const precautionaryCount = useMemo(
    () => matrix.filter((p) => p.risk_severity === "PRECAUTIONARY" || p.risk_severity === "WATCH").length,
    [matrix]
  );
  const safeCount = useMemo(
    () => matrix.filter((p) => p.risk_severity === "ADEQUATE" || p.risk_severity === "SURPLUS").length,
    [matrix]
  );
  const totalDeficitMt = useMemo(
    () => matrix.reduce((acc, p) => acc + parseFloat(p.deficit_to_safe_stock_mt || "0"), 0),
    [matrix]
  );

  return (
    <div className="space-y-6">
      {/* Page Header */}
      <div className="flex flex-col md:flex-row md:items-center justify-between gap-4 pb-4 border-b border-sail-border">
        <div>
          <div className="flex items-center gap-2 mb-1">
            <span className="text-xs uppercase tracking-widest text-rose-400 font-mono font-semibold">
              Emergency Defense Module
            </span>
            <span className="text-slate-500">|</span>
            <span className="text-xs text-slate-400 font-mono">
              SAIL 5-Plant Blast Furnace Security & Early Warning
            </span>
          </div>
          <h1 className="text-2xl font-bold tracking-tight text-white flex items-center gap-2">
            <AlertOctagon className="w-6 h-6 text-rose-500" />
            Multi-Plant Stock Starvation Early Warning & Mitigation Solver
          </h1>
        </div>

        {/* Material Selector & Refresh */}
        <div className="flex items-center gap-2.5">
          <div className="flex items-center gap-1.5 bg-sail-card px-3 py-1.5 rounded-lg border border-sail-border">
            <Flame className="w-3.5 h-3.5 text-sail-gold" />
            <select
              value={material}
              onChange={(e) => setMaterial(e.target.value)}
              className="bg-transparent text-xs text-slate-200 focus:outline-none cursor-pointer font-mono"
            >
              {MATERIAL_CATEGORIES.map((m) => (
                <option key={m.value} value={m.value} className="bg-sail-dark text-slate-200">
                  {m.label}
                </option>
              ))}
            </select>
          </div>

          <button
            onClick={fetchMatrix}
            title="Scan 5 plants"
            className="p-2 rounded-lg bg-sail-card border border-sail-border text-slate-300 hover:text-white hover:border-rose-400 transition-colors"
          >
            <RefreshCw className="w-4 h-4" />
          </button>
        </div>
      </div>

      {loading && matrix.length === 0 && (
        <div className="py-20 flex flex-col items-center justify-center space-y-4">
          <RefreshCw className="w-8 h-8 text-rose-400 animate-spin" />
          <p className="text-sm text-slate-400 font-mono">
            Scanning on-ground inventory and incoming vessel pipeline across 5 SAIL plants...
          </p>
        </div>
      )}

      {error && (
        <div className="p-4 rounded-xl bg-rose-950/40 border border-rose-500/40 text-rose-200 text-xs font-mono">
          {error}
        </div>
      )}

      {matrix.length > 0 && (
        <>
          {/* Executive Network Threat Scorecards */}
          <div className="grid grid-cols-1 md:grid-cols-4 gap-4">
            <div className="p-4 rounded-xl bg-sail-card border border-sail-border space-y-1">
              <span className="text-xs text-slate-400 font-mono">Critical Plants (&lt;7d)</span>
              <p className="text-2xl font-black text-rose-400 font-mono">{criticalCount} / 5</p>
              <p className="text-[11px] text-slate-500 font-mono">Immediate risk of blast furnace trip</p>
            </div>
            <div className="p-4 rounded-xl bg-sail-card border border-sail-border space-y-1">
              <span className="text-xs text-slate-400 font-mono">Precautionary Watch</span>
              <p className="text-2xl font-black text-amber-300 font-mono">{precautionaryCount} / 5</p>
              <p className="text-[11px] text-slate-500 font-mono">Stock runway between 7 and 21 days</p>
            </div>
            <div className="p-4 rounded-xl bg-sail-card border border-sail-border space-y-1">
              <span className="text-xs text-slate-400 font-mono">Adequate / Surplus</span>
              <p className="text-2xl font-black text-emerald-400 font-mono">{safeCount} / 5</p>
              <p className="text-[11px] text-slate-500 font-mono">Exceeds 21-day strategic threshold</p>
            </div>
            <div className="p-4 rounded-xl bg-sail-card border border-sail-border space-y-1">
              <span className="text-xs text-slate-400 font-mono">Total Network Deficit</span>
              <p className="text-2xl font-black text-white font-mono">{formatNumber(totalDeficitMt)} MT</p>
              <p className="text-[11px] text-slate-500 font-mono">Volume required to restore safe buffer</p>
            </div>
          </div>

          {/* Multi-Plant Starvation Assessment Matrix Table */}
          <div className="rounded-xl bg-sail-card border border-sail-border overflow-hidden">
            <div className="p-4 border-b border-sail-border flex items-center justify-between">
              <div>
                <h2 className="text-sm font-bold text-white uppercase tracking-wider flex items-center gap-2">
                  <Flame className="w-4 h-4 text-sail-gold" />
                  SAIL 5-Plant Inventory Runway & Starvation Assessment Matrix
                </h2>
                <p className="text-xs text-slate-400 font-mono">
                  Target Buffer: 21 Days Safe Stock • Material: {material}
                </p>
              </div>
              <span className="text-xs text-slate-400 font-mono">
                Click &quot;Solve&quot; to compute mitigation plan
              </span>
            </div>

            <div className="overflow-x-auto">
              <table className="w-full text-left text-xs font-mono">
                <thead className="bg-sail-dark text-slate-400 border-b border-sail-border uppercase">
                  <tr>
                    <th className="py-3 px-4">Plant Code</th>
                    <th className="py-3 px-4 text-right">Physical Stock (MT)</th>
                    <th className="py-3 px-4 text-right">Daily Burn</th>
                    <th className="py-3 px-4 text-right">Physical Runway</th>
                    <th className="py-3 px-4 text-right">In-Transit Ships</th>
                    <th className="py-3 px-4 text-right">Extended Runway</th>
                    <th className="py-3 px-4 text-right">Deficit to 21d</th>
                    <th className="py-3 px-4 text-center">Risk Severity</th>
                    <th className="py-3 px-4 text-center">Mitigation Action</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-sail-border">
                  {matrix.map((p) => {
                    const isSelected = selectedPlantCode === p.plant_code;
                    return (
                      <tr
                        key={p.plant_code}
                        className={`hover:bg-sail-dark/60 transition-colors cursor-pointer ${
                          isSelected ? "bg-sail-dark/90 border-l-2 border-rose-500" : ""
                        }`}
                        onClick={() => runSimulation(p.plant_code)}
                      >
                        <td className="py-3 px-4 font-bold text-white flex items-center gap-2">
                          <Flame className="w-4 h-4 text-sail-gold" />
                          <span>{p.plant_code}</span>
                          <span className="text-[11px] text-slate-400 font-normal">({p.plant_name.split(" ")[0]})</span>
                        </td>
                        <td className="py-3 px-4 text-right text-slate-200">
                          {formatNumber(p.current_stock_mt)}
                        </td>
                        <td className="py-3 px-4 text-right text-slate-400">
                          {formatNumber(p.daily_burn_rate_mt)} MT/d
                        </td>
                        <td className="py-3 px-4 text-right font-bold text-white">
                          {p.physical_runway_days} d
                        </td>
                        <td className="py-3 px-4 text-right text-sail-cyan">
                          {p.incoming_vessels_count} ({formatNumber(p.incoming_tonnage_mt)} MT)
                        </td>
                        <td className="py-3 px-4 text-right font-bold text-emerald-400">
                          {p.extended_runway_days} d
                        </td>
                        <td className="py-3 px-4 text-right font-bold text-rose-300">
                          {parseFloat(p.deficit_to_safe_stock_mt) > 0
                            ? `-${formatNumber(p.deficit_to_safe_stock_mt)} MT`
                            : "0 MT"}
                        </td>
                        <td className="py-3 px-4 text-center">
                          {getSeverityBadge(p.risk_severity)}
                        </td>
                        <td className="py-3 px-4 text-center">
                          <button
                            onClick={(e) => {
                              e.stopPropagation();
                              runSimulation(p.plant_code);
                            }}
                            className="px-2.5 py-1 rounded bg-rose-950/60 hover:bg-rose-900 text-rose-300 border border-rose-600/40 text-[11px] font-bold transition-all"
                          >
                            Solve {p.plant_code}
                          </button>
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          </div>

          {/* Consolidated Emergency Response & Mitigation Solver Panel */}
          {simulation && (
            <div className="space-y-6">
              {/* Primary Strategy Directive Banner */}
              <div className="p-5 rounded-xl bg-sail-card border border-sail-border space-y-3">
                <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-2 pb-2 border-b border-sail-border">
                  <div className="flex items-center gap-2">
                    <Zap className="w-5 h-5 text-sail-gold" />
                    <h3 className="text-base font-bold text-white font-mono">
                      Emergency Defense Directive: {simulation.target_plant_name} ({simulation.target_plant_code})
                    </h3>
                  </div>
                  <div className="flex items-center gap-2 font-mono text-xs">
                    <span className="text-slate-400">Recommended Strategy:</span>
                    <span className="px-2.5 py-1 rounded bg-gradient-to-r from-blue-900 to-purple-900 text-white font-bold border border-blue-500/40">
                      {simulation.primary_strategy.replace(/_/g, " ")}
                    </span>
                  </div>
                </div>

                <div className="p-3.5 rounded-lg bg-sail-dark border border-sail-border flex items-start gap-3">
                  <Sparkles className="w-4 h-4 text-sail-cyan shrink-0 mt-0.5" />
                  <p className="text-xs text-slate-300 leading-relaxed font-mono">
                    {simulation.summary_recommendation}
                  </p>
                </div>
              </div>

              {/* 3 Mitigation Pillars: Coastal Vessel Diversion vs BOXN Rail Transfer vs Spot Fixture */}
              <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
                {/* Pillar 1: Coastal In-Transit Vessel Diversion */}
                <div className="rounded-xl bg-sail-card border border-sail-border p-5 space-y-4 flex flex-col justify-between">
                  <div className="space-y-3">
                    <div className="flex items-center justify-between pb-2 border-b border-sail-border">
                      <div className="flex items-center gap-2">
                        <Ship className="w-4 h-4 text-sail-cyan" />
                        <h4 className="text-xs font-bold text-white uppercase tracking-wider">
                          Vessel Diversion
                        </h4>
                      </div>
                      <span className="text-[10px] text-sail-cyan font-mono font-bold px-2 py-0.5 rounded bg-blue-950/60 border border-blue-600/30">
                        {simulation.diversion_options.length} Options
                      </span>
                    </div>

                    <p className="text-xs text-slate-400 leading-relaxed font-mono">
                      Diverts active bulk vessels bound for surplus plants while guaranteeing donor safety (≥14d).
                    </p>

                    {simulation.diversion_options.length === 0 ? (
                      <div className="p-4 rounded-lg bg-sail-dark border border-sail-border text-center text-slate-500 font-mono text-xs">
                        No active in-transit vessels qualify under donor safety constraints.
                      </div>
                    ) : (
                      <div className="space-y-3 font-mono text-xs">
                        {simulation.diversion_options.map((opt) => (
                          <div
                            key={opt.voyage_id}
                            className="p-3 rounded-lg bg-sail-dark border border-sail-border space-y-2"
                          >
                            <div className="flex items-center justify-between">
                              <span className="text-white font-bold">{opt.vessel_name}</span>
                              <span className="text-emerald-400 font-bold">Score: {opt.feasibility_score}/100</span>
                            </div>
                            <div className="space-y-1 text-[11px] text-slate-300">
                              <div className="flex items-center justify-between">
                                <span className="text-slate-400">Cargo Volume:</span>
                                <span>{formatNumber(opt.quantity_mt)} MT</span>
                              </div>
                              <div className="flex items-center justify-between">
                                <span className="text-slate-400">Current Destination:</span>
                                <span className="text-slate-200">{opt.current_destination_plant} ({opt.current_discharge_port})</span>
                              </div>
                              <div className="flex items-center justify-between">
                                <span className="text-slate-400">Donor Post Runway:</span>
                                <span className="text-emerald-400 font-bold">{opt.donor_post_diversion_runway_days}d (Safe)</span>
                              </div>
                              <div className="flex items-center justify-between">
                                <span className="text-slate-400">Runway Restored:</span>
                                <span className="text-sail-cyan font-bold">+{opt.runway_days_gained} days</span>
                              </div>
                              <div className="flex items-center justify-between pt-1 border-t border-sail-border/60">
                                <span className="text-slate-400">Diversion Surcharge:</span>
                                <span className="text-rose-400 font-bold">${formatNumber(opt.estimated_diversion_cost_usd)}</span>
                              </div>
                            </div>
                          </div>
                        ))}
                      </div>
                    )}
                  </div>

                  <div className="pt-2 border-t border-sail-border text-[11px] text-slate-500 font-mono">
                    Coastal steaming calculated via port distance matrix.
                  </div>
                </div>

                {/* Pillar 2: Indian Railways BOXN Inter-Plant Rake Transfer */}
                <div className="rounded-xl bg-sail-card border border-sail-border p-5 space-y-4 flex flex-col justify-between">
                  <div className="space-y-3">
                    <div className="flex items-center justify-between pb-2 border-b border-sail-border">
                      <div className="flex items-center gap-2">
                        <Train className="w-4 h-4 text-sail-gold" />
                        <h4 className="text-xs font-bold text-white uppercase tracking-wider">
                          BOXN Rail Transfer
                        </h4>
                      </div>
                      <span className="text-[10px] text-sail-gold font-mono font-bold px-2 py-0.5 rounded bg-amber-950/60 border border-amber-600/30">
                        {simulation.rail_transfer_options.length} Donors
                      </span>
                    </div>

                    <p className="text-xs text-slate-400 leading-relaxed font-mono">
                      Transfers on-ground raw materials by Indian Railways BOXN rakes (3,800 MT/rake @ $12/MT).
                    </p>

                    {simulation.rail_transfer_options.length === 0 ? (
                      <div className="p-4 rounded-lg bg-sail-dark border border-sail-border text-center text-slate-500 font-mono text-xs">
                        No adjacent SAIL plants possess sufficient surplus stock (&gt;21d) to donor.
                      </div>
                    ) : (
                      <div className="space-y-3 font-mono text-xs">
                        {simulation.rail_transfer_options.map((opt) => (
                          <div
                            key={opt.donor_plant_code}
                            className="p-3 rounded-lg bg-sail-dark border border-sail-border space-y-2"
                          >
                            <div className="flex items-center justify-between">
                              <span className="text-white font-bold">{opt.donor_plant_name}</span>
                              <span className="text-emerald-400 font-bold">{opt.is_transfer_viable ? "Viable" : "Infeasible"}</span>
                            </div>
                            <div className="space-y-1 text-[11px] text-slate-300">
                              <div className="flex items-center justify-between">
                                <span className="text-slate-400">Transfer Tonnage:</span>
                                <span>{formatNumber(opt.total_transfer_tonnage_mt)} MT</span>
                              </div>
                              <div className="flex items-center justify-between">
                                <span className="text-slate-400">Rakes Required:</span>
                                <span className="text-sail-cyan font-bold">{opt.rakes_required} BOXN Rakes</span>
                              </div>
                              <div className="flex items-center justify-between">
                                <span className="text-slate-400">Transit Duration:</span>
                                <span className="text-slate-200">~{opt.estimated_transit_hours} hours</span>
                              </div>
                              <div className="flex items-center justify-between">
                                <span className="text-slate-400">Runway Restored:</span>
                                <span className="text-emerald-400 font-bold">+{opt.recipient_runway_days_restored} days</span>
                              </div>
                              <div className="flex items-center justify-between pt-1 border-t border-sail-border/60">
                                <span className="text-slate-400">Total Rail Freight:</span>
                                <span className="text-rose-400 font-bold">${formatNumber(opt.estimated_rail_freight_usd)}</span>
                              </div>
                            </div>
                          </div>
                        ))}
                      </div>
                    )}
                  </div>

                  <div className="pt-2 border-t border-sail-border text-[11px] text-slate-500 font-mono">
                    BOXN standard payload: 3,800 MT • Integrated railway tracks.
                  </div>
                </div>

                {/* Pillar 3: Emergency Spot Re-chartering */}
                <div className="rounded-xl bg-sail-card border border-sail-border p-5 space-y-4 flex flex-col justify-between">
                  <div className="space-y-3">
                    <div className="flex items-center justify-between pb-2 border-b border-sail-border">
                      <div className="flex items-center gap-2">
                        <Anchor className="w-4 h-4 text-purple-400" />
                        <h4 className="text-xs font-bold text-white uppercase tracking-wider">
                          Emergency Spot Fixture
                        </h4>
                      </div>
                      <span className="text-[10px] text-purple-400 font-mono font-bold px-2 py-0.5 rounded bg-purple-950/60 border border-purple-600/30">
                        Prompt Charter
                      </span>
                    </div>

                    <p className="text-xs text-slate-400 leading-relaxed font-mono">
                      Fixes emergency prompt spot cargo parcel to permanently bridge deep supply deficits.
                    </p>

                    {simulation.spot_recharter_option ? (
                      <div className="p-3.5 rounded-lg bg-sail-dark border border-sail-border space-y-2 text-xs font-mono">
                        <div className="flex items-center justify-between">
                          <span className="text-white font-bold">
                            {simulation.spot_recharter_option.recommended_vessel_class} Parcel
                          </span>
                          <span className="text-emerald-400 font-bold">
                            +{simulation.spot_recharter_option.runway_days_restored} days
                          </span>
                        </div>
                        <div className="space-y-1 text-[11px] text-slate-300">
                          <div className="flex items-center justify-between">
                            <span className="text-slate-400">Parcel Size:</span>
                            <span className="text-white font-bold">{formatNumber(simulation.spot_recharter_option.parcel_size_mt)} MT</span>
                          </div>
                          <div className="flex items-center justify-between">
                            <span className="text-slate-400">Spot Rate:</span>
                            <span className="text-purple-300">${simulation.spot_recharter_option.estimated_spot_freight_usd_per_mt}/MT</span>
                          </div>
                          <div className="flex items-center justify-between">
                            <span className="text-slate-400">Laycan Window:</span>
                            <span className="text-amber-400 font-bold">Prompt {simulation.spot_recharter_option.laycan_urgency_days} days</span>
                          </div>
                          <div className="flex items-center justify-between">
                            <span className="text-slate-400">Target Safe Runway:</span>
                            <span className="text-emerald-400 font-bold">{simulation.spot_recharter_option.target_runway_days} days</span>
                          </div>
                          <div className="flex items-center justify-between pt-1 border-t border-sail-border/60">
                            <span className="text-slate-400">Total Spot Freight:</span>
                            <span className="text-rose-400 font-bold">${formatNumber(simulation.spot_recharter_option.total_estimated_freight_usd)}</span>
                          </div>
                        </div>
                      </div>
                    ) : (
                      <div className="p-4 rounded-lg bg-sail-dark border border-sail-border text-center text-slate-500 font-mono text-xs">
                        No spot re-chartering parcel required; plant is currently adequate.
                      </div>
                    )}
                  </div>

                  <div className="pt-2 border-t border-sail-border text-[11px] text-slate-500 font-mono">
                    Spot fixture calibrated to Baltic Supramax/Panamax/Capesize rates.
                  </div>
                </div>
              </div>
            </div>
          )}
        </>
      )}
    </div>
  );
}
