"use client";

import React, { useEffect, useState, useCallback, useMemo } from "react";
import Link from "next/link";
import { useApp } from "@/context/AppContext";
import { logisticsApi } from "@/lib/api-client";
import { ActiveFleetVoyage, ActiveFleetResponse } from "@/types/dashboard";
import {
  Ship,
  Anchor,
  Compass,
  Search,
  Filter,
  RefreshCw,
  ArrowRight,
  MapPin,
  Clock,
  Gauge,
  Navigation,
  FileSpreadsheet,
  AlertTriangle,
  Flame,
  CheckCircle2,
  ChevronRight,
  Waves,
} from "lucide-react";
import BerthingGantt from "@/components/berthing/BerthingGantt";

// Geographic coordinates for key ports in the SAIL bulk shipping network
const KNOWN_PORTS: Record<string, { lat: number; lon: number; country: string; type: "DISCHARGE" | "LOAD" }> = {
  Paradip: { lat: 20.2644, lon: 86.6853, country: "India", type: "DISCHARGE" },
  Haldia: { lat: 22.0232, lon: 88.0622, country: "India", type: "DISCHARGE" },
  Visakhapatnam: { lat: 17.6868, lon: 83.2185, country: "India", type: "DISCHARGE" },
  Dhamra: { lat: 20.8256, lon: 86.9634, country: "India", type: "DISCHARGE" },
  "Hay Point": { lat: -21.2842, lon: 149.3012, country: "Australia", type: "LOAD" },
  Gladstone: { lat: -23.8378, lon: 151.2589, country: "Australia", type: "LOAD" },
  "Tanjung Bara": { lat: 0.5489, lon: 117.6322, country: "Indonesia", type: "LOAD" },
  "Richards Bay": { lat: -28.8000, lon: 32.0833, country: "South Africa", type: "LOAD" },
};

// Map projection bounds: Indian Ocean, Southeast Asia, Australia, East Africa
// Longitude: 20°E to 160°E -> width: 140°
// Latitude: -38°S to 32°N -> height: 70°
const MAP_BOUNDS = {
  minLon: 20,
  maxLon: 160,
  minLat: -38,
  maxLat: 32,
};

function projectCoords(lat: number, lon: number, width: number, height: number) {
  // Normalize lon from [20, 160] to [0, width]
  const x = ((lon - MAP_BOUNDS.minLon) / (MAP_BOUNDS.maxLon - MAP_BOUNDS.minLon)) * width;
  // Normalize lat from [-38, 32] to [height, 0] (SVG y is inverted)
  const y = ((MAP_BOUNDS.maxLat - lat) / (MAP_BOUNDS.maxLat - MAP_BOUNDS.minLat)) * height;
  return { x: Math.max(10, Math.min(width - 10, x)), y: Math.max(10, Math.min(height - 10, y)) };
}

