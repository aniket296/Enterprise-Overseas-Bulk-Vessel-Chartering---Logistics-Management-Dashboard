"use client";

import React, { useState, useEffect, useCallback, useMemo } from "react";
import { logisticsApi } from "@/lib/api-client";
import {
  PortBerthTimelineReport,
  BerthScheduleItem,
  AnchorageQueueItem,
  UserRole,
} from "@/types/dashboard";
import {
  Ship,
  Anchor,
  Clock,
  AlertTriangle,
  RefreshCw,
  CloudRain,
  Waves,
  CheckCircle2,
  Lock,
  ChevronRight,
  Maximize2,
  Calendar,
  AlertOctagon,
  ArrowRight,
  Compass,
} from "lucide-react";

interface BerthingGanttProps {
  initialPort?: string;
  role: UserRole;
  onRefreshTrigger?: number;
}

// Weather overlay windows to display seasonal weather disruption risks (e.g., monsoon swell / rain)
interface WeatherWindow {
  id: string;
  label: string;
  cause: "MONSOON_RAIN" | "HEAVY_SWELL" | "TIDAL_RESTRICTION";
  startOffsetDays: number;
  durationDays: number;
}

const DEFAULT_WEATHER_WINDOWS: WeatherWindow[] = [
  {
    id: "wx-1",
    label: "Monsoon Surge / Heavy Rain (WWD Excluded)",
    cause: "MONSOON_RAIN",
    startOffsetDays: 3.5,
    durationDays: 1.5,
  },
  {
    id: "wx-2",
    label: "High Swell Advisory",
    cause: "HEAVY_SWELL",
    startOffsetDays: 8.0,
    durationDays: 1.0,
  },
];

