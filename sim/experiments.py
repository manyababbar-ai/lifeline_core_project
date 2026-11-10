import os
import json
import time
import numpy as np
import pandas as pd
from typing import Dict, Any, List

from sim.city_model import CityModel
from sim.request_generator import RequestGenerator
from sim.policies import NearestHospitalPolicy, PortalCountPolicy, LifelineCorePolicy
from sim.simulator import SimulationEngine

def run_experiment_suite(num_seeds: int = 20, num_requests_per_run: int = 200) -> Dict[str, Any]:
    print(f"=== Starting Lifeline-Core v2 Three-Policy Evaluation Suite ===")
    print(f"Seeds: {num_seeds} | Requests per run: {num_requests_per_run}")

    city = CityModel()
    scenarios = ['normal_day', 'contention_surge', 'feed_outage_chaos']

    suite_results = {}

    for scenario in scenarios:
        print(f"\n--- Running Scenario: {scenario.upper()} ---")
        scenario_records = {'NEAREST_HOSPITAL': [], 'PORTAL_COUNT': [], 'LIFELINE_CORE': []}

        freeze_list = ['HOSP-101', 'HOSP-102', 'HOSP-103', 'HOSP-108'] if scenario == 'feed_outage_chaos' else []
        include_surge = (scenario == 'contention_surge')

        for seed in range(42, 42 + num_seeds):
            # Generate identical workload for all three policies in this seed
            gen = RequestGenerator(seed=seed)
            workload = gen.generate_workload(
                num_requests=num_requests_per_run,
                include_surge=include_surge
            )

            # 1. Policy A: Nearest
            sim_a = SimulationEngine(city=city, seed=seed, chaos_freeze_hospitals=freeze_list)
            res_a = sim_a.run_simulation(workload, NearestHospitalPolicy())
            scenario_records['NEAREST_HOSPITAL'].append(res_a)

            # 2. Policy B: Portal-Count
            sim_b = SimulationEngine(city=city, seed=seed, chaos_freeze_hospitals=freeze_list)
            res_b = sim_b.run_simulation(workload, PortalCountPolicy())
            scenario_records['PORTAL_COUNT'].append(res_b)

            # 3. Policy C: Lifeline-Core
            sim_c = SimulationEngine(city=city, seed=seed, chaos_freeze_hospitals=freeze_list)
            res_c = sim_c.run_simulation(workload, LifelineCorePolicy())
            scenario_records['LIFELINE_CORE'].append(res_c)

        # Aggregate statistics (mean +/- std)
        summary = {}
        for pol_name, runs in scenario_records.items():
            miss_rates = [r['bed_miss_rate_pct'] for r in runs]
            mean_times = [r['mean_time_to_treatment_min'] for r in runs]
            p95_times = [r['p95_time_to_treatment_min'] for r in runs]
            redirects = [r['redirect_count'] for r in runs]
            double_allocs = [r['double_allocation_count'] for r in runs]

            summary[pol_name] = {
                'bed_miss_rate_mean': round(float(np.mean(miss_rates)), 2),
                'bed_miss_rate_std': round(float(np.std(miss_rates)), 2),
                'mean_time_to_treatment_min': round(float(np.mean(mean_times)), 2),
                'mean_time_to_treatment_std': round(float(np.std(mean_times)), 2),
                'p95_time_to_treatment_min': round(float(np.mean(p95_times)), 2),
                'redirects_mean': round(float(np.mean(redirects)), 1),
                'double_allocation_count': int(np.sum(double_allocs))
            }
            print(f"[{pol_name:18s}] Bed Miss: {summary[pol_name]['bed_miss_rate_mean']}% +/- {summary[pol_name]['bed_miss_rate_std']}% | "
                  f"Mean Time: {summary[pol_name]['mean_time_to_treatment_min']}m | "
                  f"Redirects: {summary[pol_name]['redirects_mean']} | "
                  f"Double Allocs: {summary[pol_name]['double_allocation_count']}")

        suite_results[scenario] = {
            'summary': summary,
            'raw_runs': scenario_records
        }

    # Save to disk
    os.makedirs('sim/results', exist_ok=True)
    with open('sim/results/three_policy_comparison.json', 'w') as f:
        json.dump(suite_results, f, indent=2)

    # Flatten summary to CSV
    csv_rows = []
    for scn, data in suite_results.items():
        for pol, stats in data['summary'].items():
            row = {'scenario': scn, 'policy': pol, **stats}
            csv_rows.append(row)

    pd.DataFrame(csv_rows).to_csv('sim/results/three_policy_comparison.csv', index=False)
    print("\n[Simulation Engine] Successfully generated and saved results to sim/results/")
    return suite_results

if __name__ == '__main__':
    run_experiment_suite(num_seeds=20, num_requests_per_run=150)