export default function FleetTrackingPage() {
  const { role, refreshTrigger } = useApp();
  const [fleetData, setFleetData] = useState<ActiveFleetResponse | null>(null);
  const [loading, setLoading] = useState<boolean>(true);
  const [error, setError] = useState<string | null>(null);

  // Filters & Search
  const [searchQuery, setSearchQuery] = useState<string>("");
  const [portFilter, setPortFilter] = useState<string>("ALL");
  const [statusFilter, setStatusFilter] = useState<string>("ALL");
  const [cargoFilter, setCargoFilter] = useState<string>("ALL");
  const [selectedVoyage, setSelectedVoyage] = useState<ActiveFleetVoyage | null>(null);
  const [activeView, setActiveView] = useState<"CORRIDOR_MAP" | "BERTHING_GANTT">("CORRIDOR_MAP");

  const fetchFleet = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const res = await logisticsApi.getActiveFleet(
        {
          discharge_port: portFilter,
          status: statusFilter,
          cargo_type: cargoFilter,
        },
        role
      );
      setFleetData(res);
      // Auto-select first voyage if none currently selected
      if (res.fleet.length > 0 && !selectedVoyage) {
        setSelectedVoyage(res.fleet[0]);
      }
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : "Failed to load active fleet";
      setError(msg);
    } finally {
      setLoading(false);
    }
  }, [portFilter, statusFilter, cargoFilter, role, selectedVoyage]);

  useEffect(() => {
    fetchFleet();
  }, [fetchFleet, refreshTrigger]);

  // Client-side search query filtering
  const filteredFleet = useMemo(() => {
    if (!fleetData?.fleet) return [];
    return fleetData.fleet.filter((v) => {
      if (!searchQuery) return true;
      const q = searchQuery.toLowerCase();
      return (
        v.vessel_name.toLowerCase().includes(q) ||
        v.voyage_reference.toLowerCase().includes(q) ||
        v.contract_reference.toLowerCase().includes(q) ||
        v.discharge_port.toLowerCase().includes(q)
      );
    });
  }, [fleetData, searchQuery]);

  const getStatusColor = (status: string) => {
    switch (status) {
      case "DISCHARGING":
        return { text: "text-emerald-400", bg: "bg-emerald-500/20", border: "border-emerald-500/40", hex: "#2ec4b6" };
      case "BERTHED":
        return { text: "text-teal-400", bg: "bg-teal-500/20", border: "border-teal-500/40", hex: "#00b4d8" };
      case "ARRIVED":
        return { text: "text-amber-400", bg: "bg-amber-500/20", border: "border-amber-500/40", hex: "#ffb703" };
      case "IN_TRANSIT":
      default:
        return { text: "text-cyan-400", bg: "bg-blue-500/20", border: "border-blue-500/40", hex: "#0077b6" };
    }
  };

  const formatNumber = (val: string | number | undefined) => {
    if (val === undefined || val === null) return "0";
    const num = typeof val === "string" ? parseFloat(val) : val;
    return isNaN(num) ? "0" : num.toLocaleString("en-US");
  };

  const mapWidth = 900;
  const mapHeight = 440;

  return (
    <div className="space-y-6">
      {/* Page Header & Summary Badges */}
      <div className="flex flex-col md:flex-row md:items-center justify-between gap-4 pb-4 border-b border-sail-border">
        <div>
          <div className="flex items-center gap-2 mb-1">
            <span className="text-xs uppercase tracking-widest text-sail-cyan font-mono font-semibold">
              Fleet Operations
            </span>
            <span className="text-slate-500">|</span>
            <span className="text-xs text-slate-400 font-mono">
              Geospatial Telemetry & Milestone Radar
            </span>
          </div>
          <h1 className="text-2xl font-bold tracking-tight text-white flex items-center gap-2">
            <Ship className="w-6 h-6 text-sail-cyan" />
            Active Bulk Carrier Fleet & Real-Time Tracking
          </h1>
        </div>

        {fleetData && (
          <div className="flex flex-wrap items-center gap-3">
            <div className="px-3 py-1.5 rounded-lg bg-sail-card border border-sail-border text-xs font-mono">
              <span className="text-slate-400">Total Active: </span>
              <strong className="text-white">{fleetData.summary.total_active_voyages}</strong>
            </div>
            <div className="px-3 py-1.5 rounded-lg bg-blue-950/40 border border-blue-600/30 text-xs font-mono">
              <span className="text-blue-300">Underway: </span>
              <strong className="text-white">{fleetData.summary.vessels_in_transit_count}</strong>
            </div>
            <div className="px-3 py-1.5 rounded-lg bg-amber-950/40 border border-amber-600/30 text-xs font-mono">
              <span className="text-amber-300">Anchored: </span>
              <strong className="text-white">{fleetData.summary.vessels_at_anchor_count}</strong>
            </div>
            <div className="px-3 py-1.5 rounded-lg bg-emerald-950/40 border border-emerald-600/30 text-xs font-mono">
              <span className="text-emerald-300">At Berth: </span>
              <strong className="text-white">{fleetData.summary.vessels_at_berth_count}</strong>
            </div>

            {/* View Mode Toggle */}
            <div className="flex items-center gap-1 bg-sail-card p-1 rounded-xl border border-sail-border">
              <button
                onClick={() => setActiveView("CORRIDOR_MAP")}
                className={`px-3 py-1 rounded-lg text-xs font-mono font-bold flex items-center gap-1.5 transition-all ${
                  activeView === "CORRIDOR_MAP"
                    ? "bg-sail-accent text-white shadow"
                    : "text-slate-400 hover:text-white"
                }`}
              >
                <Compass className="w-3.5 h-3.5" />
                <span>Maritime Corridor</span>
              </button>
              <button
                onClick={() => setActiveView("BERTHING_GANTT")}
                className={`px-3 py-1 rounded-lg text-xs font-mono font-bold flex items-center gap-1.5 transition-all ${
                  activeView === "BERTHING_GANTT"
                    ? "bg-sail-accent text-white shadow"
                    : "text-slate-400 hover:text-white"
                }`}
              >
                <Waves className="w-3.5 h-3.5 text-sail-cyan" />
                <span>Berthing Gantt</span>
              </button>
            </div>
          </div>
        )}
      </div>

      {/* Filter and Search Toolbar */}
      <div className="p-3.5 rounded-xl bg-sail-card border border-sail-border flex flex-wrap items-center justify-between gap-3">
        <div className="flex items-center gap-3 flex-1 min-w-[280px]">
          <div className="relative flex-1">
            <Search className="w-4 h-4 text-slate-400 absolute left-3 top-2.5" />
            <input
              type="text"
              placeholder="Search vessel name, IMO, voyage ref, or port..."
              value={searchQuery}
              onChange={(e) => setSearchQuery(e.target.value)}
              className="w-full pl-9 pr-3 py-1.5 rounded-lg bg-sail-dark border border-sail-border text-xs text-white placeholder-slate-500 focus:outline-none focus:border-sail-cyan"
            />
          </div>
        </div>

        <div className="flex flex-wrap items-center gap-2">
          {/* Discharge Port Filter */}
          <select
            value={portFilter}
            onChange={(e) => setPortFilter(e.target.value)}
            className="px-2.5 py-1.5 rounded-lg bg-sail-dark border border-sail-border text-xs text-slate-200 focus:outline-none cursor-pointer"
          >
            <option value="ALL">All Discharge Ports</option>
            <option value="Paradip">Paradip</option>
            <option value="Haldia">Haldia</option>
            <option value="Visakhapatnam">Visakhapatnam</option>
            <option value="Dhamra">Dhamra</option>
          </select>

          {/* Status Filter */}
          <select
            value={statusFilter}
            onChange={(e) => setStatusFilter(e.target.value)}
            className="px-2.5 py-1.5 rounded-lg bg-sail-dark border border-sail-border text-xs text-slate-200 focus:outline-none cursor-pointer"
          >
            <option value="ALL">All Statuses</option>
            <option value="IN_TRANSIT">In Transit</option>
            <option value="ARRIVED">Arrived (Anchor)</option>
            <option value="BERTHED">Berthed</option>
            <option value="DISCHARGING">Discharging</option>
          </select>

          {/* Cargo Filter */}
          <select
            value={cargoFilter}
            onChange={(e) => setCargoFilter(e.target.value)}
            className="px-2.5 py-1.5 rounded-lg bg-sail-dark border border-sail-border text-xs text-slate-200 focus:outline-none cursor-pointer"
          >
            <option value="ALL">All Cargoes</option>
            <option value="COKING_COAL">Coking Coal</option>
            <option value="THERMAL_COAL">Thermal Coal</option>
            <option value="MET_COKE">Met Coke</option>
            <option value="LIMESTONE">Limestone</option>
          </select>
        </div>
      </div>

      {loading && !fleetData && (
        <div className="py-20 flex flex-col items-center justify-center space-y-4">
          <RefreshCw className="w-8 h-8 text-sail-cyan animate-spin" />
          <p className="text-sm text-slate-400 font-mono">
            Acquiring satellite AIS coordinates and voyage progress telemetry...
          </p>
        </div>
      )}

      {error && (
        <div className="p-4 rounded-xl bg-rose-950/40 border border-rose-500/40 text-rose-200 text-xs">
          {error}
        </div>
      )}

      {/* Main Workspace: Geospatial Map OR Berthing Gantt */}
      {activeView === "CORRIDOR_MAP" ? (
        <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
          {/* Left 2 Cols: Interactive Maritime Map Canvas */}
          <div className="lg:col-span-2 rounded-xl bg-sail-card border border-sail-border overflow-hidden flex flex-col">
            <div className="p-3.5 border-b border-sail-border flex items-center justify-between bg-sail-dark/60">
            <div className="flex items-center gap-2">
              <Compass className="w-4 h-4 text-sail-cyan" />
              <h2 className="text-xs font-bold text-white uppercase tracking-wider">
                Indian Ocean & Asia-Pacific Shipping Corridor
              </h2>
            </div>
            <span className="text-[11px] text-slate-400 font-mono">
              Live Plotted Vessels: {filteredFleet.length}
            </span>
          </div>

          {/* SVG Map Container */}
          <div className="relative bg-[#060d1f] p-4 flex items-center justify-center overflow-x-auto min-h-[460px]">
            <svg
              viewBox={`0 0 ${mapWidth} ${mapHeight}`}
              className="w-full h-auto max-w-[950px] select-none"
              style={{ filter: "drop-shadow(0 4px 12px rgba(0, 0, 0, 0.5))" }}
            >
              {/* Background Maritime Grid Lines */}
              <defs>
                <pattern id="maritime-grid" width="60" height="60" patternUnits="userSpaceOnUse">
                  <path d="M 60 0 L 0 0 0 60" fill="none" stroke="#101d3f" strokeWidth="0.75" />
                </pattern>
                {/* Glowing filters for active vessels */}
                <filter id="glow-cyan" x="-50%" y="-50%" width="200%" height="200%">
                  <feGaussianBlur stdDeviation="3" result="blur" />
                  <feMerge>
                    <feMergeNode in="blur" />
                    <feMergeNode in="SourceGraphic" />
                  </feMerge>
                </filter>
                <filter id="glow-gold" x="-50%" y="-50%" width="200%" height="200%">
                  <feGaussianBlur stdDeviation="3" result="blur" />
                  <feMerge>
                    <feMergeNode in="blur" />
                    <feMergeNode in="SourceGraphic" />
                  </feMerge>
                </filter>
              </defs>

              <rect width={mapWidth} height={mapHeight} fill="url(#maritime-grid)" />

              {/* Simplified stylized landmass contours (India, Australia, SE Asia, Africa) */}
              {/* India */}
              <path
                d="M 420 100 L 460 120 L 480 160 L 440 230 L 420 220 L 390 170 Z"
                fill="#0d1b38"
                stroke="#1c2e5e"
                strokeWidth="1.2"
                opacity="0.9"
              />
              <text x="415" y="160" fill="#293b66" fontSize="11" fontWeight="bold" fontFamily="monospace">
                INDIA
              </text>

              {/* Southeast Asia / Indonesia */}
              <path
                d="M 520 180 L 590 200 L 640 240 L 600 270 L 530 230 Z"
                fill="#0d1b38"
                stroke="#1c2e5e"
                strokeWidth="1.2"
                opacity="0.9"
              />
              <text x="560" y="235" fill="#293b66" fontSize="10" fontWeight="bold" fontFamily="monospace">
                INDONESIA
              </text>

              {/* Australia */}
              <path
                d="M 720 280 L 830 270 L 870 340 L 830 410 L 720 380 Z"
                fill="#0d1b38"
                stroke="#1c2e5e"
                strokeWidth="1.2"
                opacity="0.9"
              />
              <text x="770" y="340" fill="#293b66" fontSize="11" fontWeight="bold" fontFamily="monospace">
                AUSTRALIA
              </text>

              {/* East Africa */}
              <path
                d="M 40 200 L 100 220 L 120 320 L 80 400 L 30 380 Z"
                fill="#0d1b38"
                stroke="#1c2e5e"
                strokeWidth="1.2"
                opacity="0.9"
              />
              <text x="50" y="300" fill="#293b66" fontSize="10" fontWeight="bold" fontFamily="monospace">
                AFRICA
              </text>

              {/* Major Known Ports Plotted */}
              {Object.entries(KNOWN_PORTS).map(([name, port]) => {
                const pt = projectCoords(port.lat, port.lon, mapWidth, mapHeight);
                const isDischarge = port.type === "DISCHARGE";
                return (
                  <g key={name} transform={`translate(${pt.x}, ${pt.y})`}>
                    <circle
                      r={isDischarge ? "5" : "4"}
                      fill={isDischarge ? "#00b4d8" : "#f4a261"}
                      stroke="#050914"
                      strokeWidth="1.5"
                    />
                    <text
                      x="7"
                      y="3"
                      fill={isDischarge ? "#90e0ef" : "#f4a261"}
                      fontSize="9"
                      fontWeight="bold"
                      fontFamily="monospace"
                      opacity="0.9"
                    >
                      {name}
                    </text>
                  </g>
                );
              })}

              {/* Plotted Vessels and Trajectory Routes */}
              {filteredFleet.map((voyage) => {
                // Determine coordinates
                let lat = voyage.last_position_lat ? parseFloat(voyage.last_position_lat) : 15.0;
                let lon = voyage.last_position_lon ? parseFloat(voyage.last_position_lon) : 85.0;

                // Fallback to discharge port coords if missing
                if (isNaN(lat) || isNaN(lon)) {
                  const portCoords = KNOWN_PORTS[voyage.discharge_port] || { lat: 20.0, lon: 86.0 };
                  lat = portCoords.lat;
                  lon = portCoords.lon;
                }

                const vPos = projectCoords(lat, lon, mapWidth, mapHeight);
                const isSelected = selectedVoyage?.voyage_id === voyage.voyage_id;
                const statusStyling = getStatusColor(voyage.voyage_status);

                // Port destination coords
                const destPort = KNOWN_PORTS[voyage.discharge_port];
                const destPos = destPort ? projectCoords(destPort.lat, destPort.lon, mapWidth, mapHeight) : null;

                // Load port coords
                const loadPort = KNOWN_PORTS[voyage.load_port];
                const loadPos = loadPort ? projectCoords(loadPort.lat, loadPort.lon, mapWidth, mapHeight) : null;

                return (
                  <g
                    key={voyage.voyage_id}
                    className="cursor-pointer transition-all duration-300"
                    onClick={() => setSelectedVoyage(voyage)}
                  >
                    {/* Trajectory dotted line from load port to vessel to discharge port */}
                    {isSelected && loadPos && (
                      <line
                        x1={loadPos.x}
                        y1={loadPos.y}
                        x2={vPos.x}
                        y2={vPos.y}
                        stroke="#0077b6"
                        strokeWidth="1.5"
                        strokeDasharray="4 4"
                        opacity="0.6"
                      />
                    )}
                    {isSelected && destPos && (
                      <line
                        x1={vPos.x}
                        y1={vPos.y}
                        x2={destPos.x}
                        y2={destPos.y}
                        stroke="#00b4d8"
                        strokeWidth="2"
                        strokeDasharray="4 4"
                        opacity="0.8"
                      />
                    )}

                    {/* Outer pulse circle if selected or discharging */}
                    {isSelected && (
                      <circle
                        cx={vPos.x}
                        cy={vPos.y}
                        r="18"
                        fill="none"
                        stroke={statusStyling.hex}
                        strokeWidth="1.5"
                        opacity="0.6"
                        className="animate-ping"
                      />
                    )}

                    {/* Vessel Beacon Dot */}
                    <circle
                      cx={vPos.x}
                      cy={vPos.y}
                      r={isSelected ? "9" : "7"}
                      fill={statusStyling.hex}
                      stroke="#ffffff"
                      strokeWidth={isSelected ? "2.5" : "1.5"}
                      filter={isSelected ? "url(#glow-cyan)" : undefined}
                    />

                    {/* Vessel Name Label */}
                    <text
                      x={vPos.x + 11}
                      y={vPos.y + 4}
                      fill={isSelected ? "#ffffff" : "#cbd5e1"}
                      fontSize={isSelected ? "11" : "10"}
                      fontWeight={isSelected ? "bold" : "medium"}
                      fontFamily="monospace"
                      filter="drop-shadow(0 1px 3px rgba(0,0,0,0.9))"
                    >
                      {voyage.vessel_name}
                    </text>
                  </g>
                );
              })}
            </svg>

            {/* Map Legend Overlay */}
            <div className="absolute bottom-3 left-3 p-2.5 rounded-lg bg-sail-dark/90 border border-sail-border text-[11px] font-mono space-y-1.5 backdrop-blur-sm">
              <div className="flex items-center gap-2">
                <span className="w-2.5 h-2.5 rounded-full bg-cyan-500" />
                <span className="text-slate-300">Underway (In Transit)</span>
              </div>
              <div className="flex items-center gap-2">
                <span className="w-2.5 h-2.5 rounded-full bg-amber-400" />
                <span className="text-slate-300">Arrived (Anchorage)</span>
              </div>
              <div className="flex items-center gap-2">
                <span className="w-2.5 h-2.5 rounded-full bg-emerald-400" />
                <span className="text-slate-300">Discharging / Berthed</span>
              </div>
              <div className="flex items-center gap-2 pt-1 border-t border-sail-border/60">
                <span className="w-2 h-2 rounded-full bg-sail-gold" />
                <span className="text-slate-400">Load Port (Australia/SA/ID)</span>
              </div>
            </div>
          </div>
        </div>

        {/* Right Col: Selected Vessel Telemetry & Milestone Drawer */}
        <div className="rounded-xl bg-sail-card border border-sail-border p-5 flex flex-col justify-between space-y-4">
          {selectedVoyage ? (
            <div className="space-y-4">
              {/* Header: Vessel Name + Status Badge */}
              <div className="pb-3 border-b border-sail-border">
                <div className="flex items-center justify-between mb-1">
                  <span className="text-[11px] text-slate-400 font-mono uppercase tracking-wider">
                    {selectedVoyage.vessel_size_class} • {selectedVoyage.vessel_flag_state}
                  </span>
                  <span
                    className={`px-2 py-0.5 rounded text-[11px] font-bold font-mono border ${
                      getStatusColor(selectedVoyage.voyage_status).bg
                    } ${getStatusColor(selectedVoyage.voyage_status).text} ${
                      getStatusColor(selectedVoyage.voyage_status).border
                    }`}
                  >
                    {selectedVoyage.voyage_status}
                  </span>
                </div>
                <h3 className="text-xl font-bold text-white tracking-tight">
                  {selectedVoyage.vessel_name}
                </h3>
                <p className="text-xs text-slate-400 font-mono">
                  IMO: {selectedVoyage.vessel_imo} | Ref: {selectedVoyage.voyage_reference}
                </p>
              </div>

              {/* Telemetry Row: Speed, Coordinates, ETA */}
              <div className="grid grid-cols-2 gap-2.5 text-xs font-mono">
                <div className="p-2.5 rounded-lg bg-sail-dark border border-sail-border space-y-0.5">
                  <span className="text-[10px] text-slate-400 flex items-center gap-1">
                    <Gauge className="w-3 h-3 text-sail-cyan" /> Speed Over Ground
                  </span>
                  <p className="text-sm font-bold text-white">
                    {selectedVoyage.speed_over_ground || "0.0"} kts
                  </p>
                </div>
                <div className="p-2.5 rounded-lg bg-sail-dark border border-sail-border space-y-0.5">
                  <span className="text-[10px] text-slate-400 flex items-center gap-1">
                    <Navigation className="w-3 h-3 text-sail-gold" /> Coordinates
                  </span>
                  <p className="text-sm font-bold text-slate-200">
                    {selectedVoyage.last_position_lat ? `${parseFloat(selectedVoyage.last_position_lat).toFixed(2)}°N` : "N/A"},{" "}
                    {selectedVoyage.last_position_lon ? `${parseFloat(selectedVoyage.last_position_lon).toFixed(2)}°E` : "N/A"}
                  </p>
                </div>
              </div>

              {/* Progress Bar */}
              <div className="space-y-1.5">
                <div className="flex items-center justify-between text-xs font-mono">
                  <span className="text-slate-400">Voyage Lifecycle Progress</span>
                  <span className="text-sail-cyan font-bold">{selectedVoyage.progress_pct}%</span>
                </div>
                <div className="w-full h-2 rounded-full bg-sail-dark border border-sail-border overflow-hidden">
                  <div
                    className="h-full bg-gradient-to-r from-sail-accent to-sail-cyan transition-all duration-500 rounded-full"
                    style={{ width: `${selectedVoyage.progress_pct}%` }}
                  />
                </div>
              </div>

              {/* Voyage Itinerary Card */}
              <div className="p-3 rounded-lg bg-sail-dark border border-sail-border space-y-2 text-xs font-mono">
                <div className="flex items-center justify-between">
                  <span className="text-slate-400">Load Port:</span>
                  <span className="text-white font-bold">{selectedVoyage.load_port}</span>
                </div>
                <div className="flex items-center justify-between">
                  <span className="text-slate-400">Discharge Port:</span>
                  <span className="text-sail-cyan font-bold">
                    {selectedVoyage.discharge_port} ({selectedVoyage.discharge_berth || "TBD"})
                  </span>
                </div>
                <div className="flex items-center justify-between">
                  <span className="text-slate-400">Recipient Plant:</span>
                  <span className="text-emerald-400 font-bold">{selectedVoyage.charterer_plant}</span>
                </div>
                <div className="flex items-center justify-between pt-1 border-t border-sail-border/60">
                  <span className="text-slate-400">Cargo Type:</span>
                  <span className="text-slate-200">{selectedVoyage.cargo_type}</span>
                </div>
                <div className="flex items-center justify-between">
                  <span className="text-slate-400">Loaded Cargo:</span>
                  <span className="text-white font-bold">
                    {formatNumber(selectedVoyage.quantity_loaded_mt)} MT
                  </span>
                </div>
              </div>

              {/* Laytime Particulars */}
              <div className="p-3 rounded-lg bg-sail-dark border border-sail-border space-y-1.5 text-xs font-mono">
                <div className="text-[11px] font-bold text-slate-300 uppercase tracking-wider mb-1 flex items-center gap-1.5">
                  <FileSpreadsheet className="w-3.5 h-3.5 text-sail-gold" />
                  <span>Charter Contract Terms</span>
                </div>
                <div className="flex items-center justify-between">
                  <span className="text-slate-400">Contract Ref:</span>
                  <span className="text-slate-300">{selectedVoyage.contract_reference}</span>
                </div>
                <div className="flex items-center justify-between">
                  <span className="text-slate-400">Laytime Terms:</span>
                  <span className="text-slate-300">{selectedVoyage.laytime_terms}</span>
                </div>
                <div className="flex items-center justify-between">
                  <span className="text-slate-400">Demurrage Rate:</span>
                  <span className="text-rose-400 font-bold">${formatNumber(selectedVoyage.demurrage_rate_usd_day)}/d</span>
                </div>
              </div>
            </div>
          ) : (
            <div className="py-20 text-center text-slate-500 font-mono text-xs">
              Select a vessel on the map or list to view live telemetry.
            </div>
          )}

          {/* Quick Actions Footer */}
          {selectedVoyage && (
            <div className="pt-2 border-t border-sail-border space-y-2">
              <Link
                href="/inventory/starvation"
                className="w-full flex items-center justify-between px-3 py-2 rounded-lg bg-blue-900/40 hover:bg-blue-800 text-sail-cyan border border-blue-600/40 text-xs font-medium transition-all"
              >
                <span>Evaluate Vessel Diversion</span>
                <ChevronRight className="w-4 h-4" />
              </Link>
            </div>
          )}
        </div>
      </div>
    ) : (
      <BerthingGantt role={role} onRefreshTrigger={refreshTrigger} />
    )}

      {/* High-Density Tabular Fleet Listing */}
      <div className="rounded-xl bg-sail-card border border-sail-border overflow-hidden">
        <div className="p-4 border-b border-sail-border flex items-center justify-between">
          <h2 className="text-sm font-bold text-white uppercase tracking-wider flex items-center gap-2">
            <Waves className="w-4 h-4 text-sail-cyan" />
            Active Fleet Vessel Roster & Discharge Schedule
          </h2>
          <span className="text-xs text-slate-400 font-mono">
            Showing {filteredFleet.length} vessels
          </span>
        </div>

        <div className="overflow-x-auto">
          <table className="w-full text-left text-xs font-mono">
            <thead className="bg-sail-dark text-slate-400 border-b border-sail-border uppercase sticky top-0 z-10">
              <tr>
                <th className="py-3 px-4">Vessel Name</th>
                <th className="py-3 px-4">Voyage Ref</th>
                <th className="py-3 px-4">Cargo Type</th>
                <th className="py-3 px-4 text-right">Loaded (MT)</th>
                <th className="py-3 px-4">Load Port</th>
                <th className="py-3 px-4">Discharge Port</th>
                <th className="py-3 px-4">Plant</th>
                <th className="py-3 px-4 text-center">Progress</th>
                <th className="py-3 px-4 text-center">Status</th>
                <th className="py-3 px-4 text-center">Action</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-sail-border">
              {filteredFleet.length === 0 ? (
                <tr>
                  <td colSpan={10} className="py-8 text-center text-slate-500">
                    No active vessels match the current search and filter criteria.
                  </td>
                </tr>
              ) : (
                filteredFleet.map((voyage) => {
                  const isSelected = selectedVoyage?.voyage_id === voyage.voyage_id;
                  const statusStyling = getStatusColor(voyage.voyage_status);
                  return (
                    <tr
                      key={voyage.voyage_id}
                      className={`hover:bg-sail-dark/60 transition-colors cursor-pointer ${
                        isSelected ? "bg-sail-dark/90 border-l-2 border-sail-cyan" : ""
                      }`}
                      onClick={() => setSelectedVoyage(voyage)}
                    >
                      <td className="py-3 px-4 font-bold text-white max-w-[190px] truncate" title={voyage.vessel_name}>
                        <div className="flex items-center gap-2">
                          <Ship className="w-3.5 h-3.5 text-sail-cyan shrink-0" />
                          <span className="truncate">{voyage.vessel_name}</span>
                        </div>
                      </td>
                      <td className="py-3 px-4 text-slate-300 max-w-[130px] truncate" title={voyage.voyage_reference}>
                        {voyage.voyage_reference}
                      </td>
                      <td className="py-3 px-4 text-slate-400">{voyage.cargo_type}</td>
                      <td className="py-3 px-4 text-right text-slate-200">
                        {formatNumber(voyage.quantity_loaded_mt)}
                      </td>
                      <td className="py-3 px-4 text-slate-400 max-w-[120px] truncate" title={voyage.load_port}>
                        {voyage.load_port}
                      </td>
                      <td className="py-3 px-4 text-sail-cyan font-medium max-w-[130px] truncate" title={voyage.discharge_port}>
                        {voyage.discharge_port}
                      </td>
                      <td className="py-3 px-4 text-emerald-400">{voyage.charterer_plant}</td>
                      <td className="py-3 px-4 text-center font-bold text-white">
                        {voyage.progress_pct}%
                      </td>
                      <td className="py-3 px-4 text-center">
                        <span
                          className={`px-2 py-0.5 rounded text-[10px] font-bold border ${statusStyling.bg} ${statusStyling.text} ${statusStyling.border}`}
                        >
                          {voyage.voyage_status}
                        </span>
                      </td>
                      <td className="py-3 px-4 text-center">
                        <button
                          onClick={(e) => {
                            e.stopPropagation();
                            setSelectedVoyage(voyage);
                          }}
                          className="px-2 py-1 rounded bg-sail-card hover:bg-sail-border text-slate-200 text-[11px] border border-sail-border transition-all"
                        >
                          Telemetry
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
  );
}
