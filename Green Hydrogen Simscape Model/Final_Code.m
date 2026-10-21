tic
clc; % clear command window
clear; % clear workspace
load('initData.mat') % Load initial data

%% Specify Data Files
irradianceFile = 'irradiance.xlsx'; 
tempratureFile = 'temprature.xlsx'; 
windSpeedFile = 'wind speed.xlsx'; 
electricityPricesFile = 'electricityPrices.xlsx'; 
countryFile = 'Country.xlsx'; 

%% Import sheets data
if exist('datasets.mat', 'file')
    data = load('datasets.mat');
    CountryNameData = cellstr(data.CountryNames);
    OurStationData = struct('Country_Name', CountryNameData);
    for k = 1:242
        OurStationData(k).ElectricityPrices = double(data.ElectricityPricesMat(k));
        OurStationData(k).Irradiance = double(data.IrradianceMat(:, k));
        OurStationData(k).Temprature = double(data.TempratureMat(:, k));
        OurStationData(k).WindSpeed = double(data.WindSpeedMat(:, k));
    end
else
    irradianceTable = readtable(irradianceFile);
    electricityPrices = readtable(electricityPricesFile);
    temprature = readtable(tempratureFile);
    windSpeed = readtable(windSpeedFile);
    country = readtable(countryFile);
    CountryNameData = country.Countries; 
    OurStationData = struct('Country_Name', CountryNameData); 
    for k = 1:242
        OurStationData(k).ElectricityPrices = double(electricityPrices.(k));
        OurStationData(k).Irradiance = double(irradianceTable.(k));
        OurStationData(k).Temprature = double(temprature.(k));
        OurStationData(k).WindSpeed = double(windSpeed.(k));
    end
end

%% VARIATION MODE SELECTION
% Mode Options:
% 0 = Base Case (Original Simscape model run)
% 1 = Variation 1: Smart Storage Dispatch (Peak Shaving)
% 2 = Variation 2: 100% Off-Grid Sizing (Egrid = 0)
% 3 = Variation 3: Extended BOP + Water Treatment + Oxygen Sales Credit
mode = 0; % Default base mode

%% Base Simulation Loop (Simscape model)
model = 'Green_Hydrogen_Model'; 
warning('off', 'Simulink:blocks:MatchingFromNotFound');
if usejava('desktop')
    open_system(model); 
else
    load_system(model);
end
try
    set_param(model, 'FastRestart', 'on');
catch
end

irradiance_val = zeros(8760,1); 
Egrid_copy = zeros(1, 242);

for i = 1:242
    if mod(i, 20) == 1 || i == 242
        fprintf('Processing location %d/242 (%s)...\n', i, strtrim(OurStationData(i).Country_Name));
    end
    irradiance_val = double(OurStationData(i).Irradiance(:)) * 480 + 1000; 
    irradiance = double(irradiance_val(:)); % Set 8760x1 double column vector for Simulink Inport
    sim_res = sim(model); 
    if isstruct(sim_res.Egrid) && isfield(sim_res.Egrid, 'signals')
        Egrid_copy(i) = sim_res.Egrid.signals.values(end);
    elseif isa(sim_res.Egrid, 'timeseries')
        Egrid_copy(i) = sim_res.Egrid.Data(end);
    else
        Egrid_copy(i) = double(sim_res.Egrid(end));
    end
end

try
    set_param(model, 'FastRestart', 'off');
catch
end

%% Base Cost Analysis
Total_cost = zeros(1, 242);
cost_grid = zeros(1, 242);

for i = 1:242
    Egrid = Egrid_copy(i);
    cost_grid(i) = Egrid * OurStationData(i).ElectricityPrices;
    if isstruct(sim_res.Total_Cost_without_Grid) && isfield(sim_res.Total_Cost_without_Grid, 'signals')
        Total_Cost_of_grid = sim_res.Total_Cost_without_Grid.signals.values - cost_grid(i); 
    else
        Total_Cost_of_grid = sim_res.Total_Cost_without_Grid - cost_grid(i);
    end
    Total_cost(i) = sum(Total_Cost_of_grid) / 8760; 
end

if isstruct(sim_res.H2_Kg) && isfield(sim_res.H2_Kg, 'signals')
    Total_H2_in_year = sim_res.H2_Kg.signals.values(end,1);
else
    Total_H2_in_year = double(sim_res.H2_Kg(end));
end
H2_Kg_cost = (Total_cost) / Total_H2_in_year;