export default function BerthingGantt({
  initialPort = "Paradip",
  role,
  onRefreshTrigger,
}: BerthingGanttProps) {
  const [selectedPort, setSelectedPort] = useState<string>(initialPort);
  const [timelineData, setTimelineData] = useState<PortBerthTimelineReport | null>(null);
  const [loading, setLoading] = useState<boolean>(true);
  const [error, setError] = useState<string | null>(null);

  // Hover Tooltip State
  const [hoveredVessel, setHoveredVessel] = useState<{
    item: BerthScheduleItem;
    x: number;
    y: number;
  } | null>(null);

  // Berth Assignment Modal State
  const [modalOpen, setModalOpen] = useState<boolean>(false);
  const [assigningVoyage, setAssigningVoyage] = useState<AnchorageQueueItem | BerthScheduleItem | null>(null);
  const [targetBerth, setTargetBerth] = useState<string>("");
  const [forceOverride, setForceOverride] = useState<boolean>(false);
  const [assigning, setAssigning] = useState<boolean>(false);
  const [assignError, setAssignError] = useState<string | null>(null);
  const [assignSuccess, setAssignSuccess] = useState<string | null>(null);

  // Fetch Gantt timeline data from backend
  const fetchTimeline = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const data = await logisticsApi.getBerthTimeline(selectedPort, role);
      setTimelineData(data);
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : "Failed to load berth timeline";
      setError(msg);
    } finally {
      setLoading(false);
    }
  }, [selectedPort, role]);

  useEffect(() => {
    fetchTimeline();
  }, [fetchTimeline, onRefreshTrigger]);

  // Total window scope for Gantt: 14 days starting from beginning of today
  const timeWindowDays = 14;
  const startTimestamp = useMemo(() => {
    const d = new Date();
    d.setHours(0, 0, 0, 0);
    return d.getTime();
  }, []);
  const endTimestamp = startTimestamp + timeWindowDays * 24 * 3600 * 1000;

  // Day columns array
  const dayColumns = useMemo(() => {
    return Array.from({ length: timeWindowDays }, (_, i) => {
      const dt = new Date(startTimestamp + i * 24 * 3600 * 1000);
      const dayName = dt.toLocaleDateString("en-US", { weekday: "short" });
      const dayNum = dt.getDate();
      const monthName = dt.toLocaleDateString("en-US", { month: "short" });
      return { index: i, label: `${dayName} ${dayNum} ${monthName}` };
    });
  }, [startTimestamp, timeWindowDays]);

  // Compute position percentage along the horizontal timeline
  const getTimelinePosPct = (dateStr: string) => {
    const t = new Date(dateStr).getTime();
    const clamped = Math.max(startTimestamp, Math.min(endTimestamp, t));
    return ((clamped - startTimestamp) / (endTimestamp - startTimestamp)) * 100;
  };

  // Handle Berth Assignment submission
  const handleAssignBerth = async () => {
    if (!assigningVoyage || !targetBerth) return;
    setAssigning(true);
    setAssignError(null);
    setAssignSuccess(null);
    try {
      await logisticsApi.assignBerth(
        assigningVoyage.voyage_id,
        targetBerth,
        forceOverride,
        role
      );
      setAssignSuccess(`Berth ${targetBerth} assigned successfully to ${assigningVoyage.vessel_name}`);
      setTimeout(() => {
        setModalOpen(false);
        setAssigningVoyage(null);
        setAssignSuccess(null);
        fetchTimeline();
      }, 1200);
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : "Berth assignment rejected";
      setAssignError(msg);
    } finally {
      setAssigning(false);
    }
  };

  // Congestion Level color helper
  const getCongestionBadge = (level: string) => {
    switch (level) {
      case "LOW":
        return "bg-emerald-500/20 text-emerald-300 border-emerald-500/40";
      case "MODERATE":
        return "bg-amber-500/20 text-amber-300 border-amber-500/40";
      case "HIGH":
      default:
        return "bg-rose-500/20 text-rose-300 border-rose-500/40";
    }
  };

  return (
    <div className="space-y-6">
      {/* Header and Port Selector */}
      <div className="flex flex-col md:flex-row md:items-center justify-between gap-4 pb-4 border-b border-sail-border">
        <div>
          <div className="flex items-center gap-2 mb-1">
            <span className="text-xs uppercase tracking-widest text-sail-cyan font-mono font-semibold">
              Port Operations & Scheduling
            </span>
            <span className="text-slate-500">|</span>
            <span className="text-xs text-slate-400 font-mono">
              Dynamic Berthing Gantt & Weather Exception Radar
            </span>
          </div>
          <h2 className="text-xl font-bold tracking-tight text-white flex items-center gap-2">
            <Waves className="w-5 h-5 text-sail-cyan" />
            Discharge Port Berth Occupancy & Anchorage Queuing
          </h2>
        </div>

        {/* Port Filter & Refresh */}
        <div className="flex items-center gap-3">
          <div className="flex items-center gap-2 bg-sail-card px-3 py-1.5 rounded-lg border border-sail-border">
            <Compass className="w-4 h-4 text-sail-cyan" />
            <select
              value={selectedPort}
              onChange={(e) => setSelectedPort(e.target.value)}
              className="bg-transparent text-xs text-slate-200 focus:outline-none cursor-pointer font-mono"
            >
              <option value="Paradip" className="bg-sail-dark text-slate-200">Paradip Port (PPT)</option>
              <option value="Haldia" className="bg-sail-dark text-slate-200">Haldia Dock (HDC)</option>
              <option value="Visakhapatnam" className="bg-sail-dark text-slate-200">Visakhapatnam (VPT)</option>
              <option value="Dhamra" className="bg-sail-dark text-slate-200">Dhamra Port (DPCL)</option>
            </select>
          </div>

          <button
            onClick={() => fetchTimeline()}
            disabled={loading}
            className="p-2 rounded-lg bg-sail-card hover:bg-sail-border text-slate-300 border border-sail-border transition-all disabled:opacity-50"
            title="Refresh Berth Timeline"
          >
            <RefreshCw className={`w-4 h-4 ${loading ? "animate-spin text-sail-cyan" : ""}`} />
          </button>
        </div>
      </div>

      {/* Port Congestion Scorecard Summary */}
      {timelineData && (
        <div className="grid grid-cols-2 md:grid-cols-5 gap-3.5">
          <div className="p-3.5 rounded-xl bg-sail-card border border-sail-border">
            <span className="text-[11px] font-semibold text-slate-400 uppercase tracking-wider block">
              Active Port Vessels
            </span>
            <span className="text-2xl font-black text-white font-mono mt-1 block">
              {timelineData.kpis.total_active_vessels}
            </span>
            <span className="text-[10px] text-slate-400 font-mono">
              {timelineData.kpis.vessels_berthed_count} berthed / {timelineData.kpis.vessels_at_anchorage_count} waiting
            </span>
          </div>

          <div className="p-3.5 rounded-xl bg-sail-card border border-sail-border">
            <span className="text-[11px] font-semibold text-slate-400 uppercase tracking-wider block">
              Berthed Cargo
            </span>
            <span className="text-2xl font-black text-emerald-400 font-mono mt-1 block">
              {parseFloat(timelineData.kpis.total_cargo_berthed_mt).toLocaleString()}
            </span>
            <span className="text-[10px] text-slate-400 font-mono">MT currently working</span>
          </div>

          <div className="p-3.5 rounded-xl bg-sail-card border border-sail-border">
            <span className="text-[11px] font-semibold text-slate-400 uppercase tracking-wider block">
              Anchorage Cargo
            </span>
            <span className="text-2xl font-black text-amber-400 font-mono mt-1 block">
              {parseFloat(timelineData.kpis.total_cargo_waiting_mt).toLocaleString()}
            </span>
            <span className="text-[10px] text-slate-400 font-mono">MT awaiting berth slot</span>
          </div>

          <div className="p-3.5 rounded-xl bg-sail-card border border-sail-border">
            <span className="text-[11px] font-semibold text-slate-400 uppercase tracking-wider block">
              Avg Wait Time
            </span>
            <span className="text-2xl font-black text-sail-cyan font-mono mt-1 block">
              {timelineData.kpis.average_anchorage_wait_hours ? `${timelineData.kpis.average_anchorage_wait_hours}h` : "0.0h"}
            </span>
            <span className="text-[10px] text-slate-400 font-mono">Anchorage turn time</span>
          </div>

          <div className="p-3.5 rounded-xl bg-sail-card border border-sail-border">
            <span className="text-[11px] font-semibold text-slate-400 uppercase tracking-wider block">
              Congestion Index
            </span>
            <div className="mt-1.5 flex items-center gap-2">
              <span
                className={`px-2.5 py-1 rounded text-xs font-bold font-mono border ${getCongestionBadge(
                  timelineData.kpis.congestion_level
                )}`}
              >
                {timelineData.kpis.congestion_level}
              </span>
              {timelineData.total_conflicts_detected > 0 && (
                <span className="px-2 py-0.5 rounded text-[10px] font-bold bg-rose-500/20 text-rose-300 border border-rose-500/40 font-mono">
                  {timelineData.total_conflicts_detected} Conflicts
                </span>
              )}
            </div>
            <span className="text-[10px] text-slate-400 font-mono block mt-1">
              Port throughput status
            </span>
          </div>
        </div>
      )}

      {/* Main Gantt Timeline Chart Container */}
      <div className="rounded-xl bg-sail-card border border-sail-border overflow-hidden flex flex-col">
        {/* Timeline Header Bar */}
        <div className="p-4 border-b border-sail-border flex flex-wrap items-center justify-between gap-3 bg-sail-dark/70">
          <div className="flex items-center gap-2">
            <Calendar className="w-4 h-4 text-sail-cyan" />
            <h3 className="text-xs font-bold text-white uppercase tracking-wider font-mono">
              14-Day Forward Berth Occupancy Track ({selectedPort})
            </h3>
          </div>

          {/* Legend */}
          <div className="flex flex-wrap items-center gap-4 text-[11px] font-mono">
            <div className="flex items-center gap-1.5">
              <span className="w-3 h-2.5 rounded bg-emerald-600 border border-emerald-400" />
              <span className="text-slate-300">Active Working</span>
            </div>
            <div className="flex items-center gap-1.5">
              <span className="w-3 h-2.5 rounded bg-cyan-700 border border-cyan-400" />
              <span className="text-slate-300">Scheduled Arrival</span>
            </div>
            <div className="flex items-center gap-1.5">
              <span className="w-3 h-2.5 rounded bg-rose-900 border border-rose-500 animate-pulse" />
              <span className="text-rose-300">Conflict / Overlap</span>
            </div>
            <div className="flex items-center gap-1.5">
              <span className="w-3 h-2.5 rounded bg-amber-500/30 border border-amber-400/50" />
              <span className="text-amber-300">Weather Window (z:10)</span>
            </div>
          </div>
        </div>

        {/* Scrollable Gantt Canvas */}
        <div className="overflow-x-auto relative min-h-[380px] bg-[#070e1e]">
          <div className="min-w-[1100px] relative select-none">
            {/* 1. Time Scale Header */}
            <div className="grid grid-cols-14 border-b border-sail-border/80 bg-sail-dark/90 sticky top-0 z-30 font-mono text-[11px] text-slate-400">
              <div className="w-36 py-2.5 px-3 border-r border-sail-border/80 text-left font-bold text-white uppercase">
                Berth Track
              </div>
              <div className="flex-1 grid grid-cols-14 divide-x divide-sail-border/40">
                {dayColumns.map((col) => (
                  <div key={col.index} className="py-2.5 px-1 text-center">
                    <span className="block font-medium">{col.label}</span>
                  </div>
                ))}
              </div>
            </div>

            {/* 2. Global Weather Exception Overlay Track (z-index: 10) */}
            <div className="absolute top-10 left-36 right-0 bottom-0 pointer-events-none z-10">
              {DEFAULT_WEATHER_WINDOWS.map((wx) => {
                const leftPct = (wx.startOffsetDays / timeWindowDays) * 100;
                const widthPct = (wx.durationDays / timeWindowDays) * 100;
                return (
                  <div
                    key={wx.id}
                    className="absolute top-0 bottom-0 bg-amber-500/10 border-x border-amber-400/30 flex flex-col justify-end p-2 pointer-events-auto group cursor-help"
                    style={{ left: `${leftPct}%`, width: `${widthPct}%` }}
                    title={`${wx.label} - Laytime exception period`}
                  >
                    <div className="bg-amber-950/90 border border-amber-500/40 rounded px-1.5 py-0.5 text-[9px] font-mono text-amber-200 flex items-center gap-1 w-max">
                      <CloudRain className="w-3 h-3 text-amber-400" />
                      <span>{wx.label}</span>
                    </div>
                  </div>
                );
              })}
            </div>

            {/* 3. Berth Rows */}
            {timelineData && Object.keys(timelineData.berths).length > 0 ? (
              <div className="divide-y divide-sail-border/40 relative">
                {Object.entries(timelineData.berths).map(([berthName, items]) => {
                  return (
                    <div key={berthName} className="flex relative min-h-[68px] hover:bg-sail-dark/30 transition-colors">
                      {/* Berth Label */}
                      <div className="w-36 p-3 border-r border-sail-border/80 bg-sail-dark/50 flex flex-col justify-center shrink-0 z-20">
                        <span className="font-mono text-xs font-bold text-white">{berthName}</span>
                        <span className="text-[10px] text-slate-400 font-mono">
                          {items.length} vessel{items.length !== 1 ? "s" : ""}
                        </span>
                      </div>

                      {/* Berth Timeline Area */}
                      <div className="flex-1 relative">
                        {/* Day grid vertical guide lines */}
                        <div className="absolute inset-0 grid grid-cols-14 divide-x divide-sail-border/20 pointer-events-none">
                          {Array.from({ length: 14 }).map((_, i) => (
                            <div key={i} className="h-full" />
                          ))}
                        </div>

                        {/* Voyage Timeline Bars (z-index: 20) */}
                        {items.map((item, idx) => {
                          const leftPct = Math.max(0, Math.min(100, getTimelinePosPct(item.start_time)));
                          const rightPct = Math.max(0, Math.min(100, getTimelinePosPct(item.end_time)));
                          const widthPct = Math.max(3.5, rightPct - leftPct);

                          // Handle collision vertical stacking within track so boxes do NOT collide
                          const topOffset = item.has_conflict ? (idx % 2 === 0 ? 4 : 36) : 10;
                          const barHeight = item.has_conflict ? 26 : 46;

                          const isWorking = item.voyage_status === "DISCHARGING" || item.voyage_status === "BERTHED";

                          return (
                            <div
                              key={item.voyage_id}
                              className={`absolute rounded-md cursor-pointer transition-all border shadow-lg flex items-center justify-between px-2.5 z-20 group ${
                                item.has_conflict
                                  ? "bg-rose-950/90 border-rose-500 text-rose-200 hover:border-rose-300 ring-1 ring-rose-500/50"
                                  : isWorking
                                  ? "bg-emerald-950/90 border-emerald-500 text-emerald-200 hover:border-emerald-300"
                                  : "bg-cyan-950/90 border-cyan-500 text-cyan-200 hover:border-cyan-300"
                              }`}
                              style={{
                                left: `${leftPct}%`,
                                width: `${widthPct}%`,
                                top: `${topOffset}px`,
                                height: `${barHeight}px`,
                              }}
                              onMouseEnter={(e) => {
                                const rect = e.currentTarget.getBoundingClientRect();
                                setHoveredVessel({
                                  item,
                                  x: rect.left + rect.width / 2,
                                  y: rect.top - 10,
                                });
                              }}
                              onMouseLeave={() => setHoveredVessel(null)}
                              onClick={() => {
                                setAssigningVoyage(item);
                                setTargetBerth(berthName);
                                setForceOverride(item.has_conflict);
                                setModalOpen(true);
                              }}
                            >
                              <div className="flex items-center gap-1.5 overflow-hidden">
                                <Ship className="w-3.5 h-3.5 shrink-0" />
                                <span className="font-mono text-[11px] font-bold truncate">
                                  {item.vessel_name}
                                </span>
                              </div>

                              {item.has_conflict && (
                                <AlertTriangle className="w-3.5 h-3.5 text-rose-400 shrink-0 animate-bounce" />
                              )}

                              {/* Drag / Adjustment Handles (z-index: 30) */}
                              <div
                                className="absolute left-0 top-0 bottom-0 w-2 cursor-ew-resize opacity-0 group-hover:opacity-100 bg-white/20 rounded-l z-30"
                                title="Adjust berth arrival window"
                              />
                              <div
                                className="absolute right-0 top-0 bottom-0 w-2 cursor-ew-resize opacity-0 group-hover:opacity-100 bg-white/20 rounded-r z-30"
                                title="Adjust departure window"
                              />
                            </div>
                          );
                        })}
                      </div>
                    </div>
                  );
                })}
              </div>
            ) : (
              <div className="py-16 text-center text-slate-400 font-mono text-xs">
                No active berth allocations recorded for {selectedPort}.
              </div>
            )}
          </div>
        </div>
      </div>

      {/* Interactive Hover Tooltip (z-index: 50) */}
      {hoveredVessel && (
        <div
          className="fixed z-50 pointer-events-none transform -translate-x-1/2 -translate-y-full mb-2 p-3 rounded-xl bg-sail-dark/95 border border-sail-border shadow-2xl backdrop-blur-md font-mono text-xs text-slate-200 min-w-[260px] max-w-[320px]"
          style={{ left: hoveredVessel.x, top: hoveredVessel.y }}
        >
          <div className="pb-1.5 border-b border-sail-border/80 flex items-center justify-between">
            <span className="font-bold text-white text-sm">{hoveredVessel.item.vessel_name}</span>
            <span
              className={`px-1.5 py-0.5 rounded text-[10px] font-bold ${
                hoveredVessel.item.has_conflict
                  ? "bg-rose-500/20 text-rose-300 border border-rose-500/30"
                  : "bg-emerald-500/20 text-emerald-300 border border-emerald-500/30"
              }`}
            >
              {hoveredVessel.item.voyage_status}
            </span>
          </div>

          <div className="mt-2 space-y-1 text-[11px]">
            <div className="flex justify-between">
              <span className="text-slate-400">Voyage Ref:</span>
              <span className="text-white font-semibold">{hoveredVessel.item.voyage_reference}</span>
            </div>
            <div className="flex justify-between">
              <span className="text-slate-400">Cargo:</span>
              <span className="text-slate-200">
                {parseFloat(hoveredVessel.item.quantity_mt).toLocaleString()} MT ({hoveredVessel.item.cargo_type})
              </span>
            </div>
            <div className="flex justify-between">
              <span className="text-slate-400">Window:</span>
              <span className="text-sail-cyan">{hoveredVessel.item.duration_hours} hours</span>
            </div>

            {hoveredVessel.item.has_conflict && (
              <div className="pt-2 border-t border-rose-500/30 text-rose-300 text-[10px] flex items-start gap-1">
                <AlertTriangle className="w-3.5 h-3.5 text-rose-400 shrink-0 mt-0.5" />
                <span>
                  Overlap Conflict with: {hoveredVessel.item.conflicts_with.join(", ")}
                </span>
              </div>
            )}
          </div>
        </div>
      )}

      {/* Anchorage Waiting Queue Table & Actions */}
      <div className="rounded-xl bg-sail-card border border-sail-border overflow-hidden">
        <div className="p-4 border-b border-sail-border flex items-center justify-between">
          <div className="flex items-center gap-2">
            <Anchor className="w-4 h-4 text-amber-400" />
            <h3 className="text-xs font-bold text-white uppercase tracking-wider font-mono">
              Anchorage Waiting Queue ({timelineData?.anchorage_queue.length || 0} Vessels Awaiting Discharge)
            </h3>
          </div>
          <span className="text-xs text-slate-400 font-mono">
            FIFO Turn-Time Allocation
          </span>
        </div>

        <div className="overflow-x-auto">
          <table className="w-full text-left text-xs font-mono">
            <thead className="bg-sail-dark text-slate-400 border-b border-sail-border uppercase sticky top-0 z-10">
              <tr>
                <th className="py-3 px-4">Vessel Name</th>
                <th className="py-3 px-4">Voyage Ref</th>
                <th className="py-3 px-4">Cargo Type</th>
                <th className="py-3 px-4 text-right">Quantity (MT)</th>
                <th className="py-3 px-4">ETA / NOR Date</th>
                <th className="py-3 px-4 text-right">Anchorage Wait</th>
                <th className="py-3 px-4 text-center">Assigned Berth</th>
                <th className="py-3 px-4 text-center">Action</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-sail-border">
              {!timelineData || timelineData.anchorage_queue.length === 0 ? (
                <tr>
                  <td colSpan={8} className="py-6 text-center text-slate-500">
                    No vessels currently queued at anchorage for {selectedPort}.
                  </td>
                </tr>
              ) : (
                timelineData.anchorage_queue.map((item) => (
                  <tr key={item.voyage_id} className="hover:bg-sail-dark/50 transition-colors">
                    <td className="py-3 px-4 font-bold text-white flex items-center gap-2">
                      <Ship className="w-3.5 h-3.5 text-sail-cyan" />
                      <span>{item.vessel_name}</span>
                    </td>
                    <td className="py-3 px-4 text-slate-300">{item.voyage_reference}</td>
                    <td className="py-3 px-4 text-slate-400">{item.cargo_type}</td>
                    <td className="py-3 px-4 text-right text-slate-200">
                      {parseFloat(item.quantity_mt).toLocaleString()}
                    </td>
                    <td className="py-3 px-4 text-slate-400">
                      {new Date(item.eta_date).toLocaleDateString("en-US", {
                        month: "short",
                        day: "numeric",
                        hour: "2-digit",
                        minute: "2-digit",
                      })}
                    </td>
                    <td className="py-3 px-4 text-right text-amber-400 font-bold">
                      {item.waiting_hours ? `${item.waiting_hours}h` : "Approaching"}
                    </td>
                    <td className="py-3 px-4 text-center">
                      <span className="px-2 py-0.5 rounded text-[11px] bg-sail-dark text-slate-300 border border-sail-border">
                        {item.assigned_berth || "UNASSIGNED"}
                      </span>
                    </td>
                    <td className="py-3 px-4 text-center">
                      <button
                        onClick={() => {
                          setAssigningVoyage(item);
                          setTargetBerth(item.assigned_berth || "Berth 1");
                          setForceOverride(false);
                          setModalOpen(true);
                        }}
                        className="px-2.5 py-1 rounded bg-blue-900/40 hover:bg-blue-800 text-sail-cyan text-[11px] border border-blue-600/40 font-semibold transition-all"
                      >
                        Assign Berth
                      </button>
                    </td>
                  </tr>
                ))
              )}
            </tbody>
          </table>
        </div>
      </div>

      {/* Berth Allocation & Reassignment Modal (z-index: 50+ with React Portal / fixed overlay) */}
      {modalOpen && assigningVoyage && (
        <div className="fixed inset-0 z-50 bg-black/75 backdrop-blur-sm flex items-center justify-center p-4">
          <div className="bg-sail-card border border-sail-border rounded-2xl w-full max-w-lg p-6 space-y-5 shadow-2xl relative">
            <div className="pb-3 border-b border-sail-border flex items-center justify-between">
              <div>
                <h3 className="text-base font-bold text-white flex items-center gap-2">
                  <Lock className="w-4 h-4 text-sail-cyan" />
                  Berth Allocation & Slot Assignment
                </h3>
                <p className="text-xs text-slate-400 font-mono mt-0.5">
                  Conflict-guarded allocation for {assigningVoyage.vessel_name} ({assigningVoyage.voyage_reference})
                </p>
              </div>
            </div>

            {assignError && (
              <div className="p-3 rounded-lg bg-rose-950/60 border border-rose-500/50 text-rose-200 text-xs flex items-start gap-2 font-mono">
                <AlertOctagon className="w-4 h-4 text-rose-400 shrink-0 mt-0.5" />
                <div>
                  <p className="font-bold">Berth Conflict Detected (HTTP 409)</p>
                  <p className="text-[11px] text-rose-300 mt-0.5">{assignError}</p>
                </div>
              </div>
            )}

            {assignSuccess && (
              <div className="p-3 rounded-lg bg-emerald-950/60 border border-emerald-500/50 text-emerald-200 text-xs flex items-center gap-2 font-mono">
                <CheckCircle2 className="w-4 h-4 text-emerald-400 shrink-0" />
                <span>{assignSuccess}</span>
              </div>
            )}

            <div className="space-y-4 font-mono text-xs">
              <div>
                <label className="text-slate-300 text-[11px] block mb-1">Target Discharge Berth:</label>
                <select
                  value={targetBerth}
                  onChange={(e) => setTargetBerth(e.target.value)}
                  className="w-full p-2.5 rounded-lg bg-sail-dark border border-sail-border text-white text-xs focus:outline-none focus:border-sail-cyan cursor-pointer"
                >
                  <option value="Berth 1">Berth 1 - Bulk Mechanized Coal Terminal</option>
                  <option value="Berth 2">Berth 2 - General Bulk Cargo Berth</option>
                  <option value="CQ-1">CQ-1 - Central Quay Mechanized Discharge</option>
                  <option value="CQ-2">CQ-2 - Central Quay Deep Draft Berth</option>
                  <option value="PIER-1">PIER-1 - Outer Deepwater Jetty</option>
                </select>
              </div>

              {/* Force Override Checkbox */}
              <div className="p-3 rounded-lg bg-sail-dark border border-sail-border flex items-start gap-3">
                <input
                  type="checkbox"
                  id="forceOverride"
                  checked={forceOverride}
                  onChange={(e) => setForceOverride(e.target.checked)}
                  className="mt-0.5 cursor-pointer rounded border-sail-border text-sail-cyan"
                />
                <label htmlFor="forceOverride" className="cursor-pointer text-[11px] text-slate-300">
                  <span className="font-bold text-white block">Emergency Force Override (`force=true`)</span>
                  Bypasses 409 Conflict validation and enables double-banking or parallel crane operations if approved by Port Logistics Officer.
                </label>
              </div>
            </div>

            {/* Action Buttons */}
            <div className="flex items-center justify-end gap-3 pt-3 border-t border-sail-border font-mono text-xs">
              <button
                onClick={() => {
                  setModalOpen(false);
                  setAssigningVoyage(null);
                  setAssignError(null);
                }}
                disabled={assigning}
                className="px-4 py-2 rounded-lg bg-sail-dark hover:bg-sail-border text-slate-300 border border-sail-border transition-all"
              >
                Cancel
              </button>
              <button
                onClick={handleAssignBerth}
                disabled={assigning}
                className="px-4 py-2 rounded-lg bg-blue-900/80 hover:bg-blue-800 text-sail-cyan border border-blue-600/50 font-bold transition-all disabled:opacity-50 flex items-center gap-1.5"
              >
                {assigning ? (
                  <>
                    <RefreshCw className="w-3.5 h-3.5 animate-spin" />
                    <span>Allocating...</span>
                  </>
                ) : (
                  <>
                    <CheckCircle2 className="w-3.5 h-3.5" />
                    <span>Confirm Berth Allocation</span>
                  </>
                )}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
