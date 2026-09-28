"use client";

import React, { useEffect, useState, useCallback, useMemo } from "react";
import { useApp } from "@/context/AppContext";
import { logisticsApi } from "@/lib/api-client";
import {
  CharterContractSummary,
  ContractPerformance,
  CarrierScorecard,
} from "@/types/dashboard";
import {
  FileSignature,
  Award,
  ShieldCheck,
  Search,
  Filter,
  RefreshCw,
  Sparkles,
  CheckCircle2,
  AlertTriangle,
  Building2,
  DollarSign,
  TrendingUp,
  TrendingDown,
  Clock,
  Ship,
  Scale,
  FileSpreadsheet,
  AlertOctagon,
  ChevronRight,
  Database,
} from "lucide-react";

export default function ContractsPage() {
  const { role, selectedPlant, refreshTrigger } = useApp();
  const [activeTab, setActiveTab] = useState<"SCORECARDS" | "CONTRACTS">("SCORECARDS");

  // Carrier Scorecards state
  const [scorecards, setScorecards] = useState<CarrierScorecard[]>([]);
  const [carrierSearch, setCarrierSearch] = useState<string>("");

  // Contracts state
  const [contracts, setContracts] = useState<CharterContractSummary[]>([]);
  const [contractSearch, setContractSearch] = useState<string>("");
  const [selectedContractId, setSelectedContractId] = useState<string | null>(null);
  const [performance, setPerformance] = useState<ContractPerformance | null>(null);

  // General loading & message state
  const [loading, setLoading] = useState<boolean>(true);
  const [loadingPerf, setLoadingPerf] = useState<boolean>(false);
  const [error, setError] = useState<string | null>(null);
  const [actionSuccess, setActionSuccess] = useState<string | null>(null);
  const [seeding, setSeeding] = useState<boolean>(false);

  // 1. Fetch carrier scorecards and contracts
  const fetchData = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const [cards, conts] = await Promise.all([
        logisticsApi.getCarrierScorecards(role).catch(() => []),
        logisticsApi.getContracts(selectedPlant === "ALL" ? undefined : selectedPlant, role).catch(() => []),
      ]);
      setScorecards(cards);
      setContracts(conts);
      if (conts.length > 0 && !selectedContractId) {
        setSelectedContractId(conts[0].contract_id);
      }
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : "Failed to load contract data";
      setError(msg);
    } finally {
      setLoading(false);
    }
  }, [role, selectedPlant, selectedContractId]);

  useEffect(() => {
    fetchData();
  }, [fetchData, refreshTrigger]);

  // 2. Fetch performance for selected contract
  const fetchPerformance = useCallback(
    async (contractId: string) => {
      setLoadingPerf(true);
      try {
        const perf = await logisticsApi.getContractPerformance(contractId, role);
        setPerformance(perf);
      } catch (err: unknown) {
        console.error(err);
        setPerformance(null);
      } finally {
        setLoadingPerf(false);
      }
    },
    [role]
  );

  useEffect(() => {
    if (selectedContractId) {
      fetchPerformance(selectedContractId);
    }
  }, [selectedContractId, fetchPerformance, refreshTrigger]);

  // Seed Baltic Fixtures action
  const handleSeedBaltic = async () => {
    setSeeding(true);
    setActionSuccess(null);
    try {
      const res = await logisticsApi.seedBalticFixtures(role);
      setActionSuccess(res.message);
      fetchData();
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : "Failed to seed fixtures";
      setError(msg);
    } finally {
      setSeeding(false);
    }
  };

  // Filtered lists
  const filteredScorecards = useMemo(() => {
    return scorecards.filter((s) => {
      if (!carrierSearch) return true;
      return s.owner_name.toLowerCase().includes(carrierSearch.toLowerCase());
    });
  }, [scorecards, carrierSearch]);

  const filteredContracts = useMemo(() => {
    return contracts.filter((c) => {
      if (!contractSearch) return true;
      const q = contractSearch.toLowerCase();
      return (
        c.contract_reference.toLowerCase().includes(q) ||
        c.vessel_name.toLowerCase().includes(q) ||
        c.charterer_plant.toLowerCase().includes(q) ||
        c.cargo_type.toLowerCase().includes(q)
      );
    });
  }, [contracts, contractSearch]);

  const getTierBadge = (tier: string) => {
    switch (tier) {
      case "TIER_1_PREFERRED":
        return <span className="px-2 py-0.5 rounded text-[11px] font-bold bg-emerald-500/20 text-emerald-300 border border-emerald-500/30">TIER-1 PREFERRED</span>;
      case "TIER_2_ACCEPTABLE":
        return <span className="px-2 py-0.5 rounded text-[11px] font-bold bg-blue-500/20 text-blue-300 border border-blue-500/30">TIER-2 ACCEPTABLE</span>;
      case "TIER_3_PROBATION":
        return <span className="px-2 py-0.5 rounded text-[11px] font-bold bg-amber-500/20 text-amber-300 border border-amber-500/30">TIER-3 PROBATION</span>;
      case "BLACKLISTED":
      default:
        return <span className="px-2 py-0.5 rounded text-[11px] font-bold bg-rose-500/20 text-rose-300 border border-rose-500/30 animate-pulse">BLACKLISTED</span>;
    }
  };

  const getFulfillmentBadge = (status: string) => {
    switch (status) {
      case "COMPLIANT":
        return <span className="px-2 py-0.5 rounded text-[11px] font-bold bg-emerald-500/20 text-emerald-300 border border-emerald-500/30">COMPLIANT</span>;
      case "PENDING":
        return <span className="px-2 py-0.5 rounded text-[11px] font-bold bg-blue-500/20 text-blue-300 border border-blue-500/30">PENDING</span>;
      case "UNDER_FULFILLED":
        return <span className="px-2 py-0.5 rounded text-[11px] font-bold bg-amber-500/20 text-amber-300 border border-amber-500/30">UNDER-FULFILLED</span>;
      case "OVER_FULFILLED":
      default:
        return <span className="px-2 py-0.5 rounded text-[11px] font-bold bg-purple-500/20 text-purple-300 border border-purple-500/30">OVER-FULFILLED</span>;
    }
  };

  const formatNumber = (val: string | number | null | undefined, decimals = 0) => {
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
            <span className="text-xs uppercase tracking-widest text-purple-400 font-mono font-semibold">
              Procurement & Sanctions
            </span>
            <span className="text-slate-500">|</span>
            <span className="text-xs text-slate-400 font-mono">
              BIMCO Fixture Compliance & Reliability Index
            </span>
          </div>
          <h1 className="text-2xl font-bold tracking-tight text-white flex items-center gap-2">
            <Award className="w-6 h-6 text-purple-400" />
            Charter Fixtures, Carrier Reliability & Sanction Compliance
          </h1>
        </div>

        {/* Action Button: Seed Baltic Standard Fixtures */}
        <div className="flex items-center gap-3">
          <button
            onClick={handleSeedBaltic}
            disabled={seeding}
            className="inline-flex items-center gap-2 px-3 py-1.5 rounded-lg bg-purple-950/60 hover:bg-purple-900 text-purple-300 border border-purple-500/40 text-xs font-mono font-bold transition-all disabled:opacity-50"
          >
            <Database className="w-3.5 h-3.5" />
            <span>{seeding ? "Seeding..." : "Seed Baltic Fixtures"}</span>
          </button>
        </div>
      </div>

      {actionSuccess && (
        <div className="p-3.5 rounded-xl bg-emerald-950/40 border border-emerald-500/30 text-emerald-200 text-xs flex items-center gap-2 font-mono">
          <CheckCircle2 className="w-4 h-4 text-emerald-400 shrink-0" />
          <span>{actionSuccess}</span>
        </div>
      )}

      {error && (
        <div className="p-4 rounded-xl bg-rose-950/40 border border-rose-500/40 text-rose-200 text-xs font-mono">
          {error}
        </div>
      )}

      {/* Tabs Toolbar */}
      <div className="flex items-center gap-2 border-b border-sail-border pb-2">
        <button
          onClick={() => setActiveTab("SCORECARDS")}
          className={`flex items-center gap-2 px-4 py-2 rounded-lg text-xs font-bold font-mono transition-all ${
            activeTab === "SCORECARDS"
              ? "bg-purple-900/60 text-purple-300 border border-purple-500/40 shadow-md"
              : "text-slate-400 hover:text-white hover:bg-sail-card"
          }`}
        >
          <Award className="w-4 h-4" />
          <span>Carrier Reliability Scorecards ({scorecards.length})</span>
        </button>

        <button
          onClick={() => setActiveTab("CONTRACTS")}
          className={`flex items-center gap-2 px-4 py-2 rounded-lg text-xs font-bold font-mono transition-all ${
            activeTab === "CONTRACTS"
              ? "bg-sail-accent/40 text-sail-cyan border border-sail-cyan/40 shadow-md"
              : "text-slate-400 hover:text-white hover:bg-sail-card"
          }`}
        >
          <FileSignature className="w-4 h-4" />
          <span>Charter Fixtures & Fulfillment ({contracts.length})</span>
        </button>
      </div>

      {loading && (
        <div className="py-20 flex flex-col items-center justify-center space-y-4">
          <RefreshCw className="w-8 h-8 text-purple-400 animate-spin" />
          <p className="text-sm text-slate-400 font-mono">
            Evaluating carrier scorecards, tolerance bounds, and charter fulfillment...
          </p>
        </div>
      )}

      {/* TAB 1: CARRIER RELIABILITY SCORECARDS */}
      {!loading && activeTab === "SCORECARDS" && (
        <div className="space-y-6">
          {/* Summary Metric Cards */}
          <div className="grid grid-cols-1 md:grid-cols-4 gap-4">
            <div className="p-4 rounded-xl bg-sail-card border border-sail-border space-y-1">
              <span className="text-xs text-slate-400 font-mono">Preferred Carriers</span>
              <p className="text-2xl font-bold text-emerald-400 font-mono">
                {scorecards.filter((s) => s.reliability_tier === "TIER_1_PREFERRED").length} / {scorecards.length}
              </p>
              <p className="text-[11px] text-slate-500 font-mono">Tier-1 Approved</p>
            </div>
            <div className="p-4 rounded-xl bg-sail-card border border-sail-border space-y-1">
              <span className="text-xs text-slate-400 font-mono">Average Reliability</span>
              <p className="text-2xl font-bold text-sail-cyan font-mono">
                {scorecards.length > 0
                  ? (scorecards.reduce((acc, s) => acc + s.carrier_reliability_score, 0) / scorecards.length).toFixed(1)
                  : "0.0"}
                %
              </p>
              <p className="text-[11px] text-slate-500 font-mono">Composite Index</p>
            </div>
            <div className="p-4 rounded-xl bg-sail-card border border-sail-border space-y-1">
              <span className="text-xs text-slate-400 font-mono">Total Lifted Cargo</span>
              <p className="text-2xl font-bold text-white font-mono">
                {formatNumber(scorecards.reduce((acc, s) => acc + parseFloat(s.total_lifted_mt || "0"), 0))} MT
              </p>
              <p className="text-[11px] text-slate-500 font-mono">Across all carriers</p>
            </div>
            <div className="p-4 rounded-xl bg-sail-card border border-sail-border space-y-1">
              <span className="text-xs text-slate-400 font-mono">Carriers On Probation</span>
              <p className="text-2xl font-bold text-amber-400 font-mono">
                {scorecards.filter((s) => s.reliability_tier === "TIER_3_PROBATION" || s.reliability_tier === "BLACKLISTED").length}
              </p>
              <p className="text-[11px] text-slate-500 font-mono">Under Review</p>
            </div>
          </div>

          {/* Search Bar */}
          <div className="flex items-center gap-3">
            <div className="relative flex-1 max-w-md">
              <Search className="w-4 h-4 text-slate-400 absolute left-3 top-2.5" />
              <input
                type="text"
                placeholder="Search carrier or vessel owner name..."
                value={carrierSearch}
                onChange={(e) => setCarrierSearch(e.target.value)}
                className="w-full pl-9 pr-3 py-1.5 rounded-lg bg-sail-dark border border-sail-border text-xs text-white placeholder-slate-500 focus:outline-none focus:border-purple-400 font-mono"
              />
            </div>
          </div>

          {/* Carrier Scorecards Ranking Table */}
          <div className="rounded-xl bg-sail-card border border-sail-border overflow-hidden">
            <div className="p-4 border-b border-sail-border flex items-center justify-between">
              <h2 className="text-sm font-bold text-white uppercase tracking-wider flex items-center gap-2">
                <ShieldCheck className="w-4 h-4 text-purple-400" />
                Vessel Owner / Carrier Reliability League Table
              </h2>
              <span className="text-xs text-slate-400 font-mono">
                Ranked by Composite Reliability Score
              </span>
            </div>

            <div className="overflow-x-auto">
              <table className="w-full text-left text-xs font-mono">
                <thead className="bg-sail-dark text-slate-400 border-b border-sail-border uppercase">
                  <tr>
                    <th className="py-3 px-4">Carrier / Vessel Owner</th>
                    <th className="py-3 px-4 text-center">Reliability Score</th>
                    <th className="py-3 px-4 text-center">Tier Status</th>
                    <th className="py-3 px-4 text-right">Voyages</th>
                    <th className="py-3 px-4 text-right">Fleet Size</th>
                    <th className="py-3 px-4 text-right">Lifted Tonnage (MT)</th>
                    <th className="py-3 px-4 text-right">Gross Freight</th>
                    <th className="py-3 px-4 text-center">On-Time Laycan %</th>
                    <th className="py-3 px-4 text-center">Demurrage Rate %</th>
                    <th className="py-3 px-4 text-center">Avg Shortage %</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-sail-border">
                  {filteredScorecards.length === 0 ? (
                    <tr>
                      <td colSpan={10} className="py-8 text-center text-slate-500">
                        No carrier scorecards recorded yet. Click &quot;Seed Baltic Fixtures&quot; above to initialize standard fixtures.
                      </td>
                    </tr>
                  ) : (
                    filteredScorecards.map((carrier) => (
                      <tr key={carrier.owner_name} className="hover:bg-sail-dark/60 transition-colors">
                        <td className="py-3 px-4 font-bold text-white flex items-center gap-2">
                          <Ship className="w-3.5 h-3.5 text-purple-400" />
                          <span>{carrier.owner_name}</span>
                        </td>
                        <td className="py-3 px-4 text-center">
                          <div className="inline-flex items-center gap-1.5 font-black text-sm">
                            <span
                              className={
                                carrier.carrier_reliability_score >= 85
                                  ? "text-emerald-400"
                                  : carrier.carrier_reliability_score >= 70
                                  ? "text-blue-300"
                                  : carrier.carrier_reliability_score >= 50
                                  ? "text-amber-400"
                                  : "text-rose-400"
                              }
                            >
                              {carrier.carrier_reliability_score}
                            </span>
                            <span className="text-[10px] text-slate-500">/ 100</span>
                          </div>
                        </td>
                        <td className="py-3 px-4 text-center">
                          {getTierBadge(carrier.reliability_tier)}
                        </td>
                        <td className="py-3 px-4 text-right text-slate-200">
                          {carrier.total_voyages}
                        </td>
                        <td className="py-3 px-4 text-right text-slate-400">
                          {carrier.unique_vessels_count} vessels
                        </td>
                        <td className="py-3 px-4 text-right text-white font-medium">
                          {formatNumber(carrier.total_lifted_mt)}
                        </td>
                        <td className="py-3 px-4 text-right text-slate-300">
                          ${formatNumber(carrier.total_gross_freight_usd)}
                        </td>
                        <td className="py-3 px-4 text-center font-bold text-emerald-400">
                          {carrier.laycan_adherence_pct}%
                        </td>
                        <td className="py-3 px-4 text-center text-amber-300">
                          {carrier.demurrage_incidence_pct}%
                        </td>
                        <td className="py-3 px-4 text-center text-slate-400">
                          {carrier.average_shortage_pct}%
                        </td>
                      </tr>
                    ))
                  )}
                </tbody>
              </table>
            </div>
          </div>
        </div>
      )}

      {/* TAB 2: CHARTER CONTRACTS & FULFILLMENT */}
      {!loading && activeTab === "CONTRACTS" && (
        <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
          {/* Left 2 Cols: Contracts Roster */}
          <div className="lg:col-span-2 space-y-4">
            <div className="flex items-center gap-3">
              <div className="relative flex-1 max-w-md">
                <Search className="w-4 h-4 text-slate-400 absolute left-3 top-2.5" />
                <input
                  type="text"
                  placeholder="Search contract ref, vessel, plant, cargo..."
                  value={contractSearch}
                  onChange={(e) => setContractSearch(e.target.value)}
                  className="w-full pl-9 pr-3 py-1.5 rounded-lg bg-sail-dark border border-sail-border text-xs text-white placeholder-slate-500 focus:outline-none focus:border-sail-cyan font-mono"
                />
              </div>
            </div>

            <div className="rounded-xl bg-sail-card border border-sail-border overflow-hidden">
              <div className="p-4 border-b border-sail-border flex items-center justify-between">
                <h2 className="text-sm font-bold text-white uppercase tracking-wider flex items-center gap-2">
                  <FileSignature className="w-4 h-4 text-sail-cyan" />
                  Active Charter Contracts ({filteredContracts.length})
                </h2>
                <span className="text-xs text-slate-400 font-mono">
                  Select contract to inspect performance
                </span>
              </div>

              <div className="overflow-x-auto">
                <table className="w-full text-left text-xs font-mono">
                  <thead className="bg-sail-dark text-slate-400 border-b border-sail-border uppercase">
                    <tr>
                      <th className="py-3 px-4">Contract Ref</th>
                      <th className="py-3 px-4">Plant</th>
                      <th className="py-3 px-4">Vessel</th>
                      <th className="py-3 px-4">Cargo</th>
                      <th className="py-3 px-4 text-right">Nominated MT</th>
                      <th className="py-3 px-4 text-right">Freight Rate</th>
                      <th className="py-3 px-4 text-center">Status</th>
                      <th className="py-3 px-4 text-center">Action</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-sail-border">
                    {filteredContracts.length === 0 ? (
                      <tr>
                        <td colSpan={8} className="py-8 text-center text-slate-500">
                          No charter contracts found. Click &quot;Seed Baltic Fixtures&quot; above to populate sample contracts.
                        </td>
                      </tr>
                    ) : (
                      filteredContracts.map((c) => {
                        const isSelected = selectedContractId === c.contract_id;
                        return (
                          <tr
                            key={c.contract_id}
                            className={`hover:bg-sail-dark/60 transition-colors cursor-pointer ${
                              isSelected ? "bg-sail-dark/90 border-l-2 border-sail-cyan" : ""
                            }`}
                            onClick={() => setSelectedContractId(c.contract_id)}
                          >
                            <td className="py-3 px-4 font-bold text-white">
                              {c.contract_reference}
                            </td>
                            <td className="py-3 px-4 text-emerald-400 font-medium">
                              {c.charterer_plant}
                            </td>
                            <td className="py-3 px-4 text-slate-300">{c.vessel_name}</td>
                            <td className="py-3 px-4 text-slate-400">{c.cargo_type}</td>
                            <td className="py-3 px-4 text-right text-slate-200">
                              {formatNumber(c.nominated_quantity_mt)} MT
                            </td>
                            <td className="py-3 px-4 text-right text-sail-cyan font-bold">
                              ${c.freight_rate_usd_per_mt}/MT
                            </td>
                            <td className="py-3 px-4 text-center">
                              <span className="px-2 py-0.5 rounded text-[10px] font-bold bg-sail-dark text-slate-300 border border-sail-border">
                                {c.contract_status}
                              </span>
                            </td>
                            <td className="py-3 px-4 text-center">
                              <button
                                onClick={(e) => {
                                  e.stopPropagation();
                                  setSelectedContractId(c.contract_id);
                                }}
                                className="px-2 py-1 rounded bg-blue-900/40 hover:bg-blue-800 text-sail-cyan text-[11px] border border-blue-600/40 transition-all font-semibold"
                              >
                                Inspect
                              </button>
                            </td>
                          </tr>
                        );
                      })
                    )}
                  </tbody>
                </table>
              </div>
            </div>
          </div>

          {/* Right Col: Selected Contract Fulfillment Scorecard Drawer */}
          <div className="rounded-xl bg-sail-card border border-sail-border p-5 space-y-4 flex flex-col justify-between">
            {performance ? (
              <div className="space-y-4">
                <div className="pb-3 border-b border-sail-border">
                  <div className="flex items-center justify-between mb-1">
                    <span className="text-xs text-slate-400 font-mono uppercase tracking-wider">
                      Fulfillment Scorecard
                    </span>
                    {getFulfillmentBadge(performance.fulfillment_status)}
                  </div>
                  <h3 className="text-lg font-bold text-white font-mono">
                    {performance.contract_reference}
                  </h3>
                  <p className="text-xs text-emerald-400 font-mono">
                    {performance.charterer_plant} • {performance.cargo_type}
                  </p>
                </div>

                {/* Progress Bar of Lifted Volume */}
                <div className="space-y-1.5 font-mono text-xs">
                  <div className="flex items-center justify-between">
                    <span className="text-slate-400">Lifted Volume Progress</span>
                    <span className="text-sail-cyan font-bold">{performance.fulfillment_pct}%</span>
                  </div>
                  <div className="w-full h-2.5 rounded-full bg-sail-dark border border-sail-border overflow-hidden">
                    <div
                      className="h-full bg-gradient-to-r from-sail-accent to-sail-cyan rounded-full transition-all"
                      style={{ width: `${Math.min(100, parseFloat(performance.fulfillment_pct))}%` }}
                    />
                  </div>
                  <div className="flex items-center justify-between text-[11px] text-slate-500">
                    <span>Min: {formatNumber(performance.min_commitment_mt)} MT</span>
                    <span>Max: {formatNumber(performance.max_commitment_mt)} MT</span>
                  </div>
                </div>

                {/* Commitment Analysis Card */}
                <div className="p-3 rounded-lg bg-sail-dark border border-sail-border space-y-2 text-xs font-mono">
                  <div className="flex items-center justify-between">
                    <span className="text-slate-400">Nominated Volume:</span>
                    <span className="text-white font-bold">{formatNumber(performance.nominated_quantity_mt)} MT</span>
                  </div>
                  <div className="flex items-center justify-between">
                    <span className="text-slate-400">Actual Lifted:</span>
                    <span className="text-sail-cyan font-bold">{formatNumber(performance.actual_lifted_mt)} MT</span>
                  </div>
                  <div className="flex items-center justify-between">
                    <span className="text-slate-400">Remaining to Lift:</span>
                    <span className="text-amber-400 font-bold">{formatNumber(performance.remaining_to_lift_mt)} MT</span>
                  </div>
                  <div className="flex items-center justify-between pt-1 border-t border-sail-border/60">
                    <span className="text-slate-400">Tolerance Band:</span>
                    <span className="text-slate-300">±{performance.tolerance_pct}%</span>
                  </div>
                </div>

                {/* Financial Summary Card */}
                <div className="p-3 rounded-lg bg-sail-dark border border-sail-border space-y-2 text-xs font-mono">
                  <div className="text-[11px] font-bold text-slate-300 uppercase tracking-wider mb-1 flex items-center gap-1.5">
                    <DollarSign className="w-3.5 h-3.5 text-emerald-400" />
                    <span>Contract Financial Ledger</span>
                  </div>
                  <div className="flex items-center justify-between">
                    <span className="text-slate-400">Freight Spend:</span>
                    <span className="text-white font-bold">${formatNumber(performance.gross_freight_spend_usd)}</span>
                  </div>
                  <div className="flex items-center justify-between">
                    <span className="text-slate-400">Demurrage Paid:</span>
                    <span className="text-rose-400 font-bold">${formatNumber(performance.demurrage_paid_usd)}</span>
                  </div>
                  <div className="flex items-center justify-between">
                    <span className="text-slate-400">Despatch Earned:</span>
                    <span className="text-emerald-400 font-bold">${formatNumber(performance.despatch_earned_usd)}</span>
                  </div>
                  <div className="flex items-center justify-between pt-1 border-t border-sail-border/60">
                    <span className="text-slate-400">Net Laytime Impact:</span>
                    <span
                      className={`font-bold ${
                        parseFloat(performance.net_laytime_impact_usd) > 0 ? "text-rose-400" : "text-emerald-400"
                      }`}
                    >
                      ${formatNumber(performance.net_laytime_impact_usd)}
                    </span>
                  </div>
                </div>

                {/* Operational Reliability Card */}
                <div className="p-3 rounded-lg bg-sail-dark border border-sail-border space-y-1.5 text-xs font-mono">
                  <div className="flex items-center justify-between">
                    <span className="text-slate-400">Laycan Adherence:</span>
                    <span className="text-emerald-400 font-bold">{performance.laycan_adherence_rate_pct}%</span>
                  </div>
                  <div className="flex items-center justify-between">
                    <span className="text-slate-400">Cargo Shortage:</span>
                    <span className="text-amber-400 font-bold">{formatNumber(performance.total_shortage_mt)} MT ({performance.shortage_rate_pct}%)</span>
                  </div>
                  <div className="flex items-center justify-between">
                    <span className="text-slate-400">Completed Voyages:</span>
                    <span className="text-slate-200">{performance.voyages_count} voyages</span>
                  </div>
                </div>
              </div>
            ) : loadingPerf ? (
              <div className="py-20 flex flex-col items-center justify-center space-y-3">
                <RefreshCw className="w-6 h-6 text-sail-cyan animate-spin" />
                <p className="text-xs text-slate-400 font-mono">Loading performance scorecard...</p>
              </div>
            ) : (
              <div className="py-20 text-center text-slate-500 font-mono text-xs">
                Select a contract from the list to view commitment fulfillment.
              </div>
            )}

            <div className="p-3 rounded-lg bg-purple-950/30 border border-purple-500/20 text-[11px] text-purple-300 font-mono flex items-center gap-2">
              <CheckCircle2 className="w-4 h-4 text-purple-400 shrink-0" />
              <span>Sanction Screening Active (OFAC, UN, DG Shipping)</span>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
