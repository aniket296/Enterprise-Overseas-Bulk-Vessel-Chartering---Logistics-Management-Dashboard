"use client";

import React, { useEffect, useState, useCallback, useMemo } from "react";
import Link from "next/link";
import { useApp } from "@/context/AppContext";
import { logisticsApi } from "@/lib/api-client";
import { InboundForecastData, InboundDailyPoint, PlantCode } from "@/types/dashboard";
import {
  TrendingDown,
  TrendingUp,
  RefreshCw,
  Calendar,
  Building2,
  Ship,
  Train,
  CheckCircle2,
  AlertTriangle,
  Flame,
  ShieldCheck,
  AlertOctagon,
  ArrowRight,
  Filter,
  Layers,
  Sparkles,
  Info,
  Clock,
} from "lucide-react";

const PLANT_OPTIONS: { code: string; label: string }[] = [
  { code: "BSP", label: "BSP - Bhilai Steel Plant" },
  { code: "DSP", label: "DSP - Durgapur Steel Plant" },
  { code: "RSP", label: "RSP - Rourkela Steel Plant" },
  { code: "BSL", label: "BSL - Bokaro Steel Plant" },
  { code: "ISP", label: "ISP - IISCO Burnpur" },
];

const MATERIAL_OPTIONS = [
  { value: "COKING_COAL", label: "Coking Coal (Imported)" },
  { value: "IRON_ORE", label: "Iron Ore / Pellets" },
  { value: "MET_COKE", label: "Metallurgical Coke" },
  { value: "LIMESTONE", label: "Limestone / Flux" },
];

const HORIZON_OPTIONS = [
  { days: 30, label: "30-Day Forward" },
  { days: 60, label: "60-Day Forward" },
  { days: 90, label: "90-Day Forward" },
];