fprintf('=======================================================\n');
fprintf('  BASE CASE RESULTS (242 LOCATIONS)\n');
fprintf('=======================================================\n');
fprintf('Min Cost H2 ($/kg): %f in %s\n', min(H2_Kg_cost), strtrim(OurStationData(find(H2_Kg_cost == min(H2_Kg_cost), 1)).Country_Name));
fprintf('Max Cost H2 ($/kg): %f in %s\n', max(H2_Kg_cost), strtrim(OurStationData(find(H2_Kg_cost == max(H2_Kg_cost), 1)).Country_Name));

%% EXTENDED ANALYSIS FOR ALL 3 VARIATIONS

% System Constants
ELECTROLYZER_KW = 150.0;
AWE_USD_PER_KW = 588.7;
BOP_FRAC = 0.5;
N_PV_BASE = 988;
PV_MODULE_W = 460.0;
N_CONTROLLERS_BASE = 44;
N_BATTERIES_BASE = 125;
COST_PV = 99.0;
COST_CONTROLLER = 890.0;
COST_BATTERY = 1130.0;
SUPPLEMENT_FRAC = 0.2;
LABOR_FRAC = 0.003;
MAINT_FRAC = 0.02;
OTHER_FRAC = 0.01;

WATER_USD_PER_KG = 0.0014;
WATER_TREATMENT_USD_PER_KG = 0.0006;
STACK_REPLACEMENT_ANNUAL_USD = (ELECTROLYZER_KW * AWE_USD_PER_KW * 0.35) / 10.0;
O2_CREDIT_USD_PER_KG_O2 = 0.05;
O2_KG_PER_KG_H2 = 8.0;

% CAPEX & Fixed OPEX
awe_cost = ELECTROLYZER_KW * AWE_USD_PER_KW;
bop_cost = BOP_FRAC * awe_cost;
pv_cost = N_PV_BASE * COST_PV;
ctrl_cost = N_CONTROLLERS_BASE * COST_CONTROLLER;
batt_cost = N_BATTERIES_BASE * COST_BATTERY;
capex_base = (awe_cost + bop_cost + pv_cost + ctrl_cost + batt_cost) * (1.0 + SUPPLEMENT_FRAC);
fixed_opex_base = capex_base * (LABOR_FRAC + MAINT_FRAC + OTHER_FRAC);

prices_vec = [OurStationData.ElectricityPrices];

%% VARIATION 1: SMART STORAGE DISPATCH (PEAK SHAVING)
Egrid_v1 = zeros(1, 242);
hour_idx = mod(0:8759, 24)';
p_elec_w = zeros(8760, 1);
p_elec_w(hour_idx >= 8 & hour_idx < 24) = ELECTROLYZER_KW * 1000;
p_elec_w(hour_idx < 8) = 0.01 * ELECTROLYZER_KW * 1000;

for s = 1:242
    irrad_station = OurStationData(s).Irradiance;
    g = irrad_station * 480.0 + 1000.0;
    p_solar_w = interp1(irrv, pmax, g, 'linear', 'extrap');
    
    soc = 1000.0;
    egrid_sum = 0.0;
    charge_max_w = 125.625 * 1000;
    discharge_max_w = 187.5 * 1000;
    
    surplus = p_solar_w - p_elec_w;
    for t = 1:8760
        surp = surplus(t);
        if surp > 0
            chg = min([surp, charge_max_w, (999.0 - soc) * 1000.0]);
            chg = max(0.0, chg);
            p_sto = -chg;
        else
            defic = -surp;
            dischg = min([defic, discharge_max_w, (soc - 50.0) * 1000.0]);
            dischg = max(0.0, dischg);
            p_sto = dischg;
        end
        p_grid = p_elec_w(t) - p_solar_w(t) - p_sto;
        egrid_sum = egrid_sum - (p_grid / 1000.0);
        soc = min(999.0, max(50.0, soc - p_sto / 1000.0));
    end
    Egrid_v1(s) = egrid_sum;
end

cost_grid_v1 = Egrid_v1 .* prices_vec;
if isstruct(sim_res.Total_Cost_without_Grid) && isfield(sim_res.Total_Cost_without_Grid, 'signals')
    base_fixed_annual = mean(sim_res.Total_Cost_without_Grid.signals.values);
else
    base_fixed_annual = mean(sim_res.Total_Cost_without_Grid);
