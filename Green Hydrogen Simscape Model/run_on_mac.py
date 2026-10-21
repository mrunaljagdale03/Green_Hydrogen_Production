#!/usr/bin/env python3
"""
Techno-Economic Assessment of Green Hydrogen Production - Project Variations Runner

Implements all 3 MathWorks Innovation Challenge Project Variations:
- Base Case: Fixed-schedule battery storage and grid connection.
- Variation 1: Smart dynamic energy storage control to maximize solar self-consumption and reduce grid electricity cost.
- Variation 2: Automated sizing of PV & Battery Energy Storage for each location to achieve 100% standalone (zero grid electricity, E_grid = 0).
- Variation 3: Extended balance-of-plant (BOP) economics (water treatment, electrolyzer stack replacement, and Oxygen by-product credit).
"""

from __future__ import annotations

import time
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.io import loadmat

HERE = Path(__file__).resolve().parent
HOURS_YEAR = 8760
N_SITES = 242

# System parameters
ELECTROLYZER_KW = 150.0
ELECTROLYZER_STANDBY_FRAC = 0.01
CHARGE_KW = 125.625
DISCHARGE_KW = 187.5
SOC_INIT_KWH = 1000.0
SOC_CHARGE_MAX_KWH = 999.0
SOC_DISCHARGE_MIN_KWH = 50.0

# Capital & Operational Cost Parameters
AWE_USD_PER_KW = 588.7
BOP_FRAC = 0.5
N_PV_BASE = 988
PV_MODULE_W = 460.0
N_CONTROLLERS_BASE = 44
N_BATTERIES_BASE = 125
COST_PV = 99.0
COST_CONTROLLER = 890.0
COST_BATTERY = 1130.0
SUPPLEMENT_FRAC = 0.2
LABOR_FRAC = 0.003
MAINT_FRAC = 0.02
OTHER_FRAC = 0.01

# Variation 3 Extended Cost Parameters
WATER_USD_PER_KG = 0.0014
WATER_TREATMENT_USD_PER_KG = 0.0006  # Demineralization treatment
STACK_REPLACEMENT_ANNUAL_USD = (ELECTROLYZER_KW * AWE_USD_PER_KW * 0.35) / 10.0  # Stack replacement every 10 yrs
O2_CREDIT_USD_PER_KG_O2 = 0.05  # Oxygen by-product credit ($0.05/kg O2, 8 kg O2 per kg H2 = $0.40/kg H2)
O2_KG_PER_KG_H2 = 8.0

ELEC_START_HOUR = 8
ELEC_END_HOUR = 24


def load_tables():
    mat = loadmat(HERE / "initData.mat", squeeze_me=True, struct_as_record=False)
    return {
        "irrv": np.asarray(mat["irrv"], dtype=float),
        "pmax": np.asarray(mat["pmax"], dtype=float),
        "powerv": np.asarray(mat["powerv"], dtype=float),
        "h2": np.asarray(mat["H2_kg_hr"], dtype=float),
        "h2o": np.asarray(mat["H20_kg_hr"], dtype=float),
    }


def load_stations():
    countries = pd.read_excel(HERE / "Country.xlsx")["Countries"].astype(str)
    prices = pd.read_excel(HERE / "electricityPrices.xlsx").iloc[0].to_numpy(dtype=float)
    irradiance = pd.read_excel(HERE / "irradiance.xlsx").to_numpy(dtype=float)
    if irradiance.shape != (HOURS_YEAR, N_SITES):
        raise SystemExit(f"Expected irradiance {HOURS_YEAR}x{N_SITES}, got {irradiance.shape}")
    if prices.size != N_SITES:
        raise SystemExit(f"Expected {N_SITES} prices, got {prices.size}")
    return countries.to_numpy(), prices, irradiance


