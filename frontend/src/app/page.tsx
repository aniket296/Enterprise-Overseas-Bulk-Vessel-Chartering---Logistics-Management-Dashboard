"use client";

import React, { useEffect, useState, useCallback } from "react";
import Link from "next/link";
import { useApp } from "@/context/AppContext";
import { logisticsApi } from "@/lib/api-client";
import { ExecutiveDashboardSummary } from "@/types/dashboard";
import {
  Ship,
  DollarSign,
  ShieldCheck,
  AlertTriangle,
  Flame,
  TrendingUp,
  TrendingDown,
  Clock,
  ArrowRight,
  RefreshCw,
  CheckCircle2,
  XCircle,
  BarChart3,
  Award,
} from "lucide-react";

export default function ExecutiveDashboardPage() {
  const { role, selectedPlant, refreshTrigger } = useApp();
  const [data, setData] = useState<ExecutiveDashboardSummary | null>(null);
  const [loading, setLoading] = useState<boolean>(true);
  const [error, setError] = useState<string | null>(null);

  const fetchSummary = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const summary = await logisticsApi.getExecutiveSummary(role);
      setData(summary);
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : "Failed to connect to backend service";
      setError(msg);
    } finally {
      setLoading(false);
    }
  }, [role]);

  useEffect(() => {
    fetchSummary();
  }, [fetchSummary, refreshTrigger]);

  // Filter plant details if a specific plant is selected in the navbar
  const filteredPlants = data?.plant_security.plant_details.filter((p) => {
    if (selectedPlant === "ALL") return true;
    return p.plant_code === selectedPlant;
  }) || [];

  const getHealthBadgeColor = (status?: string) => {
    switch (status) {
      case "EXCELLENT":
        return "bg-emerald-500/20 text-emerald-400 border-emerald-500/40";
      case "STABLE":
        return "bg-blue-500/20 text-blue-400 border-blue-500/40";
      case "NEEDS_ATTENTION":
        return "bg-amber-500/20 text-amber-400 border-amber-500/40";
      case "CRITICAL_INTERVENTION_REQUIRED":
      default:
        return "bg-rose-500/20 text-rose-400 border-rose-500/40";
    }
  };

  const getSeverityBadge = (severity: string) => {
    switch (severity) {
      case "SURPLUS":
        return <span className="px-2 py-0.5 rounded text-[11px] font-bold bg-teal-500/20 text-teal-300 border border-teal-500/30">SURPLUS</span>;
      case "ADEQUATE":
        return <span className="px-2 py-0.5 rounded text-[11px] font-bold bg-emerald-500/20 text-emerald-300 border border-emerald-500/30">ADEQUATE</span>;
      case "WATCH":
        return <span className="px-2 py-0.5 rounded text-[11px] font-bold bg-blue-500/20 text-blue-300 border border-blue-500/30">WATCH</span>;
      case "PRECAUTIONARY":
        return <span className="px-2 py-0.5 rounded text-[11px] font-bold bg-amber-500/20 text-amber-300 border border-amber-500/30">PRECAUTIONARY</span>;
      case "CRITICAL":
        return <span className="px-2 py-0.5 rounded text-[11px] font-bold bg-orange-500/20 text-orange-300 border border-orange-500/30">CRITICAL</span>;
      case "CRITICAL_URGENT":
      default:
        return <span className="px-2 py-0.5 rounded text-[11px] font-bold bg-rose-500/20 text-rose-300 border border-rose-500/30 animate-pulse">URGENT</span>;
    }
  };

  const formatNumber = (val: string | number | undefined, decimals = 0) => {
    if (val === undefined || val === null) return "0";
    const num = typeof val === "string" ? parseFloat(val) : val;
    return isNaN(num) ? "0" : num.toLocaleString("en-US", { minimumFractionDigits: decimals, maximumFractionDigits: decimals });
  };

  return (
    <div className="space-y-6">
      {/* Page Header */}
      <div className="flex flex-col md:flex-row md:items-center justify-between gap-4 pb-4 border-b border-sail-border">
        <div>
          <div className="flex items-center gap-2 mb-1">
            <span className="text-xs uppercase tracking-widest text-sail-cyan font-mono font-semibold">
              Executive Overview
            </span>
            <span className="text-slate-500">|</span>
            <span className="text-xs text-slate-400 font-mono">
              SAIL Logistics Command Engine
            </span>
          </div>
          <h1 className="text-2xl font-bold tracking-tight text-white">
            Logistics & Bulk Chartering Command Center
          </h1>
        </div>

        {/* Health Score & Status Badge */}
        {data && (
          <div className="flex items-center gap-4 bg-sail-card px-4 py-2.5 rounded-xl border border-sail-border shadow-md">
            <div className="flex flex-col items-end">
              <span className="text-[10px] text-slate-400 font-mono uppercase">
                Enterprise Health
              </span>
              <div className="flex items-baseline gap-1">
                <span className="text-2xl font-black text-white font-mono">
                  {data.composite_health.enterprise_health_score}
                </span>
                <span className="text-xs text-slate-400 font-mono">/ 100</span>
              </div>
            </div>
            <div
              className={`px-3 py-1.5 rounded-lg border text-xs font-bold font-mono tracking-wider ${getHealthBadgeColor(
                data.composite_health.status_level
              )}`}
            >
              {data.composite_health.status_level.replace(/_/g, " ")}
            </div>
          </div>
        )}
      </div>

      {/* Loading & Error States */}
      {loading && !data && (
        <div className="py-20 flex flex-col items-center justify-center space-y-4">
          <RefreshCw className="w-8 h-8 text-sail-cyan animate-spin" />
          <p className="text-sm text-slate-400 font-mono">
            Aggregating enterprise logistics KPIs and plant starvation metrics...
          </p>
        </div>
      )}

      {error && (
        <div className="p-4 rounded-xl bg-rose-950/40 border border-rose-500/40 text-rose-200 flex items-start gap-3">
          <AlertTriangle className="w-5 h-5 text-rose-400 shrink-0 mt-0.5" />
          <div className="space-y-1">
            <p className="text-sm font-semibold">Backend Connection Notice</p>
            <p className="text-xs text-rose-300/90">{error}</p>
            <p className="text-xs text-slate-400">
              Ensure Django development server is running on <code className="bg-rose-900/60 px-1 py-0.5 rounded text-white">127.0.0.1:8000</code>.
            </p>
          </div>
        </div>
      )}

      {data && (
        <>
          {/* Main KPI Scorecards (4 Pillars) */}
          <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-4">
            {/* Pillar 1: Fleet Operations */}
            <div className="p-5 rounded-xl bg-sail-card border border-sail-border relative overflow-hidden group hover:border-sail-accent transition-all">
              <div className="flex items-center justify-between mb-3">
                <span className="text-xs font-semibold text-slate-400 uppercase tracking-wider">
                  Fleet Operations
                </span>
                <div className="p-2 rounded-lg bg-blue-900/40 text-sail-cyan border border-blue-700/30">
                  <Ship className="w-4 h-4" />
                </div>
              </div>
              <div className="space-y-1">
                <div className="flex items-baseline gap-2">
                  <span className="text-3xl font-extrabold text-white font-mono">
                    {data.fleet_operations.total_active_voyages}
                  </span>
                  <span className="text-xs text-slate-400">Active Voyages</span>
                </div>
                <p className="text-xs text-slate-300">
                  <strong className="text-emerald-400">{data.fleet_operations.vessels_underway}</strong> underway |{" "}
                  <strong className="text-blue-300">{data.fleet_operations.vessels_at_anchor_or_berth}</strong> at port
                </p>
              </div>
              <div className="mt-4 pt-3 border-t border-sail-border flex items-center justify-between text-xs">
                <span className="text-slate-400">Total Cargo:</span>
                <span className="text-white font-mono font-medium">
                  {formatNumber(data.fleet_operations.total_pipeline_cargo_mt)} MT
                </span>
              </div>
            </div>

            {/* Pillar 2: Financial & Laytime */}
            <div className="p-5 rounded-xl bg-sail-card border border-sail-border relative overflow-hidden group hover:border-sail-accent transition-all">
              <div className="flex items-center justify-between mb-3">
                <span className="text-xs font-semibold text-slate-400 uppercase tracking-wider">
                  Financial & Laytime
                </span>
                <div className="p-2 rounded-lg bg-emerald-900/40 text-emerald-400 border border-emerald-700/30">
                  <DollarSign className="w-4 h-4" />
                </div>
              </div>
              <div className="space-y-1">
                <div className="flex items-baseline gap-2">
                  <span className="text-3xl font-extrabold text-white font-mono">
                    ${formatNumber(data.financial_performance.total_gross_freight_committed_usd)}
                  </span>
                </div>
                <p className="text-xs text-slate-300">Gross Freight Committed</p>
              </div>
              <div className="mt-4 pt-3 border-t border-sail-border flex items-center justify-between text-xs">
                <span className="text-slate-400">Net Laytime:</span>
                <span
                  className={`font-mono font-bold ${
                    data.financial_performance.net_impact_direction === "PAYABLE"
                      ? "text-rose-400"
                      : "text-emerald-400"
                  }`}
                >
                  {data.financial_performance.net_impact_direction === "PAYABLE" ? "-" : "+"}
                  ${formatNumber(data.financial_performance.net_laytime_impact_usd)} ({data.financial_performance.net_impact_direction})
                </span>
              </div>
            </div>

            {/* Pillar 3: Plant Stock Security */}
            <div className="p-5 rounded-xl bg-sail-card border border-sail-border relative overflow-hidden group hover:border-sail-accent transition-all">
              <div className="flex items-center justify-between mb-3">
                <span className="text-xs font-semibold text-slate-400 uppercase tracking-wider">
                  Plant Raw Material Security
                </span>
                <div className="p-2 rounded-lg bg-amber-900/40 text-sail-gold border border-amber-700/30">
                  <ShieldCheck className="w-4 h-4" />
                </div>
              </div>
              <div className="space-y-1">
                <div className="flex items-baseline gap-2">
                  <span className="text-3xl font-extrabold text-white font-mono">
                    {data.plant_security.overall_security_index_pct}%
                  </span>
                  <span className="text-xs text-slate-400">Security Index</span>
                </div>
                <p className="text-xs text-slate-300">
                  Weighted Runway: <strong className="text-sail-cyan">{data.plant_security.network_weighted_runway_days} days</strong>
                </p>
              </div>
              <div className="mt-4 pt-3 border-t border-sail-border flex items-center justify-between text-xs">
                <span className="text-slate-400">Plants at Risk:</span>
                <span
                  className={`font-mono font-bold ${
                    data.plant_security.plants_at_risk_count > 0 ? "text-rose-400" : "text-emerald-400"
                  }`}
                >
                  {data.plant_security.plants_at_risk_count} / {data.plant_security.plants_reporting_count}
                </span>
              </div>
            </div>

            {/* Pillar 4: Carrier Reliability */}
            <div className="p-5 rounded-xl bg-sail-card border border-sail-border relative overflow-hidden group hover:border-sail-accent transition-all">
              <div className="flex items-center justify-between mb-3">
                <span className="text-xs font-semibold text-slate-400 uppercase tracking-wider">
                  Carrier Fulfillment
                </span>
                <div className="p-2 rounded-lg bg-purple-900/40 text-purple-400 border border-purple-700/30">
                  <Award className="w-4 h-4" />
                </div>
              </div>
              <div className="space-y-1">
                <div className="flex items-baseline gap-2">
                  <span className="text-3xl font-extrabold text-white font-mono">
                    {data.contract_fulfillment.overall_carrier_reliability_pct}%
                  </span>
                  <span className="text-xs text-slate-400">Reliability Score</span>
                </div>
                <p className="text-xs text-slate-300">
                  On-Time Arrival: <strong className="text-emerald-400">{data.contract_fulfillment.on_time_arrival_rate_pct}%</strong>
                </p>
              </div>
              <div className="mt-4 pt-3 border-t border-sail-border flex items-center justify-between text-xs">
                <span className="text-slate-400">Open Risk Flags:</span>
                <span
                  className={`font-mono font-bold ${
                    data.compliance_and_risk.open_compliance_flags_count > 0 ? "text-amber-400" : "text-emerald-400"
                  }`}
                >
                  {data.compliance_and_risk.open_compliance_flags_count} incidents
                </span>
              </div>
            </div>
          </div>

          {/* Plant Security Matrix Breakdown */}
          <div className="rounded-xl bg-sail-card border border-sail-border overflow-hidden">
            <div className="p-4 border-b border-sail-border flex items-center justify-between">
              <div>
                <h2 className="text-base font-bold text-white flex items-center gap-2">
                  <Flame className="w-4 h-4 text-sail-gold" />
                  SAIL Steel Plant Stock Coverage & Starvation Radar
                </h2>
                <p className="text-xs text-slate-400">
                  Blast furnace coking coal & iron ore forward stock levels against 21-day safety threshold
                </p>
              </div>
              <Link
                href="/inventory/starvation"
                className="inline-flex items-center gap-1.5 text-xs text-sail-cyan hover:text-white font-medium transition-colors"
              >
                <span>Full Starvation Simulator</span>
                <ArrowRight className="w-3.5 h-3.5" />
              </Link>
            </div>

            <div className="overflow-x-auto">
              <table className="w-full text-left text-xs">
                <thead className="bg-sail-dark text-slate-400 border-b border-sail-border font-mono uppercase">
                  <tr>
                    <th className="py-3 px-4">Plant Code</th>
                    <th className="py-3 px-4 text-right">Current Stock (MT)</th>
                    <th className="py-3 px-4 text-right">Daily Burn (MT/day)</th>
                    <th className="py-3 px-4 text-right">Effective Runway</th>
                    <th className="py-3 px-4 text-center">Safety Target</th>
                    <th className="py-3 px-4 text-center">Risk Band</th>
                    <th className="py-3 px-4 text-center">Mitigation Required</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-sail-border">
                  {filteredPlants.length === 0 ? (
                    <tr>
                      <td colSpan={7} className="py-6 text-center text-slate-400 font-mono">
                        No plant stock records reporting for current selection.
                      </td>
                    </tr>
                  ) : (
                    filteredPlants.map((plant) => (
                      <tr key={plant.plant_code} className="hover:bg-sail-dark/50 transition-colors font-mono">
                        <td className="py-3 px-4 font-bold text-white flex items-center gap-2">
                          <span className="w-2 h-2 rounded-full bg-sail-cyan" />
                          <span>{plant.plant_code}</span>
                        </td>
                        <td className="py-3 px-4 text-right text-slate-200">
                          {formatNumber(plant.current_stock_mt)}
                        </td>
                        <td className="py-3 px-4 text-right text-slate-400">
                          {formatNumber(plant.daily_burn_rate_mt)}
                        </td>
                        <td className="py-3 px-4 text-right font-bold text-white">
                          {plant.effective_runway_days} days
                        </td>
                        <td className="py-3 px-4 text-center text-slate-400">
                          {plant.safety_stock_threshold_days} days
                        </td>
                        <td className="py-3 px-4 text-center">
                          {getSeverityBadge(plant.risk_severity)}
                        </td>
                        <td className="py-3 px-4 text-center">
                          {plant.action_required ? (
                            <span className="inline-flex items-center gap-1 text-rose-400 font-bold">
                              <AlertTriangle className="w-3.5 h-3.5" />
                              YES
                            </span>
                          ) : (
                            <span className="inline-flex items-center gap-1 text-emerald-400">
                              <CheckCircle2 className="w-3.5 h-3.5" />
                              NORMAL
                            </span>
                          )}
                        </td>
                      </tr>
                    ))
                  )}
                </tbody>
              </table>
            </div>
          </div>

          {/* Executive Directives & Prioritized Action Highlights */}
          <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
            <div className="lg:col-span-2 p-5 rounded-xl bg-sail-card border border-sail-border space-y-4">
              <h2 className="text-base font-bold text-white flex items-center gap-2">
                <BarChart3 className="w-4 h-4 text-sail-cyan" />
                Executive Logistics Highlights & Priority Directives
              </h2>
              <div className="space-y-2.5">
                {data.composite_health.executive_highlights.map((highlight, idx) => (
                  <div
                    key={idx}
                    className="p-3 rounded-lg bg-sail-dark border border-sail-border/80 flex items-start gap-3"
                  >
                    <span className="font-mono text-xs px-2 py-0.5 rounded bg-blue-900/60 text-sail-cyan border border-blue-700/40 shrink-0 mt-0.5">
                      #{idx + 1}
                    </span>
                    <p className="text-xs text-slate-300 leading-relaxed">
                      {highlight}
                    </p>
                  </div>
                ))}
              </div>
            </div>

            {/* Quick Action Decision Hub */}
            <div className="p-5 rounded-xl bg-sail-card border border-sail-border space-y-4 flex flex-col justify-between">
              <div>
                <h3 className="text-sm font-bold text-white uppercase tracking-wider mb-2">
                  Decision Support Hub
                </h3>
                <p className="text-xs text-slate-400 leading-relaxed mb-4">
                  Trigger automated simulations across coastal vessel diversions, Indian Railways rake allocations, and laytime mitigation.
                </p>
                <div className="space-y-2">
                  <Link
                    href="/inventory/starvation"
                    className="w-full flex items-center justify-between p-3 rounded-lg bg-sail-dark border border-sail-border hover:border-sail-accent transition-all text-xs font-medium text-slate-200 hover:text-white"
                  >
                    <span>Run Multi-Plant Starvation Solver</span>
                    <ArrowRight className="w-4 h-4 text-sail-cyan" />
                  </Link>
                  <Link
                    href="/inventory/forecast"
                    className="w-full flex items-center justify-between p-3 rounded-lg bg-sail-dark border border-sail-border hover:border-sail-accent transition-all text-xs font-medium text-slate-200 hover:text-white"
                  >
                    <span>Forward Runway Simulation (60d)</span>
                    <ArrowRight className="w-4 h-4 text-sail-cyan" />
                  </Link>
                  <Link
                    href="/fleet"
                    className="w-full flex items-center justify-between p-3 rounded-lg bg-sail-dark border border-sail-border hover:border-sail-accent transition-all text-xs font-medium text-slate-200 hover:text-white"
                  >
                    <span>Inspect In-Transit Vessels</span>
                    <ArrowRight className="w-4 h-4 text-sail-cyan" />
                  </Link>
                </div>
              </div>

              <div className="p-3 rounded-lg bg-emerald-950/30 border border-emerald-500/20 text-[11px] text-emerald-300/90 flex items-center gap-2">
                <CheckCircle2 className="w-4 h-4 text-emerald-400 shrink-0" />
                <span>Deterministic Zero-Mocking calculation engine active</span>
              </div>
            </div>
          </div>
        </>
      )}
    </div>
  );
}
