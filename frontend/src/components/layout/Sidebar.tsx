"use client";

import React from "react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import {
  LayoutDashboard,
  TrendingDown,
  AlertOctagon,
  Ship,
  FileSpreadsheet,
  FileSignature,
  Flame,
  CheckCircle2,
} from "lucide-react";

interface NavItem {
  name: string;
  href: string;
  icon: React.ComponentType<{ className?: string }>;
  badge?: string;
}

const NAV_ITEMS: NavItem[] = [
  {
    name: "Executive Overview",
    href: "/",
    icon: LayoutDashboard,
  },
  {
    name: "Stock Starvation Warning",
    href: "/inventory/starvation",
    icon: AlertOctagon,
    badge: "CRITICAL",
  },
  {
    name: "Inbound Stock Runway",
    href: "/inventory/forecast",
    icon: TrendingDown,
  },
  {
    name: "Active Fleet Tracking",
    href: "/fleet",
    icon: Ship,
  },
  {
    name: "Laytime & Demurrage",
    href: "/laytime",
    icon: FileSpreadsheet,
  },
  {
    name: "Charter Fixtures",
    href: "/contracts",
    icon: FileSignature,
  },
];

export const Sidebar: React.FC = () => {
  const pathname = usePathname();

  return (
    <aside className="w-64 border-r border-sail-border bg-sail-dark min-h-[calc(100vh-4rem)] flex flex-col justify-between p-4 shrink-0">
      <div className="space-y-6">
        <div>
          <p className="text-[11px] font-semibold text-slate-400 tracking-wider uppercase px-3 mb-2 font-mono">
            Navigation Modules
          </p>
          <nav className="space-y-1">
            {NAV_ITEMS.map((item) => {
              const Icon = item.icon;
              const isActive = pathname === item.href;
              return (
                <Link
                  key={item.href}
                  href={item.href}
                  className={`flex items-center justify-between px-3 py-2.5 rounded-lg text-xs font-medium transition-all ${
                    isActive
                      ? "bg-gradient-to-r from-sail-accent to-blue-900 text-white shadow-md border border-sail-cyan/40"
                      : "text-slate-300 hover:bg-sail-card hover:text-white border border-transparent"
                  }`}
                >
                  <div className="flex items-center gap-3">
                    <Icon
                      className={`w-4 h-4 ${
                        isActive ? "text-sail-cyan" : "text-slate-400"
                      }`}
                    />
                    <span>{item.name}</span>
                  </div>
                  {item.badge && (
                    <span className="text-[10px] uppercase font-bold tracking-wider px-1.5 py-0.5 rounded bg-amber-500/20 text-amber-300 border border-amber-500/30">
                      {item.badge}
                    </span>
                  )}
                </Link>
              );
            })}
          </nav>
        </div>

        {/* Operational Highlights Card in Sidebar */}
        <div className="p-3.5 rounded-xl bg-sail-card border border-sail-border space-y-2">
          <div className="flex items-center gap-2 text-xs font-semibold text-sail-cyan">
            <Flame className="w-4 h-4 text-sail-gold" />
            <span>Operational Directive</span>
          </div>
          <p className="text-[11px] text-slate-300 leading-relaxed">
            Steel plants require continuous coking coal & iron ore feeding. Maintain
            minimum 21-day safety threshold across all blast furnaces.
          </p>
          <div className="pt-1 flex items-center gap-1.5 text-[10px] text-emerald-400 font-mono">
            <CheckCircle2 className="w-3.5 h-3.5" />
            <span>Air-Gapped Math: Decimal (ROUND_HALF_UP)</span>
          </div>
        </div>
      </div>

      {/* Footer Branding */}
      <div className="pt-4 border-t border-sail-border text-[11px] text-slate-500 flex flex-col gap-1">
        <div className="flex items-center justify-between">
          <span className="font-mono text-slate-400">SAIL ERP v2.5</span>
          <span className="text-emerald-400 font-medium">Production Ready</span>
        </div>
        <p className="text-[10px] text-slate-500">
          Indian Railways BOXN & Bulk Shipping Integrated
        </p>
      </div>
    </aside>
  );
};
