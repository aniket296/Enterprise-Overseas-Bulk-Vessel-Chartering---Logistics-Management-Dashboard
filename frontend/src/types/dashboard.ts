export interface FleetOperationsKPIs {
  total_active_voyages: number;
  vessels_underway: number;
  vessels_at_anchor_or_berth: number;
  total_tonnage_at_sea_mt: string;
  total_tonnage_at_port_mt: string;
  total_pipeline_cargo_mt: string;
  fleet_on_schedule_rate_pct: string;
  voyages_delayed_count: number;
  voyages_delayed_pct: string;
}

export interface FinancialPerformanceKPIs {
  total_gross_freight_committed_usd: string;
  total_demurrage_incurred_usd: string;
  total_dispatch_earned_usd: string;
  net_laytime_impact_usd: string;
  net_impact_direction: "PAYABLE" | "RECEIVABLE" | "NEUTRAL";
  financial_leakage_severity: "CRITICAL" | "MODERATE" | "HEALTHY";
  gross_freight_variance_pct: string;
}

export interface PlantSecurityDetail {
  plant_code: string;
  current_stock_mt: string;
  daily_burn_rate_mt: string;
  effective_runway_days: string;
  safety_stock_threshold_days: number;
  risk_severity: string;
  action_required: boolean;
}

export interface PlantSecurityKPIs {
  plants_reporting_count: number;
  overall_security_index_pct: string;
  plants_at_risk_count: number;
  plants_with_runway_lt_10d: number;
  plants_with_runway_lt_21d: number;
  total_network_stock_mt: string;
  total_network_daily_burn_mt: string;
  network_weighted_runway_days: string;
  network_starvation_risk: "EXTREME_STARVATION_RISK" | "HIGH_STARVATION_RISK" | "ELEVATED_VULNERABILITY" | "ROBUST_STOCK_COVERAGE";
  plant_details: PlantSecurityDetail[];
}

export interface ContractFulfillmentKPIs {
  total_fixtures_analyzed: number;
  overall_carrier_reliability_pct: string;
  on_time_arrival_rate_pct: string;
  documentation_accuracy_rate_pct: string;
  speed_consumption_compliance_rate_pct: string;
  carriers_below_threshold_count: number;
  carriers_blacklisted_count: number;
}

export interface ComplianceAndRiskKPIs {
  open_compliance_flags_count: number;
  severe_sanction_or_flag_breaches: number;
  unsettled_laytime_claims_count: number;
  unsettled_laytime_exposure_usd: string;
  critical_delay_incidents: number;
  risk_alert_level: "HIGH" | "MEDIUM" | "LOW";
}

export interface CompositeHealthKPIs {
  enterprise_health_score: number;
  status_level: "EXCELLENT" | "STABLE" | "NEEDS_ATTENTION" | "CRITICAL_INTERVENTION_REQUIRED";
  executive_highlights: string[];
}

export interface ExecutiveDashboardSummary {
  generated_at: string;
  fleet_operations: FleetOperationsKPIs;
  financial_performance: FinancialPerformanceKPIs;
  plant_security: PlantSecurityKPIs;
  contract_fulfillment: ContractFulfillmentKPIs;
  compliance_and_risk: ComplianceAndRiskKPIs;
  composite_health: CompositeHealthKPIs;
}

export type UserRole =
  | "EXECUTIVE_DIRECTOR"
  | "LOGISTICS_DIRECTOR"
  | "CHARTERING_MANAGER"
  | "PORT_OPERATOR"
  | "FINANCE_CONTROLLER";

export type PlantCode = "ALL" | "BSP" | "DSP" | "RSP" | "BSL" | "ISP";
