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

export interface ActiveFleetVoyage {
  voyage_id: string;
  voyage_reference: string;
  voyage_status: string;
  progress_pct: number;
  allowed_transitions: string[];
  vessel_id?: string;
  vessel_name: string;
  vessel_imo: string;
  vessel_size_class: string;
  vessel_flag_state: string;
  vessel_dwt: string;
  contract_id?: string;
  contract_reference: string;
  charterer_entity: string;
  charterer_plant: string;
  cargo_type: string;
  laytime_terms: string;
  allowed_laytime_hours: string;
  demurrage_rate_usd_day: string;
  despatch_rate_usd_day: string;
  load_port: string;
  discharge_port: string;
  discharge_berth: string;
  quantity_loaded_mt: string;
  quantity_discharged_mt: string;
  shortage_mt: string;
  eta_date: string;
  ata_date: string;
  nor_tendered_at: string;
  nor_accepted_at: string;
  berthed_at: string;
  last_position_lat?: string;
  last_position_lon?: string;
  speed_over_ground?: string;
  last_position_updated?: string;
}

export interface ActiveFleetSummary {
  total_active_voyages: number;
  total_loaded_mt_underway: string;
  vessels_in_transit_count: number;
  vessels_at_anchor_count: number;
  vessels_at_berth_count: number;
}

export interface ActiveFleetResponse {
  summary: ActiveFleetSummary;
  fleet: ActiveFleetVoyage[];
}

export interface LaytimeBalanceSnapshot {
  voyage_id: string;
  voyage_reference: string;
  outcome: "DEMURRAGE" | "DESPATCH" | "IN_PROGRESS";
  is_final: boolean;
  is_indefinite_laytime: boolean;
  allowed_hours: string | null;
  counted_hours: string;
  excepted_hours: string;
  balance_hours: string;
  demurrage_hours: string;
  despatch_hours: string;
  gross_demurrage_usd: string;
  gross_despatch_usd: string;
  net_settlement_usd: string;
  laytime_commenced_at: string | null;
  demurrage_commenced_at: string | null;
  discharge_completed_at: string | null;
  as_of_timestamp: string;
  laytime_terms: string;
  demurrage_rate_usd_per_day: string;
  despatch_rate_usd_per_day: string;
  audit_formula: string;
  allowed_laytime_formula: string;
  slice_count: number;
}

export interface LaytimeSettlementRecord {
  calc_id: string;
  voyage_id: string;
  voyage_reference: string;
  outcome: string;
  is_current: boolean;
  revision: number;
  allowed_laytime_hours: string;
  total_counted_hours: string;
  total_excepted_hours: string;
  total_port_hours: string;
  variance_hours: string;
  gross_demurrage_usd: string;
  gross_despatch_usd: string;
  net_settlement_usd: string;
  demurrage_commenced_at?: string;
  settlement_notes?: string;
  settled_by_username?: string;
  created_at: string;
}

export interface CharterContractSummary {
  contract_id: string;
  contract_reference: string;
  charter_party_type: string;
  contract_status: string;
  charterer_entity: string;
  charterer_plant: string;
  vessel: string;
  vessel_name: string;
  vessel_imo: string;
  cargo_type: string;
  nominated_quantity_mt: string;
  tolerance_pct: string;
  freight_rate_usd_per_mt: string;
  load_port: string;
  discharge_port: string;
  discharge_berth_designated: string;
  discharge_rate_mt_per_day: string;
  laytime_terms: string;
  demurrage_rate_usd_per_day: string;
  despatch_rate_usd_per_day: string;
  laycan_start: string;
  laycan_end: string;
  allowed_laytime_hours: string;
}

export interface ContractPerformance {
  contract_id: string;
  contract_reference: string;
  charterer_entity: string;
  charterer_plant: string;
  cargo_type: string;
  nominated_quantity_mt: string;
  tolerance_pct: string;
  min_commitment_mt: string;
  max_commitment_mt: string;
  actual_lifted_mt: string;
  fulfillment_pct: string;
  fulfillment_status: "COMPLIANT" | "PENDING" | "UNDER_FULFILLED" | "OVER_FULFILLED";
  remaining_to_lift_mt: string;
  freight_rate_usd_per_mt: string;
  gross_freight_spend_usd: string;
  demurrage_paid_usd: string;
  despatch_earned_usd: string;
  net_laytime_impact_usd: string;
  laycan_adherence_rate_pct: string;
  total_shortage_mt: string;
  shortage_rate_pct: string;
  voyages_count: number;
}

export interface CarrierScorecard {
  owner_name: string;
  carrier_reliability_score: number;
  reliability_tier: "TIER_1_PREFERRED" | "TIER_2_ACCEPTABLE" | "TIER_3_PROBATION" | "BLACKLISTED";
  total_voyages: number;
  unique_vessels_count: number;
  total_lifted_mt: string;
  total_gross_freight_usd: string;
  laycan_adherence_pct: string;
  demurrage_incidence_pct: string;
  average_shortage_pct: string;
  contract_count: number;
}

export interface InboundDailyPoint {
  day_index: number;
  simulation_date: string;
  opening_stock_mt: string;
  daily_burn_mt: string;
  inbound_receipts_mt: string;
  closing_stock_mt: string;
  runway_days_remaining: string;
  safety_status: "ADEQUATE" | "PRECAUTIONARY" | "CRITICAL" | "STOCKOUT" | string;
  events: string[];
}