export default function InboundForecastPage() {
  const { role, selectedPlant, refreshTrigger } = useApp();

  // Simulation controls state
  const [plant, setPlant] = useState<string>(selectedPlant === "ALL" ? "BSP" : selectedPlant);
  const [material, setMaterial] = useState<string>("COKING_COAL");
  const [horizon, setHorizon] = useState<number>(60);

  // Sync if navbar plant changes
  useEffect(() => {
    if (selectedPlant !== "ALL") {
      setPlant(selectedPlant);
    }
  }, [selectedPlant]);

  // Data state
  const [forecast, setForecast] = useState<InboundForecastData | null>(null);
  const [loading, setLoading] = useState<boolean>(true);
  const [error, setError] = useState<string | null>(null);
  const [selectedDayPoint, setSelectedDayPoint] = useState<InboundDailyPoint | null>(null);

  const fetchForecast = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const data = await logisticsApi.getInboundForecast(plant, material, horizon, role);
      setForecast(data);
      if (data.timeline.length > 0) {
        setSelectedDayPoint(data.timeline[0]);
      }
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : "Failed to compute forecast simulation";
      setError(msg);
    } finally {
      setLoading(false);
    }
  }, [plant, material, horizon, role]);

  useEffect(() => {
    fetchForecast();
  }, [fetchForecast, refreshTrigger]);

  const formatNumber = (val: string | number | null | undefined, decimals = 0) => {
    if (val === undefined || val === null) return "0";
    const num = typeof val === "string" ? parseFloat(val) : val;
    return isNaN(num) ? "0" : num.toLocaleString("en-US", { minimumFractionDigits: decimals, maximumFractionDigits: decimals });
  };

  const getSafetyBadge = (status: string) => {
    switch (status) {
      case "ADEQUATE":
        return <span className="px-2 py-0.5 rounded text-[11px] font-bold bg-emerald-500/20 text-emerald-300 border border-emerald-500/30">ADEQUATE</span>;
      case "PRECAUTIONARY":
        return <span className="px-2 py-0.5 rounded text-[11px] font-bold bg-amber-500/20 text-amber-300 border border-amber-500/30">PRECAUTIONARY</span>;
      case "CRITICAL":
        return <span className="px-2 py-0.5 rounded text-[11px] font-bold bg-orange-500/20 text-orange-300 border border-orange-500/30">CRITICAL (&lt;7d)</span>;
      case "STOCKOUT":
      default:
        return <span className="px-2 py-0.5 rounded text-[11px] font-bold bg-rose-500/20 text-rose-300 border border-rose-500/30 animate-pulse">STOCKOUT (0 MT)</span>;
    }
  };

  // SVG Chart Dimensions & Computations
  const chartWidth = 900;
  const chartHeight = 260;
  const padding = { top: 20, right: 30, bottom: 30, left: 60 };

  const chartData = useMemo(() => {
    if (!forecast || forecast.timeline.length === 0) return null;
    const tl = forecast.timeline;
    const maxStock = Math.max(
      ...tl.map((d) => parseFloat(d.closing_stock_mt)),
      parseFloat(forecast.current_stock_mt),
      1000
    ) * 1.15; // 15% headroom

    const pts = tl.map((d, i) => {
      const x = padding.left + (i / (tl.length - 1)) * (chartWidth - padding.left - padding.right);
      const stock = parseFloat(d.closing_stock_mt);
      const y = chartHeight - padding.bottom - (Math.max(0, stock) / maxStock) * (chartHeight - padding.top - padding.bottom);
      const receipts = parseFloat(d.inbound_receipts_mt);
      return { x, y, stock, receipts, d };
    });

    // SVG path string
    const pathD = pts.reduce((acc, p, idx) => `${acc} ${idx === 0 ? "M" : "L"} ${p.x.toFixed(1)} ${p.y.toFixed(1)}`, "");
    const areaD = `${pathD} L ${pts[pts.length - 1].x.toFixed(1)} ${chartHeight - padding.bottom} L ${pts[0].x.toFixed(1)} ${chartHeight - padding.bottom} Z`;

    return { pts, pathD, areaD, maxStock };
  }, [forecast, chartWidth, chartHeight, padding.left, padding.right, padding.top, padding.bottom]);

  return (
    <div className="space-y-6">
      {/* Page Header */}
      <div className="flex flex-col md:flex-row md:items-center justify-between gap-4 pb-4 border-b border-sail-border">
        <div>
          <div className="flex items-center gap-2 mb-1">
            <span className="text-xs uppercase tracking-widest text-sail-cyan font-mono font-semibold">
              Forward Trajectory
            </span>
            <span className="text-slate-500">|</span>
            <span className="text-xs text-slate-400 font-mono">
              Blast Furnace Burn Depletion & Rail Pipeline Replenishment
            </span>
          </div>
          <h1 className="text-2xl font-bold tracking-tight text-white flex items-center gap-2">
            <TrendingDown className="w-6 h-6 text-sail-cyan" />
            Inbound Stock Runway Forecaster & Starvation Radar
          </h1>
        </div>

        {/* Simulation Parameter Controls Bar */}
        <div className="flex flex-wrap items-center gap-2.5">
          {/* Plant Selector */}
          <div className="flex items-center gap-1.5 bg-sail-card px-2.5 py-1.5 rounded-lg border border-sail-border">
            <Building2 className="w-3.5 h-3.5 text-sail-cyan" />
            <select
              value={plant}
              onChange={(e) => setPlant(e.target.value)}
              className="bg-transparent text-xs text-slate-200 focus:outline-none cursor-pointer font-mono"
            >
              {PLANT_OPTIONS.map((p) => (
                <option key={p.code} value={p.code} className="bg-sail-dark text-slate-200">
                  {p.label}
                </option>
              ))}
            </select>
          </div>

          {/* Material Category Selector */}
          <div className="flex items-center gap-1.5 bg-sail-card px-2.5 py-1.5 rounded-lg border border-sail-border">
            <Flame className="w-3.5 h-3.5 text-sail-gold" />
            <select
              value={material}
              onChange={(e) => setMaterial(e.target.value)}
              className="bg-transparent text-xs text-slate-200 focus:outline-none cursor-pointer font-mono"
            >
              {MATERIAL_OPTIONS.map((m) => (
                <option key={m.value} value={m.value} className="bg-sail-dark text-slate-200">
                  {m.label}
                </option>
              ))}
            </select>
          </div>

          {/* Horizon Selector */}
          <div className="flex items-center gap-1.5 bg-sail-card px-2.5 py-1.5 rounded-lg border border-sail-border">
            <Calendar className="w-3.5 h-3.5 text-emerald-400" />
            <select
              value={horizon}
              onChange={(e) => setHorizon(parseInt(e.target.value, 10))}
              className="bg-transparent text-xs text-slate-200 focus:outline-none cursor-pointer font-mono"
            >
              {HORIZON_OPTIONS.map((h) => (
                <option key={h.days} value={h.days} className="bg-sail-dark text-slate-200">
                  {h.label}
                </option>
              ))}
            </select>
          </div>

          {/* Re-simulate Button */}
          <button
            onClick={fetchForecast}
            title="Recalculate forward trajectory"
            className="p-2 rounded-lg bg-sail-card border border-sail-border text-slate-300 hover:text-white hover:border-sail-cyan transition-colors"
          >
            <RefreshCw className="w-4 h-4" />
          </button>
        </div>
      </div>

      {loading && !forecast && (
        <div className="py-20 flex flex-col items-center justify-center space-y-4">
          <RefreshCw className="w-8 h-8 text-sail-cyan animate-spin" />
          <p className="text-sm text-slate-400 font-mono">
            Simulating blast furnace burn and in-transit rail/vessel pipeline for {plant}...
          </p>
        </div>
      )}

      {error && (
        <div className="p-4 rounded-xl bg-rose-950/40 border border-rose-500/40 text-rose-200 text-xs font-mono">
          {error}
        </div>
      )}

      {forecast && (
        <>
          {/* Top Milestone & Strategic Risk Scorecards */}
          <div className="grid grid-cols-1 md:grid-cols-4 gap-4">
            {/* Card 1: Effective Runway Coverage */}
            <div className="p-4 rounded-xl bg-sail-card border border-sail-border space-y-2">
              <div className="flex items-center justify-between text-xs text-slate-400 font-mono">
                <span>Effective Runway</span>
                <Clock className="w-4 h-4 text-sail-cyan" />
              </div>
              <div className="flex items-baseline gap-2">
                <span className="text-3xl font-black text-white font-mono">
                  {forecast.extended_runway_days}
                </span>
                <span className="text-xs text-slate-400 font-mono">days</span>
              </div>
              <p className="text-[11px] text-slate-400 font-mono">
                Baseline (no ships): <strong className="text-slate-300">{forecast.baseline_runway_days}d</strong>
              </p>
            </div>

            {/* Card 2: Stockout Threat Date */}
            <div className="p-4 rounded-xl bg-sail-card border border-sail-border space-y-2">
              <div className="flex items-center justify-between text-xs text-slate-400 font-mono">
                <span>Stockout Date (0 MT)</span>
                <AlertOctagon className="w-4 h-4 text-rose-400" />
              </div>
              <div className="flex items-baseline gap-2">
                <span
                  className={`text-xl font-black font-mono ${
                    forecast.stockout_date ? "text-rose-400" : "text-emerald-400"
                  }`}
                >
                  {forecast.stockout_date || "SAFE IN HORIZON"}
                </span>
              </div>
              <p className="text-[11px] text-slate-400 font-mono">
                Burn: <strong className="text-slate-200">{formatNumber(forecast.daily_consumption_rate_mt)} MT/d</strong>
              </p>
            </div>

            {/* Card 3: Critical Safety Breach Date (<7d) */}
            <div className="p-4 rounded-xl bg-sail-card border border-sail-border space-y-2">
              <div className="flex items-center justify-between text-xs text-slate-400 font-mono">
                <span>Critical Breach (&lt;7d)</span>
                <AlertTriangle className="w-4 h-4 text-amber-400" />
              </div>
              <div className="flex items-baseline gap-2">
                <span
                  className={`text-xl font-black font-mono ${
                    forecast.critical_breach_date ? "text-amber-300" : "text-emerald-400"
                  }`}
                >
                  {forecast.critical_breach_date || "NO CRITICAL BREACH"}
                </span>
              </div>
              <p className="text-[11px] text-slate-400 font-mono">
                Precautionary (&lt;14d): <strong className="text-slate-300">{forecast.precautionary_breach_date || "None"}</strong>
              </p>
            </div>

            {/* Card 4: Minimum Stock Trough */}
            <div className="p-4 rounded-xl bg-sail-card border border-sail-border space-y-2">
              <div className="flex items-center justify-between text-xs text-slate-400 font-mono">
                <span>Projected Trough</span>
                <TrendingDown className="w-4 h-4 text-purple-400" />
              </div>
              <div className="flex items-baseline gap-2">
                <span className="text-2xl font-black text-white font-mono">
                  {formatNumber(forecast.minimum_projected_stock_mt)}
                </span>
                <span className="text-xs text-slate-400 font-mono">MT</span>
              </div>
              <p className="text-[11px] text-slate-400 font-mono">
                Trough Date: <strong className="text-purple-300">{forecast.minimum_stock_date || "N/A"}</strong>
              </p>
            </div>
          </div>

          {/* Interactive Visual SVG Stock Depletion & Replenishment Chart */}
          <div className="p-5 rounded-xl bg-sail-card border border-sail-border space-y-3">
            <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-2">
              <div>
                <h2 className="text-sm font-bold text-white uppercase tracking-wider flex items-center gap-2">
                  <TrendingDown className="w-4 h-4 text-sail-cyan" />
                  Forward Stock Depletion Trajectory ({forecast.horizon_days} Days)
                </h2>
                <p className="text-xs text-slate-400 font-mono">
                  {forecast.plant_name} ({forecast.plant_location}) • {forecast.material_category}
                </p>
              </div>

              {/* Chart Legend */}
              <div className="flex items-center gap-4 text-[11px] font-mono">
                <div className="flex items-center gap-1.5">
                  <span className="w-3 h-0.5 bg-sail-cyan" />
                  <span className="text-slate-300">Stockyard Balance</span>
                </div>
                <div className="flex items-center gap-1.5">
                  <span className="w-2.5 h-2.5 rounded-full bg-emerald-400" />
                  <span className="text-emerald-300">Vessel Rail Delivery</span>
                </div>
              </div>
            </div>

            {/* SVG Visual Canvas */}
            {chartData && (
              <div className="relative bg-[#060d1f] p-3 rounded-lg border border-sail-border overflow-x-auto">
                <svg
                  viewBox={`0 0 ${chartWidth} ${chartHeight}`}
                  className="w-full h-auto min-w-[700px] select-none"
                >
                  <defs>
                    <linearGradient id="area-gradient" x1="0" y1="0" x2="0" y2="1">
                      <stop offset="0%" stopColor="#00b4d8" stopOpacity="0.35" />
                      <stop offset="100%" stopColor="#0077b6" stopOpacity="0.0" />
                    </linearGradient>
                  </defs>

                  {/* Horizontal Grid lines */}
                  {[0.25, 0.5, 0.75, 1.0].map((ratio) => {
                    const y = chartHeight - padding.bottom - ratio * (chartHeight - padding.top - padding.bottom);
                    const val = ratio * chartData.maxStock;
                    return (
                      <g key={ratio}>
                        <line
                          x1={padding.left}
                          y1={y}
                          x2={chartWidth - padding.right}
                          y2={y}
                          stroke="#101d3f"
                          strokeWidth="1"
                        />
                        <text
                          x={padding.left - 8}
                          y={y + 3}
                          fill="#475569"
                          fontSize="9"
                          fontFamily="monospace"
                          textAnchor="end"
                        >
                          {formatNumber(val)} MT
                        </text>
                      </g>
                    );
                  })}

                  {/* Area fill */}
                  <path d={chartData.areaD} fill="url(#area-gradient)" />

                  {/* Stock trajectory curve */}
                  <path
                    d={chartData.pathD}
                    fill="none"
                    stroke="#00b4d8"
                    strokeWidth="2.5"
                    strokeLinecap="round"
                    strokeLinejoin="round"
                  />

                  {/* Inbound Shipment Arrival Spikes & Markers */}
                  {chartData.pts.map((pt, idx) => {
                    if (pt.receipts > 0) {
                      return (
                        <g key={idx}>
                          {/* Vertical dotted beacon */}
                          <line
                            x1={pt.x}
                            y1={pt.y}
                            x2={pt.x}
                            y2={chartHeight - padding.bottom}
                            stroke="#2ec4b6"
                            strokeWidth="1.5"
                            strokeDasharray="3 3"
                            opacity="0.8"
                          />
                          {/* Beacon circle */}
                          <circle
                            cx={pt.x}
                            cy={pt.y}
                            r="5"
                            fill="#2ec4b6"
                            stroke="#ffffff"
                            strokeWidth="1.5"
                            className="cursor-pointer"
                            onClick={() => setSelectedDayPoint(pt.d)}
                          />
                        </g>
                      );
                    }
                    return null;
                  })}

                  {/* Clickable Day Data Point overlay */}
                  {chartData.pts.map((pt, idx) => {
                    if (idx % 3 === 0 || idx === chartData.pts.length - 1) {
                      const isSelected = selectedDayPoint?.day_index === pt.d.day_index;
                      return (
                        <circle
                          key={idx}
                          cx={pt.x}
                          cy={pt.y}
                          r={isSelected ? "5" : "3"}
                          fill={isSelected ? "#f4a261" : "#0077b6"}
                          stroke="#ffffff"
                          strokeWidth={isSelected ? "2" : "1"}
                          className="cursor-pointer hover:r-6 transition-all"
                          onClick={() => setSelectedDayPoint(pt.d)}
                        />
                      );
                    }
                    return null;
                  })}

                  {/* Date labels on X-axis */}
                  {chartData.pts.map((pt, idx) => {
                    if (idx % 7 === 0 || idx === chartData.pts.length - 1) {
                      return (
                        <text
                          key={idx}
                          x={pt.x}
                          y={chartHeight - 10}
                          fill="#64748b"
                          fontSize="9"
                          fontFamily="monospace"
                          textAnchor="middle"
                        >
                          D+{pt.d.day_index}
                        </text>
                      );
                    }
                    return null;
                  })}
                </svg>
              </div>
            )}
          </div>

          {/* Split Section: Inbound Vessels Contributing & Selected Day Breakdown */}
          <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
            {/* Left 2 Cols: Inbound Vessel Shipment Pipeline */}
            <div className="lg:col-span-2 rounded-xl bg-sail-card border border-sail-border p-5 space-y-4">
              <div className="flex items-center justify-between pb-2 border-b border-sail-border">
                <div className="flex items-center gap-2">
                  <Ship className="w-4 h-4 text-sail-cyan" />
                  <h3 className="text-sm font-bold text-white uppercase tracking-wider">
                    Inbound Vessel Cargo Pipeline ({forecast.inbound_vessels_count} Shipments)
                  </h3>
                </div>
                <span className="text-xs text-emerald-400 font-mono font-bold">
                  Total: {formatNumber(forecast.total_inbound_tonnage_mt)} MT
                </span>
              </div>

              {forecast.inbound_vessels.length === 0 ? (
                <div className="py-8 text-center text-slate-500 font-mono text-xs">
                  No active in-transit vessels currently allocated to {forecast.plant_code}.
                </div>
              ) : (
                <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
                  {forecast.inbound_vessels.map((v) => (
                    <div
                      key={v.voyage_id}
                      className="p-3.5 rounded-lg bg-sail-dark border border-sail-border space-y-2 text-xs font-mono"
                    >
                      <div className="flex items-center justify-between">
                        <span className="text-white font-bold">{v.vessel_name}</span>
                        <span className="px-1.5 py-0.5 rounded bg-blue-900/60 text-sail-cyan text-[10px] font-bold border border-blue-600/30">
                          {v.voyage_status}
                        </span>
                      </div>
                      <div className="space-y-1 text-slate-300 text-[11px]">
                        <div className="flex items-center justify-between">
                          <span className="text-slate-400">Cargo Volume:</span>
                          <span className="text-emerald-400 font-bold">{formatNumber(v.quantity_mt)} MT</span>
                        </div>
                        <div className="flex items-center justify-between">
                          <span className="text-slate-400">Discharge Port:</span>
                          <span className="text-sail-cyan">{v.discharge_port}</span>
                        </div>
                        <div className="flex items-center justify-between">
                          <span className="text-slate-400">Port ETA:</span>
                          <span className="text-slate-300">{v.eta_date?.slice(0, 10) || "N/A"}</span>
                        </div>
                        <div className="flex items-center justify-between pt-1 border-t border-sail-border/60 text-slate-400">
                          <span className="flex items-center gap-1">
                            <Train className="w-3 h-3 text-sail-gold" />
                            <span>BOXN Rail Transit:</span>
                          </span>
                          <span className="text-white">+3 days to plant</span>
                        </div>
                      </div>
                    </div>
                  ))}
                </div>
              )}
            </div>

            {/* Right Col: Selected Simulation Day Inspection */}
            <div className="rounded-xl bg-sail-card border border-sail-border p-5 space-y-4 flex flex-col justify-between">
              {selectedDayPoint ? (
                <div className="space-y-4">
                  <div className="pb-3 border-b border-sail-border">
                    <div className="flex items-center justify-between mb-1">
                      <span className="text-xs text-slate-400 font-mono uppercase tracking-wider">
                        Day D+{selectedDayPoint.day_index} Inspection
                      </span>
                      {getSafetyBadge(selectedDayPoint.safety_status)}
                    </div>
                    <h3 className="text-lg font-bold text-white font-mono">
                      {selectedDayPoint.simulation_date}
                    </h3>
                  </div>

                  <div className="p-3.5 rounded-lg bg-sail-dark border border-sail-border space-y-2 text-xs font-mono">
                    <div className="flex items-center justify-between">
                      <span className="text-slate-400">Opening Stock:</span>
                      <span className="text-slate-200">{formatNumber(selectedDayPoint.opening_stock_mt)} MT</span>
                    </div>
                    <div className="flex items-center justify-between">
                      <span className="text-slate-400">Daily Burn:</span>
                      <span className="text-rose-400 font-bold">-{formatNumber(selectedDayPoint.daily_burn_mt)} MT</span>
                    </div>
                    <div className="flex items-center justify-between">
                      <span className="text-slate-400">Inbound Delivery:</span>
                      <span className="text-emerald-400 font-bold">
                        +{formatNumber(selectedDayPoint.inbound_receipts_mt)} MT
                      </span>
                    </div>
                    <div className="flex items-center justify-between pt-2 border-t border-sail-border/60">
                      <span className="text-slate-400">Closing Stock:</span>
                      <span className="text-white font-bold text-sm">
                        {formatNumber(selectedDayPoint.closing_stock_mt)} MT
                      </span>
                    </div>
                    <div className="flex items-center justify-between">
                      <span className="text-slate-400">Runway Remaining:</span>
                      <span className="text-sail-cyan font-bold">
                        {selectedDayPoint.runway_days_remaining} days
                      </span>
                    </div>
                  </div>

                  {/* Day Events */}
                  <div className="space-y-1 font-mono text-xs">
                    <span className="text-slate-400 text-[11px]">Timeline Events:</span>
                    {selectedDayPoint.events.length === 0 ? (
                      <p className="text-slate-500 text-[11px] italic">No extraordinary supply events logged.</p>
                    ) : (
                      <div className="space-y-1">
                        {selectedDayPoint.events.map((ev, i) => (
                          <div key={i} className="p-2 rounded bg-sail-dark text-[11px] text-emerald-300 border border-sail-border">
                            {ev}
                          </div>
                        ))}
                      </div>
                    )}
                  </div>
                </div>
              ) : (
                <div className="py-20 text-center text-slate-500 font-mono text-xs">
                  Click a data point on the chart to inspect daily burn dynamics.
                </div>
              )}

              {/* Action Button: Evaluate Emergency Starvation Mitigation */}
              <div className="pt-2 border-t border-sail-border">
                <Link
                  href="/inventory/starvation"
                  className="w-full flex items-center justify-between px-3.5 py-2.5 rounded-lg bg-rose-950/60 hover:bg-rose-900 text-rose-300 border border-rose-600/40 text-xs font-mono font-bold transition-all"
                >
                  <span>Launch Starvation Solver</span>
                  <ArrowRight className="w-4 h-4" />
                </Link>
              </div>
            </div>
          </div>

          {/* Daily Forward Depletion Ledger Table */}
          <div className="rounded-xl bg-sail-card border border-sail-border overflow-hidden">
            <div className="p-4 border-b border-sail-border flex items-center justify-between">
              <h2 className="text-sm font-bold text-white uppercase tracking-wider flex items-center gap-2">
                <Calendar className="w-4 h-4 text-sail-cyan" />
                Full Forward Simulation Depletion Ledger ({forecast.timeline.length} Days)
              </h2>
              <span className="text-xs text-slate-400 font-mono">
                Click row to inspect day details
              </span>
            </div>

            <div className="overflow-x-auto max-h-[440px]">
              <table className="w-full text-left text-xs font-mono">
                <thead className="bg-sail-dark text-slate-400 border-b border-sail-border uppercase sticky top-0">
                  <tr>
                    <th className="py-3 px-4">Offset</th>
                    <th className="py-3 px-4">Simulation Date</th>
                    <th className="py-3 px-4 text-right">Opening Stock</th>
                    <th className="py-3 px-4 text-right">Daily Burn</th>
                    <th className="py-3 px-4 text-right">Inbound Delivered</th>
                    <th className="py-3 px-4 text-right">Closing Stock</th>
                    <th className="py-3 px-4 text-right">Runway (d)</th>
                    <th className="py-3 px-4 text-center">Safety Status</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-sail-border">
                  {forecast.timeline.map((d) => {
                    const isSelected = selectedDayPoint?.day_index === d.day_index;
                    return (
                      <tr
                        key={d.day_index}
                        className={`hover:bg-sail-dark/60 transition-colors cursor-pointer ${
                          isSelected ? "bg-sail-dark/90 border-l-2 border-sail-cyan" : ""
                        }`}
                        onClick={() => setSelectedDayPoint(d)}
                      >
                        <td className="py-2.5 px-4 font-bold text-slate-400">D+{d.day_index}</td>
                        <td className="py-2.5 px-4 text-white font-medium">{d.simulation_date}</td>
                        <td className="py-2.5 px-4 text-right text-slate-300">{formatNumber(d.opening_stock_mt)}</td>
                        <td className="py-2.5 px-4 text-right text-rose-300">-{formatNumber(d.daily_burn_mt)}</td>
                        <td className="py-2.5 px-4 text-right text-emerald-400 font-bold">
                          {parseFloat(d.inbound_receipts_mt) > 0 ? `+${formatNumber(d.inbound_receipts_mt)}` : "-"}
                        </td>
                        <td className="py-2.5 px-4 text-right text-white font-bold">{formatNumber(d.closing_stock_mt)}</td>
                        <td className="py-2.5 px-4 text-right text-sail-cyan font-bold">{d.runway_days_remaining} d</td>
                        <td className="py-2.5 px-4 text-center">{getSafetyBadge(d.safety_status)}</td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          </div>
        </>
      )}
    </div>
  );
}
