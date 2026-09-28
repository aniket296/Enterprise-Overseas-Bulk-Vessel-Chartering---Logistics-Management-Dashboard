"use client";

import React, { useEffect, useState, useCallback } from "react";
import { useApp } from "@/context/AppContext";
import { logisticsApi } from "@/lib/api-client";
import {
  ActiveFleetVoyage,
  LaytimeBalanceSnapshot,
  LaytimeSettlementRecord,
} from "@/types/dashboard";
import {
  FileSpreadsheet,
  DollarSign,
  Clock,
  CheckCircle2,
  AlertTriangle,
  RefreshCw,
  Lock,
  FileCheck,
  Scale,
  ShieldCheck,
  Building2,
  Ship,
  ArrowRight,
  TrendingDown,
  TrendingUp,
  AlertOctagon,
} from "lucide-react";

export default function LaytimeDashboardPage() {
  const { role, refreshTrigger } = useApp();
  const [voyages, setVoyages] = useState<ActiveFleetVoyage[]>([]);
  const [selectedVoyageId, setSelectedVoyageId] = useState<string>("");
  const [balance, setBalance] = useState<LaytimeBalanceSnapshot | null>(null);
  const [settlement, setSettlement] = useState<LaytimeSettlementRecord | null>(null);
  const [loading, setLoading] = useState<boolean>(true);
  const [loadingDetail, setLoadingDetail] = useState<boolean>(false);
  const [error, setError] = useState<string | null>(null);
  const [detailError, setDetailError] = useState<string | null>(null);

  // Settlement Form State
  const [settlementNotes, setSettlementNotes] = useState<string>("");
  const [settleOutcome, setSettleOutcome] = useState<string>("");
  const [submittingSettlement, setSubmittingSettlement] = useState<boolean>(false);
  const [settlementSuccessMessage, setSettlementSuccessMessage] = useState<string | null>(null);

  // 1. Fetch Voyages list
  const fetchVoyages = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const res = await logisticsApi.getActiveFleet({}, role);
      setVoyages(res.fleet);
      if (res.fleet.length > 0 && !selectedVoyageId) {
        setSelectedVoyageId(res.fleet[0].voyage_id);
      }
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : "Failed to load voyages";
      setError(msg);
    } finally {
      setLoading(false);
    }
  }, [role, selectedVoyageId]);

  useEffect(() => {
    fetchVoyages();
  }, [fetchVoyages, refreshTrigger]);

  // 2. Fetch selected voyage's laytime balance & authoritative settlement
  const fetchLaytimeDetails = useCallback(
    async (voyageId: string) => {
      if (!voyageId) return;
      setLoadingDetail(true);
      setDetailError(null);
      setSettlementSuccessMessage(null);
      try {
        // Try fetching live laytime balance
        try {
          const bal = await logisticsApi.getLaytimeBalance(voyageId, role);
          setBalance(bal);
        } catch (err: unknown) {
          setBalance(null);
          const msg = err instanceof Error ? err.message : "Prerequisite SOF missing";
          setDetailError(`Laytime Balance Notice: ${msg}`);
        }

        // Try fetching saved authoritative settlement
        try {
          const sett = await logisticsApi.getSettlement(voyageId, role);
          setSettlement(sett);
        } catch {
          // 404 is normal if not settled yet
          setSettlement(null);
        }
      } finally {
        setLoadingDetail(false);
      }
    },
    [role]
  );

  useEffect(() => {
    if (selectedVoyageId) {
      fetchLaytimeDetails(selectedVoyageId);
    }
  }, [selectedVoyageId, fetchLaytimeDetails, refreshTrigger]);

  const handlePostSettlement = async (outcomeOverride?: string) => {
    if (!selectedVoyageId) return;
    setSubmittingSettlement(true);
    setSettlementSuccessMessage(null);
    try {
      const payload: { outcome?: string; settlement_notes?: string } = {
        settlement_notes: settlementNotes || "Audited and verified against BIMCO Laytime 2013 rules.",
      };
      if (outcomeOverride) {
        payload.outcome = outcomeOverride;
      }
      const updated = await logisticsApi.postSettlement(selectedVoyageId, payload, role);
      setSettlement(updated);
      setSettlementSuccessMessage(
        outcomeOverride === "SETTLED"
          ? "Authoritative settlement locked successfully."
          : outcomeOverride === "DISPUTED"
          ? "Settlement marked as DISPUTED. Audit escalation active."
          : "Laytime calculation persisted."
      );
      fetchLaytimeDetails(selectedVoyageId);
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : "Settlement operation failed";
      setDetailError(msg);
    } finally {
      setSubmittingSettlement(false);
    }
  };

  const selectedVoyage = voyages.find((v) => v.voyage_id === selectedVoyageId);

  const formatNumber = (val: string | number | null | undefined, decimals = 2) => {
    if (val === undefined || val === null) return "0.00";
    const num = typeof val === "string" ? parseFloat(val) : val;
    return isNaN(num)
      ? "0.00"
      : num.toLocaleString("en-US", { minimumFractionDigits: decimals, maximumFractionDigits: decimals });
  };

  return (
    <div className="space-y-6">
      {/* Page Header */}
      <div className="flex flex-col md:flex-row md:items-center justify-between gap-4 pb-4 border-b border-sail-border">
        <div>
          <div className="flex items-center gap-2 mb-1">
            <span className="text-xs uppercase tracking-widest text-emerald-400 font-mono font-semibold">
              Financial Settlement
            </span>
            <span className="text-slate-500">|</span>
            <span className="text-xs text-slate-400 font-mono">
              BIMCO Laytime 2013 Compliance Engine
            </span>
          </div>
          <h1 className="text-2xl font-bold tracking-tight text-white flex items-center gap-2">
            <FileSpreadsheet className="w-6 h-6 text-emerald-400" />
            Laytime Settlement & Demurrage / Despatch Workbench
          </h1>
        </div>

        {/* Selected Vessel Selector Dropdown */}
        <div className="flex items-center gap-3">
          <div className="flex items-center gap-2 bg-sail-card px-3 py-1.5 rounded-lg border border-sail-border">
            <Ship className="w-4 h-4 text-sail-cyan" />
            <select
              value={selectedVoyageId}
              onChange={(e) => setSelectedVoyageId(e.target.value)}
              className="bg-transparent text-xs text-slate-200 focus:outline-none cursor-pointer pr-1 font-mono"
            >
              {voyages.map((v) => (
                <option key={v.voyage_id} value={v.voyage_id} className="bg-sail-dark text-slate-200">
                  {v.vessel_name} ({v.voyage_reference}) - {v.discharge_port}
                </option>
              ))}
            </select>
          </div>
        </div>
      </div>

      {loading && !selectedVoyage && (
        <div className="py-20 flex flex-col items-center justify-center space-y-4">
          <RefreshCw className="w-8 h-8 text-emerald-400 animate-spin" />
          <p className="text-sm text-slate-400 font-mono">
            Loading charter party fixtures and laytime profiles...
          </p>
        </div>
      )}

      {error && (
        <div className="p-4 rounded-xl bg-rose-950/40 border border-rose-500/40 text-rose-200 text-xs">
          {error}
        </div>
      )}

      {selectedVoyage && (
        <>
          {/* Top Level Summary Cards for Selected Voyage */}
          <div className="grid grid-cols-1 md:grid-cols-4 gap-4">
            {/* Card 1: Net Financial Outcome */}
            <div className="p-4 rounded-xl bg-sail-card border border-sail-border space-y-2">
              <div className="flex items-center justify-between text-xs text-slate-400">
                <span>Net Settlement Balance</span>
                <Scale className="w-4 h-4 text-sail-cyan" />
              </div>
              <div className="flex items-baseline gap-2">
                <span
                  className={`text-2xl font-black font-mono ${
                    balance?.outcome === "DEMURRAGE"
                      ? "text-rose-400"
                      : balance?.outcome === "DESPATCH"
                      ? "text-emerald-400"
                      : "text-slate-200"
                  }`}
                >
                  ${formatNumber(balance ? balance.net_settlement_usd : "0.00")}
                </span>
              </div>
              <div className="pt-1 flex items-center justify-between text-[11px] font-mono">
                <span className="text-slate-400">Claim Type:</span>
                <span
                  className={`px-2 py-0.5 rounded font-bold ${
                    balance?.outcome === "DEMURRAGE"
                      ? "bg-rose-500/20 text-rose-300 border border-rose-500/30"
                      : balance?.outcome === "DESPATCH"
                      ? "bg-emerald-500/20 text-emerald-300 border border-emerald-500/30"
                      : "bg-blue-500/20 text-blue-300 border border-blue-500/30"
                  }`}
                >
                  {balance ? balance.outcome : "PENDING SOF"}
                </span>
              </div>
            </div>

            {/* Card 2: Allowed Laytime Hours */}
            <div className="p-4 rounded-xl bg-sail-card border border-sail-border space-y-2">
              <div className="flex items-center justify-between text-xs text-slate-400">
                <span>Allowed Laytime</span>
                <Clock className="w-4 h-4 text-emerald-400" />
              </div>
              <div className="flex items-baseline gap-2">
                <span className="text-2xl font-black text-white font-mono">
                  {formatNumber(balance ? balance.allowed_hours : selectedVoyage.allowed_laytime_hours, 1)}
                </span>
                <span className="text-xs text-slate-400 font-mono">hours</span>
              </div>
              <p className="text-[11px] text-slate-400 font-mono">
                Terms: <strong className="text-sail-cyan">{selectedVoyage.laytime_terms}</strong>
              </p>
            </div>

            {/* Card 3: Counted Port Time Used */}
            <div className="p-4 rounded-xl bg-sail-card border border-sail-border space-y-2">
              <div className="flex items-center justify-between text-xs text-slate-400">
                <span>Counted Laytime</span>
                <TrendingUp className="w-4 h-4 text-amber-400" />
              </div>
              <div className="flex items-baseline gap-2">
                <span className="text-2xl font-black text-amber-300 font-mono">
                  {formatNumber(balance ? balance.counted_hours : "0.00", 1)}
                </span>
                <span className="text-xs text-slate-400 font-mono">hours</span>
              </div>
              <p className="text-[11px] text-slate-400 font-mono">
                Excepted (Rain/Shifts): <strong className="text-slate-300">{formatNumber(balance?.excepted_hours || "0", 1)}h</strong>
              </p>
            </div>

            {/* Card 4: Agreed Demurrage / Despatch Rates */}
            <div className="p-4 rounded-xl bg-sail-card border border-sail-border space-y-2">
              <div className="flex items-center justify-between text-xs text-slate-400">
                <span>Agreed Daily Rates</span>
                <DollarSign className="w-4 h-4 text-sail-gold" />
              </div>
              <div className="text-xs font-mono space-y-1">
                <div className="flex items-center justify-between">
                  <span className="text-slate-400">Demurrage:</span>
                  <span className="text-rose-400 font-bold">
                    ${formatNumber(selectedVoyage.demurrage_rate_usd_day)}/day
                  </span>
                </div>
                <div className="flex items-center justify-between">
                  <span className="text-slate-400">Despatch:</span>
                  <span className="text-emerald-400 font-bold">
                    ${formatNumber(selectedVoyage.despatch_rate_usd_day)}/day
                  </span>
                </div>
              </div>
            </div>
          </div>

          {detailError && (
            <div className="p-3.5 rounded-xl bg-blue-950/40 border border-blue-500/30 text-blue-200 text-xs flex items-center gap-2">
              <AlertTriangle className="w-4 h-4 text-amber-400 shrink-0" />
              <span>{detailError}</span>
            </div>
          )}

          {settlementSuccessMessage && (
            <div className="p-3.5 rounded-xl bg-emerald-950/40 border border-emerald-500/30 text-emerald-200 text-xs flex items-center gap-2">
              <CheckCircle2 className="w-4 h-4 text-emerald-400 shrink-0" />
              <span>{settlementSuccessMessage}</span>
            </div>
          )}

          {/* Main Inspection Grid: Mathematical Breakdown & Settlement Workflow Panel */}
          <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
            {/* Left 2 Cols: Mathematical Audit & Time Allocation Breakdown */}
            <div className="lg:col-span-2 space-y-6">
              {/* Laytime Progress Visual Meter */}
              <div className="p-5 rounded-xl bg-sail-card border border-sail-border space-y-4">
                <div className="flex items-center justify-between">
                  <h2 className="text-sm font-bold text-white uppercase tracking-wider flex items-center gap-2">
                    <Clock className="w-4 h-4 text-sail-cyan" />
                    Laytime Consumption & Port Timeline Segmentation
                  </h2>
                  <span className="text-xs font-mono text-slate-400">
                    BIMCO Strict Decimal Math
                  </span>
                </div>

                {balance ? (
                  <div className="space-y-3 font-mono text-xs">
                    <div className="flex items-center justify-between text-slate-300">
                      <span>Port Turnaround Timeline:</span>
                      <span>
                        Counted: <strong className="text-amber-300">{formatNumber(balance.counted_hours, 1)}h</strong> / Allowed: <strong className="text-emerald-400">{formatNumber(balance.allowed_hours, 1)}h</strong>
                      </span>
                    </div>

                    {/* Stacked Progress Bar */}
                    <div className="w-full h-4 rounded-full bg-sail-dark border border-sail-border flex overflow-hidden">
                      {/* Counted normal laytime portion */}
                      <div
                        className="bg-emerald-500 h-full transition-all"
                        style={{
                          width: `${Math.min(
                            100,
                            (parseFloat(balance.counted_hours) / (parseFloat(balance.allowed_hours || "1") || 1)) * 100
                          )}%`,
                        }}
                        title={`Counted: ${balance.counted_hours}h`}
                      />
                      {/* Overtime (Demurrage) portion */}
                      {parseFloat(balance.demurrage_hours) > 0 && (
                        <div
                          className="bg-rose-500 h-full animate-pulse transition-all"
                          style={{
                            width: `${Math.min(
                              50,
                              (parseFloat(balance.demurrage_hours) / (parseFloat(balance.allowed_hours || "1") || 1)) * 100
                            )}%`,
                          }}
                          title={`Demurrage: ${balance.demurrage_hours}h`}
                        />
                      )}
                    </div>

                    <div className="flex items-center justify-between text-[11px] text-slate-400 pt-1">
                      <div className="flex items-center gap-1.5">
                        <span className="w-2.5 h-2.5 rounded-full bg-emerald-500" />
                        <span>Allowed Port Window</span>
                      </div>
                      <div className="flex items-center gap-1.5">
                        <span className="w-2.5 h-2.5 rounded-full bg-rose-500" />
                        <span>Demurrage Incurred ({formatNumber(balance.demurrage_hours, 1)}h)</span>
                      </div>
                      <div className="flex items-center gap-1.5">
                        <span className="w-2.5 h-2.5 rounded-full bg-amber-400" />
                        <span>Rain / Shift Excepted ({formatNumber(balance.excepted_hours, 1)}h)</span>
                      </div>
                    </div>
                  </div>
                ) : (
                  <p className="text-xs text-slate-400 py-4 font-mono">
                    No Statement of Facts (SOF) events recorded yet for this voyage. Ingest SOF via port agent portal.
                  </p>
                )}
              </div>

              {/* Audit Formula & Decimal Invariance Proof */}
              <div className="p-5 rounded-xl bg-sail-card border border-sail-border space-y-3 font-mono text-xs">
                <div className="flex items-center justify-between pb-2 border-b border-sail-border">
                  <span className="text-xs font-bold text-white uppercase tracking-wider flex items-center gap-2">
                    <ShieldCheck className="w-4 h-4 text-emerald-400" />
                    BIMCO Statutory Audit Formula & Proof
                  </span>
                  <span className="text-[10px] text-emerald-400 px-2 py-0.5 rounded bg-emerald-950/60 border border-emerald-500/30">
                    AIR-GAPPED DECIMAL INVARIANCE
                  </span>
                </div>

                <div className="space-y-2 text-slate-300">
                  <div className="p-3 rounded-lg bg-sail-dark border border-sail-border space-y-1">
                    <span className="text-[11px] text-slate-400 uppercase">Allowed Laytime Derivation:</span>
                    <p className="text-white font-bold text-xs">
                      {balance?.allowed_laytime_formula ||
                        `${formatNumber(selectedVoyage.quantity_loaded_mt, 0)} MT / Discharge Rate MT/day * 24h = ${formatNumber(
                          selectedVoyage.allowed_laytime_hours,
                          2
                        )} hours`}
                    </p>
                  </div>

                  <div className="p-3 rounded-lg bg-sail-dark border border-sail-border space-y-1">
                    <span className="text-[11px] text-slate-400 uppercase">Settlement Calculation Formula:</span>
                    <p className="text-sail-cyan font-bold text-xs">
                      {balance?.audit_formula || "Awaiting discharge milestone completion and SOF normalization."}
                    </p>
                  </div>
                </div>

                <div className="pt-2 flex items-center justify-between text-[11px] text-slate-400">
                  <span>SOF Timeline Time Slices Analyzed: <strong>{balance?.slice_count || 0} intervals</strong></span>
                  <span>Once on Demurrage, Always on Demurrage: <strong>Active</strong></span>
                </div>
              </div>
            </div>

            {/* Right Col: Authoritative Settlement & Locking Action Panel */}
            <div className="rounded-xl bg-sail-card border border-sail-border p-5 flex flex-col justify-between space-y-5">
              <div className="space-y-4">
                <div className="pb-3 border-b border-sail-border">
                  <div className="flex items-center justify-between mb-1">
                    <span className="text-xs font-bold text-white uppercase tracking-wider flex items-center gap-1.5">
                      <Lock className="w-4 h-4 text-sail-gold" />
                      Authoritative Settlement
                    </span>
                    {settlement && (
                      <span className="text-[10px] px-2 py-0.5 rounded bg-emerald-950 text-emerald-300 border border-emerald-500/40 font-mono font-bold">
                        {settlement.outcome}
                      </span>
                    )}
                  </div>
                  <p className="text-[11px] text-slate-400">
                    Officially execute or lock demurrage/despatch claims with the shipowner.
                  </p>
                </div>

                {settlement ? (
                  <div className="p-3.5 rounded-lg bg-sail-dark border border-sail-border space-y-2 text-xs font-mono">
                    <div className="flex items-center justify-between">
                      <span className="text-slate-400">Settlement ID:</span>
                      <span className="text-slate-200">{settlement.calc_id.slice(0, 8)}...</span>
                    </div>
                    <div className="flex items-center justify-between">
                      <span className="text-slate-400">Final Outcome:</span>
                      <span className="text-white font-bold">{settlement.outcome}</span>
                    </div>
                    <div className="flex items-center justify-between">
                      <span className="text-slate-400">Net Payable:</span>
                      <span className="text-rose-400 font-bold">${formatNumber(settlement.net_settlement_usd)}</span>
                    </div>
                    <div className="flex items-center justify-between">
                      <span className="text-slate-400">Settled By:</span>
                      <span className="text-slate-300">{settlement.settled_by_username || "FINANCE_OFFICER"}</span>
                    </div>
                    <div className="pt-2 border-t border-sail-border/60">
                      <span className="text-[11px] text-slate-400">Notes:</span>
                      <p className="text-slate-300 text-[11px] mt-0.5 italic">
                        &quot;{settlement.settlement_notes || "Authoritative claim reconciled."}&quot;
                      </p>
                    </div>
                  </div>
                ) : (
                  <div className="p-3.5 rounded-lg bg-sail-dark border border-sail-border space-y-2 text-xs text-slate-400 font-mono">
                    <p>No authoritative settlement record recorded in the ledger yet.</p>
                    <p className="text-[11px] text-slate-500">
                      Discharge completed milestone is required before locking claim figures.
                    </p>
                  </div>
                )}

                {/* Settlement Notes Input */}
                <div className="space-y-1.5 font-mono text-xs">
                  <label className="text-slate-300 text-[11px] block">Settlement Notes / Charterer Voucher Ref:</label>
                  <textarea
                    rows={3}
                    placeholder="Enter formal claim agreement notes, weather allowance references, or arbitration remarks..."
                    value={settlementNotes}
                    onChange={(e) => setSettlementNotes(e.target.value)}
                    className="w-full p-2.5 rounded-lg bg-sail-dark border border-sail-border text-xs text-white placeholder-slate-500 focus:outline-none focus:border-sail-cyan"
                  />
                </div>
              </div>

              {/* Action Buttons */}
              <div className="space-y-2 pt-2 border-t border-sail-border font-mono text-xs">
                <button
                  onClick={() => handlePostSettlement()}
                  disabled={submittingSettlement}
                  className="w-full py-2.5 rounded-lg bg-blue-900/60 hover:bg-blue-800 text-sail-cyan border border-blue-600/40 font-bold transition-all disabled:opacity-50"
                >
                  {submittingSettlement ? "Processing..." : "Compute / Save Settlement Draft"}
                </button>

                <div className="grid grid-cols-2 gap-2">
                  <button
                    onClick={() => handlePostSettlement("SETTLED")}
                    disabled={submittingSettlement}
                    className="py-2 rounded-lg bg-emerald-950/60 hover:bg-emerald-900 text-emerald-300 border border-emerald-600/40 font-bold transition-all disabled:opacity-50 flex items-center justify-center gap-1.5"
                  >
                    <Lock className="w-3.5 h-3.5" />
                    <span>Lock (SETTLED)</span>
                  </button>

                  <button
                    onClick={() => handlePostSettlement("DISPUTED")}
                    disabled={submittingSettlement}
                    className="py-2 rounded-lg bg-rose-950/60 hover:bg-rose-900 text-rose-300 border border-rose-600/40 font-bold transition-all disabled:opacity-50 flex items-center justify-center gap-1.5"
                  >
                    <AlertOctagon className="w-3.5 h-3.5" />
                    <span>Mark DISPUTED</span>
                  </button>
                </div>
              </div>
            </div>
          </div>

          {/* Fleet-Wide Laytime & Demurrage Exposure Ledger */}
          <div className="rounded-xl bg-sail-card border border-sail-border overflow-hidden">
            <div className="p-4 border-b border-sail-border flex items-center justify-between">
              <h2 className="text-sm font-bold text-white uppercase tracking-wider flex items-center gap-2">
                <FileCheck className="w-4 h-4 text-emerald-400" />
                Enterprise Laytime Exposure Ledger (All Active Vessels)
              </h2>
              <span className="text-xs text-slate-400 font-mono">
                {voyages.length} voyages tracked
              </span>
            </div>

            <div className="overflow-x-auto">
              <table className="w-full text-left text-xs font-mono">
                <thead className="bg-sail-dark text-slate-400 border-b border-sail-border uppercase">
                  <tr>
                    <th className="py-3 px-4">Voyage Ref</th>
                    <th className="py-3 px-4">Vessel Name</th>
                    <th className="py-3 px-4">Contract Ref</th>
                    <th className="py-3 px-4">Port</th>
                    <th className="py-3 px-4">Plant</th>
                    <th className="py-3 px-4">Terms</th>
                    <th className="py-3 px-4 text-right">Allowed (h)</th>
                    <th className="py-3 px-4 text-right">Demurrage Rate</th>
                    <th className="py-3 px-4 text-center">Status</th>
                    <th className="py-3 px-4 text-center">Action</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-sail-border">
                  {voyages.map((v) => (
                    <tr
                      key={v.voyage_id}
                      className={`hover:bg-sail-dark/60 transition-colors ${
                        v.voyage_id === selectedVoyageId ? "bg-sail-dark/90 border-l-2 border-emerald-400" : ""
                      }`}
                    >
                      <td className="py-3 px-4 font-bold text-white">{v.voyage_reference}</td>
                      <td className="py-3 px-4 text-slate-300">{v.vessel_name}</td>
                      <td className="py-3 px-4 text-slate-400">{v.contract_reference}</td>
                      <td className="py-3 px-4 text-sail-cyan">{v.discharge_port}</td>
                      <td className="py-3 px-4 text-emerald-400">{v.charterer_plant}</td>
                      <td className="py-3 px-4 text-slate-300">{v.laytime_terms}</td>
                      <td className="py-3 px-4 text-right text-slate-200">
                        {formatNumber(v.allowed_laytime_hours, 1)}
                      </td>
                      <td className="py-3 px-4 text-right text-rose-400 font-bold">
                        ${formatNumber(v.demurrage_rate_usd_day, 0)}/d
                      </td>
                      <td className="py-3 px-4 text-center">
                        <span className="px-2 py-0.5 rounded text-[10px] font-bold bg-sail-dark text-slate-300 border border-sail-border">
                          {v.voyage_status}
                        </span>
                      </td>
                      <td className="py-3 px-4 text-center">
                        <button
                          onClick={() => setSelectedVoyageId(v.voyage_id)}
                          className="px-2.5 py-1 rounded bg-blue-900/40 hover:bg-blue-800 text-sail-cyan text-[11px] border border-blue-600/40 transition-all font-semibold"
                        >
                          Audit
                        </button>
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