export interface InboundVesselContribution {
  voyage_id: string;
  voyage_reference: string;
  vessel_name: string;
  cargo_type: string;
  quantity_mt: string;
  voyage_status: string;
  eta_date: string;
  discharge_port: string;
}

export interface InboundForecastData {
  plant_code: string;
  plant_name: string;
  plant_location: string;
  material_category: string;
  horizon_days: number;
  as_of_date: string;
  current_stock_mt: string;
  daily_consumption_rate_mt: string;
  baseline_runway_days: string;
  total_inbound_tonnage_mt: string;
  inbound_vessels_count: number;
  extended_runway_days: string;
  stockout_date: string | null;
  critical_breach_date: string | null;
  precautionary_breach_date: string | null;
  minimum_projected_stock_mt: string;
  minimum_stock_date: string | null;
  is_starvation_risk: boolean;
  timeline: InboundDailyPoint[];
  inbound_vessels: InboundVesselContribution[];
  generated_at: string;
}

export interface PlantStarvationProfile {
  plant_code: string;
  plant_name: string;
  plant_location: string;
  material_category: string;
  current_stock_mt: string;
  daily_burn_rate_mt: string;
  physical_runway_days: string;
  incoming_vessels_count: number;
  incoming_tonnage_mt: string;
  extended_runway_days: string;
  target_safe_stock_mt: string;
  deficit_to_safe_stock_mt: string;
  risk_severity: "CRITICAL_URGENT" | "CRITICAL" | "PRECAUTIONARY" | "WATCH" | "ADEQUATE" | "SURPLUS" | string;
  is_starvation_risk: boolean;
}

export interface DiversionOption {
  voyage_id: string;
  voyage_reference: string;
  vessel_name: string;
  cargo_type: string;
  quantity_mt: string;
  current_destination_plant: string;
  current_discharge_port: string;
  target_plant: string;
  target_discharge_port: string;
  donor_current_runway_days: string;
  donor_post_diversion_runway_days: string;
  is_donor_safe: boolean;
  recipient_post_diversion_runway_days: string;
  runway_days_gained: string;
  estimated_diversion_cost_usd: string;
  feasibility_score: number;
  recommendation: string;
}

export interface EmergencyRecharterOption {
  recommended_vessel_class: string;
  parcel_size_mt: string;
  estimated_spot_freight_usd_per_mt: string;
  total_estimated_freight_usd: string;
  laycan_urgency_days: number;
  runway_days_restored: string;
  target_runway_days: string;
}

export interface EmergencyInterPlantTransferOption {
  donor_plant_code: string;
  donor_plant_name: string;
  donor_surplus_stock_mt: string;
  rakes_required: number;
  total_transfer_tonnage_mt: string;
  estimated_transit_hours: number;
  estimated_rail_freight_usd: string;
  recipient_runway_days_restored: string;
  is_transfer_viable: boolean;
}

export interface StarvationMitigationSimulation {
  target_plant_code: string;
  target_plant_name: string;
  material_category: string;
  starvation_profile: PlantStarvationProfile;
  primary_strategy: "DIVERSION" | "INTERPLANT_RAIL" | "SPOT_RECHARTER" | "ADEQUATE_NO_ACTION" | string;
  summary_recommendation: string;
  diversion_options: DiversionOption[];
  spot_recharter_option: EmergencyRecharterOption | null;
  rail_transfer_options: EmergencyInterPlantTransferOption[];
  all_plants_matrix: PlantStarvationProfile[];
  simulated_at: string;
}

export interface BerthScheduleItem {
  voyage_id: string;
  voyage_reference: string;
  vessel_name: string;
  vessel_imo: string;
  cargo_type: string;
  quantity_mt: string;
  voyage_status: string;
  is_actual: boolean;
  start_time: string;
  end_time: string;
  duration_hours: string;
  has_conflict: boolean;
  conflicts_with: string[];
}

export interface AnchorageQueueItem {
  voyage_id: string;
  voyage_reference: string;
  vessel_name: string;
  vessel_imo: string;
  cargo_type: string;
  quantity_mt: string;
  voyage_status: string;
  eta_date: string;
  ata_date: string | null;
  nor_tendered_at: string | null;
  assigned_berth: string | null;
  waiting_hours: string | null;
}

export interface PortCongestionKPIs {
  discharge_port: string;
  total_active_vessels: number;
  vessels_berthed_count: number;
  vessels_at_anchorage_count: number;
  vessels_unassigned_berth_count: number;
  total_cargo_berthed_mt: string;
  total_cargo_waiting_mt: string;
  average_anchorage_wait_hours: string | null;
  congestion_level: "LOW" | "MODERATE" | "HIGH" | string;
  as_of: string;
}

export interface PortBerthTimelineReport {
  discharge_port: string;
  kpis: PortCongestionKPIs;
  berths: Record<string, BerthScheduleItem[]>;
  anchorage_queue: AnchorageQueueItem[];
  total_conflicts_detected: number;
}

export type UserRole =
  | "EXECUTIVE_DIRECTOR"
  | "LOGISTICS_DIRECTOR"
  | "CHARTERING_MANAGER"
  | "PORT_OPERATOR"
  | "FINANCE_CONTROLLER"
  | "PORT_LOGISTICS_AGENT"
  | "LOGISTICS_AGENT"
  | "CHARTERING_BROKER"
  | "PLANT_PROCUREMENT_OFFICER"
  | "PROCUREMENT_OFFICER"
  | "FINANCE_OFFICER"
  | "SYSTEM_ADMIN"
  | string;

export type PlantCode = "ALL" | "BSP" | "DSP" | "RSP" | "BSL" | "ISP";

