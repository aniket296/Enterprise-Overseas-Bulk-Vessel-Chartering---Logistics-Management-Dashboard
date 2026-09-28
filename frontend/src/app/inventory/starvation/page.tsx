"use client";

import React, { useEffect, useState, useCallback } from "react";
import { useApp } from "@/context/AppContext";
import { logisticsApi } from "@/lib/api-client";
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
} from "lucide-react";

interface StarvationPlantSummary {
  plant_code: string;
  material_category: string;
  current_stock_mt: string;
  daily_burn_rate_mt: string;
  effective_runway_days: string;
  safety_stock_threshold_days: number;
  stockout_projected_date: string;
  safety_breach_projected_date: string;
  risk_severity: string;
  requires_immediate_mitigation: boolean;
}

interface StarvationMatrixResponse {
  lookahead_days: number;
  total_plants_evaluated: number;
  plants_in_critical_or_urgent_state: number;
  plants_in_precautionary_state: number;
  plants_in_adequate_or_surplus_state: number;
  generated_at: string;
  plant_starvation_profiles: StarvationPlantSummary[];
}

export default function StarvationWarningPage() {
  const { role, refreshTrigger } = useApp();
  const [data, setData] = useState<StarvationMatrixResponse | null>(null);
  const [loading, setLoading] = useState<boolean>(true);
  const [error, setError] = useState<string | null>(null);
  const [selectedMitigationPlant, setSelectedMitigationPlant] = useState<string>("BSP");
  const [mitigationSimulation, setMitigationSimulation] = useState<any>(null);
  const [simulating, setSimulating] = useState<boolean>(false);

  const fetchMatrix = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const res: any = await logisticsApi.getStarvationMatrix(role);
      setData(res);
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : "Failed to load starvation matrix";
      setError(msg);
    } finally {
      setLoading(false);
    }
  }, [role]);

  useEffect(() => {
    fetchMatrix();
  }, [fetchMatrix, refreshTrigger]);

  const handleRunSimulation = async (plant: string) => {
    setSelectedMitigationPlant(plant);
    setSimulating(true);
    try {
      const sim = await logisticsApi.simulateMitigation(plant, "COKING_COAL", role);
      setMitigationSimulation(sim);
    } catch (err: unknown) {
      console.error(err);
    } finally {
      setSimulating(false);
    }
  };

  const formatNumber = (val: string | number | undefined) => {
    if (val === undefined || val === null) return "0";
    const num = typeof val === "string" ? parseFloat(val) : val;
    return isNaN(num) ? "0" : num.toLocaleString("en-US");
  };

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
              SAIL 5-Plant Blast Furnace Security
            </span>
          </div>
          <h1 className="text-2xl font-bold tracking-tight text-white flex items-center gap-2">
            <AlertOctagon className="w-6 h-6 text-rose-500" />
            Stock Starvation Early Warning & Mitigation Solver
          </h1>
        </div>

        {data && (
          <div className="flex items-center gap-3">
            <div className="px-3 py-1.5 rounded-lg bg-rose-950/40 border border-rose-500/30 text-rose-300 font-mono text-xs">
              <strong className="text-white">{data.plants_in_critical_or_urgent_state}</strong> Critical / Urgent
            </div>
            <div className="px-3 py-1.5 rounded-lg bg-emerald-950/40 border border-emerald-500/30 text-emerald-300 font-mono text-xs">
              <strong className="text-white">{data.plants_in_adequate_or_surplus_state}</strong> Adequate / Surplus
            </div>
          </div>
        )}
      </div>

      {loading && !data && (
        <div className="py-20 flex flex-col items-center justify-center space-y-4">
          <RefreshCw className="w-8 h-8 text-rose-400 animate-spin" />
          <p className="text-sm text-slate-400 font-mono">
            Scanning 5 SAIL plants across 30-day blast furnace burn trajectories...
          </p>
        </div>
      )}

      {error && (
        <div className="p-4 rounded-xl bg-rose-950/40 border border-rose-500/40 text-rose-200 text-xs">
          {error}
        </div>
      )}

      {data && (
        <>
          {/* Starvation Severity Table */}
          <div className="rounded-xl bg-sail-card border border-sail-border overflow-hidden">
            <div className="p-4 border-b border-sail-border">
              <h2 className="text-sm font-bold text-white uppercase tracking-wider">
                Multi-Plant Starvation Assessment Matrix
              </h2>
            </div>
            <div className="overflow-x-auto">
              <table className="w-full text-left text-xs font-mono">
                <thead className="bg-sail-dark text-slate-400 border-b border-sail-border uppercase">
                  <tr>
                    <th className="py-3 px-4">Plant</th>
                    <th className="py-3 px-4 text-right">Current Stock</th>
                    <th className="py-3 px-4 text-right">Daily Burn</th>
                    <th className="py-3 px-4 text-right">Runway (Days)</th>
                    <th className="py-3 px-4 text-center">Safety Breach</th>
                    <th className="py-3 px-4 text-center">Stockout Date</th>
                    <th className="py-3 px-4 text-center">Risk Severity</th>
                    <th className="py-3 px-4 text-center">Simulate Action</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-sail-border">
                  {data.plant_starvation_profiles.map((p) => (
                    <tr key={p.plant_code} className="hover:bg-sail-dark/50">
                      <td className="py-3 px-4 font-bold text-white flex items-center gap-2">
                        <Flame className="w-4 h-4 text-sail-gold" />
                        <span>{p.plant_code}</span>
                      </td>
                      <td className="py-3 px-4 text-right text-slate-200">
                        {formatNumber(p.current_stock_mt)} MT
                      </td>
                      <td className="py-3 px-4 text-right text-slate-400">
                        {formatNumber(p.daily_burn_rate_mt)} MT/d
                      </td>
                      <td className="py-3 px-4 text-right font-bold text-white">
                        {p.effective_runway_days} d
                      </td>
                      <td className="py-3 px-4 text-center text-amber-300">
                        {p.safety_breach_projected_date || "N/A"}
                      </td>
                      <td className="py-3 px-4 text-center text-rose-300">
                        {p.stockout_projected_date || "N/A"}
                      </td>
                      <td className="py-3 px-4 text-center">
                        <span className="px-2 py-0.5 rounded text-[10px] font-bold bg-sail-border text-slate-200">
                          {p.risk_severity}
                        </span>
                      </td>
                      <td className="py-3 px-4 text-center">
                        <button
                          onClick={() => handleRunSimulation(p.plant_code)}
                          className="px-2.5 py-1 rounded bg-blue-900/60 hover:bg-blue-800 text-sail-cyan border border-blue-600/40 text-[11px] font-semibold transition-all"
                        >
                          Solve {p.plant_code}
                        </button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>

          {/* Mitigation Solver Output Panel */}
          {mitigationSimulation && (
            <div className="p-5 rounded-xl bg-sail-card border border-sail-cyan/40 space-y-4">
              <div className="flex items-center justify-between">
                <div className="flex items-center gap-2">
                  <Anchor className="w-5 h-5 text-sail-cyan" />
                  <h3 className="text-base font-bold text-white">
                    Emergency Mitigation Solver: {mitigationSimulation.starving_plant} ({mitigationSimulation.material_category})
                  </h3>
                </div>
                <span className="text-xs font-mono text-slate-400">
                  Target Deficit: <strong className="text-rose-400">{formatNumber(mitigationSimulation.target_deficit_mt)} MT</strong>
                </span>
              </div>

              <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
                {/* Option 1: Coastal Vessel Diversion */}
                <div className="p-4 rounded-lg bg-sail-dark border border-sail-border space-y-3">
                  <div className="flex items-center gap-2 text-xs font-bold text-sail-cyan uppercase">
                    <Ship className="w-4 h-4" />
                    <span>Coastal Vessel Diversion</span>
                  </div>
                  <div className="text-xs text-slate-300 space-y-1 font-mono">
                    <p>Options Evaluated: <strong>{mitigationSimulation.vessel_diversion_options?.length || 0}</strong></p>
                    {mitigationSimulation.vessel_diversion_options?.[0] ? (
                      <div className="pt-2 border-t border-sail-border/60 text-[11px] text-slate-400">
                        <p className="text-white font-bold">{mitigationSimulation.vessel_diversion_options[0].vessel_name}</p>
                        <p>Cargo: {formatNumber(mitigationSimulation.vessel_diversion_options[0].cargo_quantity_mt)} MT</p>
                        <p>Cost: ${formatNumber(mitigationSimulation.vessel_diversion_options[0].total_estimated_diversion_cost_usd)}</p>
                      </div>
                    ) : (
                      <p className="text-slate-500 pt-2">No vessels qualify donor safety threshold (≥14d).</p>
                    )}
                  </div>
                </div>

                {/* Option 2: Indian Railways BOXN Rake Transfer */}
                <div className="p-4 rounded-lg bg-sail-dark border border-sail-border space-y-3">
                  <div className="flex items-center gap-2 text-xs font-bold text-sail-gold uppercase">
                    <Train className="w-4 h-4" />
                    <span>Railways BOXN Rake Transfer</span>
                  </div>
                  <div className="text-xs text-slate-300 space-y-1 font-mono">
                    <p>Feasible Donor Plants: <strong>{mitigationSimulation.inter_plant_transfer_options?.length || 0}</strong></p>
                    {mitigationSimulation.inter_plant_transfer_options?.[0] ? (
                      <div className="pt-2 border-t border-sail-border/60 text-[11px] text-slate-400">
                        <p className="text-white font-bold">Donor: {mitigationSimulation.inter_plant_transfer_options[0].donor_plant}</p>
                        <p>Rakes: {mitigationSimulation.inter_plant_transfer_options[0].rakes_required} BOXN rakes</p>
                        <p>Transfer: {formatNumber(mitigationSimulation.inter_plant_transfer_options[0].transfer_tonnage_mt)} MT</p>
                        <p>Freight: ${formatNumber(mitigationSimulation.inter_plant_transfer_options[0].total_freight_cost_usd)}</p>
                      </div>
                    ) : (
                      <p className="text-slate-500 pt-2">No donor plant has sufficient surplus stocks.</p>
                    )}
                  </div>
                </div>

                {/* Option 3: Spot Emergency Re-chartering */}
                <div className="p-4 rounded-lg bg-sail-dark border border-sail-border space-y-3">
                  <div className="flex items-center gap-2 text-xs font-bold text-purple-400 uppercase">
                    <Anchor className="w-4 h-4" />
                    <span>Emergency Spot Fixture</span>
                  </div>
                  <div className="text-xs text-slate-300 space-y-1 font-mono">
                    <p>Spot Fixture Sizes: <strong>{mitigationSimulation.emergency_recharter_options?.length || 0}</strong></p>
                    {mitigationSimulation.emergency_recharter_options?.[0] ? (
                      <div className="pt-2 border-t border-sail-border/60 text-[11px] text-slate-400">
                        <p className="text-white font-bold">{mitigationSimulation.emergency_recharter_options[0].vessel_class} ({formatNumber(mitigationSimulation.emergency_recharter_options[0].parcel_size_mt)} MT)</p>
                        <p>Rate: ${mitigationSimulation.emergency_recharter_options[0].freight_rate_usd_per_mt}/MT</p>
                        <p>Total: ${formatNumber(mitigationSimulation.emergency_recharter_options[0].estimated_gross_freight_usd)}</p>
                        <p>Lead Time: {mitigationSimulation.emergency_recharter_options[0].estimated_lead_days} days</p>
                      </div>
                    ) : (
                      <p className="text-slate-500 pt-2">No spot parcel options configured.</p>
                    )}
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