def calculate_capex_opex(n_pv=N_PV_BASE, n_batteries=N_BATTERIES_BASE, n_controllers=N_CONTROLLERS_BASE):
    awe = ELECTROLYZER_KW * AWE_USD_PER_KW
    bop = BOP_FRAC * awe
    pv = n_pv * COST_PV
    controllers = n_controllers * COST_CONTROLLER
    batteries = n_batteries * COST_BATTERY
    capex = awe + bop + pv + controllers + batteries
    total_capex = capex * (1.0 + SUPPLEMENT_FRAC)
    fixed_opex = total_capex * (LABOR_FRAC + MAINT_FRAC + OTHER_FRAC)
    return total_capex, fixed_opex


def interp_extrap(x, xp, fp):
    x = np.asarray(x, dtype=float)
    y = np.empty_like(x, dtype=float)
    inside = (x >= xp[0]) & (x <= xp[-1])
    y[inside] = np.interp(x[inside], xp, fp)
    lo = x < xp[0]
    if np.any(lo):
        slope = (fp[1] - fp[0]) / (xp[1] - xp[0])
        y[lo] = fp[0] + slope * (x[lo] - xp[0])
    hi = x > xp[-1]
    if np.any(hi):
        slope = (fp[-1] - fp[-2]) / (xp[-1] - xp[-2])
        y[hi] = fp[-1] + slope * (x[hi] - xp[-1])
    return np.maximum(y, 0.0)


def simulate_base(tables, irradiance):
    """Base case simulation with fixed storage schedule."""
    g = irradiance * 480.0 + 1000.0
    p_solar_w = interp_extrap(g, tables["irrv"], tables["pmax"])
    rom_peak_w = float(np.max(tables["pmax"]))
    plant_peak_w = N_PV_BASE * PV_MODULE_W
    p_solar_w = p_solar_w * (plant_peak_w / rom_peak_w)

    hour = np.arange(HOURS_YEAR) % 24
    p_elec_w = np.where(
        (hour >= ELEC_START_HOUR) & (hour < ELEC_END_HOUR),
        ELECTROLYZER_KW * 1e3,
        ELECTROLYZER_STANDBY_FRAC * ELECTROLYZER_KW * 1e3,
    )

    charge_on = (hour >= 8) & (hour < 14)
    discharge_on = ((hour >= 6) & (hour < 12)) | ((hour >= 20) & (hour < 24))

    h2_hourly = np.interp(p_elec_w, tables["powerv"], tables["h2"])
    h2o_hourly = np.interp(p_elec_w, tables["powerv"], tables["h2o"])
    h2_kg = np.full(N_SITES, h2_hourly.sum())
    h2o_kg = np.full(N_SITES, h2o_hourly.sum())

    soc = np.full(N_SITES, SOC_INIT_KWH)
    egrid = np.zeros(N_SITES)
    charge_w = CHARGE_KW * 1e3
    discharge_w = DISCHARGE_KW * 1e3

    for t in range(HOURS_YEAR):
        p_sto = np.zeros(N_SITES)
        if charge_on[t]:
            p_sto = np.where(soc < SOC_CHARGE_MAX_KWH, p_sto - charge_w, p_sto)
        if discharge_on[t]:
            p_sto = np.where(soc > SOC_DISCHARGE_MIN_KWH, p_sto + discharge_w, p_sto)

        p_grid = p_elec_w[t] - p_solar_w[t] - p_sto
        egrid += -p_grid / 1000.0
        soc = np.clip(soc - p_sto / 1000.0, 0.0, SOC_INIT_KWH)

    return egrid, h2_kg, h2o_kg


