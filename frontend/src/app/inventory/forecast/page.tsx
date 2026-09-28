"use client";

import React, { useEffect, useState, useCallback } from "react";
import { useApp } from "@/context/AppContext";
import { logisticsApi } from "@/lib/api-client";
import {
  TrendingDown,
  RefreshCw,
  Calendar,
  Building2,
  Ship,
  CheckCircle2,
  AlertTriangle,
} from "lucide-react";

export default function InboundForecastPage() {
  const { role, selectedPlant, refreshTrigger } = useApp();
  const plantCode = selectedPlant === "ALL" ? "BSP" : selectedPlant;
  const [forecast, setForecast] = useState<any>(null);
  const [loading, setLoading] = useState<boolean>(true);
  const [error, setError] = useState<string | null>(null);

  const fetchForecast = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const data: any = await logisticsApi.getInboundForecast(plantCode, "COKING_COAL", 60, role);
      setForecast(data);
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : "Failed to load forecast";
      setError(msg);
    } finally {
      setLoading(false);
    }
  }, [plantCode, role]);

  useEffect(() => {
    fetchForecast();
  }, [fetchForecast, refreshTrigger]);

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
            <span className="text-xs uppercase tracking-widest text-sail-cyan font-mono font-semibold">
              Forward Trajectory
            </span>
            <span className="text-slate-500">|</span>
            <span className="text-xs text-slate-400 font-mono">
              60-Day Runway Simulator
            </span>
          </div>
          <h1 className="text-2xl font-bold tracking-tight text-white flex items-center gap-2">
            <TrendingDown className="w-6 h-6 text-sail-cyan" />
            Inbound Stock Runway & Blast Furnace Burn Forecaster
          </h1>
        </div>

        {forecast && (
          <div className="flex items-center gap-3">
            <div className="px-3 py-1.5 rounded-lg bg-sail-card border border-sail-border text-slate-300 font-mono text-xs">
              Plant: <strong className="text-sail-cyan">{forecast.plant_code}</strong>
            </div>
            <div className="px-3 py-1.5 rounded-lg bg-sail-card border border-sail-border text-slate-300 font-mono text-xs">
              Daily Burn: <strong className="text-white">{formatNumber(forecast.daily_burn_rate_mt)} MT/d</strong>
            </div>
          </div>
        )}
      </div>

      {loading && !forecast && (
        <div className="py-20 flex flex-col items-center justify-center space-y-4">
          <RefreshCw className="w-8 h-8 text-sail-cyan animate-spin" />
          <p className="text-sm text-slate-400 font-mono">
            Simulating forward burn and in-transit vessel pipeline for {plantCode}...
          </p>
        </div>
      )}

      {error && (
        <div className="p-4 rounded-xl bg-rose-950/40 border border-rose-500/40 text-rose-200 text-xs">
          {error}
        </div>
      )}

      {forecast && (
        <>
          {/* Summary Metric Cards */}
          <div className="grid grid-cols-1 md:grid-cols-4 gap-4">
            <div className="p-4 rounded-xl bg-sail-card border border-sail-border space-y-1">
              <span className="text-xs text-slate-400 font-mono">Current Stock</span>
              <p className="text-2xl font-bold text-white font-mono">{formatNumber(forecast.current_stock_mt)} MT</p>
            </div>
            <div className="p-4 rounded-xl bg-sail-card border border-sail-border space-y-1">
              <span className="text-xs text-slate-400 font-mono">Inbound Shipments</span>
              <p className="text-2xl font-bold text-sail-cyan font-mono">{forecast.total_inbound_shipments_scheduled}</p>
            </div>
            <div className="p-4 rounded-xl bg-sail-card border border-sail-border space-y-1">
              <span className="text-xs text-slate-400 font-mono">Scheduled Inbound</span>
              <p className="text-2xl font-bold text-emerald-400 font-mono">{formatNumber(forecast.total_inbound_volume_mt)} MT</p>
            </div>
            <div className="p-4 rounded-xl bg-sail-card border border-sail-border space-y-1">
              <span className="text-xs text-slate-400 font-mono">Safety Stock Breach</span>
              <p className="text-lg font-bold text-amber-400 font-mono">
                {forecast.projected_safety_breach_date || "No breach in 60d"}
              </p>
            </div>
          </div>

          {/* Daily Trajectory Points (First 15 Days Sample) */}
          <div className="rounded-xl bg-sail-card border border-sail-border overflow-hidden">
            <div className="p-4 border-b border-sail-border flex items-center justify-between">
              <h2 className="text-sm font-bold text-white uppercase tracking-wider">
                Simulated Forward Stock Depletion (Daily Timeline)
              </h2>
              <span className="text-xs text-slate-400 font-mono">
                Safety Stock Level: {formatNumber(forecast.safety_stock_mt)} MT ({forecast.safety_stock_threshold_days} days)
              </span>
            </div>
            <div className="overflow-x-auto max-h-96">
              <table className="w-full text-left text-xs font-mono">
                <thead className="bg-sail-dark text-slate-400 border-b border-sail-border uppercase sticky top-0">
                  <tr>
                    <th className="py-2.5 px-4">Day</th>
                    <th className="py-2.5 px-4">Date</th>
                    <th className="py-2.5 px-4 text-right">Opening Stock</th>
                    <th className="py-2.5 px-4 text-right">Inbound Delivered</th>
                    <th className="py-2.5 px-4 text-right">Burn Consumed</th>
                    <th className="py-2.5 px-4 text-right">Closing Stock</th>
                    <th className="py-2.5 px-4 text-center">Status</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-sail-border">
                  {forecast.daily_trajectory?.slice(0, 30).map((pt: any) => (
                    <tr key={pt.day_offset} className="hover:bg-sail-dark/50">
                      <td className="py-2 px-4 text-slate-400 font-bold">D+{pt.day_offset}</td>
                      <td className="py-2 px-4 text-slate-200">{pt.date}</td>
                      <td className="py-2 px-4 text-right text-slate-300">{formatNumber(pt.opening_stock_mt)}</td>
                      <td className="py-2 px-4 text-right text-emerald-400 font-bold">
                        {parseFloat(pt.inbound_received_mt) > 0 ? `+${formatNumber(pt.inbound_received_mt)}` : "-"}
                      </td>
                      <td className="py-2 px-4 text-right text-rose-300">-{formatNumber(pt.daily_burn_mt)}</td>
                      <td className="py-2 px-4 text-right text-white font-bold">{formatNumber(pt.closing_stock_mt)}</td>
                      <td className="py-2 px-4 text-center">
                        {pt.is_stockout ? (
                          <span className="px-1.5 py-0.5 rounded bg-rose-500/20 text-rose-300 text-[10px] font-bold">
                            STOCKOUT
                          </span>
                        ) : pt.is_safety_breached ? (
                          <span className="px-1.5 py-0.5 rounded bg-amber-500/20 text-amber-300 text-[10px] font-bold">
                            BREACH
                          </span>
                        ) : (
                          <span className="px-1.5 py-0.5 rounded bg-emerald-500/20 text-emerald-300 text-[10px] font-bold">
                            SAFE
                          </span>
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        </>
      )}
    </div>
  );
}