end
Total_cost_v1 = base_fixed_annual - cost_grid_v1;
lcoh_v1 = Total_cost_v1 / Total_H2_in_year;
grid_savings = (-cost_grid) - (-cost_grid_v1);

fprintf('\n=======================================================\n');
fprintf('  VARIATION 1: SMART STORAGE DISPATCH (PEAK SHAVING)\n');
fprintf('=======================================================\n');
fprintf('Min Cost H2 ($/kg): %f in %s\n', min(lcoh_v1), strtrim(OurStationData(find(lcoh_v1 == min(lcoh_v1), 1)).Country_Name));
fprintf('Max Cost H2 ($/kg): %f in %s\n', max(lcoh_v1), strtrim(OurStationData(find(lcoh_v1 == max(lcoh_v1), 1)).Country_Name));
fprintf('Avg Grid Electricity Savings: $%.2f per site/year\n', mean(grid_savings));

%% VARIATION 2: 100% OFF-GRID STANDALONE SIZING (Egrid = 0)
opt_pv = zeros(1, 242);
opt_batt = zeros(1, 242);
lcoh_v2 = zeros(1, 242);

pv_mults = [1.2, 1.5, 1.8, 2.0, 2.4, 2.8, 3.2];
batt_mults = [1.2, 1.5, 1.8, 2.2, 2.6, 3.0];

for s = 1:242
    g = OurStationData(s).Irradiance * 480.0 + 1000.0;
    base_solar_w = interp1(irrv, pmax, g, 'linear', 'extrap') / N_PV_BASE;
    best_pv = round(N_PV_BASE * 3.0);
    best_batt = round(N_BATTERIES_BASE * 3.0);
    min_lcoh = Inf;
    found_zero_grid = false;
    
    for pm = pv_mults
        n_pv = round(N_PV_BASE * pm);
        p_solar_w = base_solar_w * n_pv;
        
        for bm = batt_mults
            n_batt = round(N_BATTERIES_BASE * bm);
            soc_max_kwh = n_batt * 8.0;
            charge_max_w = (n_batt / N_BATTERIES_BASE) * 125.625 * 1000;
            discharge_max_w = (n_batt / N_BATTERIES_BASE) * 187.5 * 1000;
            
            soc = soc_max_kwh * 0.5;
            grid_deficit_kwh = 0.0;
            surplus = p_solar_w - p_elec_w;
            
            for t = 1:8760
                surp = surplus(t);
                if surp > 0
                    chg = min([surp, charge_max_w, (soc_max_kwh - soc) * 1000.0]);
                    soc = soc + max(0.0, chg) / 1000.0;
                else
                    defic = -surp;
                    dischg = min([defic, discharge_max_w, (soc - 20.0) * 1000.0]);
                    soc = soc - max(0.0, dischg) / 1000.0;
                    unmet = defic - max(0.0, dischg);
                    if unmet > 0
                        grid_deficit_kwh = grid_deficit_kwh + unmet / 1000.0;
                    end
                end
            end
            
            if grid_deficit_kwh <= 10.0
                found_zero_grid = true;
                n_ctrl = ceil(n_pv / 22.5);
                tot_capex = (awe_cost + bop_cost + (n_pv * COST_PV) + (n_ctrl * COST_CONTROLLER) + (n_batt * COST_BATTERY)) * (1.0 + SUPPLEMENT_FRAC);
                fix_opex = tot_capex * (LABOR_FRAC + MAINT_FRAC + OTHER_FRAC);
                wat_cost = Total_H2_in_year * 9.0 * (WATER_USD_PER_KG + WATER_TREATMENT_USD_PER_KG);
                tot_annual_cost = (tot_capex / 20.0) + fix_opex + wat_cost + STACK_REPLACEMENT_ANNUAL_USD;
                lcoh = tot_annual_cost / Total_H2_in_year;
                
                if lcoh < min_lcoh
                    min_lcoh = lcoh;
                    best_pv = n_pv;
                    best_batt = n_batt;
                end
                break;
            end
        end
    end
    
    if ~found_zero_grid
        best_pv = round(N_PV_BASE * 3.0);
        best_batt = round(N_BATTERIES_BASE * 3.0);
        n_ctrl = ceil(best_pv / 22.5);
        tot_capex = (awe_cost + bop_cost + (best_pv * COST_PV) + (n_ctrl * COST_CONTROLLER) + (best_batt * COST_BATTERY)) * (1.0 + SUPPLEMENT_FRAC);
        fix_opex = tot_capex * (LABOR_FRAC + MAINT_FRAC + OTHER_FRAC);
        wat_cost = Total_H2_in_year * 9.0 * (WATER_USD_PER_KG + WATER_TREATMENT_USD_PER_KG);
        tot_annual_cost = (tot_capex / 20.0) + fix_opex + wat_cost + STACK_REPLACEMENT_ANNUAL_USD;
        min_lcoh = tot_annual_cost / Total_H2_in_year;
    end
    
    opt_pv(s) = best_pv;
    opt_batt(s) = best_batt;
    lcoh_v2(s) = min_lcoh;