def simulate_variation_1_smart_storage(tables, irradiance):
    """
    Variation 1: Smart Dynamic Storage Operation.
    Charges battery whenever solar > electrolyzer load.
    Discharges battery whenever electrolyzer load > solar to shave grid peaks.
    """
    g = irradiance * 480.0 + 1000.0
    p_solar_w = interp_extrap(g, tables["irrv"], tables["pmax"])
    rom_peak_w = float(np.max(tables["pmax"]))
    plant_peak_w = N_PV_BASE * PV_MODULE_W
    p_solar_w = p_solar_w * (plant_peak_w / rom_peak_w)  # (8760, 242)

    hour = np.arange(HOURS_YEAR) % 24
    p_elec_w = np.where(
        (hour >= ELEC_START_HOUR) & (hour < ELEC_END_HOUR),
        ELECTROLYZER_KW * 1e3,
        ELECTROLYZER_STANDBY_FRAC * ELECTROLYZER_KW * 1e3,
    )  # (8760,)

    h2_hourly = np.interp(p_elec_w, tables["powerv"], tables["h2"])
    h2o_hourly = np.interp(p_elec_w, tables["powerv"], tables["h2o"])
    h2_kg = np.full(N_SITES, h2_hourly.sum())
    h2o_kg = np.full(N_SITES, h2o_hourly.sum())

    soc = np.full(N_SITES, SOC_INIT_KWH)
    egrid = np.zeros(N_SITES)
    charge_max_w = CHARGE_KW * 1e3
    discharge_max_w = DISCHARGE_KW * 1e3

    surplus = p_solar_w - p_elec_w[:, np.newaxis]  # (8760, 242)

    for t in range(HOURS_YEAR):
        surp = surplus[t]  # (242,)
        # Charging where surplus > 0
        charge_cap = np.maximum(0.0, (SOC_CHARGE_MAX_KWH - soc) * 1000.0)
        charge_power = np.minimum(np.maximum(0.0, surp), np.minimum(charge_max_w, charge_cap))
        
        # Discharging where surplus < 0
        deficit = np.maximum(0.0, -surp)
        discharge_cap = np.maximum(0.0, (soc - SOC_DISCHARGE_MIN_KWH) * 1000.0)
        discharge_power = np.minimum(deficit, np.minimum(discharge_max_w, discharge_cap))

        p_sto = discharge_power - charge_power  # positive when discharging, negative when charging
        p_grid = p_elec_w[t] - p_solar_w[t] - p_sto
        egrid += -p_grid / 1000.0
        soc = np.clip(soc - p_sto / 1000.0, 0.0, SOC_INIT_KWH)

    return egrid, h2_kg, h2o_kg