end

fprintf('\n=======================================================\n');
fprintf('  VARIATION 2: 100%% STANDALONE OFF-GRID SIZING (Egrid = 0)\n');
fprintf('=======================================================\n');
fprintf('Min Cost H2 ($/kg): %f in %s\n', min(lcoh_v2), strtrim(OurStationData(find(lcoh_v2 == min(lcoh_v2), 1)).Country_Name));
fprintf('Avg PV Panels (Off-Grid): %.0f panels (vs %d base)\n', mean(opt_pv), N_PV_BASE);
fprintf('Avg Batteries (Off-Grid): %.0f batteries (vs %d base)\n', mean(opt_batt), N_BATTERIES_BASE);

%% VARIATION 3: EXTENDED BOP & O2 BYPRODUCT CREDIT
water_demin_cost = Total_H2_in_year * 9.0 * WATER_TREATMENT_USD_PER_KG;
total_extended_opex = water_demin_cost + STACK_REPLACEMENT_ANNUAL_USD;
net_cost_v3_gross = Total_cost_v1 + total_extended_opex;
lcoh_v3_gross = net_cost_v3_gross / Total_H2_in_year;

o2_revenue_annual = Total_H2_in_year * O2_KG_PER_KG_H2 * O2_CREDIT_USD_PER_KG_O2;
net_cost_v3_net = net_cost_v3_gross - o2_revenue_annual;
lcoh_v3_net = net_cost_v3_net / Total_H2_in_year;

fprintf('\n=======================================================\n');
fprintf('  VARIATION 3: EXTENDED BALANCE-OF-PLANT & O2 CREDIT\n');
fprintf('=======================================================\n');
fprintf('Extended BOP LCOH (Without O2 Credit): %f $/kg H2 (Best: %s)\n', min(lcoh_v3_gross), strtrim(OurStationData(find(lcoh_v3_gross == min(lcoh_v3_gross), 1)).Country_Name));
fprintf('Extended BOP LCOH (With O2 Credit @ $0.05/kg O2): %f $/kg H2 (Best: %s)\n', min(lcoh_v3_net), strtrim(OurStationData(find(lcoh_v3_net == min(lcoh_v3_net), 1)).Country_Name));
fprintf('=======================================================\n');


%% Save Results Table & Workspace
country_names = strtrim(cellstr(char({OurStationData.Country_Name}')));
results_table = table(...
    country_names, ...
    prices_vec', ...
    Egrid_copy', ...
    H2_Kg_cost', ...
    Egrid_v1', ...
    lcoh_v1', ...
    opt_pv', ...
    opt_batt', ...
    lcoh_v2', ...
    lcoh_v3_gross', ...
    lcoh_v3_net', ...
    'VariableNames', { ...
        'Country', ...
        'ElectricityPrice_USD_per_kWh', ...
        'Base_Egrid_kWh', ...
        'Base_LCOH_USD_per_kg', ...
        'Var1_Smart_Egrid_kWh', ...
        'Var1_Smart_LCOH_USD_per_kg', ...
        'Var2_OffGrid_PV_Panels', ...
        'Var2_OffGrid_Batteries', ...
        'Var2_OffGrid_LCOH_USD_per_kg', ...
        'Var3_ExtendedBOP_Gross_LCOH_USD_per_kg', ...
        'Var3_ExtendedBOP_Net_LCOH_USD_per_kg' ...
    });

writetable(results_table, 'matlab_comprehensive_results.csv');
save('matlab_results.mat', 'results_table', 'OurStationData', 'H2_Kg_cost', 'lcoh_v1', 'lcoh_v2', 'lcoh_v3_gross', 'lcoh_v3_net', 'opt_pv', 'opt_batt', 'Egrid_copy', 'Egrid_v1');
fprintf('\nSuccessfully saved comprehensive results to matlab_comprehensive_results.csv and matlab_results.mat!\n');
toc;