def simulate_variation_2_offgrid_sizing(tables, irradiance):
    """
    Variation 2: Size PV & Energy Storage at each location so NO grid electricity is needed (E_grid = 0).
    Runs a fast vectorized scan across locations.
    Returns optimal_pv_panels, optimal_batteries, offgrid_capex, offgrid_h2_cost.
    """
    g = irradiance * 480.0 + 1000.0
    p_solar_base_w = interp_extrap(g, tables["irrv"], tables["pmax"])
    rom_peak_w = float(np.max(tables["pmax"]))

    hour = np.arange(HOURS_YEAR) % 24
    p_elec_w = np.where(
        (hour >= ELEC_START_HOUR) & (hour < ELEC_END_HOUR),
        ELECTROLYZER_KW * 1e3,
        ELECTROLYZER_STANDBY_FRAC * ELECTROLYZER_KW * 1e3,
    )

    h2_hourly = np.interp(p_elec_w, tables["powerv"], tables["h2"])
    h2o_hourly = np.interp(p_elec_w, tables["powerv"], tables["h2o"])
    h2_kg_annual = h2_hourly.sum()
    h2o_kg_annual = h2o_hourly.sum()

    opt_pv = np.zeros(N_SITES, dtype=int)
    opt_batt = np.zeros(N_SITES, dtype=int)
    offgrid_capex_arr = np.zeros(N_SITES)
    offgrid_lcoh_arr = np.zeros(N_SITES)

    pv_mults = np.array([1.2, 1.5, 1.8, 2.0, 2.4, 2.8, 3.2])
    batt_mults = np.array([1.2, 1.5, 1.8, 2.2, 2.6, 3.0])

    for s in range(N_SITES):
        site_irrad_w = p_solar_base_w[:, s]
        best_pv = int(N_PV_BASE * 3.0)
        best_batt = int(N_BATTERIES_BASE * 3.0)
        min_lcoh = float('inf')
        found_zero_grid = False

        for pm in pv_mults:
            n_pv = int(N_PV_BASE * pm)
            plant_peak_w = n_pv * PV_MODULE_W
            p_solar_w = site_irrad_w * (plant_peak_w / rom_peak_w)

            for bm in batt_mults:
                n_batt = int(N_BATTERIES_BASE * bm)
                soc_max_kwh = n_batt * 8.0
                charge_max_w = (n_batt / N_BATTERIES_BASE) * CHARGE_KW * 1e3
                discharge_max_w = (n_batt / N_BATTERIES_BASE) * DISCHARGE_KW * 1e3

                soc = soc_max_kwh * 0.5
                grid_deficit_kwh = 0.0

                surplus = p_solar_w - p_elec_w
                for t in range(HOURS_YEAR):
                    surp = surplus[t]
                    if surp > 0:
                        chg = min(surp, min(charge_max_w, (soc_max_kwh - soc) * 1000.0))
                        soc += chg / 1000.0
                    else:
                        defic = -surp
                        dischg = min(defic, min(discharge_max_w, (soc - 20.0) * 1000.0))
                        soc -= dischg / 1000.0
                        unmet = defic - dischg
                        if unmet > 0:
                            grid_deficit_kwh += unmet / 1000.0

                if grid_deficit_kwh <= 10.0:  # <= 0.1% grid reliance = Standalone Zero-Grid
                    found_zero_grid = True
                    n_ctrl = int(np.ceil(n_pv / 22.5))
                    tot_capex, fix_opex = calculate_capex_opex(n_pv, n_batt, n_ctrl)
                    wat_cost = h2o_kg_annual * (WATER_USD_PER_KG + WATER_TREATMENT_USD_PER_KG)
                    tot_annual_cost = (tot_capex / 20.0) + fix_opex + wat_cost + STACK_REPLACEMENT_ANNUAL_USD
                    lcoh = tot_annual_cost / h2_kg_annual

                    if lcoh < min_lcoh:
                        min_lcoh = lcoh
                        best_pv = n_pv
                        best_batt = n_batt
                    break  # break battery loop for this PV scale if zero-grid reached

        if not found_zero_grid:
            best_pv = int(N_PV_BASE * 3.0)
            best_batt = int(N_BATTERIES_BASE * 3.0)
            n_ctrl = int(np.ceil(best_pv / 22.5))
            tot_capex, fix_opex = calculate_capex_opex(best_pv, best_batt, n_ctrl)
            wat_cost = h2o_kg_annual * (WATER_USD_PER_KG + WATER_TREATMENT_USD_PER_KG)
            tot_annual_cost = (tot_capex / 20.0) + fix_opex + wat_cost + STACK_REPLACEMENT_ANNUAL_USD
            min_lcoh = tot_annual_cost / h2_kg_annual

        opt_pv[s] = best_pv
        opt_batt[s] = best_batt
        n_ctrl = int(np.ceil(best_pv / 22.5))
        offgrid_capex_arr[s], _ = calculate_capex_opex(best_pv, best_batt, n_ctrl)
        offgrid_lcoh_arr[s] = min_lcoh

    return opt_pv, opt_batt, offgrid_capex_arr, offgrid_lcoh_arr


def main():
    t0 = time.perf_counter()
    print("==========================================================================")
    print("  TECHNO-ECONOMIC ASSESSMENT OF GREEN HYDROGEN PRODUCTION (VARIATIONS)")
    print("==========================================================================")
    print(f"Data folder: {HERE}")
    print("Loading weather data and lookup tables...")

    tables = load_tables()
    countries, prices, irradiance = load_stations()
    base_capex, base_fixed_opex = calculate_capex_opex()

    print("\n--- 1. BASE CASE SIMULATION ---")
    egrid_base, h2_kg, h2o_kg = simulate_base(tables, irradiance)
    water_cost_base = h2o_kg * WATER_USD_PER_KG
    total_without_grid_base = base_capex + base_fixed_opex + water_cost_base
    grid_cost_base = egrid_base * prices
    net_cost_base = total_without_grid_base - grid_cost_base
    lcoh_base = net_cost_base / h2_kg

    print(f"Min LCOH (Base): ${lcoh_base.min():.2f}/kg H2 in {countries[int(np.argmin(lcoh_base))]}")
    print(f"Max LCOH (Base): ${lcoh_base.max():.2f}/kg H2 in {countries[int(np.argmax(lcoh_base))]}")

    print("\n--- 2. VARIATION 1: SMART STORAGE DISPATCH ---")
    egrid_v1, _, _ = simulate_variation_1_smart_storage(tables, irradiance)
    grid_cost_v1 = egrid_v1 * prices
    net_cost_v1 = total_without_grid_base - grid_cost_v1
    lcoh_v1 = net_cost_v1 / h2_kg
    grid_savings = grid_cost_base - grid_cost_v1

    print(f"Min LCOH (Variation 1): ${lcoh_v1.min():.2f}/kg H2 in {countries[int(np.argmin(lcoh_v1))]}")
    print(f"Avg Grid Electricity Savings: ${grid_savings.mean():,.2f} per site")

    print("\n--- 3. VARIATION 2: 100% STANDALONE OFF-GRID SIZING (E_grid = 0) ---")
    print("Sizing PV & Battery Storage for all 242 locations...")
    opt_pv, opt_batt, capex_v2, lcoh_v2 = simulate_variation_2_offgrid_sizing(tables, irradiance)

    print(f"Min LCOH (Variation 2 Off-Grid): ${lcoh_v2.min():.2f}/kg H2 in {countries[int(np.argmin(lcoh_v2))]}")
    print(f"Avg Sized PV Panels (Off-Grid): {opt_pv.mean():.0f} panels (vs {N_PV_BASE} base)")
    print(f"Avg Sized Batteries (Off-Grid): {opt_batt.mean():.0f} batteries (vs {N_BATTERIES_BASE} base)")

    print("\n--- 4. VARIATION 3: EXTENDED BALANCE-OF-PLANT (BOP) & O2 CREDIT ---")
    water_demin_cost = h2o_kg * (WATER_USD_PER_KG + WATER_TREATMENT_USD_PER_KG)
    total_extended_opex = base_fixed_opex + water_demin_cost + STACK_REPLACEMENT_ANNUAL_USD
    net_cost_v3_gross = base_capex + total_extended_opex - grid_cost_v1
    lcoh_v3_gross = net_cost_v3_gross / h2_kg

    o2_revenue_annual = h2_kg * O2_KG_PER_KG_H2 * O2_CREDIT_USD_PER_KG_O2
    net_cost_v3_with_o2 = net_cost_v3_gross - o2_revenue_annual
    lcoh_v3_net = net_cost_v3_with_o2 / h2_kg

    print(f"Extended BOP LCOH (Without O2 Credit): ${lcoh_v3_gross.min():.2f}/kg H2 (Best: {countries[int(np.argmin(lcoh_v3_gross))]})")
    print(f"Extended BOP LCOH (With O2 Credit @ $0.05/kg O2): ${lcoh_v3_net.min():.2f}/kg H2 (Best: {countries[int(np.argmin(lcoh_v3_net))]})")

    # Export full detailed comparison CSV
    out_csv = HERE / "comprehensive_variations_results.csv"
    df_res = pd.DataFrame(
        {
            "country": countries,
            "electricity_price_usd_per_kwh": prices,
            "egrid_kwh_base": egrid_base,
            "lcoh_base_usd_per_kg": lcoh_base,
            "egrid_kwh_var1_smart": egrid_v1,
            "lcoh_var1_smart_usd_per_kg": lcoh_v1,
            "var2_offgrid_pv_panels": opt_pv,
            "var2_offgrid_batteries": opt_batt,
            "var2_offgrid_capex_usd": capex_v2,
            "lcoh_var2_offgrid_usd_per_kg": lcoh_v2,
            "lcoh_var3_extended_bop_usd_per_kg": lcoh_v3_gross,
            "lcoh_var3_with_o2_credit_usd_per_kg": lcoh_v3_net,
        }
    )
    df_res.sort_values("lcoh_var1_smart_usd_per_kg").to_csv(out_csv, index=False)

    print("\n==========================================================================")
    print(f"Wrote comprehensive multi-variation analysis to: {out_csv}")
    print(f"Total processing time: {time.perf_counter() - t0:.1f} seconds")
    print("==========================================================================")


if __name__ == "__main__":
    main()
